"""Explicit configuration loading; importing this module does not read secrets."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token
from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(ValueError):
    """Configuration is missing or invalid; messages never include its values."""


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    database_url: str = "sqlite+aiosqlite:///handover.sqlite3"


def load_database_url(env_file: str | Path = ".env") -> str:
    """Migrations do not require a Telegram token."""
    values = dotenv_values(env_file, interpolate=False)
    value = os.environ.get("DATABASE_URL", values.get("DATABASE_URL", "sqlite+aiosqlite:///handover.sqlite3"))
    try:
        url = make_url(value or "")
        if (url.drivername != "sqlite+aiosqlite" or not url.database or url.query
                or url.username or url.password or url.host or url.port):
            raise ValueError
    except (ArgumentError, ValueError):
        raise ConfigurationError("DATABASE_URL must be a sqlite+aiosqlite database URL without query parameters.") from None
    return value


def load_settings(env_file: str | Path = ".env") -> Settings:
    values = dotenv_values(env_file, interpolate=False)
    token = os.environ.get("BOT_TOKEN", values.get("BOT_TOKEN"))
    if not token or not token.strip():
        raise ConfigurationError("BOT_TOKEN is required. Set it in .env.")
    try:
        validate_token(token)
    except TokenValidationError:
        raise ConfigurationError("BOT_TOKEN has an invalid format. Check .env.") from None
    return Settings(bot_token=token, database_url=load_database_url(env_file))
