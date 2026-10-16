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
def app(db_path):
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["CSRF_ENABLED"] = False
    flask_app.config["SUGGEST_COOLDOWN_SECONDS"] = 0
    yield flask_app
