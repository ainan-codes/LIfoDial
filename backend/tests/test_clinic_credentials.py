# -*- coding: utf-8 -*-
"""
Verifies audit P2 fixes:
  - The password generated at clinic creation is PERSISTED (hashed), so a clinic
    can actually log in with the credentials shown on the success screen. (The
    bug: create_tenant never set admin_password, so login always failed.)
  - Login works with the exact admin email entered (case-insensitive).
  - The case-insensitive unique index blocks two clinics sharing an email.

Run:
    python -m pytest backend/tests/test_clinic_credentials.py -v
"""
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_clinic_credentials.db"

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.services.tenant_service import create_tenant
from backend.security import hash_password
from backend.routers.agents import clinic_login, ClinicLoginPayload


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
async def test_wizard_clinic_login_actually_works(fresh_db):
    # Mirror exactly what create_agent now does for a new clinic.
    entered_email = "ZZZ_Audit_Test@Example.com"
    shown_password = "Lf-shownOnce123"
    async with AsyncSessionLocal() as s:
        await create_tenant(
            s, clinic_name="ZZZ Audit Clinic", admin_email=entered_email,
            admin_password=hash_password(shown_password),
        )
        await s.commit()

    # Log in with the entered email (any case) + the password that was shown.
    res = await clinic_login(ClinicLoginPayload(email="zzz_audit_test@example.com",
                                                password=shown_password))
    assert res["role"] == "clinic"
    assert res["email"] == "zzz_audit_test@example.com"
    assert res["access_token"]

    # Wrong password must be rejected.
    with pytest.raises(Exception):
        await clinic_login(ClinicLoginPayload(email="zzz_audit_test@example.com",
                                              password="not-the-password"))


@pytest.mark.asyncio
async def test_admin_email_unique_index_blocks_duplicates(fresh_db):
    # Apply the same index the migration creates (SQLite form).
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_admin_email_lower "
            "ON tenants (admin_email COLLATE NOCASE)"
        )
    async with AsyncSessionLocal() as s:
        await create_tenant(s, clinic_name="Clinic A", admin_email="dup@example.com",
                            admin_password=hash_password("x"))
        await s.commit()

    with pytest.raises(IntegrityError):
        async with AsyncSessionLocal() as s:
            # Same email, different case — must collide.
            await create_tenant(s, clinic_name="Clinic B", admin_email="DUP@Example.com",
                                admin_password=hash_password("y"))
            await s.commit()
