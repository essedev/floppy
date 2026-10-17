---
paths:
  - "server/**"
---
# The control plane (server/)

Commands, from `server/` (secrets in `server/.env`, copied from `.env.example`, outside git):

```bash
docker compose up -d db valkey                      # only the stores, for the tests
docker compose up -d --build                        # full stack: migrates, bootstraps, serves on :4507
uv run --env-file .env pytest
uv run --env-file .env floppy-server migrate|bootstrap --from-env|reconcile|reencrypt
```

- **The schema is docs/DATABASE_SCHEMA.md**: a migration and the schema doc change in the same commit.
- **The application role has no DELETE**: soft delete or a terminal state, never a delete (DECISIONS #16).
- **Debian already has a `floppy` group**: the container's user is `controlplane`.
- **After `ACL DELUSER` redis-py does not reconnect by itself**: the Valkey adapter reconnects explicitly.
- **The admin web against this server in dev** needs a `/api` proxy to `127.0.0.1:4507` in `web/vite.config.ts` and `VITE_API=http`.
