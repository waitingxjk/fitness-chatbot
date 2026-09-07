import pytest


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    """Automated tests never connect to the development MySQL or use real credentials."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("IMAGE_DIRECTORY", str(tmp_path / "uploads"))
