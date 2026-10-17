"""Sign-in by link and code, its state machine, lockout, invitations and web sessions."""

from datetime import timedelta

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from floppy_server.context import Context, utcnow
from floppy_server.models import AuditEvent, Membership, SignInRequest, User, WebSession
from tests.conftest import ClientFactory, Org
from tests.helpers import code_in, invite_and_join, mailer, sign_in, token_in


async def test_link_signs_in_and_sets_an_httponly_cookie(
    org: Org, ctx: Context, client: httpx.AsyncClient
) -> None:
    started = await client.post("/auth/sign-in", json={"email": "Giulia.Admin@Studio-A.test "})
    assert started.status_code == 200
    body = started.json()
    assert body["email"] == org.admin_email
    assert set(body) == {"requestId", "email", "expiresAt", "resendAt"}
    assert body["expiresAt"].endswith("Z")

    token = token_in(mailer(ctx).last_to(org.admin_email))
    response = await client.post("/auth/magic-link", json={"token": token})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("floppy_session=")  # plain http in tests: no __Host- prefix
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    session = response.json()
    assert session["role"] == "admin"
    assert session["user"]["name"] == "Giulia"
    assert session["organization"] == {
        "id": str(org.id),
        "name": "Studio studio-a",
        "diskCount": 0,
        "peopleCount": 1,
        "deviceCount": 0,
    }
    assert (await client.get("/session")).json() == session
    status = await client.get(f"/auth/sign-in/{body['requestId']}")
    assert status.json() == {"status": "completed"}


async def test_link_is_single_use(org: Org, ctx: Context, clients: ClientFactory) -> None:
    first, second = clients(), clients()
    await first.post("/auth/sign-in", json={"email": org.admin_email})
    token = token_in(mailer(ctx).last_to(org.admin_email))
    assert (await first.post("/auth/magic-link", json={"token": token})).status_code == 200
    again = await second.post("/auth/magic-link", json={"token": token})
    assert again.status_code == 410
    assert again.json()["code"] == "link_invalid"


async def test_unknown_address_gets_the_same_answer_and_can_never_sign_in(
    org: Org, ctx: Context, client: httpx.AsyncClient
) -> None:
    known = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    unknown = (await client.post("/auth/sign-in", json={"email": "stranger@else.test"})).json()
    assert set(known) == set(unknown)
    mail = mailer(ctx).last_to("stranger@else.test")
    assert "nessun team" in mail.text
    assert "token=" not in mail.text
    wrong = await client.post(f"/auth/sign-in/{unknown['requestId']}/code", json={"code": "123456"})
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "invalid_code"


async def test_polling_never_grants_a_session(
    org: Org, ctx: Context, clients: ClientFactory
) -> None:
    attacker, victim = clients(), clients()
    started = (await attacker.post("/auth/sign-in", json={"email": org.admin_email})).json()
    token = token_in(mailer(ctx).last_to(org.admin_email))
    assert (await victim.post("/auth/magic-link", json={"token": token})).status_code == 200
    polled = await attacker.get(f"/auth/sign-in/{started['requestId']}")
    assert polled.json() == {"status": "completed"}
    assert "floppy_session" not in polled.headers.get("set-cookie", "")
    assert (await attacker.get("/session")).status_code == 401


async def test_code_signs_in(org: Org, ctx: Context, client: httpx.AsyncClient) -> None:
    started = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    code = code_in(mailer(ctx).last_to(org.admin_email))
    response = await client.post(f"/auth/sign-in/{started['requestId']}/code", json={"code": code})
    assert response.status_code == 200
    assert (await client.get("/session")).status_code == 200


async def test_five_wrong_codes_lock_the_request_and_its_link(
    org: Org, ctx: Context, owner_engine: AsyncEngine, clients: ClientFactory
) -> None:
    client = clients()
    started = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    mail = mailer(ctx).last_to(org.admin_email)
    right = code_in(mail)
    wrong = "000000" if right != "000000" else "111111"
    url = f"/auth/sign-in/{started['requestId']}/code"
    for left in (4, 3, 2, 1):
        response = await client.post(url, json={"code": wrong})
        assert response.status_code == 422
        assert response.json() == {
            "code": "invalid_code",
            "message": "Wrong code",
            "details": {"attemptsLeft": left},
        }
    fifth = await client.post(url, json={"code": wrong})
    assert fifth.status_code == 429
    assert fifth.json()["code"] == "request_locked"
    # Locked: the right code and the link are dead too.
    assert (await client.post(url, json={"code": right})).json()["code"] == "request_locked"
    link = await clients().post("/auth/magic-link", json={"token": token_in(mail)})
    assert link.json()["code"] == "link_invalid"
    assert (await client.get(f"/auth/sign-in/{started['requestId']}")).json() == {
        "status": "locked"
    }
    async with owner_engine.connect() as conn:
        actions = (
            await conn.execute(select(AuditEvent.action, AuditEvent.actor_kind, AuditEvent.detail))
        ).all()
    assert ("sign_in_locked", "system", {}) in actions


async def test_a_new_request_supersedes_the_old_one(
    org: Org, ctx: Context, owner_engine: AsyncEngine, client: httpx.AsyncClient
) -> None:
    first = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    first_mail = mailer(ctx).last_to(org.admin_email)
    # Before resendAt the same request comes back, with no new mail.
    again = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    assert again["requestId"] == first["requestId"]
    assert len(mailer(ctx).outbox) == 1
    async with owner_engine.begin() as conn:
        await conn.execute(update(SignInRequest).values(resend_at=utcnow() - timedelta(seconds=1)))
    second = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    assert second["requestId"] != first["requestId"]
    assert (await client.get(f"/auth/sign-in/{first['requestId']}")).json() == {"status": "expired"}
    old = await client.post("/auth/magic-link", json={"token": token_in(first_mail)})
    assert old.json()["code"] == "link_invalid"
    code = await client.post(
        f"/auth/sign-in/{first['requestId']}/code", json={"code": code_in(first_mail)}
    )
    assert code.json()["code"] == "request_expired"


async def test_an_expired_request_reads_expired_and_refuses_both_ways(
    org: Org, ctx: Context, owner_engine: AsyncEngine, client: httpx.AsyncClient
) -> None:
    started = (await client.post("/auth/sign-in", json={"email": org.admin_email})).json()
    mail = mailer(ctx).last_to(org.admin_email)
    async with owner_engine.begin() as conn:
        await conn.execute(update(SignInRequest).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert (await client.get(f"/auth/sign-in/{started['requestId']}")).json() == {
        "status": "expired"
    }
    code = await client.post(
        f"/auth/sign-in/{started['requestId']}/code", json={"code": code_in(mail)}
    )
    assert (code.status_code, code.json()["code"]) == (410, "request_expired")
    link = await client.post("/auth/magic-link", json={"token": token_in(mail)})
    assert (link.status_code, link.json()["code"]) == (410, "link_invalid")


async def test_mail_failure_leaves_nothing_behind(
    org: Org, ctx: Context, owner_engine: AsyncEngine, client: httpx.AsyncClient
) -> None:
    mailer(ctx).failing = True
    response = await client.post("/auth/sign-in", json={"email": org.admin_email})
    assert response.status_code == 503
    assert response.json()["code"] == "mail_failed"
    async with owner_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM sign_in_requests"))).scalar_one() == 0
        state = (
            await conn.execute(text("SELECT mail_state FROM organization_status"))
        ).scalar_one()
    assert state == "failing"


async def test_sign_out_revokes_the_session(
    admin: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    response = await admin.delete("/session")
    assert response.status_code == 204
    assert (await admin.get("/session")).status_code == 401
    async with owner_engine.connect() as conn:
        reasons = (await conn.execute(select(WebSession.revoke_reason))).scalars().all()
    assert reasons == ["signed_out"]


async def test_idle_and_expired_sessions_are_refused(
    admin: httpx.AsyncClient, ctx: Context, owner_engine: AsyncEngine
) -> None:
    assert (await admin.get("/session")).status_code == 200
    async with owner_engine.begin() as conn:
        await conn.execute(
            update(WebSession).values(last_seen_at=utcnow() - ctx.settings.session_idle)
        )
    assert (await admin.get("/session")).status_code == 401
    async with owner_engine.begin() as conn:
        await conn.execute(
            update(WebSession).values(
                last_seen_at=utcnow(), expires_at=utcnow() - timedelta(seconds=1)
            )
        )
    assert (await admin.get("/session")).status_code == 401


async def test_unauthorized_and_forbidden(
    org: Org, ctx: Context, admin: httpx.AsyncClient, clients: ClientFactory
) -> None:
    anonymous = clients()
    response = await anonymous.get("/people")
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    member = clients()
    await invite_and_join(admin, member, ctx, "marco@studio-a.test")
    response = await member.get("/people")
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    assert (await member.get("/devices")).status_code == 200


async def test_invitation_link_activates_and_signs_in(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    member = clients()
    person = await invite_and_join(admin, member, ctx, "marco@studio-a.test")
    assert person["status"] == "active"
    assert person["name"] == "Marco"
    session = (await member.get("/session")).json()
    assert session["role"] == "member"
    async with owner_engine.connect() as conn:
        actions = (
            (await conn.execute(select(AuditEvent.action).order_by(AuditEvent.id))).scalars().all()
        )
    assert actions[-3:] == ["person_invited", "invite_accepted", "signed_in"]
    # The invitation link is single use.
    token = token_in(mailer(ctx).last_to("marco@studio-a.test"))
    again = await clients().post("/auth/invitation", json={"token": token})
    assert again.json()["code"] == "link_invalid"


async def test_invited_person_signs_in_by_address_while_the_invitation_lives(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    await admin.post("/people", json={"email": "sara@studio-a.test", "role": "member"})
    sara = clients()
    session = await sign_in(sara, ctx, "sara@studio-a.test")
    assert session["role"] == "member"
    async with owner_engine.connect() as conn:
        status = (
            await conn.execute(
                select(Membership.status)
                .join(User, User.id == Membership.user_id)
                .where(User.email == "sara@studio-a.test")
            )
        ).scalar_one()
        invitation = (await conn.execute(text("SELECT state FROM invitations"))).scalar_one()
    assert (status, invitation) == ("active", "accepted")


async def test_expired_invitation_cannot_sign_in_until_resent(
    org: Org,
    ctx: Context,
    admin: httpx.AsyncClient,
    clients: ClientFactory,
    owner_engine: AsyncEngine,
) -> None:
    created = (
        await admin.post("/people", json={"email": "luca@studio-a.test", "role": "member"})
    ).json()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE invitations SET sent_at = now() - interval '8 days', expires_at = now() - interval '1 day'"
            )
        )
    luca = clients()
    started = (await luca.post("/auth/sign-in", json={"email": "luca@studio-a.test"})).json()
    mail = mailer(ctx).last_to("luca@studio-a.test")
    assert "nessun team" in mail.text
    response = await luca.post(
        f"/auth/sign-in/{started['requestId']}/code", json={"code": "123456"}
    )
    assert response.json()["code"] == "invalid_code"
    assert (await admin.post(f"/people/{created['id']}/resend")).status_code == 200
    async with owner_engine.begin() as conn:
        await conn.execute(update(SignInRequest).values(resend_at=utcnow() - timedelta(seconds=1)))
    assert (await sign_in(luca, ctx, "luca@studio-a.test"))["role"] == "member"


async def test_cross_origin_writes_are_refused(admin: httpx.AsyncClient) -> None:
    response = await admin.delete("/session", headers={"Origin": "https://evil.test"})
    assert (response.status_code, response.json()["code"]) == (403, "forbidden")
    same = await admin.delete("/session", headers={"Origin": "http://localhost:4508"})
    assert same.status_code == 204


async def test_a_secure_session_cookie_is_host_only(
    org: Org, ctx: Context, client: httpx.AsyncClient
) -> None:
    # `__Host-`: Secure, Path=/ and no Domain, so no subdomain (the disks' *.meta.<domain>)
    # can set or overwrite it. Over plain http (cookie_secure off) the plain name stays.
    ctx.settings.cookie_secure = True
    await client.post("/auth/sign-in", json={"email": org.admin_email})
    token = token_in(mailer(ctx).last_to(org.admin_email))
    response = await client.post("/auth/magic-link", json={"token": token})
    cookie = response.headers["set-cookie"]
    name, _, rest = cookie.partition("=")
    assert name == "__Host-floppy_session"
    attributes = {a.strip().split("=")[0].lower() for a in rest.split(";")[1:]}
    assert {"secure", "httponly", "path"} <= attributes
    assert "domain" not in attributes
    assert "Path=/" in cookie
    value = rest.split(";")[0]
    session = await client.get("/session", headers={"Cookie": f"__Host-floppy_session={value}"})
    assert session.status_code == 200
    plain = await client.get("/session", headers={"Cookie": f"floppy_session={value}"})
    assert plain.status_code == 401
    out = await client.delete("/session", headers={"Cookie": f"__Host-floppy_session={value}"})
    assert out.status_code == 204
    assert "__Host-floppy_session=" in out.headers["set-cookie"]
    again = await client.get("/session", headers={"Cookie": f"__Host-floppy_session={value}"})
    assert again.status_code == 401
