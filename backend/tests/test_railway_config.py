from app.config import Settings


def test_standard_railway_postgres_url_uses_async_driver():
    cfg = Settings(database_url="postgresql://user:password@postgres.railway.internal:5432/railway")
    assert cfg.database_url == "postgresql+asyncpg://user:password@postgres.railway.internal:5432/railway"


def test_existing_async_url_is_preserved():
    value = "postgresql+asyncpg://user:password@localhost:5432/jarvis"
    assert Settings(database_url=value).database_url == value


def test_test_database_url_is_preserved():
    assert Settings(database_url="sqlite+aiosqlite:///:memory:").database_url == "sqlite+aiosqlite:///:memory:"
