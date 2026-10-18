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

- **A domain.** The disks' addresses get one wildcard certificate if its DNS is at a provider Traefik can update by API (Cloudflare and OVH are documented below, any provider Traefik supports works), or one certificate per disk without any DNS credential.
- **An SMTP server** for the sign-in links.
- **S3 storage** at any provider, one bucket per disk, in the same region as the server, with versioning and, where the provider has it, Object Lock (see [A disk's bucket](#a-disks-bucket)).

## How Macs reach it

Everything goes through port 443, the one office networks leave open:

- `https://disk.example.com` is the admin web, with the API under `/api/`.
- `<label>.meta.disk.example.com` on the same port is one disk's Valkey. Traefik reads the name in the TLS handshake and sends the connection to that disk; the label is random, so no disk's name shows up in DNS or in certificate logs.

TLS ends at Traefik, which also renews the certificates; behind it the traffic stays on the server's private network.

## Install with Dokploy

1. **DNS**: point `disk.example.com` and `*.meta.disk.example.com` at the server (A, and AAAA if it has IPv6). Only ports 80 and 443 need to be open.
2. **Traefik**: in Dokploy, under Web Server, Traefik, edit its configuration and merge in the blocks of `deploy/traefik/traefik.yml`, then reload Traefik. Traefik's timeouts, and every other application it serves, stay as they are: idle disk connections are not cut. The disks' certificates come one of two ways:
   - **One wildcard**, by DNS challenge: merge both blocks, the resolver and the address the meta service publishes its routes on, and under Modify Environment add the DNS provider's credentials. **Cloudflare**: `CF_DNS_API_TOKEN`, a token limited to the zone with Zone DNS Edit and Zone Read. **OVH**: `OVH_ENDPOINT=ovh-eu`, `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET` and `OVH_CONSUMER_KEY`, with a consumer key allowed `POST` and `DELETE` on `/domain/zone/*`.
   - **One certificate per disk**, with no DNS credential in the shared Traefik: merge only the routes block, and set `FLOPPY_META_CERT_SCOPE=host` and `FLOPPY_META_CERT_RESOLVER=letsencrypt` (Dokploy's own resolver). A new disk gets its certificate by HTTP challenge within seconds of its creation; its random label shows up in certificate logs, and says nothing about the disk.
3. **The service**: create a Compose service from %repo% with the compose path `deploy/docker-compose.yml`, type Docker Compose. Leave its Domains tab empty and Isolated Deployments off: the routes are in the file.
4. **Variables**: copy `deploy/.env.example` into the Environment tab and fill it in: the domain, the generated secrets, SMTP, the subnet of `dokploy-network` as the trusted proxy, and the first organization with its admin's address and default S3 endpoint.
5. **Deploy.** The first start migrates the database and creates the organization; its admin signs in at `https://disk.example.com` with the link that arrives by mail, then creates the disks and invites people.
6. **Backups**: in the compose's Backups, add a database backup of the `db` service (database `floppy`, user `floppy_owner`) and a volume backup of `meta-data`, both daily to an S3 destination at another provider. The volume backup runs with the service up (see [Durability and backups](#durability-and-backups)).

Without Dokploy, the same file runs with `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build` on any host whose Traefik reads Docker labels and has the same two blocks.

## A disk's bucket

In manual mode each disk has a bucket and a key that you create at the provider and paste when you create the disk. The key is stored in the disk's file tree, which every Mac that may see the disk replicates: count it as known to all of them. Set the bucket up so that this key can do the disk's work and nothing that destroys history.

1. **Create the bucket** in the server's region with versioning and Object Lock. Object Lock can only be turned on when a bucket is created. Then set its default retention to **governance mode, 7 days**: every block is kept at least that long, and JuiceFS's deletions become delete markers.
2. **Add the lifecycle rule** in `deploy/s3/lifecycle.json`: old versions expire 7 days after they are replaced or deleted, so deleted blocks are not billed forever.
3. **Create a user for the disk** and give it the policy in `deploy/s3/disk-policy.json`, with the bucket's name in place of `BUCKET`. It allows what JuiceFS uses, on that bucket only, and denies changing versioning, the lifecycle or Object Lock and bypassing the retention. Set the lifecycle before the policy: afterwards the disk's key cannot.
4. **Check it**, with the user's key in a file holding `AWS_ACCESS_KEY_ID=` and `AWS_SECRET_ACCESS_KEY=` lines (the script never prints it):

   ```bash
   S3_ENDPOINT=https://s3.example.com AWS_REGION=<region> deploy/s3/check-disk-bucket.sh <bucket> <key file>
   ```

   It writes, reads and deletes a probe object, then tries what the policy must refuse with requests that would change nothing if they went through. A new policy can take a few minutes to apply: if refusals pass at first, run it again.
5. **Create the disk** in the admin web with the bucket and the user's key.

What it costs: a block JuiceFS deletes or compacts stays billed for 7 more days. What it covers: a deleted file stays 7 days in the disk's trash; an edited one comes back from a night's metadata backup; and no key, the disk's included, can erase a block in its first 7 days.

- **OVH** has no separate permission for erasing versions: `s3:DeleteObject` does both, and JuiceFS needs it, so there only Object Lock protects the versions. The control panel may show the default retention wrong; read it with `aws s3api get-object-lock-configuration`.
- **AWS**: add `s3:DeleteObjectVersion` to the policy's denials as well.

## Security

- The admin web sends a strict Content Security Policy, HSTS, no referrer and refuses to be framed.
- Each Mac has its own password on each disk it may see, with read-only or read-write rights; revoking a Mac or lowering someone's access applies at once.
- Secrets the control plane must use, such as S3 keys and the SMTP password, are encrypted in the database with a key that lives only in the server's environment. Keep a copy of that key outside the server.

## Durability and backups

- **The file tree is the filesystem.** Without it the blocks on S3 cannot be put back together into files. Each disk's Valkey writes every change to disk before answering, and its ACL users are saved with it, so a restart of the meta service brings every disk back within seconds.
- **Back up both volumes every day, outside the server.** On Dokploy, its database backup of `db` and its volume backup of `meta-data` (step 6). The volume backup is sound with the service up: each disk's Valkey has written every change before answering, and its snapshots are written whole. Keep `FLOPPY_SECRET_KEY` outside the server too, or the database backup cannot decrypt the disks' keys.
- **Try a restore once.** From the volume backup, take a disk's directory under `instances/`, start `valkey-server` on it, and mount the disk read-only on its bucket with JuiceFS (`juicefs mount --read-only redis://<host>:<port>/1`); its files must read back. The same mount brings back an edited file from a night's backup.
- **The replicas on the Macs are not a backup**: they follow the server, deletions included.
- **JuiceFS is not a backup either.** Object Lock covers a misused key, not the provider account: before a bucket holds the only copy of a team's work, replicate it every night to a second provider, next to that night's metadata backup.

If the meta service stops, mounted disks turn read-only: browsing and cached clips keep working from each Mac's replica, saving waits until it is back. If the control plane stops, nothing changes for mounted disks.
