"""
Superadmin "Go to Clinic Admin Dashboard" — impersonate one clinic.

Every test here maps to a promise the feature makes, and most of them to a way it
could quietly stop keeping it:

  * server-side role gate — a clinic token / no token cannot mint a session
  * scoped to ONE clinic — the token reads its own clinic and 404s on others
  * not a privilege escalation — the token opens no superadmin endpoint
  * the clinic's password is never read, returned, or needed
  * audited — start and end are both in audit_logs, with who/which/when
  * revocable — after "exit" the token is dead, not merely hidden
  * expiring — a session past its TTL is dead even if nobody exited
  * no untraceable session — a failed audit write means no token at all

Run: python -m pytest backend/tests/test_clinic_impersonation.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-impersonation-tests")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SUPERADMIN_EMAIL", "root@lifodial.com")
os.environ.setdefault("SUPERADMIN_PASSWORD", "test-superadmin-password")

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.db as db_module
from backend.models.agent_config import AgentConfig
from backend.models.audit_log import AuditLog
from backend.models.impersonation_session import ImpersonationSession
from backend.models.tenant import Tenant
from backend.security import create_access_token, hash_password

CLINIC_PASSWORD = "the-clinic-real-password"


@pytest_asyncio.fixture
async def app_client():
    from backend.db import Base, engine
    from backend.main import app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_ai_number_seq = iter(range(20_000, 99_999))


async def _make_clinic(clinic_name="Impersonation Clinic", email="imp@test.com"):
    async with db_module.AsyncSessionLocal() as s:
        t = Tenant(
            clinic_name=clinic_name, admin_email=email,
            admin_password=hash_password(CLINIC_PASSWORD), language="en-IN",
            ai_number=f"+91 90002 {next(_ai_number_seq)}", is_active=True,
        )
        s.add(t)
        await s.flush()
        s.add(AgentConfig(tenant_id=t.id, agent_name="Receptionist"))
        await s.commit()
        return t.id


def _super_headers() -> dict:
    return {"Authorization": f"Bearer {create_access_token('superadmin', 'superadmin')}"}


def _clinic_headers(tenant_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(tenant_id, 'clinic')}"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _impersonate(app_client, tenant_id: str) -> dict:
    r = await app_client.post(f"/admin/clinics/{tenant_id}/impersonate", headers=_super_headers())
    assert r.status_code == 200, r.text
    return r.json()


# ── The server-side gate ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clinic_token_cannot_mint_an_impersonation_session(app_client):
    """The role check must be the backend's, not the button's absence."""
    tenant_id = await _make_clinic()
    other_id = await _make_clinic("Other Clinic", "other@test.com")

    # A clinic trying to impersonate itself...
    r = await app_client.post(
        f"/admin/clinics/{tenant_id}/impersonate", headers=_clinic_headers(tenant_id)
    )
    assert r.status_code == 403, r.text

    # ...and a clinic trying to impersonate someone else.
    r = await app_client.post(
        f"/admin/clinics/{other_id}/impersonate", headers=_clinic_headers(tenant_id)
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_unauthenticated_cannot_mint_an_impersonation_session(app_client):
    tenant_id = await _make_clinic()
    r = await app_client.post(f"/admin/clinics/{tenant_id}/impersonate")
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_impersonating_an_unknown_clinic_is_404(app_client):
    r = await app_client.post(
        "/admin/clinics/00000000-0000-0000-0000-000000000000/impersonate",
        headers=_super_headers(),
    )
    assert r.status_code == 404, r.text
    async with db_module.AsyncSessionLocal() as s:
        rows = (await s.execute(select(ImpersonationSession))).scalars().all()
    assert rows == [], "a 404 must not leave a session row behind"


# ── What the minted session can and cannot do ─────────────────────────────────

@pytest.mark.asyncio
async def test_token_opens_the_clinics_own_dashboard(app_client):
    """No login form, no clinic password — the clinic's real dashboard data."""
    tenant_id = await _make_clinic("Kmct Clinic", "kmct@test.com")
    body = await _impersonate(app_client, tenant_id)

    assert body["tenant_id"] == tenant_id
    assert body["clinic_name"] == "Kmct Clinic"
    assert body["role"] == "clinic", "an impersonation token must not carry superadmin authority"
    assert body["expires_in"] > 0

    headers = _bearer(body["access_token"])
    # The clinic dashboard's own KPI endpoint, tenant-scoped off the token.
    r = await app_client.get("/api/clinic/stats", headers=headers)
    assert r.status_code == 200, r.text
    # And the clinic's own profile.
    r = await app_client.get(f"/tenants/{tenant_id}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["clinic_name"] == "Kmct Clinic"


@pytest.mark.asyncio
async def test_the_clinics_password_is_never_involved(app_client):
    """Nothing in the flow reads, needs, or echoes Tenant.admin_password."""
    tenant_id = await _make_clinic("Secret Clinic", "secret@test.com")
    body = await _impersonate(app_client, tenant_id)

    blob = str(body)
    assert CLINIC_PASSWORD not in blob
    assert "password" not in blob.lower(), f"impersonate response mentions a password: {body}"

    async with db_module.AsyncSessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        # Unchanged: no rotation, no reset, no plaintext written anywhere.
        assert tenant.admin_password.startswith("pbkdf2_sha256$")

        audit_details = [
            (a.detail or "") + (a.target or "")
            for a in (await s.execute(select(AuditLog))).scalars().all()
        ]
    assert all(CLINIC_PASSWORD not in d for d in audit_details)


@pytest.mark.asyncio
async def test_token_is_scoped_to_exactly_one_clinic(app_client):
    """The scoping bug this guards: reading a DIFFERENT clinic's data."""
    mine = await _make_clinic("Mine", "mine@test.com")
    theirs = await _make_clinic("Theirs", "theirs@test.com")

    body = await _impersonate(app_client, mine)
    headers = _bearer(body["access_token"])

    r = await app_client.get(f"/tenants/{theirs}", headers=headers)
    assert r.status_code == 404, f"impersonation token reached another clinic: {r.text}"

    r = await app_client.get(f"/tenants/{theirs}/doctors", headers=headers)
    assert r.status_code == 404, f"impersonation token listed another clinic's doctors: {r.text}"


@pytest.mark.asyncio
async def test_token_is_not_a_superadmin_token(app_client):
    """It must not be a general-purpose bypass: no superadmin endpoint opens."""
    tenant_id = await _make_clinic()
    headers = _bearer((await _impersonate(app_client, tenant_id))["access_token"])

    for method, path in [
        ("GET", "/admin/clinics"),
        ("GET", "/admin/overview"),
        ("GET", "/admin/impersonation/sessions"),
        ("GET", "/platform/audit-logs"),
        ("POST", f"/admin/clinics/{tenant_id}/impersonate"),
    ]:
        r = await app_client.request(method, path, headers=headers)
        assert r.status_code == 403, f"{method} {path} opened to an impersonation token: {r.status_code}"


@pytest.mark.asyncio
async def test_a_forged_superadmin_impersonation_token_is_rejected(app_client):
    """Defence in depth: `imp` + role=superadmin must never be honoured."""
    tenant_id = await _make_clinic()
    forged = create_access_token(
        subject=tenant_id, role="superadmin", extra={"imp": True, "jti": "whatever"}
    )
    r = await app_client.get("/admin/clinics", headers=_bearer(forged))
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_an_impersonation_token_with_no_session_row_is_rejected(app_client):
    """A signed token alone is not enough — the session row is the authority."""
    tenant_id = await _make_clinic()
    orphan = create_access_token(
        subject=tenant_id, role="clinic",
        extra={"imp": True, "jti": "11111111-1111-1111-1111-111111111111"},
    )
    r = await app_client.get("/api/clinic/stats", headers=_bearer(orphan))
    assert r.status_code == 401, r.text


# ── Audit trail ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_and_end_are_both_audited(app_client):
    """"Who looked at this clinic's dashboard, and when" must be answerable."""
    tenant_id = await _make_clinic("Audited Clinic", "audited@test.com")
    body = await _impersonate(app_client, tenant_id)

    async with db_module.AsyncSessionLocal() as s:
        start = (
            await s.execute(select(AuditLog).where(AuditLog.action == "impersonation.start"))
        ).scalars().all()
    assert len(start) == 1
    assert start[0].actor == "superadmin"
    assert start[0].target == tenant_id
    assert "Audited Clinic" in start[0].detail
    assert body["impersonation_id"] in start[0].detail
    assert start[0].created_at is not None

    r = await app_client.post("/admin/impersonation/end", headers=_bearer(body["access_token"]))
    assert r.status_code == 200, r.text

    async with db_module.AsyncSessionLocal() as s:
        end = (
            await s.execute(select(AuditLog).where(AuditLog.action == "impersonation.end"))
        ).scalars().all()
        row = (await s.execute(select(ImpersonationSession))).scalars().one()
    assert len(end) == 1
    assert end[0].actor == "superadmin"
    assert end[0].target == tenant_id
    # The session row is the queryable per-clinic record of the same events.
    assert row.actor == "superadmin"
    assert row.tenant_id == tenant_id
    assert row.started_at is not None
    assert row.ended_at is not None, "ended_at is how 'when did it end' is answered"
    assert row.ended_reason == "exit"


@pytest.mark.asyncio
async def test_no_token_is_issued_if_the_trail_cannot_be_written(app_client, monkeypatch):
    """An untraceable impersonation session must be impossible, not merely rare.

    The session row and its audit entry go in on ONE commit, so a failed write
    takes both down together. Injected at the commit itself because that is the
    real failure — the database being unavailable, not the row objects.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    tenant_id = await _make_clinic()

    async def _boom(self):
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr(AsyncSession, "commit", _boom)

    r = await app_client.post(f"/admin/clinics/{tenant_id}/impersonate", headers=_super_headers())
    assert r.status_code == 500, r.text
    assert "access_token" not in r.text, "a token was handed out despite the failed write"

    monkeypatch.undo()

    async with db_module.AsyncSessionLocal() as s:
        rows = (await s.execute(select(ImpersonationSession))).scalars().all()
        audits = (await s.execute(select(AuditLog))).scalars().all()
    assert rows == [], "a session row survived a failed audit write"
    assert audits == []


@pytest.mark.asyncio
async def test_sessions_endpoint_lists_the_trail_for_one_clinic(app_client):
    a = await _make_clinic("Clinic A", "a@test.com")
    b = await _make_clinic("Clinic B", "b@test.com")
    await _impersonate(app_client, a)
    await _impersonate(app_client, b)

    r = await app_client.get(f"/admin/impersonation/sessions?clinic_id={a}", headers=_super_headers())
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == a
    assert rows[0]["clinic_name"] == "Clinic A"
    assert rows[0]["actor"] == "superadmin"
    assert rows[0]["active"] is True

    r = await app_client.get("/admin/impersonation/sessions", headers=_super_headers())
    assert len(r.json()) == 2


# ── Ending and expiring ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exiting_kills_the_token_for_good(app_client):
    """"Exit" must revoke, not just navigate away."""
    tenant_id = await _make_clinic()
    token = (await _impersonate(app_client, tenant_id))["access_token"]

    assert (await app_client.get("/api/clinic/stats", headers=_bearer(token))).status_code == 200

    r = await app_client.post("/admin/impersonation/end", headers=_bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ended"

    # Same token, same endpoint, now dead — and it stays dead on replay.
    for _ in range(2):
        r = await app_client.get("/api/clinic/stats", headers=_bearer(token))
        assert r.status_code == 401, f"a token survived its own exit: {r.status_code}"

    # It cannot be used to reach the clinic's other data either.
    r = await app_client.get(f"/tenants/{tenant_id}", headers=_bearer(token))
    assert r.status_code == 401, r.text

    # And ending it again cannot succeed (there is no live session to end).
    r = await app_client.post("/admin/impersonation/end", headers=_bearer(token))
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_a_normal_clinic_login_cannot_end_anything(app_client):
    """This endpoint must not become a way to invalidate a real clinic's token."""
    tenant_id = await _make_clinic()
    r = await app_client.post("/admin/impersonation/end", headers=_clinic_headers(tenant_id))
    assert r.status_code == 400, r.text

    # The clinic's own session is untouched.
    r = await app_client.get("/api/clinic/stats", headers=_clinic_headers(tenant_id))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_one_exit_does_not_end_another_superadmins_session(app_client):
    """Ending is scoped to the caller's own `jti`."""
    a = await _make_clinic("Clinic A", "a2@test.com")
    b = await _make_clinic("Clinic B", "b2@test.com")
    token_a = (await _impersonate(app_client, a))["access_token"]
    token_b = (await _impersonate(app_client, b))["access_token"]

    assert (await app_client.post("/admin/impersonation/end", headers=_bearer(token_a))).status_code == 200

    assert (await app_client.get("/api/clinic/stats", headers=_bearer(token_a))).status_code == 401
    assert (await app_client.get("/api/clinic/stats", headers=_bearer(token_b))).status_code == 200


@pytest.mark.asyncio
async def test_a_session_past_its_ttl_is_dead_even_without_an_exit(app_client):
    """The TTL is a real stop, not a display detail — a forgotten tab closes itself."""
    tenant_id = await _make_clinic()
    body = await _impersonate(app_client, tenant_id)
    token = body["access_token"]

    # Backdate the row's expiry rather than sleep. The JWT's own exp is 30 minutes
    # out, so this proves the SESSION ROW is what gates access.
    async with db_module.AsyncSessionLocal() as s:
        row = (
            await s.execute(
                select(ImpersonationSession).where(ImpersonationSession.id == body["impersonation_id"])
            )
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()

    r = await app_client.get("/api/clinic/stats", headers=_bearer(token))
    assert r.status_code == 401, r.text

    # The trail still reports when it ended, without an explicit exit.
    r = await app_client.get("/admin/impersonation/sessions", headers=_super_headers())
    listed = r.json()[0]
    assert listed["active"] is False
    assert listed["ended_reason"] == "expired"
    assert listed["ended_at"] is not None


@pytest.mark.asyncio
async def test_impersonation_ttl_is_short(app_client):
    from backend.services.impersonation import IMPERSONATION_TTL

    assert timedelta(0) < IMPERSONATION_TTL <= timedelta(hours=1), (
        "an impersonation session must be short-lived; this is the ceiling on how "
        "long a forgotten tab keeps another party's data open"
    )
    tenant_id = await _make_clinic()
    body = await _impersonate(app_client, tenant_id)
    assert body["expires_in"] <= IMPERSONATION_TTL.total_seconds()


@pytest.mark.asyncio
async def test_the_websocket_revocation_gate_tracks_the_session(app_client):
    """The WS endpoints authenticate from ?token= and never see backend/auth.py.

    Without their own check they would honour an impersonation token for the full
    30 minutes of its JWT exp after the superadmin exited — a live dashboard socket
    outliving the session it belongs to. This exercises the gate itself; the test
    below proves every socket endpoint is actually behind it.

    (Driving a real websocket_connect() here is not possible: TestClient runs the
    app on another thread, where an in-memory SQLite database is a DIFFERENT
    database, so the schema this test created is invisible to it.)
    """
    from backend.security import decode_access_token
    from backend.services.impersonation import claims_still_active

    tenant_id = await _make_clinic("Socket Clinic", "socket@test.com")
    token = (await _impersonate(app_client, tenant_id))["access_token"]
    claims = decode_access_token(token)

    assert await claims_still_active(claims) is True

    r = await app_client.post("/admin/impersonation/end", headers=_bearer(token))
    assert r.status_code == 200, r.text

    assert await claims_still_active(claims) is False, (
        "a socket would still open with a token whose session was exited"
    )

    # A genuine clinic login carries no impersonation claim, so the gate is a
    # no-op for it — and costs it no database round trip.
    assert await claims_still_active(decode_access_token(create_access_token(tenant_id, "clinic"))) is True
    assert await claims_still_active(decode_access_token(create_access_token("superadmin", "superadmin"))) is True
    assert await claims_still_active(None) is True


def test_every_websocket_endpoint_is_behind_the_revocation_gate():
    """Any WS handler that decodes a token itself MUST also call the gate.

    This is the drift guard: the next websocket endpoint to authenticate from
    ?token= inherits nothing from backend/auth.py, so forgetting this line is how
    a revoked impersonation session would quietly get a socket again.
    """
    import inspect

    from backend.routers import agent_test, ws

    for module in (ws, agent_test):
        src = inspect.getsource(module)
        decodes = src.count("decode_access_token(token)")
        gates = src.count("claims_still_active(claims)")
        assert decodes and gates >= decodes, (
            f"{module.__name__} decodes a token in {decodes} place(s) but gates "
            f"only {gates} — every ?token= websocket needs the impersonation check"
        )


# ── Regressions the feature must not cause ────────────────────────────────────

@pytest.mark.asyncio
async def test_the_clinics_own_login_still_works(app_client):
    """Impersonation must not disturb the credential path it bypasses."""
    tenant_id = await _make_clinic("Login Clinic", "login@test.com")
    await _impersonate(app_client, tenant_id)

    r = await app_client.post(
        "/auth/clinic-login", json={"email": "login@test.com", "password": CLINIC_PASSWORD}
    )
    assert r.status_code == 200, r.text
    assert r.json()["tenant_id"] == tenant_id
    assert r.json()["role"] == "clinic"

    # A genuine clinic token carries no impersonation state, so no banner shows.
    from backend.security import decode_access_token

    claims = decode_access_token(r.json()["access_token"])
    assert "imp" not in claims and "jti" not in claims

    r = await app_client.post(
        "/auth/clinic-login", json={"email": "login@test.com", "password": "wrong"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_deleting_a_clinic_removes_its_sessions_but_keeps_the_trail(app_client):
    tenant_id = await _make_clinic("Doomed Clinic", "doomed@test.com")
    await _impersonate(app_client, tenant_id)

    r = await app_client.delete(f"/admin/clinics/{tenant_id}", headers=_super_headers())
    assert r.status_code == 204, r.text

    async with db_module.AsyncSessionLocal() as s:
        sessions = (
            await s.execute(
                select(ImpersonationSession).where(ImpersonationSession.tenant_id == tenant_id)
            )
        ).scalars().all()
        audits = (
            await s.execute(select(AuditLog).where(AuditLog.action == "impersonation.start"))
        ).scalars().all()

    assert sessions == [], "auth state for a deleted clinic must not linger"
    assert len(audits) == 1, "the record of who viewed this clinic must survive its deletion"
    assert audits[0].target == tenant_id
