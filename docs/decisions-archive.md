# Decisions archive

Entries of [DECISIONS.md](DECISIONS.md) that are no longer active, with their numbers kept so every `DECISIONS #N` still resolves. Read, never updated.

## 4. Only standard S3 and standard database protocols in the core

**Superseded by #22, 17/10/2026.** The core uses plain S3 and a Redis-compatible metadata server; no provider API. Per-person S3 keys were dropped: the S3 key lives in the volume's metadata, a revoked Mac loses it with its metadata access, and the key is rotated by pasting a new one (`juicefs config`).
**Ruled out:** provisioning S3 users through the OVH API, which would tie the product to one provider.

## 19. Valkey users per Mac and per organization, not per disk

**Superseded by #23, 17/10/2026.** A consequence of #2 and #7: a Mac's replica copies the whole Valkey instance, every disk and its S3 key included, so one user per Mac on the organization's instance gives exactly what the Mac can read anyway. Valkey does not replicate ACL users, so the control plane keeps each password's SHA-256 (the form Valkey stores) and rebuilds the users on a promoted or restored master.
**Ruled out:** a user per disk (it would restrict writes, not reads), an instance per disk until per-disk access is really needed.

## 22. Storage in two modes, with team, per-person or per-Mac keys

**Superseded by #24, 17/10/2026.** Supersedes #4; the core still speaks only plain S3. **Manual**, for any S3: the admin picks one team key (kept in the volumes' format, rotated by hand when someone leaves, #18) or one key per person (made at the provider, limited to the bucket by a policy, pasted when inviting; removing the person or losing one of their Macs means deleting that one key, no rotation). With a team key, bucket versioning is recommended (old versions expiring after 7 days, the key denied version deletion), and the control plane checks and shows whether it is on. **Managed**: an optional, isolated adapter (OVH, AWS, MinIO, Garage to start) creates the bucket and gives each Mac (or person) its own key, deleted at the provider on revocation; where a provider cannot scope keys, it rotates a team key by itself. The hosted service always uses managed; mode and scope are fixed once a disk exists. Per-person and per-Mac keys leave the format empty and need JuiceFS to mount with a key from the environment (the AWS SDK's chain when the format holds none, or a patch), and a team rotation without the key in argv needs patch 0003. Details in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md#storage-modes).
**Ruled out:** provider APIs in the core (adapters are optional and outside it), per-person or per-Mac keys stored in the format (every replica would carry every key), managed-only (it would drop any S3 without an adapter), versioning without a key that is denied version deletion (a copied key would erase the versions too).
