"""Common error handling; business handlers belong to later backlog tasks."""

import logging

from aiogram import Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent

from app.bot.texts import STALE_ACTION, UNEXPECTED_ERROR

logger = logging.getLogger(__name__)


async def handle_error(event: ErrorEvent) -> bool:
    # Do not log update bodies, exception messages, or user-submitted secrets.
    logger.error("Update %s failed (%s)", event.update.update_id,
                 type(event.exception).__name__)
    try:
        if event.update.callback_query:
            await event.update.callback_query.answer(UNEXPECTED_ERROR, show_alert=True)
        elif event.update.message:
            await event.update.message.answer(UNEXPECTED_ERROR)
    except TelegramAPIError as exc:
        logger.warning("Could not deliver error response (%s)", type(exc).__name__)
    return True


async def handle_stale_callback(query: CallbackQuery) -> None:
    await query.answer(STALE_ACTION, show_alert=True)


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.errors.register(handle_error)
    # Register future business routers before this fallback.
    dispatcher.callback_query.register(handle_stale_callback)
    return dispatcher
