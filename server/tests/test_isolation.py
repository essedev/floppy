"""Tenant isolation: nothing of another organization is reachable, through any endpoint."""

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.disks.service import run_disk_formats
from floppy_server.keys.service import run_rotations
from tests.conftest import ClientFactory, make_org
from tests.helpers import enrol_mac, invite_and_join, sign_in


async def test_another_organizations_rows_are_never_reachable(
    ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    a = await make_org(ctx, "studio-a", "anna@a.test")
    b = await make_org(ctx, "studio-b", "bruno@b.test", key="AKIASTUDIOBKEY001")
    admin_a, admin_b = clients(), clients()
    await sign_in(admin_a, ctx, a.admin_email)
    await sign_in(admin_b, ctx, b.admin_email)

    # Organization B's things.
    member_b = clients()
    person_b = await invite_and_join(admin_b, member_b, ctx, "bea@b.test")
    mac_b = await enrol_mac(clients(), member_b, ctx, "bea@b.test")
    disk_b = (await admin_b.post("/disks", json={"name": "Bruno"})).json()
    await run_disk_formats(ctx)
    rotation_b = (
        await admin_b.post(
            "/key/rotations",
            json={"accessKeyId": "AKIABNEWKEY00002", "secretKey": "b-secret-000002"},
        )
    ).json()
    await run_rotations(ctx)

    attempts: list[httpx.Response] = [
        await admin_a.patch(f"/people/{person_b['id']}", json={"role": "admin"}),
        await admin_a.delete(f"/people/{person_b['id']}"),
        await admin_a.post(f"/people/{person_b['id']}/resend"),
        await admin_a.post(f"/macs/{mac_b.id}/revoke"),
        await admin_a.get(f"/disks/{disk_b['id']}"),
        await admin_a.patch(f"/disks/{disk_b['id']}", json={"trashDays": 1}),
        await admin_a.get(f"/key/rotations/{rotation_b['id']}"),
        await admin_a.post(
            f"/key/rotations/{rotation_b['id']}/close", json={"oldKeyDeleted": True}
        ),
    ]
    assert [(r.status_code, r.json()["code"]) for r in attempts] == [(404, "not_found")] * len(
        attempts
    )

    # Lists show only the caller's organization.
    assert {p["email"] for p in (await admin_a.get("/people")).json()} == {a.admin_email}
    assert (await admin_a.get("/macs")).json() == []
    assert (await admin_a.get("/disks")).json() == []
    audit_a = (await admin_a.get("/audit", params={"sinceDays": 90, "limit": 100})).json()["items"]
    assert all(e["target"]["id"] != person_b["id"] for e in audit_a)
    assert {e["actor"]["label"] for e in audit_a if e["actor"]["kind"] == "person"} == {"Anna"}
    key_a = (await admin_a.get("/key")).json()
    assert (key_a["bucket"], key_a["accessKeyTail"], key_a["rotation"]) == (a.bucket, "00001", None)
    assert (await admin_a.get("/settings")).json()["storage"]["bucket"] == a.bucket
    session_a = (await admin_a.get("/session")).json()["organization"]
    assert (session_a["peopleCount"], session_a["diskCount"], session_a["macCount"]) == (1, 0, 0)

    # A device token works only for its own organization's configuration.
    config = (await clients().get("/device/config", headers=mac_b.auth)).json()
    assert config["valkeyUrl"] == b.valkey_url
    assert [d["name"] for d in config["disks"]] == ["Bruno"]

    # B is untouched by A's attempts.
    people_b = (await admin_b.get("/people")).json()
    assert {p["email"]: p["role"] for p in people_b} == {
        b.admin_email: "admin",
        "bea@b.test": "member",
    }
    assert [m["state"] for m in (await admin_b.get("/macs")).json()] == ["active"]


async def test_one_address_one_organization(ctx: Context, clients: ClientFactory) -> None:
    a = await make_org(ctx, "studio-a", "anna@a.test")
    b = await make_org(ctx, "studio-b", "bruno@b.test", key="AKIASTUDIOBKEY001")
    admin_b = clients()
    await sign_in(admin_b, ctx, b.admin_email)
    response = await admin_b.post("/people", json={"email": a.admin_email, "role": "member"})
    assert (response.status_code, response.json()["code"]) == (409, "already_member")
