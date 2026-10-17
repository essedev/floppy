# PostgreSQL 18 (uuidv7(), DECISIONS #16) with the script that creates the application's
# role at the first start, baked in: Dokploy re-clones the repository at every deploy, so a
# bind mount from it is not a stable file.
FROM postgres:18
COPY server/docker/db-init.sh /docker-entrypoint-initdb.d/10-app-role.sh
