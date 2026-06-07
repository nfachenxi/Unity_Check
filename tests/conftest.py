import pytest
from sqlalchemy import create_engine, text

from unity_check.db import Base
from unity_check.models import EvaluationRound, GithubEvent  # noqa: F401 — register ORM tables


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
    """Prevent any real LLM API call in the test suite.

    Mocks evaluate_file_dimension to return canned results for both dimensions.
    """

    def fake_evaluate_file_dimension(file_path, file_diff, event_summary, dimension):
        return {
            "score": 85.0,
            "summary": f"Mocked {dimension} for {file_path}",
            "findings": [
                {
                    "title": "Mocked finding",
                    "category": "best_practice" if "func" in dimension else "performance",
                    "severity": "low",
                    "description": "Mocked description",
                    "suggestion": "Mocked suggestion",
                    "line_hint": "line 10",
                }
            ],
            "tokens_used": 100,
            "duration_ms": 500,
            "model_name": "deepseek-chat-mock",
        }

    monkeypatch.setattr(
        "unity_check.orchestrator.evaluate_file_dimension",
        fake_evaluate_file_dimension,
    )
