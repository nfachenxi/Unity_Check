import pytest
from sqlalchemy import create_engine, text

from unity_check.db import Base
from unity_check.models import GithubEvent, RepoScanConfig, RuleResult  # noqa: F401 — register ORM table


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

    def fake_evaluate(event_type, action, summary, diff_content=""):
        return {"risk_level": "low", "summary": "mocked summary"}

    monkeypatch.setattr("unity_check.llm.evaluate_with_llm", fake_evaluate)
    monkeypatch.setattr("unity_check.tasks.evaluate_with_llm", fake_evaluate)


@pytest.fixture(autouse=True)
def _mock_roslyn(monkeypatch):
    """Prevent any real Roslyn HTTP calls in the test suite.

    Also patches task-local imports so process_github_event and
    run_baseline_scan_task never make real HTTP requests.
    """

    def fake_run_roslyn_analysis(files):
        return []

    def fake_extract_cs_files(diff_content):
        return []

    def fake_filter_targets(file_paths, analyze_paths):
        return []

    def fake_ensure_baseline(event, db):
        return None

    def fake_run_roslyn_incremental(event, db):
        return 0

    monkeypatch.setattr("unity_check.tasks.run_roslyn_analysis", fake_run_roslyn_analysis)
    monkeypatch.setattr("unity_check.tasks.extract_cs_files_from_diff", fake_extract_cs_files)
    monkeypatch.setattr("unity_check.tasks.filter_analyze_targets", fake_filter_targets)
    monkeypatch.setattr("unity_check.tasks._ensure_baseline_scan", fake_ensure_baseline)
    monkeypatch.setattr("unity_check.tasks._run_roslyn_incremental", fake_run_roslyn_incremental)
