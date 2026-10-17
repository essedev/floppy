"""Settings, health, the audit log's reading, and secrets kept out of answers and the log."""

import json
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.disks.service import run_disk_formats
from floppy_server.keys.service import run_rotations
from floppy_server.models import AuditEvent, Organization
from tests.conftest import ClientFactory, Org
from tests.helpers import enrol_mac, invite_and_join, mailer


async def test_settings_round_trip_with_a_write_only_password(
    org: Org, ctx: Context, admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    settings = (await admin.get("/settings")).json()
    assert settings == {
        "organizationName": "Studio studio-a",
        "mail": {"host": "", "port": 0, "username": "", "sender": "", "passwordSet": False},
        "storage": {"endpoint": "https://s3.test", "region": "eu-south", "bucket": org.bucket},
        "storageLocked": False,
    }
    body = {
        "organizationName": "Studio Esempio",
        "mail": {
            "host": "smtp.studio.test",
            "port": 587,
            "username": "floppy",
            "sender": "Floppy <f@studio.test>",
            "password": "smtp-pass-9",
        },
    }
    updated = await admin.patch("/settings", json=body)
    assert updated.status_code == 200
    assert updated.json()["mail"]["passwordSet"] is True
    assert "smtp-pass-9" not in updated.text
    # An omitted password keeps the stored one.
    again = await admin.patch("/settings", json={"mail": {**body["mail"], "password": ""}})  # type: ignore[dict-item]
    assert again.json()["mail"]["passwordSet"] is True
    async with owner_engine.connect() as conn:
        stored = (await conn.execute(select(Organization.smtp_password_enc))).scalar_one()
        fields = (
            (
                await conn.execute(
                    select(AuditEvent.detail).where(AuditEvent.action == "settings_updated")
                )
            )
            .scalars()
            .all()
        )
    assert stored is not None and b"smtp-pass-9" not in stored
    assert fields == [
        {
            "fields": [
                "organizationName",
                "mail.host",
                "mail.port",
                "mail.username",
                "mail.sender",
                "mail.password",
            ]
        }
    ]
    async with ctx.sessions() as db:
        org_row = await db.get(Organization, org.id)
    assert org_row is not None
    smtp = ctx.smtp_for(org_row)
    assert smtp is not None and smtp.password == "smtp-pass-9"
    # An empty host returns to the instance's SMTP server.
    cleared = await admin.patch("/settings", json={"mail": {"host": "", "port": 0}})
    assert cleared.json()["mail"] == {
        "host": "",
        "port": 0,
        "username": "",
        "sender": "",
        "passwordSet": False,
    }


async def test_storage_is_frozen_once_a_disk_exists(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    storage = {"endpoint": "https://s3.other.test", "region": "eu-west", "bucket": "other"}
    assert (await admin.patch("/settings", json={"storage": storage})).json()["storage"] == storage
    await admin.post("/disks", json={"name": "Archivio"})
    response = await admin.patch("/settings", json={"storage": {**storage, "bucket": "third"}})
    assert response.status_code == 409
    assert response.json() == {
        "code": "storage_in_use",
        "message": "Storage cannot change while disks exist",
        "details": {"disks": 1},
    }
    assert (await admin.get("/settings")).json()["storageLocked"] is True


async def test_mail_test_and_storage_probe_update_health(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    sent = await admin.post("/settings/mail/test")
    assert sent.json() == {"sentTo": org.admin_email}
    assert "prova" in mailer(ctx).last_to(org.admin_email).subject
    ok = await admin.post(
        "/settings/storage/probe",
        json={"endpoint": "https://s3.test", "region": "eu-south", "bucket": org.bucket},
    )
    assert ok.status_code == 204
    bad = await admin.post(
        "/settings/storage/probe",
        json={"endpoint": "https://s3.test", "region": "eu-south", "bucket": "missing-bucket"},
    )
    assert bad.status_code == 422
    assert bad.json() == {
        "code": "probe_failed",
        "message": "The test write failed",
        "details": {"code": "NoSuchBucket", "request": "PUT /missing-bucket/floppy-probe-settings"},
    }
    health = (await admin.get("/health")).json()
    assert health["storage"]["state"] == "failing"
    assert health["mail"]["state"] == "ok"
    assert health["mail"]["lastSentAt"] is not None
    mailer(ctx).failing = True
    failed = await admin.post("/settings/mail/test")
    assert (failed.status_code, failed.json()["code"]) == (503, "mail_failed")
    assert (await admin.get("/health")).json()["mail"]["state"] == "failing"


async def test_audit_filters_search_and_pages(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    for n in range(5):
        await admin.post("/people", json={"email": f"p{n}@studio-a.test", "role": "member"})
    page = (await admin.get("/audit", params={"sinceDays": 7, "limit": 3})).json()
    assert len(page["items"]) == 3
    assert page["nextCursor"]
    rest = (
        await admin.get("/audit", params={"sinceDays": 7, "limit": 3, "cursor": page["nextCursor"]})
    ).json()
    ids = [e["id"] for e in page["items"] + rest["items"]]
    assert len(ids) == len(set(ids)) == 6  # five invitations and the admin's sign-in
    assert rest["nextCursor"] is None
    first = page["items"][0]
    assert first["action"] == "person_invited"
    assert first["actor"] == {"kind": "person", "id": first["actor"]["id"], "label": "Giulia"}
    assert first["target"]["kind"] == "person"
    people = (await admin.get("/audit", params={"sinceDays": 30, "category": "people"})).json()[
        "items"
    ]
    assert {e["category"] for e in people} == {"people"}
    found = (await admin.get("/audit", params={"sinceDays": 90, "q": "p3@"})).json()["items"]
    assert [e["target"]["label"] for e in found] == ["p3@studio-a.test"]
    bads: list[dict[str, Any]] = [
        {"sinceDays": 8},
        {"sinceDays": 7, "cursor": "nope"},
        {"sinceDays": 7, "category": "x"},
    ]
    for bad in bads:
        response = await admin.get("/audit", params=bad)
        assert (response.status_code, response.json()["code"]) == (422, "validation"), bad


async def test_no_secret_reaches_an_answer_or_the_audit_log(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    secrets: list[str] = [
        "first-secret-key-value-0001",
        "second-secret-key-value-0002",
        "smtp-pass-7",
    ]
    answers: list[str] = []
    marco = clients()
    await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    app = clients()
    mac = await enrol_mac(app, marco, ctx, "marco@studio-a.test")
    assert mac.valkey_password
    secrets += [mac.token, mac.valkey_password]
    secrets += [
        m.text.split("token=")[1].split()[0] for m in mailer(ctx).outbox if "token=" in m.text
    ]
    await admin.post("/disks", json={"name": "Archivio"})
    await run_disk_formats(ctx)
    rotation = await admin.post(
        "/key/rotations", json={"accessKeyId": "AKIASECONDKEY00002", "secretKey": secrets[1]}
    )
    answers.append(rotation.text)
    await run_rotations(ctx)
    answers.append(
        (
            await admin.patch(
                "/settings",
                json={
                    "mail": {
                        "host": "smtp.test",
                        "port": 25,
                        "sender": "f@x.test",
                        "password": secrets[2],
                    }
                },
            )
        ).text
    )
    for path in (
        "/people",
        "/macs",
        "/disks",
        "/key",
        f"/key/rotations/{rotation.json()['id']}",
        "/settings",
        "/health",
        "/session",
    ):
        answers.append((await admin.get(path)).text)
    answers.append((await admin.get("/audit", params={"sinceDays": 90, "limit": 100})).text)
    answers.append((await app.get("/device/config", headers=mac.auth)).text)
    async with owner_engine.connect() as conn:
        log = json.dumps(
            [
                list(map(str, row))
                for row in (
                    await conn.execute(
                        text("SELECT actor_label, target_label, detail FROM audit_events")
                    )
                ).all()
            ]
        )
    for secret in secrets:
        assert all(secret not in answer for answer in answers), secret
        assert secret not in log, secret
