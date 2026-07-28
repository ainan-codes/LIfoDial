"""
Tests for the shared clinic cascade delete
(backend/services/tenant_service.py::delete_tenant_cascade).

Why these exist: deleting a clinic used to be duplicated by hand in two
endpoints, and BOTH copies missed embed_events — leaving orphaned rows in
production for every deleted clinic. These tests pin the two properties that
actually matter:

  1. Every tenant-scoped table is emptied for that tenant (no orphans), and
     in particular embed_events, which has no FK to protect it.
  2. A clinic with a doctor who has appointments can actually be deleted.
     appointments.doctor_id is declared ON DELETE SET NULL on a NOT NULL
     column, which Postgres cannot satisfy — so appointments MUST be deleted
     before doctors or the whole delete raises IntegrityError.

Run: python -m pytest backend/tests/test_tenant_cascade_delete.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db import Base
from backend.models.agent_config import AgentConfig
from backend.models.appointment import Appointment
from backend.models.call_record import CallRecord
from backend.models.doctor import Doctor
from backend.models.embed_analytics import EmbedEvent
from backend.models.tenant import Tenant
from backend.services.tenant_service import delete_tenant_cascade


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_clinic(session, name: str) -> Tenant:
    """A clinic with the child rows that historically broke the delete."""
    tenant = Tenant(clinic_name=name, admin_email=f"{name}@x.com", admin_password="h")
    session.add(tenant)
    await session.flush()

    agent = AgentConfig(tenant_id=tenant.id, agent_name="Receptionist")
    doctor = Doctor(tenant_id=tenant.id, name="Dr. Real", specialization="GP")
    session.add_all([agent, doctor])
    await session.flush()

    session.add_all([
        # An appointment for that doctor — the IntegrityError trap.
        Appointment(
            tenant_id=tenant.id, doctor_id=doctor.id, patient_phone="+911",
            slot_time=datetime(2026, 8, 1, 10, 0),
        ),
        CallRecord(tenant_id=tenant.id, agent_id=agent.id),
        # No FK on tenant_id at all, which is how this got missed.
        EmbedEvent(
            tenant_id=tenant.id, agent_id=agent.id,
            event_type="widget_open", session_id=f"sess-{name}",
        ),
    ])
    await session.commit()
    return tenant


async def _counts(session, tenant_id: str) -> dict[str, int]:
    out = {}
    for model, label in (
        (Appointment, "appointments"), (Doctor, "doctors"),
        (AgentConfig, "agent_configs"), (CallRecord, "call_records"),
        (EmbedEvent, "embed_events"), (Tenant, "tenants"),
    ):
        col = Tenant.id if model is Tenant else model.tenant_id
        out[label] = (await session.execute(
            select(func.count()).select_from(model).where(col == tenant_id)
        )).scalar()
    return out


@pytest.mark.asyncio
async def test_cascade_leaves_no_orphans(session):
    tenant = await _make_clinic(session, "cascade-clinic")
    tid = tenant.id

    before = await _counts(session, tid)
    assert all(v > 0 for v in before.values()), f"fixture did not populate: {before}"

    await delete_tenant_cascade(session, tenant)
    await session.commit()

    after = await _counts(session, tid)
    assert after == {k: 0 for k in after}, f"orphaned rows survived: {after}"


@pytest.mark.asyncio
async def test_embed_events_are_deleted(session):
    """Regression: embed_events has no FK, so nothing in the DB catches this."""
    tenant = await _make_clinic(session, "embed-clinic")
    tid = tenant.id

    await delete_tenant_cascade(session, tenant)
    await session.commit()

    remaining = (await session.execute(
        select(func.count()).select_from(EmbedEvent).where(EmbedEvent.tenant_id == tid)
    )).scalar()
    assert remaining == 0, "embed_events orphaned — this is the bug that shipped"


@pytest.mark.asyncio
async def test_doctor_with_appointments_does_not_block_delete(session):
    """appointments MUST be deleted before doctors (NOT NULL + SET NULL trap)."""
    tenant = await _make_clinic(session, "booked-clinic")
    await delete_tenant_cascade(session, tenant)
    await session.commit()  # would raise IntegrityError if ordered wrongly


@pytest.mark.asyncio
async def test_cascade_does_not_touch_other_clinics(session):
    """Multi-tenant safety: deleting one clinic must not scratch its neighbour."""
    keep = await _make_clinic(session, "keep-clinic")
    doomed = await _make_clinic(session, "doomed-clinic")
    keep_id = keep.id

    await delete_tenant_cascade(session, doomed)
    await session.commit()

    survivors = await _counts(session, keep_id)
    assert all(v > 0 for v in survivors.values()), f"collateral damage: {survivors}"


@pytest.mark.asyncio
async def test_reports_what_it_deleted(session):
    tenant = await _make_clinic(session, "report-clinic")
    report = await delete_tenant_cascade(session, tenant)
    await session.commit()

    assert report["tenants"] == 1
    assert report["appointments"] == 1
    assert report["doctors"] == 1
    assert report["embed_events"] == 1
