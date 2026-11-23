import os
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.database import init_db
from app.api import app


@pytest.fixture
def temp_db():
    """Provides a clean temporary SQLite database for test isolation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)
    init_db(db_path)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def client(temp_db, monkeypatch):
    """Provides a FastAPI test client configured with a temporary database."""
    monkeypatch.setattr("app.config.DATABASE_PATH", temp_db)
    with TestClient(app) as test_client:
        yield test_client
