import os
import tempfile

os.environ["SEEKO_NO_PERSIST"] = "1"   # tests must not write the user's real settings
# Never the user's database: tests default to a throwaway SQLite file (even if DATABASE_URL is set in the shell).
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="seeko-test-"), "test.db")

import uuid  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@pytest.fixture(scope="session")
def pg_server_url():
    """A real PostgreSQL: TEST_DATABASE_URL (CI service container) or the embedded pgserver. Skips if neither."""
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        try:
            import pgserver
        except ImportError:
            pytest.skip("no PostgreSQL: set TEST_DATABASE_URL or install pgserver")
        try:
            url = pgserver.get_server(tempfile.mkdtemp(prefix="seeko-pg-"), cleanup_mode="delete").get_uri()
        except Exception as e:  # pragma: no cover - platform without pgserver binaries
            pytest.skip(f"embedded PostgreSQL unavailable: {e}")
    import db
    return db.normalize_url(url)


@pytest.fixture
def pg_engine(pg_server_url):
    """Fresh schema per test (tests only ever touch their own schema, never `public`)."""
    schema = "t_" + uuid.uuid4().hex[:10]
    admin = create_engine(pg_server_url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    eng = create_engine(pg_server_url, connect_args={"options": f"-csearch_path={schema}"})
    eng.info_schema = schema
    try:
        yield eng
    finally:
        eng.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def sqlite_engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    yield eng
    eng.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def migrated(request):
    """(engine, sessionmaker) on both backends, schema created through the Alembic migrations."""
    import db
    eng = request.getfixturevalue("sqlite_engine" if request.param == "sqlite" else "pg_engine")
    db.migrate(eng)
    return eng, sessionmaker(eng, expire_on_commit=False)
