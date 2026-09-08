# Cleanup Retry Demo

[Guide home](../../../docs/MOD_AUTHORING.md) | [Descriptor](evejs-launcher.mod.json) | [Helper](helper.js)

Import this folder into a disposable installation and enable it. It creates two
synthetic owned records in its own `demo-state.json`; it changes no game data,
client file or service. Node.js must be available on PATH.

Remove or disable it. The first call retires one record, persists progress and
reports `pending`. The launcher keeps the package and its activation available
for cleanup. Repeat the same action: the second record is retired, `ready` is
reported and the launcher may finish removal/disable. Further cleanup calls are
safe. Undo restores the folder disabled; enabling creates a new demonstration run.

The persisted `retiring` phase represents gameplay disabled while its cleanup
provider remains available. A real loader must read its own retirement state and
stop normal work during that phase. This example has no gameplay loop.

To check ownership, add a record with a different `owner` string to this private
demonstration file between calls. It must survive. Never delete all records merely
because they belong to the same entity family. Keep progress durable before
replying: if the reply is lost, retry resumes from committed state. Corrupt state
produces a failed result and is preserved; do not report ready to hide an error.

This is an example of the public lifecycle, not a transaction template for a
running database. Real stateful mods need their own database transaction/locking,
runtime retirement hooks and crash recovery. Helpers are ordinary executable code;
the launcher serializes its own lifecycle calls but cannot lock an external game
server's independent writes. Require its shutdown or use a proper runtime API.
