---
paths:
  - "server/**"
---
# The control plane (server/)

Commands, from `server/` (secrets in `server/.env`, copied from `.env.example`, outside git):

```bash
docker compose --profile test up -d --build db meta-test-1 meta-test-2 meta-test-3 meta-test   # the stores for the tests (the fixtures empty meta-test-*; meta-1..3 are the dev pool; meta-test is the real meta service)
docker compose up -d --build                        # full stack: migrates, bootstraps, serves on :4507
uv run --env-file .env pytest
uv run --env-file .env floppy-server migrate|bootstrap --from-env|reconcile|reencrypt
docker compose --profile s3 up -d garage && docker/garage-setup.sh   # local S3 and Garage's admin API (4540)
docker compose build server                         # before the JuiceFS tests: the image carries the patched juicefs
```

- **The schema is docs/DATABASE_SCHEMA.md**: a migration and the schema doc change in the same commit.
- **The application role has no DELETE**: soft delete or a terminal state, never a delete (DECISIONS #16). The one exception is `file_events`, the activity purged after 30 days.
- **Debian already has a `floppy` group**: the container's user is `controlplane`.
- **After `ACL DELUSER` redis-py does not reconnect by itself**: the Valkey adapter reconnects explicitly.
- **The admin web against this server in dev** needs a `/api` proxy to `127.0.0.1:4507` in `web/vite.config.ts` and `VITE_API=http`.
- **Compose subnet 10.250.48.0/24**: spike/juicefs-replica/test.sh uses .46, the spike .45.
- **Managed formats hold no S3 key**: the control plane formats with its own key in `AWS_*`, never `ACCESS_KEY` (a new format would store it); manual formats hold the disk's key (DECISIONS #24).
- **Compose subnets**: `deploy/` uses 10.250.49.0/24, `deploy/check` .50 and .51, `deploy/local` .56 and .57, its manual stack (`STACK=manual`) .58 and .59.
- **MCP tools carry no secret** (DECISIONS #29): a tool that would take an S3 key or a password stays in the admin web, since what a person types to Claude lands in the model's context and the client's transcript. A tool calls its REST endpoint's router function, so both change together. The OAuth paths and `/mcp` are at the origin's root: a new one needs its Traefik rule (`deploy/docker-compose.yml`, `deploy/local/compose.yml`) and the Vite proxy (`web/vite.config.ts`).
- **A device's Valkey user needs more than reads and writes**: INFO (the patch's probe), PSYNC, REPLCONF and PING for its replica, and on a writable disk `@transaction`, EVAL and EVALSHA for the optimistic writes; `-@dangerous` also drops INFO, PSYNC and REPLCONF, so they are added after it. The lists are in `spike/perdisk.py` and `infra/metadata.py`.
