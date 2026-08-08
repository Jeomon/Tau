"""Remote access to a running Tau: framing, protocol, server and client.

Tau's ``--mode rpc`` speaks the same command surface over stdin/stdout, one
process per session. This package is the socket-shaped sibling: several clients
can attach to one session, and the framing underneath is length-prefixed rather
than newline-delimited, because a socket offers none of the guarantees a pipe
between related processes does.

The command vocabulary is deliberately *not* redefined here — the dispatcher in
``tau.modes.rpc.mode`` is the single implementation, and this package supplies
it with a per-connection sink instead of stdout. Two command surfaces that
drift apart is exactly the failure the ``json``/``rpc`` event lists already had
once.
"""

from typing import TYPE_CHECKING

from tau.remote.framing import (
    DEFAULT_MAX_FRAME_LENGTH,
    FRAME_HEADER_LENGTH,
    FrameDecoder,
    FrameError,
    encode_frame,
)
from tau.remote.protocol import PROTOCOL_VERSION, ProtocolError, decode_message, encode_message

if TYPE_CHECKING:
    # Re-exported lazily at runtime by __getattr__ below; imported here so type
    # checkers and editors can still resolve them.
    from tau.remote.client import RemoteClient, RemoteDisconnected
    from tau.remote.server import RemoteServer, SocketInUseError

__all__ = [
    "DEFAULT_MAX_FRAME_LENGTH",
    "FRAME_HEADER_LENGTH",
    "PROTOCOL_VERSION",
    "FrameDecoder",
    "FrameError",
    "ProtocolError",
    "RemoteClient",
    "RemoteDisconnected",
    "RemoteServer",
    "SocketInUseError",
    "decode_message",
    "encode_frame",
    "encode_message",
]


def __getattr__(name: str) -> object:
    """Load the socket halves lazily.

    ``server`` pulls in the RPC dispatcher and ``client`` opens asyncio stream
    machinery, neither of which a caller importing only the framing helpers
    should pay for — and importing the dispatcher eagerly would drag stdio
    protocol state into processes that never speak it.
    """
    if name in ("RemoteServer", "SocketInUseError"):
        from tau.remote import server

        return getattr(server, name)
    if name in ("RemoteClient", "RemoteDisconnected"):
        from tau.remote import client

        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
