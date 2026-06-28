# Peer

Global Tau extension for local peer discovery and messaging.

## Commands

```text
/peer
/peer join <name>
/peer list
/peer send <name> <message>
/peer inbox [limit]
/peer receipts [limit]
/peer leave
```

The extension also registers the `peer` tool for agent-driven communication.

State is stored under `~/.tau/peers/` by default:

```text
registry/       active peer registrations
sockets/        Unix-domain sockets
mailboxes/      pending and delivered messages
receipts/       durable delivery receipts
```

Socket notifications provide immediate delivery. Mailbox files remain the source
of truth when a recipient is busy or temporarily unreachable.

Optional extension settings:

```json
{
  "name": "coordinator",
  "root": "~/.tau/peers",
  "auto_join": true
}
```
