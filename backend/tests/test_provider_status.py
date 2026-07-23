# -*- coding: utf-8 -*-
"""
Verifies audit P3 — System Health and AI Platform now resolve provider keys from
ONE source of truth (backend/services/provider_status.py), DB-first then env.

  - An active DB ApiKeyConfig row wins (this is what the AI Platform UI writes; the
    old System Health probe ignored it, causing the Gemini disagreement).
  - Env/settings is the fallback when no active DB row exists.
  - An inactive DB row is ignored, and with no env key the provider reads as unset.

Run:
    python -m pytest backend/tests/test_provider_status.py -v
"""
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_provider_status.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-fernet-000000000000")

import pytest
import pytest_asyncio

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.models.api_key_config import ApiKeyConfig
from backend.services.provider_status import resolve_provider_key, is_provider_configured


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
async def test_active_db_row_wins(fresh_db):
    async with AsyncSessionLocal() as s:
        cfg = ApiKeyConfig(provider="gemini", category="llm", display_name="Google Gemini", is_active=True)
        cfg.set_key("db-gemini-key-123")
        s.add(cfg)
        await s.commit()
    async with AsyncSessionLocal() as s:
        # DB-configured key wins regardless of whatever env holds.
        assert await resolve_provider_key(s, "gemini") == "db-gemini-key-123"
        assert await is_provider_configured(s, "gemini") is True


@pytest.mark.asyncio
async def test_env_fallback_when_no_db_row(fresh_db, monkeypatch):
    # Use a provider name nothing else sets, and provide it only via env.
    monkeypatch.setenv("FAKETEST_API_KEY", "env-fake-key")
    async with AsyncSessionLocal() as s:
        assert await resolve_provider_key(s, "faketest") == "env-fake-key"
        assert await is_provider_configured(s, "faketest") is True


@pytest.mark.asyncio
async def test_inactive_db_row_ignored_and_no_env_is_unconfigured(fresh_db, monkeypatch):
    monkeypatch.delenv("FAKETWO_API_KEY", raising=False)
    async with AsyncSessionLocal() as s:
        cfg = ApiKeyConfig(provider="faketwo", category="llm", display_name="Fake Two", is_active=False)
        cfg.set_key("inactive-key")
        s.add(cfg)
        await s.commit()
    async with AsyncSessionLocal() as s:
        assert await resolve_provider_key(s, "faketwo") is None
        assert await is_provider_configured(s, "faketwo") is False
