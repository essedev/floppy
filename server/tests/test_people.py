"""People: invitations and their state machine, roles, the last admin, removal."""

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context
from floppy_server.infra.metadata import FakeMetadataAdmin
from floppy_server.models import AuditEvent, Device, Invitation, KeyExposure, WebSession
from tests.conftest import ClientFactory, Org
from tests.helpers import enrol_mac, invite_and_join, mailer, token_in


async def test_invite_lists_the_person_as_invited(
    org: Org, ctx: Context, admin: httpx.AsyncClient
) -> None:
    response = await admin.post("/people", json={"email": " Marco@Studio-A.test", "role": "member"})
    assert response.status_code == 201
    person = response.json()
    assert person["email"] == "marco@studio-a.test"
    assert person["status"] == "invited"
    assert person["name"] is None
    assert person["invitedBy"] == "Giulia"
    assert person["inviteExpiresAt"] is not None
    assert person["deviceCount"] == 0
    assert person["isMe"] is False
    mail = mailer(ctx).last_to("marco@studio-a.test")
    assert "/invito?token=" in mail.text
    assert "Giulia" in mail.text
    people = (await admin.get("/people")).json()
    assert [p["status"] for p in people] == ["active", "invited"]
    assert people[0]["isMe"] is True


async def test_invite_twice_is_already_member(org: Org, admin: httpx.AsyncClient) -> None:
    await admin.post("/people", json={"email": "marco@studio-a.test", "role": "member"})
    again = await admin.post("/people", json={"email": "marco@studio-a.test", "role": "admin"})
    assert (again.status_code, again.json()["code"]) == (409, "already_member")
    me = await admin.post("/people", json={"email": org.admin_email, "role": "member"})
    assert me.json()["code"] == "already_member"


async def test_invalid_email_is_a_validation_error(admin: httpx.AsyncClient) -> None:
    response = await admin.post("/people", json={"email": "not-an-address", "role": "member"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation"
    assert "not-an-address" not in response.text


async def test_resend_supersedes_the_previous_link(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    person = (
        await admin.post("/people", json={"email": "marco@studio-a.test", "role": "member"})
    ).json()
    first = token_in(mailer(ctx).last_to("marco@studio-a.test"))
    resent = await admin.post(f"/people/{person['id']}/resend")
    assert resent.status_code == 200
    second = token_in(mailer(ctx).last_to("marco@studio-a.test"))
    assert first != second
    old = await clients().post("/auth/invitation", json={"token": first})
    assert old.json()["code"] == "link_invalid"
    assert (await clients().post("/auth/invitation", json={"token": second})).status_code == 200
    async with owner_engine.connect() as conn:
        states = (
            (await conn.execute(select(Invitation.state).order_by(Invitation.sent_at)))
            .scalars()
            .all()
        )
    assert states == ["superseded", "accepted"]
    # An active person gets no new invitation.
    again = await admin.post(f"/people/{person['id']}/resend")
    assert (again.status_code, again.json()["code"]) == (409, "invalid_state")


async def test_role_change_and_the_last_admin(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    me = next(p for p in (await admin.get("/people")).json() if p["isMe"])
    demote = await admin.patch(f"/people/{me['id']}", json={"role": "member"})
    assert (demote.status_code, demote.json()["code"]) == (409, "last_admin")

    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    promoted = await admin.patch(f"/people/{person['id']}", json={"role": "admin"})
    assert promoted.json()["role"] == "admin"
    # The role is read on every request: Marco is an admin at once.
    assert (await marco.get("/people")).status_code == 200
    assert (await admin.patch(f"/people/{me['id']}", json={"role": "member"})).status_code == 200
    assert (await admin.get("/people")).status_code == 403
    async with owner_engine.connect() as conn:
        details = (
            (
                await conn.execute(
                    select(AuditEvent.detail).where(AuditEvent.action == "role_changed")
                )
            )
            .scalars()
            .all()
        )
    assert details == [{"from": "member", "to": "admin"}, {"from": "admin", "to": "member"}]


async def test_remove_self_and_last_admin_are_refused(org: Org, admin: httpx.AsyncClient) -> None:
    me = next(p for p in (await admin.get("/people")).json() if p["isMe"])
    response = await admin.delete(f"/people/{me['id']}")
    assert (response.status_code, response.json()["code"]) == (409, "cannot_remove_self")


async def test_removing_an_invited_person_revokes_the_invitation_and_opens_no_window(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    person = (
        await admin.post("/people", json={"email": "sara@studio-a.test", "role": "member"})
    ).json()
    token = token_in(mailer(ctx).last_to("sara@studio-a.test"))
    response = await admin.delete(f"/people/{person['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["person"]["status"] == "removed"
    assert body["revokedDevices"] == 0
    assert body["key"]["window"] is None
    dead = await clients().post("/auth/invitation", json={"token": token})
    assert dead.json()["code"] == "link_invalid"
    # Removed people stay listed; invited again they get a new membership.
    again = await admin.post("/people", json={"email": "sara@studio-a.test", "role": "admin"})
    assert again.status_code == 201
    statuses = [
        p["status"]
        for p in (await admin.get("/people")).json()
        if p["email"] == "sara@studio-a.test"
    ]
    assert statuses == ["invited"]


async def test_removing_a_person_revokes_macs_sessions_and_opens_the_window(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test")
    app = clients()
    active = await enrol_mac(app, marco, ctx, "marco@studio-a.test", name="iMac di Marco")
    await enrol_mac(app, marco, ctx, "marco@studio-a.test", name="Mini", register=False)
    metadata = ctx.metadata
    assert isinstance(metadata, FakeMetadataAdmin)
    assert active.valkey_user in metadata.users[org.valkey_url]

    response = await admin.delete(f"/people/{person['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["revokedDevices"] == 2
    assert body["person"]["deviceCount"] == 0
    window = body["key"]["window"]
    assert [r["kind"] for r in window["reasons"]] == ["person_removed"]
    assert window["reasons"][0]["label"] == "Marco"

    assert active.valkey_user not in metadata.users[org.valkey_url]
    assert (await marco.get("/session")).status_code == 401
    heartbeat = await app.post(
        "/device/heartbeat",
        json={"appVersion": "0.4.0", "macosVersion": "15.5"},
        headers=active.auth,
    )
    assert heartbeat.json() == {"state": "revoked"}
    async with owner_engine.connect() as conn:
        reasons = (await conn.execute(select(Device.revoke_reason))).scalars().all()
        cleaned = (await conn.execute(select(Device.valkey_user_deleted_at))).scalars().all()
        sessions = (
            (
                await conn.execute(
                    select(WebSession.revoke_reason).where(WebSession.revoke_reason.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        exposures = (await conn.execute(select(KeyExposure.kind))).scalars().all()
        logged = (
            await conn.execute(select(AuditEvent.action, AuditEvent.detail).order_by(AuditEvent.id))
        ).all()
    assert reasons == ["member_removed", "member_removed"]
    assert all(cleaned)
    assert sessions == ["membership_removed"]
    assert exposures == ["person_removed"]
    assert ("person_removed", {"devices": 2}) in logged
    assert logged.count(("device_revoked", {"reason": "member_removed"})) == 2
    # Removed people cannot sign in again.
    started = await clients().post("/auth/sign-in", json={"email": "marco@studio-a.test"})
    assert started.status_code == 200
    assert "nessun team" in mailer(ctx).last_to("marco@studio-a.test").text


async def test_removing_the_last_admin_is_refused_even_by_another_admin(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    marco = clients()
    person = await invite_and_join(admin, marco, ctx, "marco@studio-a.test", role="admin")
    me = next(p for p in (await admin.get("/people")).json() if p["isMe"])
    assert (await marco.delete(f"/people/{me['id']}")).status_code == 200
    # Marco is now the only admin and cannot be demoted by anyone.
    response = await marco.patch(f"/people/{person['id']}", json={"role": "member"})
    assert response.json()["code"] == "last_admin"
    async with owner_engine.connect() as conn:
        admins = (
            await conn.execute(
                text("SELECT count(*) FROM memberships WHERE role = 'admin' AND status = 'active'")
            )
        ).scalar_one()
    assert admins == 1


async def test_unknown_person_is_not_found(admin: httpx.AsyncClient) -> None:
    missing = "01900000-0000-7000-8000-000000000000"
    for response in (
        await admin.patch(f"/people/{missing}", json={"role": "admin"}),
        await admin.delete(f"/people/{missing}"),
        await admin.post(f"/people/{missing}/resend"),
    ):
        assert (response.status_code, response.json()["code"]) == (404, "not_found")
