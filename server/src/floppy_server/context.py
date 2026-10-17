"""
What every service needs, built once at startup: settings, the database, the secret box and
the external systems (mail, Valkey, JuiceFS, the storage probe) behind their interfaces.

Also resolves, per organization, which Valkey, SMTP server and volume location to use: an
organization's own columns win, the instance's environment fills the gaps (self-hosted).
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from floppy_server.config import MailBackend, Settings
from floppy_server.crypto import SecretBox
from floppy_server.infra.juicefs import S3Credentials, VolumeFormatter, VolumeLocation
from floppy_server.infra.mail import Mailer, SmtpConfig
from floppy_server.infra.metadata import MetadataAdmin, ValkeyTarget
from floppy_server.infra.providers import GarageAdapter, ProviderError, StorageAdapter
from floppy_server.infra.storage import StorageProber
from floppy_server.models import Organization, S3Key, Volume
from floppy_server.ratelimit import RateLimiter


def utcnow() -> datetime:
    """The current time, timezone-aware in UTC."""
    return datetime.now(UTC)


@dataclass
class Context:
    """The services' dependencies; one per process."""

    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    box: SecretBox
    mailer: Mailer
    metadata: MetadataAdmin
    formatter: VolumeFormatter
    prober: StorageProber
    rotation_wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    disk_wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    # Tests put a fake here; otherwise each managed organization gets its provider's.
    adapter_override: StorageAdapter | None = None

    def valkey_target(self, org: Organization) -> ValkeyTarget:
        """The organization's Valkey as the control plane reaches it, with admin credentials."""
        password: str | None
        if org.valkey_admin_password_enc is not None:
            password = self.box.open(
                org.valkey_admin_password_enc,
                table="organizations",
                column="valkey_admin_password_enc",
                row_id=org.id,
            )
        elif self.settings.valkey_admin_password is not None:
            password = self.settings.valkey_admin_password.get_secret_value()
        else:
            password = None
        return ValkeyTarget(
            url=org.valkey_admin_url or org.valkey_url,
            username=org.valkey_admin_username or self.settings.valkey_admin_username,
            password=password,
        )

    def smtp_for(self, org: Organization | None) -> SmtpConfig | None:
        """The SMTP server for an organization's mail: its own, else the instance's, else none."""
        s = self.settings
        if org is not None and org.smtp_host is not None and org.smtp_port is not None:
            password = None
            if org.smtp_password_enc is not None:
                password = self.box.open(
                    org.smtp_password_enc,
                    table="organizations",
                    column="smtp_password_enc",
                    row_id=org.id,
                )
            return SmtpConfig(
                host=org.smtp_host,
                port=org.smtp_port,
                username=org.smtp_username,
                password=password,
                sender=org.smtp_sender or s.smtp_sender,
                starttls=s.smtp_starttls,
                timeout_seconds=s.smtp_timeout_seconds,
            )
        if s.smtp_host:
            return SmtpConfig(
                host=s.smtp_host,
                port=s.smtp_port,
                username=s.smtp_username,
                password=s.smtp_password.get_secret_value() if s.smtp_password else None,
                sender=s.smtp_sender,
                starttls=s.smtp_starttls,
                timeout_seconds=s.smtp_timeout_seconds,
            )
        return None

    def mail_configured(self, org: Organization | None) -> bool:
        """Whether mail can leave for this organization (the console backend counts)."""
        return self.settings.mail_backend is MailBackend.CONSOLE or self.smtp_for(org) is not None

    def volume_location(self, org: Organization, volume: Volume) -> VolumeLocation:
        """Where a volume's format and objects live."""
        target = self.valkey_target(org)
        return VolumeLocation(
            meta_url=target.url,
            meta_username=target.username,
            meta_password=target.password,
            meta_db=volume.meta_db,
            volume_name=volume.volume_name,
            s3_endpoint=volume.s3_endpoint,
            s3_region=volume.s3_region,
            s3_bucket=volume.s3_bucket,
        )

    def adapter_for(self, org: Organization) -> StorageAdapter:
        """The storage adapter of a managed organization, with its credentials or the instance's."""
        if org.storage_mode != "managed":
            raise ProviderError("the organization's storage is manual: it has no adapter")
        if self.adapter_override is not None:
            return self.adapter_override
        credentials: dict[str, str] = {}
        if org.storage_admin_credentials_enc is not None:
            credentials = json.loads(
                self.box.open(
                    org.storage_admin_credentials_enc,
                    table="organizations",
                    column="storage_admin_credentials_enc",
                    row_id=org.id,
                )
            )
        if org.storage_provider == "garage":
            url = credentials.get("adminUrl") or self.settings.garage_admin_url
            token = credentials.get("adminToken") or (
                self.settings.garage_admin_token.get_secret_value()
                if self.settings.garage_admin_token
                else None
            )
            if not url or not token:
                raise ProviderError("no Garage admin URL and token for this organization")
            return GarageAdapter(url, token)
        raise ProviderError(f"no adapter for {org.storage_provider} yet")

    def credentials(self, key: S3Key) -> S3Credentials:
        """The key pair of a stored S3 key whose secret is still kept."""
        if key.secret_key_enc is None:
            raise ValueError("the key's secret was wiped: it is retired or rejected")
        secret = self.box.open(
            key.secret_key_enc, table="s3_keys", column="secret_key_enc", row_id=key.id
        )
        return S3Credentials(access_key_id=key.access_key_id, secret_key=secret)
