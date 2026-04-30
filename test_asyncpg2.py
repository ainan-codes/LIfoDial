import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("postgresql+asyncpg://postgres:postgres@localhost/db", prepared_statement_name_cache_size=0)
print(getattr(engine.dialect, "prepared_statement_name_cache_size", None))
