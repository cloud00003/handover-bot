import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """Never read the developer's .env or contact Telegram in tests."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def token():
    return "123456789:" + "A" * 35
