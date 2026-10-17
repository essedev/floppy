"""
Storage adapters for managed organizations (DECISIONS #22): through a provider's own API they
create the bucket and the S3 keys, each allowed on that bucket only, and delete keys when
access ends. The core never needs one: a manual organization pastes its keys.

`GarageAdapter` speaks Garage's admin API v2; OVH, AWS and MinIO come next. `FakeAdapter`
keeps keys in memory for tests.
"""

import secrets
from dataclasses import dataclass, field
from typing import Protocol

import httpx


class ProviderError(Exception):
    """The provider refused or did not answer; the message never contains a secret."""


@dataclass(frozen=True)
class ProviderKey:
    """A key the provider created: its pair and the id to delete it with."""

    access_key_id: str
    secret_key: str
    provider_ref: str

    def __repr__(self) -> str:
        return f"ProviderKey(access_key_id=...{self.access_key_id[-5:]})"


class StorageAdapter(Protocol):
    """What a managed organization's provider does for the control plane."""

    async def ensure_bucket(self, bucket: str) -> None:
        """Creates the bucket, or does nothing when it exists."""
        ...

    async def create_key(self, bucket: str, name: str) -> ProviderKey:
        """A new key allowed to read and write that bucket, and nothing else."""
        ...

    async def delete_key(self, provider_ref: str) -> None:
        """Deletes a key at the provider; a key already gone is not an error."""
        ...


class GarageAdapter:
    """`StorageAdapter` over Garage's admin API v2 (`/v2/...`, bearer token)."""

    def __init__(self, admin_url: str, admin_token: str, timeout_seconds: float = 15.0) -> None:
        self._url = admin_url.rstrip("/")
        self._token = admin_token
        self._timeout = timeout_seconds

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method,
                    f"{self._url}{path}",
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as error:
            raise ProviderError(f"garage admin API unreachable: {type(error).__name__}") from error

    @staticmethod
    def _fail(response: httpx.Response, what: str) -> ProviderError:
        try:
            code = str(response.json().get("code", ""))
        except ValueError:
            code = ""
        return ProviderError(f"garage refused {what}: {response.status_code} {code}".strip())

    async def _bucket_id(self, bucket: str) -> str:
        response = await self._call("GET", "/v2/GetBucketInfo", params={"globalAlias": bucket})
        if response.status_code != 200:
            raise self._fail(response, "to find the bucket")
        return str(response.json()["id"])

    async def ensure_bucket(self, bucket: str) -> None:
        response = await self._call("POST", "/v2/CreateBucket", json={"globalAlias": bucket})
        if response.status_code == 409:
            return
        if response.status_code != 200:
            raise self._fail(response, "to create the bucket")

    async def create_key(self, bucket: str, name: str) -> ProviderKey:
        bucket_id = await self._bucket_id(bucket)
        response = await self._call("POST", "/v2/CreateKey", json={"name": name})
        if response.status_code != 200:
            raise self._fail(response, "to create a key")
        body = response.json()
        key = ProviderKey(
            access_key_id=str(body["accessKeyId"]),
            secret_key=str(body["secretAccessKey"]),
            provider_ref=str(body["accessKeyId"]),
        )
        allowed = await self._call(
            "POST",
            "/v2/AllowBucketKey",
            json={
                "bucketId": bucket_id,
                "accessKeyId": key.access_key_id,
                "permissions": {"read": True, "write": True, "owner": False},
            },
        )
        if allowed.status_code != 200:
            await self.delete_key(key.provider_ref)
            raise self._fail(allowed, "to allow the key on the bucket")
        return key

    async def delete_key(self, provider_ref: str) -> None:
        response = await self._call("POST", "/v2/DeleteKey", params={"id": provider_ref})
        if response.status_code in (200, 204, 404):
            return
        raise self._fail(response, "to delete a key")


@dataclass
class FakeAdapter:
    """In-memory `StorageAdapter`; `down` makes every call fail like an unreachable provider."""

    buckets: set[str] = field(default_factory=set)
    keys: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    down: bool = False

    def _guard(self) -> None:
        if self.down:
            raise ProviderError("provider unreachable")

    async def ensure_bucket(self, bucket: str) -> None:
        self._guard()
        self.buckets.add(bucket)

    async def create_key(self, bucket: str, name: str) -> ProviderKey:
        self._guard()
        access = "FAKE" + secrets.token_hex(8).upper()
        self.keys[access] = bucket
        return ProviderKey(
            access_key_id=access, secret_key=secrets.token_urlsafe(24), provider_ref=access
        )

    async def delete_key(self, provider_ref: str) -> None:
        self._guard()
        self.keys.pop(provider_ref, None)
        self.deleted.append(provider_ref)
