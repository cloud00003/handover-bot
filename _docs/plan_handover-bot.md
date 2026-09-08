# Telegram Handover Bot — Product and Implementation Plan

## 1. Purpose

Build a production-oriented Telegram bot that records and manages the handover of a physical item between a courier and a customer at an agreed place and time.

The bot must provide a simple, auditable workflow that records:

- order creation;
- courier arrival;
- submitted location or 2GIS link;
- product photo before handover;
- courier confirmation that the item is ready;
- issuance and validation of a one-time pickup code;
- customer confirmation of receipt;
- customer no-show;
- cancellation and disputed receipt;
- a timestamped event history for each order.

The bot is intended for real operational use, not as a demo-only project.

---

## 2. Language and UX Requirements

All user-facing Telegram content must be in Russian.

This includes:

- menus;
- buttons;
- prompts;
- validation messages;
- error messages;
- status labels;
- notifications;
- confirmation dialogs;
- empty states.

Internal code, database fields, Python identifiers, comments, and test names may be in English.

The UX must be simple and button-driven. Users should not need to know technical commands or Telegram IDs.

---

## 3. User Roles

The system has three roles:

1. Administrator
2. Courier
3. Customer

A Telegram account must have at most one active role at a time.

Role checks must be enforced on every protected action, not only when the user opens the bot.

---

## 4. First Administrator Registration

The application starts in an uninitialized state with no administrator.

When the first user sends `/start`:

- if no administrator exists, show only one button:
  - `Подтвердить роль администратора`
- after the user confirms:
  - save the user's Telegram user ID;
  - assign the Administrator role;
  - record username and display name when available;
  - mark the system as initialized.

After an administrator exists:

- no other user may self-register as administrator;
- the administrator confirmation button must no longer be shown;
- unknown users should not receive administrator access.

The application must not rely on a secret administrator command as its security mechanism.

---

## 5. Administrator Main Menu

After `/start`, the Administrator should see a simple main menu:

- `Заказы`
- `Участники`
- `История`
- `Настройки`

Avoid exposing technical concepts such as Telegram user IDs, database IDs, tokens, or configuration keys in the normal interface.

---

## 6. Participant Registration via Invite Links

The Administrator adds Couriers and Customers through one-time invitation links.

### 6.1 Add Courier

Flow:

1. Administrator opens:
   - `Участники` → `Добавить курьера`
2. Bot asks:
   - `Как назвать курьера?`
3. Administrator enters a human-friendly name, for example:
   - `Алексей`
4. Bot creates a single-use Telegram deep link containing a secure random invitation token.
5. Bot shows the generated link and asks the Administrator to send it to the Courier.
6. The Courier opens the link and starts the bot.
7. The bot:
   - validates the token;
   - verifies it is unused and not revoked;
   - binds the Courier's Telegram user ID to the Courier role;
   - stores the human-friendly name chosen by the Administrator;
   - stores username/display name when available;
   - marks the invitation as used.
8. The same invitation cannot be reused.

### 6.2 Add Customer

Use the same flow, but assign the Customer role.

### 6.3 Participant Display Names

The system must display the names assigned by the Administrator, not only generic role labels.

Examples:

- `Алексей — Курьер`
- `Компания Orion — Заказчик`

---

## 7. Remove Participant Binding

In `Участники`, the Administrator must be able to open a registered participant and choose:

- `Удалить привязку`

After confirmation:

- remove the active Telegram role binding;
- keep historical order/event records intact;
- invalidate any active invitation related to that participant if applicable;
- allow a new invitation to be created later.

This feature is required for:

- replacing a real participant;
- correcting an incorrect registration;
- testing multiple roles with the same Telegram account.

---

## 8. Default Participants and Pickup Address

The operational scenario assumes that the same Courier, Customer, and pickup address are used most of the time.

The system must support:

- default Courier;
- default Customer;
- default pickup address.

When creating a new order, these fields must be prefilled automatically.

The Administrator can change them for an individual order.

Defaults must be configurable from the Administrator settings.

---

## 9. Order Creation

The Administrator opens:

- `Заказы` → `Создать заказ`

The creation flow should be step-by-step and intuitive.

### 9.1 Order Name

Required.

Prompt:

- `Введите название или номер заказа.`

Helper text:

- `Например: Заказ №154 или Ноутбук для Айданы.`

### 9.2 Date

Required.

Prompt:

- `Укажите дату передачи товара.`

Use a clear accepted format and validate it.

### 9.3 Time

Required.

Prompt:

- `Укажите согласованное время встречи.`

Validate time format.

### 9.4 Pickup Address

Required.

The field is prefilled with the configured default address.

Helper text:

- `По умолчанию используется постоянная точка выдачи. При необходимости измените адрес.`

The Administrator must be able to keep the default or replace it for this order.

### 9.5 Courier

Prefilled with the default Courier.

Helper text:

- `Выберите курьера, который будет передавать товар.`

Allow the Administrator to select another registered Courier.

### 9.6 Customer

Prefilled with the default Customer.

Helper text:

- `Выберите заказчика, который должен получить товар.`

Allow the Administrator to select another registered Customer.

### 9.7 Product Description

Optional.

Prompt:

- `Кратко укажите, что именно передаётся.`

Provide a button:

- `Пропустить`

### 9.8 Final Confirmation

Before creating the order, show a summary containing:

- order name;
- date;
- time;
- pickup address;
- Courier;
- Customer;
- product description.

Buttons:

- `Создать заказ`
- `Изменить`
- `Отмена`

After confirmation, create the order with status:

- `Запланировано`

---

## 10. Order Timing Rules

Each order has:

- scheduled date;
- scheduled time;
- waiting window after the scheduled time.

Default waiting window:

- 15 minutes.

The waiting window should be configurable.

The one-time pickup code has a separate lifetime:

- 20 minutes from generation.

Use server time for all critical timestamps.

Do not rely on the user's device clock.

---

## 11. Courier Workflow

### 11.1 Current Order

After `/start`, the Courier should see the active/current order, including:

- order name;
- date;
- time;
- pickup address;
- Customer name;
- current status.

### 11.2 Arrival Confirmation

When the handover window is relevant, show:

- `Я на месте`

After pressing it:

- record server timestamp;
- add an event to the order history;
- move the order to the appropriate arrival state;
- ask for location information.

The same arrival action must not be recorded multiple times.

### 11.3 Location Submission

Prompt:

- `Отправьте текущую геолокацию или ссылку на точку в 2GIS.`

Accepted inputs for the first version:

- Telegram location;
- text message containing a 2GIS link.

Store:

- submitted coordinates if Telegram location is used;
- raw submitted URL if a 2GIS link is used;
- server timestamp.

Do not automatically calculate or enforce distance from the pickup point in the first version.

### 11.4 Product Photo

After location submission, prompt:

- `Пришлите фотографию товара, который готов к передаче.`

Accept a Telegram photo and store:

- Telegram file ID;
- server timestamp;
- order reference.

After a valid photo is received, show:

- `Товар готов к выдаче`

---

## 12. Ready for Pickup

After the Courier presses:

- `Товар готов к выдаче`

the bot must:

- record server timestamp;
- move the order to `Товар готов к выдаче`;
- create a one-time pickup code;
- notify the Customer;
- add an event to history.

Customer notification should include:

- order name;
- pickup address;
- readiness time;
- product photo;
- one-time pickup code.

---

## 13. One-Time Pickup Code

Requirements:

- belongs to exactly one order;
- random and not easily guessable;
- single-use;
- valid for 20 minutes;
- invalid after successful use;
- invalid after expiration;
- generating a replacement code must invalidate the previous active code.

Do not store the code in plaintext if a simple secure hash-based verification approach is practical.

The Customer sees:

- `Ваш код получения: XXXXX`

The Courier sees:

- `Подтвердить передачу`

---

## 14. Courier Confirms Handover Code

When the Courier presses:

- `Подтвердить передачу`

the bot asks:

- `Введите код заказчика.`

Validation must check:

- code belongs to the active order;
- code is not expired;
- code has not already been used;
- order is currently in a state where code confirmation is allowed.

On success:

- record server timestamp;
- mark the code as verified/used according to the chosen state model;
- update order status to `Ожидается подтверждение заказчика`;
- notify the Customer.

Invalid attempts should produce a clear Russian message without exposing sensitive internal details.

---

## 15. Customer Confirmation

After successful code validation, Customer receives:

- `Подтверждаете получение товара?`

Buttons:

- `Да, товар получил`
- `Нет, товар не получил`

### 15.1 Customer Confirms Receipt

On `Да, товар получил`:

- record server timestamp;
- mark the order as `Завершено`;
- record the final event;
- prevent any further handover actions on the order.

### 15.2 Customer Denies Receipt

On `Нет, товар не получил`:

- do not complete the order;
- set status to `Спорная ситуация`;
- notify the Administrator;
- record the event.

No automatic resolution workflow is required in the first version.

---

## 16. Customer No-Show

If the order is not successfully completed within the configured waiting window after the scheduled time, the system must support a no-show outcome.

Courier should be able to see appropriate actions such as:

- `Продлить ожидание`
- `Завершить ожидание`

When waiting is ended without successful handover:

- set order status to `Заказчик не явился` or `Выдача не состоялась`;
- record server timestamp;
- record an event in history;
- notify the Administrator.

The bot does not need to ask the Customer whether they met the Courier.

---

## 17. Cancellation

Administrator can cancel an active order.

Flow:

1. Open order.
2. Press:
   - `Отменить заказ`
3. Bot asks for confirmation.
4. On confirmation:
   - status becomes `Отменено`;
   - all active handover actions are blocked;
   - Courier and Customer are notified;
   - cancellation is added to history.

---

## 18. Order Status Model

Use an explicit state machine. Suggested statuses:

- `SCHEDULED` → user label: `Запланировано`
- `COURIER_ARRIVED` → `Курьер на месте`
- `LOCATION_SUBMITTED` → `Местоположение отправлено`
- `PHOTO_SUBMITTED` → `Фото товара получено`
- `READY_FOR_PICKUP` → `Товар готов к выдаче`
- `AWAITING_CODE_CONFIRMATION` → `Ожидается подтверждение передачи`
- `AWAITING_CUSTOMER_CONFIRMATION` → `Ожидается подтверждение заказчика`
- `COMPLETED` → `Завершено`
- `CUSTOMER_NO_SHOW` → `Заказчик не явился`
- `CANCELLED` → `Отменено`
- `DISPUTED` → `Спорная ситуация`

Only valid transitions may be allowed.

Do not allow handlers to change order state arbitrarily.

---

## 19. Event History / Audit Trail

Every significant order action must be recorded as an immutable event.

Store at least:

- event ID;
- order ID;
- event type;
- server timestamp;
- actor Telegram user ID when applicable;
- actor role;
- structured metadata when relevant.

Examples of metadata:

- submitted location coordinates;
- 2GIS URL;
- Telegram photo file ID;
- previous status;
- new status;
- code expiration time.

Example human-readable timeline:

- `17:57 — Курьер нажал «Я на месте»`
- `17:58 — Отправлена геолокация`
- `17:59 — Загружено фото товара`
- `18:00 — Товар готов к выдаче`
- `18:05 — Код подтверждён`
- `18:06 — Заказчик подтвердил получение`
- `18:06 — Заказ завершён`

---

## 20. Administrator Order Views

Administrator must be able to see:

### Active Orders

For each active order:

- name;
- scheduled date/time;
- Courier;
- Customer;
- current status.

### Order Details

Show:

- all order fields;
- current status;
- Courier and Customer;
- submitted location/2GIS link;
- product photo;
- arrival time;
- readiness time;
- code expiry information without exposing sensitive code details unnecessarily;
- handover confirmation time;
- receipt confirmation time;
- event history.

### History

Show completed, cancelled, no-show, and disputed orders.

Allow opening an individual order to inspect its timeline.

---

## 21. User and Role Data

For each user, store:

- internal ID;
- Telegram user ID;
- Telegram username, if available;
- Telegram display name, if available;
- Administrator-assigned display name;
- role;
- active/inactive flag;
- registration timestamp.

Telegram user ID is the primary identity for authorization.

Username must not be used as the sole authorization identity because it can change.

---

## 22. Invitation Data

Store:

- invitation ID;
- secure token or token hash;
- intended role;
- Administrator-assigned participant name;
- created timestamp;
- used timestamp;
- revoked flag;
- invited-by Administrator ID.

An invitation must be single-use.

---

## 23. Order Data

Store at minimum:

- internal order ID;
- order name;
- optional product description;
- scheduled date;
- scheduled time;
- pickup address;
- Courier ID;
- Customer ID;
- current status;
- created timestamp;
- Courier arrival timestamp;
- location type;
- coordinates or location URL;
- location submission timestamp;
- Telegram product photo file ID;
- photo submission timestamp;
- ready-for-pickup timestamp;
- code hash or secure code representation;
- code expiration timestamp;
- code verification timestamp;
- Customer confirmation timestamp;
- completion timestamp;
- cancellation/no-show/dispute timestamps when applicable.

---

## 24. Default Settings

Store configurable defaults:

- default pickup address;
- default Courier;
- default Customer;
- no-show waiting window in minutes (default 15);
- pickup code lifetime in minutes (fixed/default 20).

Administrator should be able to edit operational defaults through `Настройки` where practical.

Do not expose secrets such as bot token through the Telegram interface.

---

## 25. Security Requirements

- Use Telegram user ID for authentication/authorization.
- Enforce role authorization on every protected handler.
- Do not trust callback data alone for authorization.
- Invite tokens must be cryptographically random enough for this use case.
- Invite links are single-use.
- Pickup codes are single-use.
- Expired pickup codes must be rejected.
- Repeated button presses must be idempotent or safely rejected.
- Do not expose bot token or secrets in logs, messages, README, commits, or GitHub.
- Store secrets in `.env`.
- Add `.env` to `.gitignore`.
- Validate all user input.
- Handle unknown or stale callback queries safely.
- Do not show stack traces or database errors to Telegram users.

---

## 26. Error Handling

All user-facing errors must be clear and in Russian.

Examples:

- `Код неверный. Проверьте код и попробуйте снова.`
- `Срок действия кода истёк.`
- `Это действие уже было выполнено.`
- `Заказ уже завершён.`
- `У вас нет доступа к этому действию.`
- `Ссылка приглашения недействительна или уже использована.`

Technical details should be logged for developers but not shown to users.

---

## 27. Testing Requirements

Create automated tests for at least:

### Roles and Registration

- first user can become Administrator when system is clean;
- second user cannot self-register as Administrator;
- valid invite registers Courier;
- valid invite registers Customer;
- used invite cannot be reused;
- removed participant can be re-invited.

### Authorization

- Customer cannot access Administrator actions;
- Courier cannot access Administrator actions;
- Customer cannot perform Courier actions;
- unknown user has no protected access.

### Orders

- Administrator can create a valid order;
- defaults are applied correctly;
- required fields are validated;
- cancellation works.

### Courier Flow

- arrival is recorded once;
- location can be stored;
- photo can be stored;
- ready-for-pickup can only happen after required prior steps.

### Pickup Codes

- valid code works;
- wrong code fails;
- expired code fails;
- used code cannot be reused;
- regenerated code invalidates old code;
- lifetime is 20 minutes.

### Customer Confirmation

- `Да, товар получил` completes the order;
- `Нет, товар не получил` creates disputed state;
- repeated confirmation does not duplicate completion.

### No-Show

- no-show outcome is recorded correctly;
- completed order cannot later become no-show.

### State Machine

- invalid state transitions are rejected.

### Reset

- developer reset returns the application to a clean first-run state.

---

## 28. Developer Reset for Testing

Do not add a destructive `Сбросить всё` button to the Telegram user interface.

Provide a separate developer-only command or script, for example:

```bash
python reset_bot.py
```

Requirements:

- run only from the local/server environment;
- require explicit confirmation such as typing `RESET`;
- delete all test/application data necessary to restore first-run state:
  - Administrator;
  - participants;
  - Telegram role bindings;
  - invitations;
  - orders;
  - pickup codes;
  - event history;
  - defaults if appropriate for a full reset;
- after reset, the next `/start` user can register as the first Administrator again.

The reset script must not be triggered from Telegram.

---

## 29. BotFather / Telegram Setup

The project assumes the bot is created through Telegram BotFather.

Required external setup:

- create bot;
- receive bot token;
- configure bot name;
- configure bot username;
- configure bot description;
- optionally configure basic command descriptions.

Store the bot token only in `.env`.

Example variable:

```env
BOT_TOKEN=...
```

Do not commit `.env`.

---

## 30. Recommended Technical Stack

Preferred first-version stack:

- Python 3.11+;
- aiogram 3.x;
- Telegram Bot API;
- SQLite for initial deployment;
- SQLAlchemy 2.x with async support where practical;
- Alembic for migrations if SQLAlchemy is used directly;
- python-dotenv or pydantic-settings for configuration;
- pytest;
- pytest-asyncio where required;
- Git/GitHub.

If deployment requirements justify it later, SQLite can be replaced by PostgreSQL.

Keep architecture simple enough for one bot and a small number of users.

---

## 31. Suggested Project Structure

A clean structure is preferred, for example:

```text
app/
  bot/
    handlers/
      admin/
      courier/
      customer/
      common/
    keyboards/
    middlewares/
    states/
  db/
    models/
    repositories/
    migrations/
  services/
    orders.py
    invitations.py
    pickup_codes.py
    event_log.py
  config.py
  main.py

tests/
  test_registration.py
  test_authorization.py
  test_orders.py
  test_courier_flow.py
  test_pickup_codes.py
  test_customer_flow.py
  test_no_show.py
  test_reset.py

scripts/
  reset_bot.py

_docs/
  plan.md

.env.example
.gitignore
README.md
requirements.txt or pyproject.toml
```

This structure is a recommendation, not a rigid requirement. Prefer clarity over unnecessary abstraction.

---

## 32. Implementation Principles

- Use spec-driven development.
- Break work into small backlog tasks.
- Implement and verify tasks incrementally.
- Run tests after each meaningful change.
- Keep business logic outside Telegram handlers where practical.
- Keep handlers thin: parse input, call service logic, render response.
- Centralize status transition rules.
- Centralize user-facing Russian text where practical to keep wording consistent.
- Avoid premature microservices, Docker orchestration, message queues, or complex infrastructure.
- Do not add features that are outside this specification.

---

## 33. Explicitly Out of Scope for Version 1

Do NOT implement the following unless explicitly requested later:

- automatic GPS distance verification;
- mandatory 2–3 meter location accuracy;
- 2GIS API integration;
- Google Maps API integration;
- automatic image recognition or photo content validation;
- facial recognition;
- electronic signature;
- payment processing;
- payment gateway integration;
- separate web dashboard;
- mobile application;
- multiple Couriers simultaneously assigned to one order;
- route optimization;
- live Courier tracking;
- background geofencing;
- SMS;
- email notifications;
- advanced analytics;
- ranking or scoring of users;
- chat between Courier and Customer;
- file/document upload beyond the required product photo;
- cloud object storage unless Telegram file IDs prove insufficient for the deployment;
- complex role hierarchy;
- user self-selection of roles;
- public registration;
- destructive reset through Telegram;
- automatic resolution of disputes;
- microservices;
- Redis, Celery, Kafka, or similar infrastructure unless a real need appears;
- PostgreSQL migration unless deployment requires it.

---

## 34. Definition of Done

Version 1 is complete when all of the following are true:

- clean system allows first Administrator registration;
- Administrator menu works in Russian;
- Administrator can create Courier and Customer invite links;
- invite links bind real Telegram accounts to roles;
- role authorization works;
- Administrator can remove participant bindings;
- defaults for address, Courier, and Customer work;
- Administrator can create an order;
- Courier can confirm arrival;
- Courier can submit Telegram location or 2GIS link;
- Courier can submit a product photo;
- Courier can mark item ready for pickup;
- Customer receives a one-time pickup code;
- code expires after 20 minutes;
- expired and reused codes are rejected;
- Courier can validate the code;
- Customer can confirm or deny receipt;
- successful flow ends in `Завершено`;
- denial ends in `Спорная ситуация`;
- no-show flow works;
- Administrator can cancel an order;
- event history is recorded;
- Administrator can inspect active and historical orders;
- repeated actions are handled safely;
- developer reset works;
- all critical workflows have automated tests;
- README explains setup, BotFather configuration, environment variables, local run, tests, and reset;
- bot can be run and tested end-to-end in Telegram;
- no out-of-scope features were added without approval.

---

## 35. Development Sequence

Recommended implementation order:

1. Project scaffold and configuration.
2. Database models and migrations.
3. First Administrator registration.
4. Role authorization.
5. Invitation system for Courier and Customer.
6. Participant list and remove-binding flow.
7. Default settings.
8. Order creation.
9. Order list and order details for Administrator.
10. Courier arrival flow.
11. Location submission.
12. Product photo submission.
13. Ready-for-pickup transition.
14. Pickup code generation and expiry.
15. Courier code validation.
16. Customer receipt confirmation.
17. Dispute state.
18. No-show flow.
19. Cancellation.
20. Event history.
21. Developer reset.
22. Full automated test suite.
23. README and operational documentation.
24. End-to-end Telegram testing.
25. Final cleanup and production deployment preparation.

---

## 36. Instructions to the Coding Agent

When implementing this project:

- Read this plan fully before making architectural decisions.
- Do not implement the entire project in one step.
- First inspect the repository and existing code.
- Propose a small, ordered backlog with clear dependencies and acceptance criteria.
- Save the backlog to `backlog.md`.
- Do not implement backlog items until explicitly asked.
- Work on one requested task at a time.
- Do not add out-of-scope functionality.
- Preserve the Russian user interface requirement.
- Add or update tests for each implemented behavior.
- Run relevant tests and system checks after each task.
- Update documentation when setup or commands change.
- Never commit `.env`, bot token, secrets, or personal credentials.
- Ask for clarification when the specification is genuinely ambiguous instead of inventing business rules.
