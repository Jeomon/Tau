from typing import Optional, Dict, Any, Callable,Annotated, List, Awaitable
from operator import add
import websockets
from websockets.exceptions import ConnectionClosed
import asyncio
import logging
import json

from .domains import Domains

_TIMEOUT_UNSET = object()


class Client(Domains):
    """
    Core client for interacting with Chrome DevTools Protocol (CDP).
    
    This class provides a high-level API to send commands and listen for events across
    various CDP domains. It manages the underlying WebSocket connection and dispatches
    messages to the appropriate handlers.
    
    Attributes:
        url (str): The WebSocket URL of the remote debugging target.
        ws (websockets.ClientConnection): The active WebSocket connection.
        listen_task (asyncio.Task): Background task processing incoming CDP messages.
        id_counter (int): Counter for generating unique request IDs.
        pending_requests (Dict[int, asyncio.Future]): Tracks outstanding requests by ID.
        event_handlers (Dict[str, List[Callable]]): Registered callbacks for CDP events.
    """
    def __init__(self, url: str, refresh: bool = False, timeout: float | None = 60.0):
        """
        Initialize the CDP Client.

        Args:
            url (str): WebSocket debugger URL.
            refresh (bool): If True, regenerates the CDP protocol definitions on initialization.
            timeout (float, optional): Maximum seconds to wait for a command response
                before raising TimeoutError. Guards against a socket that stays open
                while the remote browser/container has become unresponsive. None disables
                the timeout.
        """
        super().__init__(self)
        self.url = url
        self.timeout = timeout
        self.ws :Optional[websockets.ClientConnection] = None
        self.listen_task :Optional[asyncio.Task] = None
        self.id_counter: Annotated[int, add] = 0
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.event_handlers: Dict[str, List[Callable[[Any, Optional[str]], None]]] = {}
        self.disconnect_handlers: List[
            Callable[[BaseException | None], Awaitable[None] | None]
        ] = []
        self._closing = False

        if refresh:
            self.refresh()

    async def __aenter__(self):
        """Connect to the WebSocket and start the background listener."""
        return await self.connect()

    async def connect(self):
        """Open the WebSocket connection if it is not already connected."""
        if self.ws is not None:
            return self
        self._closing = False
        self.ws = await websockets.connect(self.url,max_size=100*1024*1024)
        self.listen_task = asyncio.create_task(self.listen())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cancel the listener, clear pending requests, and close the WebSocket."""
        await self.close()

    async def close(self) -> None:
        """Close the transport without notifying disconnect handlers."""
        self._closing = True
        self._fail_pending(ConnectionError("CDP WebSocket connection closed"))
        if self.listen_task:
            try:
                self.listen_task.cancel()
                await self.listen_task
            except asyncio.CancelledError:
                pass
            finally:
                self.listen_task = None
        if self.ws:
            await self.ws.close()
            self.ws = None
        self._closing = False

    async def send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: Any = _TIMEOUT_UNSET,
    ) -> Any:
        """
        Send a CDP command and wait for the result.

        Args:
            method (str): The CDP method name (e.g., 'Page.navigate').
            params (dict, optional): Parameters for the method.
            session_id (str, optional): Target session ID for flat protocol usage.
            timeout (float, optional): Override the client-level default timeout
                for this call (e.g. a longer budget for a slow full-page
                screenshot). Pass None to disable the timeout for this call.
                Defaults to the client's configured `self.timeout`.

        Returns:
            Any: The 'result' object from the CDP response.

        Raises:
            Exception: If the CDP returns an error or the connection is lost.
        """
        if self.ws is None:
            raise ConnectionError("CDP client is not connected")
        self.id_counter+=1
        request_id = self.id_counter
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        effective_timeout = self.timeout if timeout is _TIMEOUT_UNSET else timeout

        try:
            message = {"id": request_id, "method": method, "params": params or {}}
            if session_id:
                message['sessionId'] = session_id
            await self.ws.send(json.dumps(message))
            if effective_timeout is None:
                return await future
            try:
                return await asyncio.wait_for(future, timeout=effective_timeout)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"CDP command {method!r} did not respond within "
                    f"{effective_timeout:g} seconds"
                ) from exc
        except Exception as e:
            self.pending_requests.pop(request_id, None)
            raise e

    def on_disconnect(
        self,
        callback: Callable[[BaseException | None], Awaitable[None] | None],
    ) -> None:
        """Register a callback for unexpected transport disconnections."""
        self.disconnect_handlers.append(callback)

    def remove_disconnect_handler(
        self,
        callback: Callable[[BaseException | None], Awaitable[None] | None],
    ) -> None:
        try:
            self.disconnect_handlers.remove(callback)
        except ValueError:
            pass

    def on(self, event: str, callback: Callable[[Any, Optional[str]], None]) -> None:
        """
        Register an event handler. Alias for `Client.register`.
        
        Args:
            event (str): The CDP event name (e.g., 'Page.loadEventFired').
            callback (callable): Function called with (params, session_id).
        """
        self.register(event, callback)

    def register(self, event: str, callback: Callable[[Any, Optional[str]], None]) -> None:
        """
        Register a handler for a specific CDP event.
        
        Args:
            event (str): The CDP event name.
            callback (callable): Function called with (params, session_id).
        """
        if event not in self.event_handlers:
            self.event_handlers[event] = []
        self.event_handlers[event].append(callback)

    def unregister(
        self,
        event: str,
        callback: Callable[[Any, Optional[str]], None] | None = None,
    ) -> None:
        """
        Unregister one handler, or all handlers for a specific CDP event.
        
        Args:
            event (str): The CDP event name.
            callback (callable, optional): Specific handler to remove.
        """
        if callback is None:
            self.event_handlers.pop(event, None)
            return
        handlers = self.event_handlers.get(event)
        if handlers is None:
            return
        try:
            handlers.remove(callback)
        except ValueError:
            return
        if not handlers:
            del self.event_handlers[event]

    async def listen(self):
        """
        Internal background loop that receives messages from the WebSocket.
        Dispatches responses to pending request futures and events to registered handlers.
        """
        disconnect_error: BaseException | None = None
        while True:
            try:
                message = await self.ws.recv()
                data = json.loads(message)
                if "id" in data:
                    # Method
                    request_id=data["id"]
                    logging.debug(f"Received method response: {data}")
                    if request_id not in self.pending_requests:
                        continue
                    future = self.pending_requests.pop(request_id)
                    if not future.done():
                        if "error" in data:
                            future.set_exception(Exception(data.get("error")))
                        else:
                            future.set_result(data.get("result"))
                elif 'method' in data:
                    # Event
                    method=data.get("method")
                    params = data.get("params", {})
                    session_id=data.get("sessionId")
                    logging.debug(f"Received event: {data}")
                    if method not in self.event_handlers:
                        continue
                    
                    handlers = self.event_handlers[method]
                    for handler in handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                asyncio.create_task(handler(params,session_id))
                            else:
                                handler(params,session_id)
                        except Exception as e:
                            logging.error(f"Error in event handler for {method}: {e}")
                            continue
            except ConnectionClosed:
                logging.info("CDP WebSocket connection closed")
                disconnect_error = ConnectionError("CDP WebSocket connection closed")
                break
            except Exception as e:
                logging.error(f"Error in CDP listen loop: {e}")
                disconnect_error = e
                break
        self.ws = None
        self.listen_task = None
        self._fail_pending(
            disconnect_error or ConnectionError("CDP WebSocket connection closed")
        )
        if not self._closing:
            for handler in list(self.disconnect_handlers):
                try:
                    result = handler(disconnect_error)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logging.exception("CDP disconnect handler failed")

    def _fail_pending(self, error: BaseException) -> None:
        for future in self.pending_requests.values():
            if not future.done():
                future.set_exception(error)
        self.pending_requests.clear()
