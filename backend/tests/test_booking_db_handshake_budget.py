"""
How many DATABASE CONNECTIONS a voice action is allowed to open.

This is a latency test written as a correctness test, because on this stack the
connection count IS the latency and nothing else in the turn comes close.

Measured against the live Supabase database on 2026-08-13: a session that runs
only ``SELECT 1`` takes **1.97-3.01s, median 2.31s**. That is not query time, it
is TCP + TLS + auth, paid in full every time, because the engine uses NullPool
(see backend/db.py — pooling was tried on the agent worker and correctly
reverted: livekit-agents closes the event loop each job ends, and pooled
connections do not survive that).

So the per-turn budget before this test existed:

    BOOK          4 sessions   ~9.2s
    RESCHEDULE    4 sessions   ~9.2s
    CANCEL        1 session    ~2.3s
    availability block   2     ~4.6s
    caller appointments  1     ~2.3s

which is how live booking turns measured 15-31s end to end.

For contrast, and this is the part worth remembering: the 11,951-character
system prompt costs about **0.1-0.2s**. Timed against Groq on the same day with
the real prompt vs a 63-character one, TTFT was 1.55s vs 1.50s. Trimming the
prompt — the intuitive fix — would have bought nothing. The connection count was
the whole problem.

A regression here does not fail loudly; it just makes every caller wait another
2.3s. That is exactly the kind of thing that creeps back one innocent helper call
at a time, which is why the budget is asserted rather than documented.

Run: python -m pytest backend/tests/test_booking_db_handshake_budget.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import select

import backend.db as db
from backend.models.appointment import Appointment
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.tenant import Tenant

TENANT = "11111111-1111-1111-1111-111111111111"
DOCTOR = "22222222-2222-2222-2222-222222222222"
PHONE = "9148768120"
NAME = "Ainan"

#: Median seconds per fresh Supabase connection, measured 2026-08-13. Only used
#: to make the failure message state the real-world cost.
HANDSHAKE_SECONDS = 2.31


class _CountingSessionmaker:
    """Wraps AsyncSessionLocal and counts how often a NEW session is opened."""

    def __init__(self, inner):
        self._inner = inner
        self.count = 0

    def __call__(self, *a, **k):
        self.count += 1
        return self._inner(*a, **k)


@pytest_asyncio.fixture
async def counter(monkeypatch):
    """Count connections while keeping real DB behaviour.

    Patches the sessionmaker in backend.db AND in every module that imported the
    name directly — a module holding its own reference would otherwise open
    uncounted connections and the budget would silently stop being enforced.
    """
    import backend.services.availability as availability
    import backend.services.his as his

    async with db.engine.begin() as c:
        await c.run_sync(db.Base.metadata.create_all)
    async with db.AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT, clinic_name="C", language="hi-IN",
                     is_active=True, status="active"))
        s.add(Doctor(id=DOCTOR, tenant_id=TENANT, name="Dr Salman",
                     specialization="Cardiologist", is_available=True))
        for dow in range(7):
            s.add(DoctorAvailability(tenant_id=TENANT, doctor_id=DOCTOR,
                                     day_of_week=dow,
                                     start_time=dt.time(9, 0),
                                     end_time=dt.time(18, 0)))
        await s.commit()

    counting = _CountingSessionmaker(db.AsyncSessionLocal)
    monkeypatch.setattr(db, "AsyncSessionLocal", counting)
    for mod in (his, availability):
        if hasattr(mod, "AsyncSessionLocal"):
            monkeypatch.setattr(mod, "AsyncSessionLocal", counting)
    yield counting

    async with db.engine.begin() as c:
        await c.run_sync(db.Base.metadata.drop_all)


def _budget_msg(action, opened, allowed):
    extra = (opened - allowed) * HANDSHAKE_SECONDS
    return (
        f"{action} opened {opened} database connections, budget is {allowed}. "
        f"Each one costs ~{HANDSHAKE_SECONDS}s against live Supabase, so this "
        f"adds ~{extra:.1f}s to every caller's wait. Wrap the new work in the "
        f"existing backend.db.session_scope() instead of opening its own session."
    )


def _tomorrow() -> str:
    return (dt.date.today() + dt.timedelta(days=1)).strftime("%d/%m/%Y")


@pytest.mark.asyncio
async def test_book_opens_one_connection(counter):
    from backend.services.his import execute_booking_action

    res = await execute_booking_action(
        action="BOOK", tenant_id=TENANT, name=NAME, phone=PHONE,
        date_str=_tomorrow(), time_str="02:00 PM", doctor_name="Dr Salman",
        notes="N/A", call_id="call-1", source="voice",
    )
    assert res["success"] is True, res
    assert counter.count <= 1, _budget_msg("BOOK", counter.count, 1)


@pytest.mark.asyncio
async def test_reschedule_opens_one_connection(counter):
    from backend.services.his import execute_booking_action

    await execute_booking_action(
        action="BOOK", tenant_id=TENANT, name=NAME, phone=PHONE,
        date_str=_tomorrow(), time_str="02:00 PM", doctor_name="Dr Salman",
        notes="N/A", call_id="call-1", source="voice",
    )
    counter.count = 0
    res = await execute_booking_action(
        action="RESCHEDULE", tenant_id=TENANT, name=NAME, phone=PHONE,
        date_str=_tomorrow(), time_str="03:00 PM", doctor_name="",
        notes="N/A", call_id="call-2", source="voice",
    )
    assert res["success"] is True, res
    assert counter.count <= 1, _budget_msg("RESCHEDULE", counter.count, 1)


@pytest.mark.asyncio
async def test_cancel_opens_one_connection(counter):
    from backend.services.his import execute_booking_action

    await execute_booking_action(
        action="BOOK", tenant_id=TENANT, name=NAME, phone=PHONE,
        date_str=_tomorrow(), time_str="02:00 PM", doctor_name="Dr Salman",
        notes="N/A", call_id="call-1", source="voice",
    )
    counter.count = 0
    res = await execute_booking_action(
        action="CANCEL", tenant_id=TENANT, name=NAME, phone=PHONE,
        date_str="", time_str="", doctor_name="", notes="N/A",
        call_id="call-3", source="voice",
    )
    assert res["success"] is True, res
    assert counter.count <= 1, _budget_msg("CANCEL", counter.count, 1)


@pytest.mark.asyncio
async def test_the_availability_block_opens_one_connection(counter):
    """Built on the caller's turn, so its connections are turn latency too."""
    from backend.services.availability_prompt import real_availability_block

    block = await real_availability_block(TENANT, "kal do baje Dr Salman")
    assert "Dr Salman" in block
    assert counter.count <= 1, _budget_msg("real_availability_block", counter.count, 1)


# ── The mechanism itself ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_scope_reuses_one_connection_for_everything_inside(counter):
    from sqlalchemy import text

    async with db.session_scope():
        for _ in range(5):
            async with db.scoped_session() as s:
                await s.execute(text("SELECT 1"))
    assert counter.count == 1, (
        f"the scope opened {counter.count} connections instead of reusing one"
    )


@pytest.mark.asyncio
async def test_nesting_a_scope_does_not_open_a_second_connection(counter):
    """A helper that opens its own scope must stay correct when called from
    inside a bigger one — otherwise composing them silently costs a handshake."""
    from sqlalchemy import text

    async with db.session_scope():
        async with db.session_scope():
            async with db.scoped_session() as s:
                await s.execute(text("SELECT 1"))
    assert counter.count == 1, f"nesting opened {counter.count} connections"


@pytest.mark.asyncio
async def test_outside_a_scope_behaviour_is_unchanged(counter):
    """Every existing caller — the API, tests, scripts — must be unaffected:
    with no scope open, each step still gets its own short-lived session."""
    from sqlalchemy import text

    for _ in range(3):
        async with db.scoped_session() as s:
            await s.execute(text("SELECT 1"))
    assert counter.count == 3


@pytest.mark.asyncio
async def test_the_scoped_session_is_still_usable_after_a_step_returns(counter):
    """scoped_session must NOT close the shared session on exit — the scope owns
    it. If it closed, the second step in any action would fail."""
    from sqlalchemy import text

    async with db.session_scope():
        async with db.scoped_session() as s:
            await s.execute(text("SELECT 1"))
        async with db.scoped_session() as s2:
            result = await s2.execute(text("SELECT 1"))
            assert result.scalar() == 1


@pytest.mark.asyncio
async def test_writes_inside_a_scope_are_actually_persisted(counter):
    """The reason this is opt-in: inside a scope the session is shared, so a
    commit in one step commits everything staged. Assert a real booking's row is
    genuinely durable rather than lost with the scope."""
    from backend.services.his import execute_booking_action

    res = await execute_booking_action(
        action="BOOK", tenant_id=TENANT, name=NAME, phone=PHONE,
        date_str=_tomorrow(), time_str="11:00 AM", doctor_name="Dr Salman",
        notes="N/A", call_id="call-durable", source="voice",
    )
    assert res["success"] is True, res

    async with db.AsyncSessionLocal() as s:
        rows = (await s.execute(select(Appointment))).scalars().all()
    assert len(rows) == 1, "the booking did not survive its session scope"
    assert rows[0].patient_phone == PHONE
