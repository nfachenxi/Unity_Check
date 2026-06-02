import pytest
from sqlalchemy import create_engine, text

from unity_check.db import Base
from unity_check.models import GithubEvent  # noqa: F401 — register ORM table


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    """Session-scoped file-based SQLite engine for all tests."""
    db_path = tmp_path_factory.mktemp("sqlite") / "test.db"
    _engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=_engine)
    yield _engine


def _clean_all_tables(engine):
    with engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"DELETE FROM {table.name}"))
        conn.commit()


@pytest.fixture()
def session(engine):
    """Function-scoped session with full clean isolation."""
    _clean_all_tables(engine)
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    yield db
    db.rollback()
    db.close()
    _clean_all_tables(engine)


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    """Prevent any real LLM API call in the test suite."""

    def fake_evaluate(event_type, action, summary):
        return {"risk_level": "low", "summary": "mocked summary"}

    monkeypatch.setattr("unity_check.llm.evaluate_with_llm", fake_evaluate)
    monkeypatch.setattr("unity_check.tasks.evaluate_with_llm", fake_evaluate)
