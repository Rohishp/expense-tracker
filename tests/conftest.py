import os

# Tests never send traces, even if LANGSMITH_TRACING is set in the shell.
# Set before langsmith is imported, because it caches the setting.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import pytest

import database.db as db


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Redirect every get_db() call at a fresh, seeded temp database.

    get_db() reads db.DB_PATH dynamically on every call, so patching this
    attribute redirects app.py's routes and database/queries.py's helpers
    alike, without touching the real expense_tracker.db.
    """
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    db.seed_db()
    return path


@pytest.fixture
def app(db_path, monkeypatch):
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["CSRF_ENABLED"] = False
    flask_app.config["SUGGEST_COOLDOWN_SECONDS"] = 0
    # Advisor runs happen in the request, and the real model is never
    # configured: a test that wants answers replaces app._advisor_caller.
    flask_app.config["ADVISOR_INLINE"] = True
    monkeypatch.delenv("ADVISOR_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield flask_app
