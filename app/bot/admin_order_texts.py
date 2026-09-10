"""Russian administrator order views. Never render raw event metadata."""

from app.bot import texts
from app.db.models import Role
from app.time import format_local_datetime

EMPTY_ACTIVE = "Активных заказов пока нет."
EMPTY_HISTORY = "Завершённых передач пока нет."
MISSING = "Заказ не найден."
CANCEL = "Отменить заказ"
CONFIRM = "Да, отменить заказ"
ABORT = "Нет, вернуться к заказу"
QUESTION = "Отменить заказ «{name}»? Курьер и заказчик получат уведомление."
DENIED = "Заказ уже завершён, отменён или передача подтверждена. Отмена недоступна."
CANCELLED = "Заказ отменён."
NOTICE = "Заказ «{name}» отменён администратором. Передача товара по этому заказу отменена."
DELIVERY_FAILED = "Заказ отменён, но не все уведомления удалось доставить. Сообщите участникам об отмене."
TIMELINE = "История событий"
EMPTY_TIMELINE = "Событий пока нет."
PHOTO = "Фото товара"
LOCATION = "Местоположение"
NO_EVIDENCE = "Эти данные пока не добавлены."
NOT_SET = "Не указано"
TIMES = {
    "created_at": "Создан", "arrived_at": "Прибытие курьера",
    "location_submitted_at": "Местоположение отправлено", "photo_submitted_at": "Фото отправлено",
    "ready_at": "Готовность к выдаче", "code_verified_at": "Код подтверждён",
    "customer_confirmed_at": "Получение подтверждено", "completed_at": "Завершён",
    "cancelled_at": "Отменён", "no_show_at": "Неявка заказчика", "disputed_at": "Спорная ситуация",
}
ROLES = {Role.ADMINISTRATOR: "Администратор", Role.COURIER: "Курьер", Role.CUSTOMER: "Заказчик"}


def stamp(value):
    return format_local_datetime(value) if value else NOT_SET


def name(user):
    return user.display_name or user.telegram_display_name or ROLES[user.role]


def brief(order, courier, customer):
    return (f"{order.name}\nДата и время: {stamp(order.scheduled_at)}\n"
            f"Курьер: {name(courier)}\nЗаказчик: {name(customer)}\n"
            f"Статус: {texts.STATUS_LABELS[order.status.value]}")


def details(order, courier, customer, expiry):
    location = (f"{order.latitude}, {order.longitude}" if order.location_type == "TELEGRAM"
                else order.location_url or NOT_SET)
    return (brief(order, courier, customer) + f"\nАдрес: {order.pickup_address}\n"
            f"Описание: {order.product_description or NOT_SET}\nОжидание, минут: {order.waiting_minutes}\n"
            f"Местоположение: {location}\nФото товара: {'Добавлено' if order.photo_file_id else NOT_SET}\n"
            + "\n".join(f"{label}: {stamp(getattr(order, field))}" for field, label in TIMES.items())
            + f"\nПоследний код действителен до: {stamp(expiry)}\n\nДата и время указаны по Бишкеку.")


def event_line(event):
    label = ("Заказ создан" if event.event_type == "ORDER_CREATED" else
             texts.STATUS_LABELS.get(event.event_type, "Событие передачи"))
    actor = ROLES.get(event.actor_role, "Система")
    # Event type, time and role suffice for the timeline; arbitrary metadata may
    # contain secrets or technical IDs and is deliberately not serialized.
    return f"{stamp(event.occurred_at)} — {label} ({actor})"
