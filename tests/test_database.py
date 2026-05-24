"""
tests/test_database.py — integration tests for Lifodial database connection and model loading.
"""
import pytest
from sqlalchemy import text
from backend.db import AsyncSessionLocal, get_database_url, Base

@pytest.mark.asyncio
async def test_database_url_resolves() -> None:
    url = get_database_url()
    assert url is not None
    assert "sqlite" in url or "postgres" in url

@pytest.mark.asyncio
async def test_database_session_executes_query() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        val = result.scalar()
    assert val == 1

@pytest.mark.asyncio
async def test_all_models_registered_metadata() -> None:
    # Trigger model imports to ensure Base.metadata is fully populated
    from backend.db import _import_all_models
    _import_all_models()
    
    tables = list(Base.metadata.tables.keys())
    assert len(tables) > 0
    # Core tables should be present in the metadata
    assert "tenants" in tables
    assert "agent_configs" in tables
