# /api/workspace

Parent of the workspace read proxy (see `[...seg]/`). Forwards knowledge-graph tree/file reads to agent-api `/api/workspace/*`.

Acting for the shared product-repo identity (`?for=` on any request, or `for_subject` in the `swap` body) is
admin-only here — a non-admin gets `403` and agent-api is never called. Every other request is forwarded as before.
