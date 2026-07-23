# -*- coding: utf-8 -*-
"""
Verifies audit P4 fixes — credit balances can no longer silently go negative.

  - check_call_allowed() blocks a call unless the balance covers the WORST-CASE
    cost of a full-length call (rate x ceil(max_duration/60)), so a call can
    never drive the balance negative.
  - A suspended (is_active=False) clinic is blocked regardless of balance.
  - deduct_call_credits() records an overdraw HONESTLY (true negative balance in
    the ledger) and auto-suspends the clinic rather than silently clamping to 0.

Run:
    python -m pytest backend/tests/test_credit_gate.py -v
"""
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_credit_gate.db"

import pytest
import pytest_asyncio
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.models.tenant import Tenant
from backend.models.clinic_credits import ClinicCredits, CreditTransaction
from backend.services.credit_service import CreditService
from backend.services.tenant_service import create_tenant
from backend.security import hash_password


@pytest_asyncio.fixture
async def fresh_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _make_clinic(email: str, balance: float, rate: float = 1.50, is_active: bool = True) -> str:
    """Create a tenant + a ClinicCredits row with a precise balance; return tenant_id."""
    async with AsyncSessionLocal() as s:
        await create_tenant(
            s, clinic_name=f"Clinic {email}", admin_email=email,
            admin_password=hash_password("x"),
        )
        await s.commit()
        tid = (await s.execute(select(Tenant.id).where(Tenant.admin_email == email))).scalar_one()
        s.add(ClinicCredits(tenant_id=tid, balance=balance, rate_per_minute=rate, is_active=is_active))
        await s.commit()
        return tid


@pytest.mark.asyncio
async def test_gate_blocks_when_balance_below_worst_case(fresh_db):
    # Balance covers 3 min but a full 5-min call would overdraw -> blocked.
    tid = await _make_clinic("gate-low@example.com", balance=5.00, rate=1.50)
    async with AsyncSessionLocal() as s:
        gate = await CreditService.check_call_allowed(s, tid, max_duration_seconds=300)
    assert gate["allowed"] is False
    assert gate["reason"] == "insufficient_balance"
    assert gate["required"] == 7.50  # 1.50 * ceil(300/60) = 1.50 * 5


@pytest.mark.asyncio
async def test_gate_allows_when_balance_covers_worst_case(fresh_db):
    tid = await _make_clinic("gate-ok@example.com", balance=10.00, rate=1.50)
    async with AsyncSessionLocal() as s:
        gate = await CreditService.check_call_allowed(s, tid, max_duration_seconds=300)
    assert gate["allowed"] is True
    assert gate["reason"] == "ok"


@pytest.mark.asyncio
async def test_gate_blocks_suspended_clinic_even_with_balance(fresh_db):
    tid = await _make_clinic("gate-susp@example.com", balance=1000.00, is_active=False)
    async with AsyncSessionLocal() as s:
        gate = await CreditService.check_call_allowed(s, tid, max_duration_seconds=300)
    assert gate["allowed"] is False
    assert gate["reason"] == "credit_suspended"


@pytest.mark.asyncio
async def test_overdraw_records_true_negative_and_suspends(fresh_db):
    # A 5-minute call (₹7.50) against a ₹2 balance: the gate would normally stop
    # this, but if a deduction slips through it must record the real -5.50 and
    # suspend — never silently clamp to 0.
    tid = await _make_clinic("overdraw@example.com", balance=2.00, rate=1.50)
    async with AsyncSessionLocal() as s:
        res = await CreditService.deduct_call_credits(s, tid, duration_seconds=300, call_id="c1")
    assert res["deducted"] == 7.50
    assert res["balance_after"] == -5.50  # honest negative, NOT clamped

    async with AsyncSessionLocal() as s:
        credits = (await s.execute(select(ClinicCredits).where(ClinicCredits.tenant_id == tid))).scalar_one()
        assert credits.is_active is False  # auto-suspended
        txn = (await s.execute(
            select(CreditTransaction).where(CreditTransaction.tenant_id == tid)
        )).scalars().first()
        assert txn.balance_after == -5.50
        assert "OVERDRAWN" in txn.description


@pytest.mark.asyncio
async def test_normal_deduction_does_not_suspend(fresh_db):
    tid = await _make_clinic("normal@example.com", balance=100.00, rate=1.50)
    async with AsyncSessionLocal() as s:
        res = await CreditService.deduct_call_credits(s, tid, duration_seconds=120, call_id="c2")
    assert res["deducted"] == 3.00
    assert res["balance_after"] == 97.00
    async with AsyncSessionLocal() as s:
        credits = (await s.execute(select(ClinicCredits).where(ClinicCredits.tenant_id == tid))).scalar_one()
        assert credits.is_active is True
