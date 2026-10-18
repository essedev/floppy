---
title: Self-hosting
description: Run your own %name% on one server with Dokploy, with your domain and any S3.
---

Everything %name% needs runs on infrastructure you pick. It speaks only standard S3 and the Redis protocol, which Valkey implements: no provider API is part of the core.

## What you run

One Docker Compose file, `deploy/docker-compose.yml` in the repository, with four services:

- **The control plane**: the API that holds organizations, people, Macs and disks, sends the sign-in links and decides who may see each disk. It holds no file data, and mounted disks keep working while it is down.
- **The admin web**, served from the same address as the API.
- **PostgreSQL 18**, for the control plane's tables.
- **The meta service**, which runs one Valkey per disk: the disk's file tree, written by JuiceFS, with a replica of it on every Mac that may see the disk. A replica copies a whole Valkey, so each disk has its own and no Mac ever receives a disk it may not see.

Besides the server you need:

- **A domain** whose DNS is at a provider Traefik can update by API, for the wildcard certificate of the disks' addresses. Cloudflare and OVH are documented below; any DNS provider Traefik supports works.
- **An SMTP server** for the sign-in links.
- **S3 storage** at any provider, one bucket per disk, in the same region as the server.

## How Macs reach it

Everything goes through port 443, the one office networks leave open:

- `https://disk.example.com` is the admin web, with the API under `/api/`.
- `<label>.meta.disk.example.com` on the same port is one disk's Valkey. Traefik reads the name in the TLS handshake and sends the connection to that disk; the label is random, so no disk's name shows up in DNS or in certificate logs.

TLS ends at Traefik, which also renews the certificates; behind it the traffic stays on the server's private network.

## Install with Dokploy

1. **DNS**: point `disk.example.com` and `*.meta.disk.example.com` at the server (A, and AAAA if it has IPv6). Only ports 80 and 443 need to be open.
2. **Traefik**: in Dokploy, under Web Server, Traefik, edit its configuration and merge in the two blocks of `deploy/traefik/traefik.yml`: a resolver that obtains the wildcard by DNS challenge, and the address the meta service publishes its routes on. Traefik's timeouts, and every other application it serves, stay as they are: idle disk connections are not cut. Under Modify Environment, add the DNS provider's credentials, then reload Traefik.
   - **Cloudflare**: `CF_DNS_API_TOKEN`, a token limited to the zone with Zone DNS Edit and Zone Read.
   - **OVH**: `OVH_ENDPOINT=ovh-eu`, `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET` and `OVH_CONSUMER_KEY`, with a consumer key allowed `POST` and `DELETE` on `/domain/zone/*`.
3. **The service**: create a Compose service from %repo% with the compose path `deploy/docker-compose.yml`, type Docker Compose. Leave its Domains tab empty and Isolated Deployments off: the routes are in the file.
4. **Variables**: copy `deploy/.env.example` into the Environment tab and fill it in: the domain, the generated secrets, SMTP, the subnet of `dokploy-network` as the trusted proxy, and the first organization with its admin's address and default S3 endpoint.
5. **Deploy.** The first start migrates the database and creates the organization; its admin signs in at `https://disk.example.com` with the link that arrives by mail, then creates the disks and invites people.

Without Dokploy, the same file runs with `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build` on any host whose Traefik reads Docker labels and has the same two blocks.

## Security

- The admin web sends a strict Content Security Policy, HSTS, no referrer and refuses to be framed.
- Each Mac has its own password on each disk it may see, with read-only or read-write rights; revoking a Mac or lowering someone's access applies at once.
- Secrets the control plane must use, such as S3 keys and the SMTP password, are encrypted in the database with a key that lives only in the server's environment. Keep a copy of that key outside the server.

## Durability and backups

- **The file tree is the filesystem.** Without it the blocks on S3 cannot be put back together into files. Each disk's Valkey writes every change to disk before answering, and its ACL users are saved with it, so a restart of the meta service brings every disk back within seconds.
- **Back up both volumes** (`db-data` and `meta-data`) and keep a daily `juicefs dump` of each disk outside the server.
- **The replicas on the Macs are not a backup**: they follow the server, deletions included.
- **JuiceFS is not a backup either.** Replicate the buckets to a second provider, with versioning there.

If the meta service stops, mounted disks turn read-only: browsing and cached clips keep working from each Mac's replica, saving waits until it is back. If the control plane stops, nothing changes for mounted disks.
