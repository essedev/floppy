"""
Secrets at rest and tokens in flight (docs/DATABASE_SCHEMA.md, "Secrets").

- Stored secrets the control plane must use again (S3 secret key, SMTP and Valkey admin
  passwords) are sealed with AES-256-GCM. The blob is a format byte, a key id, a 96-bit
  nonce and the ciphertext with its tag; the additional data binds it to `table.column:row
  id`, so a blob copied into another row or column does not open.
- High-entropy tokens (session cookie, device token, magic link, invitation, app
  authorization code) are stored as SHA-256: nobody can guess a 256-bit preimage.
- The 6-digit code is an HMAC-SHA256 under a key derived from the secret with HKDF: a
  dump alone cannot reverse one million values.
"""

import base64
import hashlib
import hmac
import secrets
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT_V1 = 1
NONCE_BYTES = 12
CODE_HMAC_LABEL = b"floppy sign-in code v1"


class SecretBoxError(Exception):
    """A sealed value that cannot be opened: unknown key id, wrong row, or tampering."""


class SecretBox:
    """AES-256-GCM over a key ring; the highest key id seals, any known id opens."""

    def __init__(self, ring: dict[int, bytes]) -> None:
        if not ring:
            raise ValueError("the key ring is empty")
        self._ring = {key_id: AESGCM(key) for key_id, key in ring.items()}
        self._current = max(ring)
        self._code_key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=CODE_HMAC_LABEL
        ).derive(ring[self._current])

    @property
    def current_key_id(self) -> int:
        """The key id new values are sealed with."""
        return self._current

    @staticmethod
    def aad(table: str, column: str, row_id: UUID) -> bytes:
        """The additional authenticated data binding a blob to one cell."""
        return f"{table}.{column}:{row_id}".encode()

    def seal(self, plaintext: str, *, table: str, column: str, row_id: UUID) -> bytes:
        """The sealed blob of a secret, for one table, column and row."""
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._ring[self._current].encrypt(
            nonce, plaintext.encode(), self.aad(table, column, row_id)
        )
        return bytes([FORMAT_V1, self._current]) + nonce + sealed

    def open(self, blob: bytes, *, table: str, column: str, row_id: UUID) -> str:
        """The secret inside a blob sealed for this table, column and row."""
        if len(blob) < 2 + NONCE_BYTES + 16 or blob[0] != FORMAT_V1:
            raise SecretBoxError("unknown sealed format")
        cipher = self._ring.get(blob[1])
        if cipher is None:
            raise SecretBoxError(f"sealed with key id {blob[1]}, which is not in the key ring")
        nonce = blob[2 : 2 + NONCE_BYTES]
        try:
            return cipher.decrypt(
                nonce, blob[2 + NONCE_BYTES :], self.aad(table, column, row_id)
            ).decode()
        except InvalidTag as error:
            raise SecretBoxError("the sealed value does not open for this cell") from error

    def key_id_of(self, blob: bytes) -> int:
        """The key id a blob was sealed with."""
        return blob[1]

    def code_hmac(self, request_id: UUID, code: str) -> bytes:
        """The keyed hash of a 6-digit code, bound to its sign-in request."""
        return hmac.new(self._code_key, request_id.bytes + code.encode(), hashlib.sha256).digest()


def new_token() -> str:
    """A 256-bit random token, URL-safe base64 without padding."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    """The SHA-256 a token is stored and looked up by."""
    return hashlib.sha256(token.encode()).digest()


def new_code() -> str:
    """A 6-digit code, uniformly drawn."""
    return f"{secrets.randbelow(1_000_000):06d}"


def pkce_challenge(verifier: str) -> str:
    """The PKCE S256 challenge of a verifier: base64url of its SHA-256, without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def valkey_password() -> tuple[str, bytes]:
    """A new Valkey password and its SHA-256, the form Valkey keeps in its ACL."""
    password = secrets.token_urlsafe(32)
    return password, hashlib.sha256(password.encode()).digest()
