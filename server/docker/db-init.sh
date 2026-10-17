#!/usr/bin/env bash
# Creates the application's role at the database's first start: it logs in, owns nothing,
# and gets its grants (no DELETE anywhere) from the migrations (DATABASE_SCHEMA.md, "First
# migration"). Runs once, from /docker-entrypoint-initdb.d, as the owner.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$FLOPPY_DB_APP_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE floppy_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'floppy_app')
\gexec
GRANT CONNECT ON DATABASE floppy TO floppy_app;
GRANT USAGE ON SCHEMA public TO floppy_app;
SQL
