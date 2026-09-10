"""Run locally with python -m app.main."""

import asyncio
import logging

from aiogram import Bot

from app.bot.handlers import create_dispatcher
from app.config import ConfigurationError, Settings, load_settings
from app.logging_config import configure_logging
from app.db.session import create_engine, session_factory

logger = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    try:
        dispatcher = create_dispatcher(sessions=session_factory(engine))
        # Context ownership closes the session even if polling fails during startup.
        async with Bot(token=settings.bot_token).context() as bot:
            await dispatcher.start_polling(
                bot, close_bot_session=False, allowed_updates=["message", "callback_query"]
            )
    finally:
        await engine.dispose()


def main() -> int:
    configure_logging()
    try:
        settings = load_settings()
    except (ConfigurationError, OSError, UnicodeError) as exc:
        message = str(exc) if isinstance(exc, ConfigurationError) else "Cannot read .env."
        logger.error("Configuration error: %s", message)
        return 1
    configure_logging((settings.bot_token,))
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
    except Exception as exc:
        logger.error("Bot stopped unexpectedly (%s). Check configuration and connectivity.",
                     type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
