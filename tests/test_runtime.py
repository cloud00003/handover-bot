import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import Update

from app import main
from app.bot.handlers import create_dispatcher
from app.bot.texts import STALE_ACTION, UNEXPECTED_ERROR
from app.config import Settings
from app.logging_config import RedactingFormatter


@pytest.mark.parametrize("failure", [None, RuntimeError("startup failure"), asyncio.CancelledError()])
async def test_polling_closes_session(monkeypatch, token, failure):
    bot = Bot(token)
    bot.session.close = AsyncMock()
    dispatcher = create_dispatcher()
    dispatcher.start_polling = AsyncMock(side_effect=failure)
    monkeypatch.setattr(main, "Bot", lambda **kwargs: bot)
    monkeypatch.setattr(main, "create_dispatcher", lambda: dispatcher)
    if failure is None:
        await main.run(Settings(token))
    else:
        with pytest.raises(type(failure)):
            await main.run(Settings(token))
    bot.session.close.assert_awaited_once()
    dispatcher.start_polling.assert_awaited_once_with(
        bot, close_bot_session=False, allowed_updates=["message", "callback_query"]
    )


def test_missing_configuration_exits_without_starting(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(main, "run", run)
    monkeypatch.setattr(main, "configure_logging", lambda *args: None)
    assert main.main() == 1
    run.assert_not_called()


def test_log_redaction_includes_tracebacks(token):
    try:
        raise RuntimeError(f"https://api.telegram.org/bot{token}/getMe")
    except RuntimeError:
        import sys

        record = logging.LogRecord("test", logging.ERROR, __file__, 1,
                                   "Failed for %s", (token,), sys.exc_info())
    output = RedactingFormatter((token,)).format(record)
    assert token not in output
    assert "[REDACTED]" in output
    assert "RuntimeError" in output


@pytest.mark.parametrize("kind", ["message", "callback_query"])
async def test_dispatcher_error_responses_are_russian_and_safe(token, kind, caplog):
    dispatcher = create_dispatcher()
    bot = Bot(token)
    bot.session.make_request = AsyncMock(return_value=True)
    user = {"id": 1, "is_bot": False, "first_name": "Тест"}
    payload = ({"message_id": 1, "date": datetime.now(timezone.utc),
                "chat": {"id": 1, "type": "private"}, "from": user, "text": "/test"}
               if kind == "message" else
               {"id": "query", "from": user, "chat_instance": "chat", "data": "old"})
    update = Update.model_validate({"update_id": 1, kind: payload})

    async def fail(event):
        raise RuntimeError("private error " + token)

    # Replace the callback fallback for the error-path test.
    getattr(dispatcher, kind).handlers.clear()
    getattr(dispatcher, kind).register(fail)
    async with bot.context():
        await dispatcher.feed_update(bot, update)
    request = bot.session.make_request.call_args.args[1]
    assert isinstance(request, SendMessage if kind == "message" else AnswerCallbackQuery)
    assert request.text == UNEXPECTED_ERROR
    assert token not in caplog.text
    assert "private error" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_unknown_callback_is_safely_answered(token):
    bot = Bot(token)
    bot.session.make_request = AsyncMock(return_value=True)
    update = Update.model_validate({"update_id": 2, "callback_query": {
        "id": "stale", "from": {"id": 1, "is_bot": False, "first_name": "Тест"},
        "chat_instance": "chat", "data": "unknown"}})
    async with bot.context():
        await create_dispatcher().feed_update(bot, update)
    request = bot.session.make_request.call_args.args[1]
    assert isinstance(request, AnswerCallbackQuery)
    assert request.text == STALE_ACTION
