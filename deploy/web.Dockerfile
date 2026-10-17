# The admin web (web/): built with pnpm, then served as static files by Caddy with the
# security headers of deploy/web/Caddyfile, as an unprivileged user. The API is not here:
# Traefik sends /api/ to the control plane on the same origin.
FROM node:24-alpine AS build
RUN corepack enable && corepack prepare pnpm@11.17.0 --activate
WORKDIR /src
COPY web/package.json web/pnpm-lock.yaml web/
RUN cd web && pnpm install --frozen-lockfile
# The shared tokens and marks are imported from design/ at build time (DECISIONS #9).
COPY design design
COPY web web
RUN cd web && pnpm build

FROM caddy:2.11-alpine
COPY deploy/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/web/dist /srv
# Caddy keeps nothing: no admin endpoint, no certificates (TLS ends at Traefik).
ENV XDG_DATA_HOME=/tmp XDG_CONFIG_HOME=/tmp
USER 65534:65534
EXPOSE 8080
