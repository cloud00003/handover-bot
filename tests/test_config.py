import pytest

from app.config import ConfigurationError, load_settings


def test_dotenv_loading_and_secret_repr(tmp_path, token):
    path = tmp_path / ".env"
    path.write_text(f"BOT_TOKEN={token}\n", encoding="utf-8")
    settings = load_settings(path)
    assert settings.bot_token == token
    assert token not in repr(settings)


def test_environment_overrides_file(monkeypatch, tmp_path, token):
    path = tmp_path / ".env"
    path.write_text("BOT_TOKEN=invalid", encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", token)
    assert load_settings(path).bot_token == token


@pytest.mark.parametrize("value", [None, "", "   ", "secret-invalid-token"])
def test_invalid_configuration_is_safe(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("BOT_TOKEN", value)
    with pytest.raises(ConfigurationError, match="BOT_TOKEN") as caught:
        load_settings()
    if value and value.strip():
        assert value not in str(caught.value)


def test_example_requires_replacement():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    with pytest.raises(ConfigurationError):
        load_settings(example)
