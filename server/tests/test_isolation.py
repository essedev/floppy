"""Tenant isolation: nothing of another organization is reachable, through any endpoint."""

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.keys.service import run_rotations
from tests.conftest import ClientFactory, make_org
from tests.helpers import create_disk, disk_key, enrol_device, invite_and_join, sign_in


async def test_another_organizations_rows_are_never_reachable(
    ctx: Context, clients: ClientFactory, owner_engine: AsyncEngine
) -> None:
    a = await make_org(ctx, "studio-a", "anna@a.test")
    b = await make_org(ctx, "studio-b", "bruno@b.test")
    admin_a, admin_b = clients(), clients()
    await sign_in(admin_a, ctx, a.admin_email)
    await sign_in(admin_b, ctx, b.admin_email)

    # Organization B's things.
    member_b = clients()
    person_b = await invite_and_join(admin_b, member_b, ctx, "bea@b.test")
    disk_b = await create_disk(
        admin_b,
        ctx,
        "Bruno",
        key=disk_key(1),
        access=[{"personId": person_b["id"], "access": "write"}],
    )
    device_b = await enrol_device(clients(), member_b, ctx, "bea@b.test")
    rotation_b = (
        await admin_b.post("/key/rotations", json={"diskId": disk_b["id"], **disk_key(2)})
    ).json()
    await run_rotations(ctx)
    person_a = next(p for p in (await admin_a.get("/people")).json())

    attempts: list[httpx.Response] = [
        await admin_a.patch(f"/people/{person_b['id']}", json={"role": "admin"}),
        await admin_a.delete(f"/people/{person_b['id']}"),
        await admin_a.post(f"/people/{person_b['id']}/resend"),
        await admin_a.post(f"/devices/{device_b.id}/revoke"),
        await admin_a.get(f"/disks/{disk_b['id']}"),
        await admin_a.patch(f"/disks/{disk_b['id']}", json={"trashDays": 1}),
        await admin_a.post(f"/disks/{disk_b['id']}/probe"),
        await admin_a.put(
            f"/disks/{disk_b['id']}/access/{person_b['id']}", json={"access": "none"}
        ),
        await admin_a.put(
            f"/disks/{disk_b['id']}/access/{person_a['id']}", json={"access": "write"}
        ),
        await admin_a.get(f"/key/rotations/{rotation_b['id']}"),
        await admin_a.post(
            f"/key/rotations/{rotation_b['id']}/close", json={"oldKeyDeleted": True}
        ),
        await admin_a.post("/key/rotations", json={"diskId": disk_b["id"], **disk_key(3)}),
    ]
    assert [(r.status_code, r.json()["code"]) for r in attempts] == [(404, "not_found")] * len(
        attempts
    )

    # Lists show only the caller's organization.
    assert {p["email"] for p in (await admin_a.get("/people")).json()} == {a.admin_email}
    assert (await admin_a.get("/devices")).json() == []
    assert (await admin_a.get("/disks")).json() == []
    audit_a = (await admin_a.get("/audit", params={"sinceDays": 90, "limit": 100})).json()["items"]
    assert all(e["target"]["id"] != person_b["id"] for e in audit_a)
    assert {e["actor"]["label"] for e in audit_a if e["actor"]["kind"] == "person"} == {"Anna"}
    assert (await admin_a.get("/key")).json()["disks"] == []
    assert (await admin_a.get("/access")).json() == []
    session_a = (await admin_a.get("/session")).json()["organization"]
    assert (session_a["peopleCount"], session_a["diskCount"], session_a["deviceCount"]) == (1, 0, 0)

    # A device token works only for its own organization's configuration.
    config = (await clients().get("/device/config", headers=device_b.auth)).json()
    assert config["organization"]["id"] == str(b.id)
    assert [(d["name"], d["access"]) for d in config["disks"]] == [("Bruno", "write")]

    # B is untouched by A's attempts.
    people_b = (await admin_b.get("/people")).json()
    assert {p["email"]: p["role"] for p in people_b} == {
        b.admin_email: "admin",
        "bea@b.test": "member",
    }
    assert [m["state"] for m in (await admin_b.get("/devices")).json()] == ["active"]


async def test_one_address_one_organization(ctx: Context, clients: ClientFactory) -> None:
    a = await make_org(ctx, "studio-a", "anna@a.test")
    b = await make_org(ctx, "studio-b", "bruno@b.test")
    admin_b = clients()
    await sign_in(admin_b, ctx, b.admin_email)
    response = await admin_b.post("/people", json={"email": a.admin_email, "role": "member"})
    assert (response.status_code, response.json()["code"]) == (409, "already_member")
