from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.bot.texts import ACCESS_DENIED
from app.services.authorization import AccessDenied, require_role


class RoleMiddleware(BaseMiddleware):
    """Attach to each protected router; query the current binding on every update."""

    def __init__(self, *roles):
        self.roles = roles

    async def __call__(self, handler, event, data):
        try:
            if not event.from_user or event.from_user.is_bot:
                raise AccessDenied("User identity required")
            async with data["sessions"]() as session:
                user = await require_role(session, event.from_user.id, *self.roles)
            data["authorized_user"] = user
        except AccessDenied:
            if isinstance(event, CallbackQuery):
                await event.answer(ACCESS_DENIED, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(ACCESS_DENIED)
            return
        return await handler(event, data)
