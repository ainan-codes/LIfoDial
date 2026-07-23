# -*- coding: utf-8 -*-
"""
Verifies audit P1 — the /admin/billing endpoint returns ONLY real data and never
fabricates invoices/MRR/plan revenue.

  - Clinic counts (total / active / by plan) come from the tenants table.
  - has_paid_billing=False, mrr=0, total_collected=0 (no subscription system).
  - Credit aggregates come from the real clinic_credits ledger.
  - recent_transactions always resolves a real clinic name (inner join) — the
    fixture's "Unknown" clinic can never appear.

Run:
    python -m pytest backend/tests/test_billing_overview.py -v
"""
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_billing_overview.db"

import pytest
import pytest_asyncio
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.models.tenant import Tenant
from backend.models.clinic_credits import ClinicCredits, CreditTransaction
from backend.services.tenant_service import create_tenant
from backend.security import hash_password
from backend.routers.admin import billing_overview


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


@pytest.mark.asyncio
async def test_billing_reports_real_data_only(fresh_db):
    async with AsyncSessionLocal() as s:
        a = await create_tenant(s, clinic_name="Clinic A", admin_email="a@x.com", admin_password=hash_password("x"))
        b = await create_tenant(s, clinic_name="Clinic B", admin_email="b@x.com", admin_password=hash_password("x"))
        c = await create_tenant(s, clinic_name="Clinic C", admin_email="c@x.com", admin_password=hash_password("x"))
        a.plan, a.is_active = "Free", True
        b.plan, b.is_active = "Pro", True
        c.plan, c.is_active = "Free", False
        s.add(ClinicCredits(tenant_id=a.id, balance=10.0, total_added=50.0, total_deducted=40.0))
        s.add(ClinicCredits(tenant_id=b.id, balance=5.0, total_added=5.0, total_deducted=0.0))
        s.add(CreditTransaction(
            tenant_id=a.id, transaction_type="topup", amount=50.0, balance_after=50.0,
            description="Admin top-up", performed_by="super_admin",
        ))
        await s.commit()

    async with AsyncSessionLocal() as s:
        result = await billing_overview(user=None, db=s)

    # No fabricated subscription/invoice numbers.
    assert result["has_paid_billing"] is False
    assert result["mrr"] == 0
    assert result["total_collected"] == 0

    # Real clinic counts.
    assert result["total_clinics"] == 3
    assert result["active_clinics"] == 2
    assert result["clinics_by_plan"] == {"Free": 2, "Pro": 1}
    assert result["paid_plan_clinics"] == 1

    # Real credit ledger aggregates.
    assert result["credits"]["total_added"] == 55.0
    assert result["credits"]["total_used"] == 40.0
    assert result["credits"]["outstanding_balance"] == 15.0

    # Ledger rows always resolve a real clinic — never "Unknown".
    assert len(result["recent_transactions"]) == 1
    assert result["recent_transactions"][0]["clinic_name"] == "Clinic A"
    assert result["recent_transactions"][0]["type"] == "topup"


@pytest.mark.asyncio
async def test_billing_empty_system_is_honest_zero(fresh_db):
    async with AsyncSessionLocal() as s:
        result = await billing_overview(user=None, db=s)
    assert result["total_clinics"] == 0
    assert result["active_clinics"] == 0
    assert result["mrr"] == 0
    assert result["total_collected"] == 0
    assert result["credits"]["outstanding_balance"] == 0
    assert result["recent_transactions"] == []
