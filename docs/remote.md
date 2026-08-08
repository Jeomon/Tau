# Remote Access

Remote access exposes one running Tau session over a unix socket so that **several clients can watch and drive the same agent at once**. It is the socket-shaped sibling of [RPC mode](rpc.md): the same commands, the same events, a different transport.

Use it when one session needs more than one viewer or driver — a TUI plus a dashboard, an editor plugin alongside a log tailer, a supervisor watching a worker. If you only need one client, RPC mode over stdin/stdout is simpler and needs no socket.

## Table of Contents

- [Starting a Server](#starting-a-server)
- [Scope](#scope)
- [Server API](#server-api)
- [Client](#client)
- [Framing](#framing)
- [Responses and Events](#responses-and-events)
- [Extension Dialogs](#extension-dialogs)
- [Slow Clients](#slow-clients)
- [Socket Hygiene](#socket-hygiene)
- [Limits](#limits)

## Starting a Server

```bash
tau --mode remote
# tau: serving on /Users/you/.tau/remote/<session-id>.sock
# tau: press Ctrl-C to stop
```

The default path is named for the session. Override it with `--socket`:

```bash
tau --mode remote --socket /tmp/tau/work.sock
```

Unlike `--mode rpc`, stdout is **not** a protocol stream — it prints where it is listening and nothing else, so it is safe to read as a human or log to a file. Ctrl-C stops the server and removes the socket.

## Scope

**One server serves one runtime.** A `Runtime` owns its session, so this is multi-client access to the session the server was handed — not a daemon that lists, creates, and hosts sessions on demand.

That boundary is deliberate. Hosting several sessions needs a session-factory layer Tau does not have, and a half-built one would be worse than none.

## Server API

Use this to embed a server in your own process; the CLI above is a thin wrapper over it.


```python
import asyncio
from tau.remote import RemoteServer

async def main(runtime):
    server = RemoteServer(runtime, "/tmp/tau/session.sock")
    await server.start()
    await server.serve_forever()
```

`start()` binds the socket and subscribes to the runtime's events. `close()` unsubscribes, disconnects every client, and removes the socket file.

| Argument | Default | Meaning |
| --- | --- | --- |
| `runtime` | — | The `Runtime` to serve. |
| `socket_path` | — | Where to bind. Keep it short (see [Limits](#limits)). |
| `max_frame_length` | 16 MiB | Largest single message accepted. |
| `max_queued` | 1024 | Messages a client may fall behind by before it is dropped. |

## Client

```python
from tau.remote import RemoteClient

async with RemoteClient("/tmp/tau/session.sock") as client:
    response = await client.request({"type": "prompt", "message": "hello"})
    event = await client.next_event()
```

`connect()` returns the server's `ready` greeting rather than discarding it, so the version and capability handshake cannot be skipped by accident:

```json
{
  "type": "ready",
  "protocolVersion": 1,
  "capabilities": {"toolCallBlocking": true, "interceptableEvents": ["..."], "projectTrust": true},
  "attached": 1
}
```

`capabilities` is the same block RPC mode announces, derived from live state rather than declared — see [RPC Mode](rpc.md).

## Framing

Each message is a UTF-8 JSON object prefixed with its unsigned 32-bit big-endian byte length:

```
+--------+--------+--------+--------+----------------------+
| length (4 bytes, big-endian)      | JSON payload         |
+--------+--------+--------+--------+----------------------+
```

RPC mode delimits with newlines instead, which is safe between related processes. A socket offers no such guarantee: reads arrive split or coalesced, and "read until newline" on a stream that never sends one allocates without bound at the peer's discretion. Reading the length first is what lets an oversized frame be refused before its payload is ever buffered.

Message *shapes* are identical to RPC mode — same commands, same `snake_case` event fields, same serialization fallbacks — because both use one encoder. Only the delimiting differs.

## Responses and Events

**Responses are point-to-point; events are broadcast.** A response goes only to the client that sent the command; an event describes the shared session, so every attached client receives it.

Correlate responses by `id`. `RemoteClient.request()` does this for you: it awaits the matching id and lets everything else fall through to `next_event()`. A client that simply read the next message would routinely mistake an event for its answer.

## Extension Dialogs

An extension calling `ctx.select()`, `ctx.confirm()`, or `ctx.input()` emits an `extension_ui_request`, which is **broadcast to every attached client**. The first client to answer with `extension_ui_response` carrying the matching `id` wins; later answers for that id are discarded, because the request is resolved and removed when the first arrives.

Fire-and-forget calls (`notify`, `set_status`) are broadcast the same way and expect no reply.

If no client answers, a dialog with a `timeout` resolves to `None` — the same value as a cancel — so an unattended server cannot wedge an extension forever. A dialog with no timeout waits indefinitely, exactly as it does over stdio.

## Slow Clients

A client that stops reading is **disconnected**, not waited for. Its outbound queue is bounded by `max_queued`; when it fills, the connection is dropped.

The alternative — an unbounded queue, or awaiting a blocked write inside event delivery — would let any observer stall the agent that every other client is using. A dropped client can reconnect; a stalled agent helps no one.

## Socket Hygiene

- The parent directory is created `0o700` and the socket `0o600` — owner only.
- A **socket** left behind by a crashed server is probed; if nothing is listening, it is replaced.
- A **regular file** at the socket path is refused, never deleted. Everything the server removes it removes unprompted, so it only ever removes what a server clearly left behind.
- A **live** server on the same path raises `SocketInUseError` rather than silently splitting clients between two servers.
- Dead sockets from earlier runs are **swept on startup**. Paths are named for their session and never reused, so a server killed without unwinding (SIGKILL, crash, power loss) leaves a file the replace-on-bind check would never revisit. The sweep removes only sockets nothing is listening on, and never the path being bound.

## Limits

- **Unix sockets only.** No TCP, so there is no authentication surface to get wrong; access is governed by file permissions.
- **Path length.** `sun_path` is capped near 104 bytes on macOS and 108 on Linux. Long paths fail at bind — prefer `/tmp/tau/<id>.sock` over a deeply nested directory.
- **No session multiplexing.** One server, one session. See [Scope](#scope).
- **No reconnect-and-resume.** A reconnecting client gets a fresh `ready` greeting and the events from that point on; it does not replay what it missed while disconnected.
- **No initial prompt.** `--prompt` and `--file` are rejected rather than accepted and ignored: remote mode serves a session instead of running one turn, so a `tau -p ... --mode remote` invocation would otherwise serve forever having never run the prompt. Start the server, then send a `prompt` command from a client.

## Next Steps

- [RPC Mode](rpc.md) — the command and event vocabulary in full
- [Python API](python-api.md) — driving a `Runtime` in process
- [Extensions](extensions.md) — hooks, including the interceptable events advertised in `capabilities`
