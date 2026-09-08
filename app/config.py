"""Explicit configuration loading; importing this module does not read secrets."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token
from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Configuration is missing or invalid; messages never include its values."""


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)


def load_settings(env_file: str | Path = ".env") -> Settings:
    values = dotenv_values(env_file, interpolate=False)
    token = os.environ.get("BOT_TOKEN", values.get("BOT_TOKEN"))
    if not token or not token.strip():
        raise ConfigurationError("BOT_TOKEN is required. Set it in .env.")
    try:
        validate_token(token)
    except TokenValidationError:
        raise ConfigurationError("BOT_TOKEN has an invalid format. Check .env.") from None
    return Settings(bot_token=token)
