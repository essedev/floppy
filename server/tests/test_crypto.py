"""Sealed secrets, the key ring, codes and PKCE (no database)."""

import base64
import uuid

import pytest

from floppy_server.config import decode_key, parse_key_ring
from floppy_server.crypto import SecretBox, SecretBoxError, new_code, pkce_challenge, token_hash

KEY_1 = bytes(range(32))
KEY_2 = bytes(range(32, 64))


def test_a_sealed_secret_opens_only_in_its_cell() -> None:
    box = SecretBox({1: KEY_1})
    row = uuid.uuid4()
    blob = box.seal("s3-secret", table="s3_keys", column="secret_key_enc", row_id=row)
    assert b"s3-secret" not in blob
    assert blob[:2] == bytes([1, 1])
    assert box.open(blob, table="s3_keys", column="secret_key_enc", row_id=row) == "s3-secret"
    with pytest.raises(SecretBoxError):
        box.open(blob, table="s3_keys", column="secret_key_enc", row_id=uuid.uuid4())
    with pytest.raises(SecretBoxError):
        box.open(blob, table="organizations", column="smtp_password_enc", row_id=row)
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(SecretBoxError):
        box.open(tampered, table="s3_keys", column="secret_key_enc", row_id=row)


def test_the_newest_key_seals_and_old_ones_still_open() -> None:
    row = uuid.uuid4()
    old = SecretBox({1: KEY_1}).seal("x", table="t", column="c", row_id=row)
    ring = SecretBox({1: KEY_1, 2: KEY_2})
    assert ring.current_key_id == 2
    assert ring.open(old, table="t", column="c", row_id=row) == "x"
    assert ring.key_id_of(ring.seal("x", table="t", column="c", row_id=row)) == 2
    with pytest.raises(SecretBoxError, match="not in the key ring"):
        SecretBox({1: KEY_1}).open(
            ring.seal("x", table="t", column="c", row_id=row), table="t", column="c", row_id=row
        )


def test_code_hmac_is_keyed_and_bound_to_the_request() -> None:
    box, other = SecretBox({1: KEY_1}), SecretBox({1: KEY_2})
    request = uuid.uuid4()
    assert box.code_hmac(request, "123456") == box.code_hmac(request, "123456")
    assert box.code_hmac(request, "123456") != box.code_hmac(uuid.uuid4(), "123456")
    assert box.code_hmac(request, "123456") != other.code_hmac(request, "123456")


def test_codes_and_tokens() -> None:
    codes = {new_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 150
    assert len(token_hash("abc")) == 32
    # RFC 7636, appendix B.
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_key_ring_parsing() -> None:
    encoded = base64.b64encode(KEY_1).decode()
    assert parse_key_ring(f"1:{encoded}, 7:{base64.urlsafe_b64encode(KEY_2).decode()}") == {
        1: KEY_1,
        7: KEY_2,
    }
    assert decode_key(encoded.rstrip("=")) == KEY_1
    for bad in ("", "x:abc", "0:" + encoded, "1:" + base64.b64encode(b"short").decode()):
        with pytest.raises(ValueError):
            parse_key_ring(bad)
