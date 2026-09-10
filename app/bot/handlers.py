"""Dispatcher assembly and common error handling."""

import logging

from aiogram import Dispatcher, Router
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent

from app.bot.texts import STALE_ACTION, UNEXPECTED_ERROR
from app.bot.registration import administrator_router, registration_router
from app.bot.participants import invitation_router, participants_router
from app.bot.order_creation import order_creation_router
from app.bot.admin_orders import admin_orders_router
from app.bot.courier import courier_router

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


def create_dispatcher(sessions=None) -> Dispatcher:
    dispatcher = Dispatcher(sessions=sessions, events_isolation=SimpleEventIsolation())
    dispatcher.errors.register(handle_error)
    dispatcher.include_router(invitation_router())
    dispatcher.include_router(registration_router())
    dispatcher.include_router(participants_router())
    dispatcher.include_router(admin_orders_router())
    dispatcher.include_router(order_creation_router())
    dispatcher.include_router(administrator_router())
    dispatcher.include_router(courier_router())
    fallback = Router(name="fallback")
    fallback.callback_query.register(handle_stale_callback)
    dispatcher.include_router(fallback)
    return dispatcher
