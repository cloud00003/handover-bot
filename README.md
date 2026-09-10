# Handover Bot

Telegram bot for recording and confirming physical item handovers.
Specification: [_docs/plan_handover-bot.md](_docs/plan_handover-bot.md).
Implementation order: [backlog.md](backlog.md).

Task 1 provides configuration, polling, safe logging, common Russian error
responses, and offline tests. Task 2 adds SQLite models, migrations,
transaction helpers, structural state transitions, and immutable audit events
with Asia/Bishkek scheduling and per-order waiting snapshots.
Task 3 adds first-administrator registration, the Russian administrator menu,
and current-role authorization. Task 4 is in progress: courier/customer invite
links and participant browsing are implemented; binding removal awaits the
replacement-policy clarification in the backlog. Task 5 adds operational defaults
and order creation. Task 6 adds administrator active/history views, evidence and
timelines, and confirmed cancellation. Task 7 adds courier order selection,
arrival, location and photo recording. Readiness, pickup-code handover, customer
confirmation, and reset remain later tasks.

## Local setup

Requires Python 3.11+ and network access to install dependencies. From the
repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

On Linux/macOS use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`.
The manifest declares aiogram 3.x, SQLAlchemy 2.x, aiosqlite, Alembic,
python-dotenv, tzdata, pytest, and pytest-asyncio.

## Configuration

Create a bot through Telegram's BotFather and obtain its token. Configure its
name, username, and Russian description there. Copy `.env.example` to `.env`
only if `.env` does not already exist, then replace the placeholder locally:

```dotenv
BOT_TOKEN=replace_with_your_bot_token
```

`BOT_TOKEN` is required to run the bot. Optional `DATABASE_URL` defaults to
`sqlite+aiosqlite:///handover.sqlite3`; only SQLite with the async driver is
supported. The application reads `.env`
from the working directory; an existing environment variable takes precedence.
Missing, blank, or malformed tokens stop startup with exit code 1 and a safe
configuration message. Format validation does not verify Telegram credentials.
Never commit `.env` or share its contents. Tests use synthetic tokens and
temporary directories and do not read the local `.env`.

## Run

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.main
```

The bot uses long polling and requires Telegram connectivity and a valid token.
Use one polling process for the token; an existing webhook must be removed before
polling. Stop with Ctrl+C. The HTTP session and database engine are cleaned up
on shutdown or polling failure.
The lifecycle uses aiogram's [polling API](https://docs.aiogram.dev/en/latest/dispatcher/dispatcher.html).
Logs go to the console with token redaction. User-facing errors are in Russian;
update failures log the update ID and exception type without message bodies.

## First administrator and access

On a migrated database without an administrator, `/start` shows a Russian welcome
and the single button `Подтвердить роль администратора`. Pressing it saves the Telegram user ID,
username and display name when available, active Administrator role, and server
registration timestamp. The first successful confirmation initializes the system;
concurrent or stale confirmations cannot create another administrator.

Initialization is represented by the persisted administrator record. An inactive
administrator still counts as initialized, so deactivation never reopens public
administrator registration. No secret command grants access.

The active administrator sees `Заказы`, `Участники`, `История`, and `Настройки`.
`Участники` opens participant browsing and invitation creation. `Заказы` opens
active orders and order creation, and `Настройки` edits operational defaults.
`История` shows completed, cancelled, no-show, and disputed orders.
Unknown or inactive users and other roles receive `У вас нет доступа к этому действию.`
when attempting an administrator action.

`require_role()` in `app.services.authorization` checks active bindings by
Telegram user ID, never username. Each protected menu action runs
`RoleMiddleware`, which queries the database again; an old menu does not retain
access after deactivation. Future protected message/callback handlers must use
the same guard for their required roles and enforce order assignment where
applicable. The middleware follows aiogram's
[handler middleware interface](https://docs.aiogram.dev/en/latest/dispatcher/middlewares.html).

## Participant invitations (task 4 in progress)

Open `Участники`, select `Добавить курьера` or `Добавить заказчика`, and enter
the participant's name (1–100 characters on one line). `Отмена` cancels the
name-entry form. The bot returns a single-use link for the administrator to
forward personally. Opening it registers the participant with the intended
role and administrator-assigned name; users cannot choose their own role.

Invitation tokens contain 32 random bytes and only their SHA-256 hashes are
stored in SQLite. The [Telegram deep-link payload](https://core.telegram.org/bots/features#deep-linking)
stays within Telegram's 64-character limit. Redemption consumes the invitation
and creates its role binding in one transaction. Invalid, revoked, used links,
and accounts with an existing active role are rejected without creating another
binding. Profile information is saved when available. `/start` subsequently
shows a participant's assigned name and role.

The list displays active couriers/customers by assigned name with pagination
and individual profiles. The unfinished removal flow will preserve historical
rows, but handling of references from active orders/defaults must be clarified
before it is implemented. No removal button is exposed yet.

Pending name-entry forms are held in memory and can be restarted after a bot
restart. Created invitations persist in SQLite. Per-user event isolation prevents
overlapping form updates; protected steps recheck the current administrator role.

## Database foundation

From the repository root, apply migrations before using the persistence services:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

These commands read `DATABASE_URL` from the environment or `.env` and do not
require `BOT_TOKEN` or Telegram connectivity. Relative database paths resolve
from the working directory; the default file and its SQLite sidecars are ignored
by Git. Migrations are explicit: importing the application and starting polling
do not create tables or silently upgrade the schema. Registration and role
checks use the session factory supplied to the dispatcher by the application.

The initial migration creates users/role bindings, invitations, operational
defaults, orders, pickup-code records, and order events. It seeds only the
defaults row (15-minute waiting, fixed 20-minute code lifetime), not an
administrator. Inactive binding rows remain referenced by historical orders;
another active binding can later use the same Telegram ID. Invitation/code
tables contain hashes, not plaintext credentials.

Use `create_engine()` and `session_factory()` from `app.db.session`, with
`async with factory.begin() as session:` for a unit of work. Dispose the engine
when its owner shuts down. Foreign-key enforcement and explicit SQLite
transactions are enabled on every connection. These choices follow the
[SQLAlchemy SQLite guidance](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
and [Alembic async migration recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).

`record_order()` and `transition_order()` write state and audit events in the
same transaction. Transitions use the expected status to reject stale actions.
The public order status is read-only; future handlers must call action services
instead of modifying the private ORM field. The transition map is structural:
future workflows must add active-role/assignment checks, evidence validation,
and timing eligibility before calling it. The intermediate
`AWAITING_CODE_CONFIRMATION` represents requesting the courier's code entry.
No Telegram handover actions or code-generation workflow have been added.

Audit rows cannot be updated or deleted, including via SQL. A future developer
reset must explicitly account for these triggers; migration downgrade to base
drops the schema and its data and is not a Telegram reset feature.

Critical timestamps are server-generated and round-trip as timezone-aware UTC.
`scheduled_at` stores the scheduled date and time together as an aware instant;
naive datetimes are rejected at the persistence boundary. `scheduled_utc()` in
`app.time` interprets local calendar fields in **Asia/Bishkek**. The order's
`scheduled_date`/`scheduled_time` properties and `format_local_datetime()` use
that same timezone for display. The `tzdata` dependency supplies timezone data
on systems such as Windows, following the [Python zoneinfo guidance](https://docs.python.org/3/library/zoneinfo.html).

`record_order()` copies the current waiting default onto the order at creation,
overriding any stale value on the draft. Changing defaults later affects only
new orders. The initial `waiting_deadline` is scheduled time plus that snapshot;
pickup codes expire independently, 20 minutes after generation. Waiting
extensions belong to task 10 and are not implemented here.

After successful code verification, `AWAITING_CUSTOMER_CONFIRMATION` can only
transition to `COMPLETED` or `DISPUTED`. No-show and cancellation are both
rejected at this stage. Terminal states have no outgoing transitions.

## Operational defaults and order creation (Task 5)

`Настройки` edits the pickup address, default courier/customer, and waiting minutes
(initially 15). Participant selection lists active bindings by friendly name, with
pagination. Invalid/inactive participant defaults are displayed as unset and must
be replaced for new orders; existing assignments and history are never reassigned.
No unbinding policy is introduced. Pickup-code lifetime stays fixed at 20 minutes.

`Заказы` → `Создать заказ` collects name, date (`ДД.ММ.ГГГГ`), time (`ЧЧ:ММ`,
Asia/Bishkek), address, courier, customer, and optional description (`Пропустить`).
Current defaults are offered with a keep button and can be replaced per order.
The summary offers creation, individual field editing, or cancellation. Only final
confirmation writes the scheduled order and its immutable creation event in one
transaction. Authorization and active participant roles are rechecked on save.
Waiting minutes are copied from the default at creation, preserving older orders.

Drafts use the existing in-memory FSM: `/start`, section navigation, cancellation,
or process restart discards an unfinished draft without creating an order. Each
screen has a fresh callback marker; stale/duplicate confirmations are rejected.
Run only one polling process (the existing deployment model); its event isolation
serializes simultaneous callbacks for a conversation. Settings and confirmed
orders persist in SQLite. This task does not add order lists, cancellation, or
handover actions. No additional migration or environment variable is needed.

## Administrator order views and cancellation (Task 6)

Active orders and history are paginated and show names, local scheduled date/time,
assigned participants, and Russian statuses. Details show description, address,
waiting snapshot, recorded location, photo availability, all milestone timestamps,
and the last code's expiry without loading or displaying its verifier. Long details
and event timelines are paginated. Photo/location buttons retrieve already recorded
evidence; they do not implement courier submission. Historical participant names
remain available after deactivation. All displayed times use Asia/Bishkek.

`Отменить заказ` asks for confirmation before making any changes. Confirmation
rechecks administrator access and current status, atomically records cancellation
and its audit event, and invalidates unused pickup codes. Verified handovers and
terminal orders cannot be cancelled, including through stale buttons. Duplicate and
concurrent confirmations cannot append another cancellation event. The existing
terminal-state rules block further handover transitions.

After commit, the bot attempts separate notifications to the assigned courier and
customer. If Telegram rejects one delivery, the other is still attempted and the
administrator sees a Russian warning; cancellation remains committed. There is no
background retry queue, so notification delivery is not guaranteed if the process
stops after commit. Error logs omit Telegram payloads and exception messages.
No schema migration or additional configuration is required.

## Courier arrival, location and photo (Task 7)

Courier `/start` keeps the Russian orientation and lists active assigned orders.
The courier explicitly selects an order; the bot never assumes a single current
order. Selected details show name, scheduled date/time, address, customer name,
and status. Lists are paginated and exclude terminal orders and other couriers'
assignments. Current role and assignment are checked again for every write.

`Я на месте` opens 30 minutes before the scheduled time, but never before midnight
on the scheduled Asia/Bishkek date. For example, a 00:10 handover opens at 00:00
that day. There is no late cutoff while the order remains active. Arrival is
recorded only once; subsequent steps replace the arrival button with their prompt.

After arrival, send a Telegram location (through Telegram's attachment menu) or
text containing a 2GIS URL. The bot validates coordinates or the URL's host and
structure and stores coordinates or the original URL without resolving links,
calling a map API, or enforcing distance. Supported hosts are 2gis.ru, 2gis.kg,
2gis.kz, 2gis.com (also www), and go.2gis.com. Next, send the product as a Telegram
photo, not a document. Each step atomically saves evidence, a UTC server timestamp,
the status transition, and an audit event. Duplicate or stale steps cannot replace
existing evidence; cancellation prevents further submissions.

Selecting another order, returning to the list, or `/start` clears the input
selection. After a process restart, reopening the chosen order resumes the prompt
from its persisted status. After a photo, `Товар готов к выдаче` is displayed but
its action remains unavailable: readiness notifications and codes belong to Task 8
and are not implemented here. Task 7 requires no new migration or configuration.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip check
```

Tests cover configuration, logging, polling cleanup, Russian errors, migrations
and schema consistency, reopening SQLite files, constraints, immutable events,
UTC timestamps, structural state transitions, single-use record operations, and
transaction rollback/concurrency, first registration, duplicate/concurrent
confirmations, and current-role authorization. Each database test uses a temporary migrated
SQLite file. Telegram requests are mocked; these checks do not verify live
credentials or connectivity.
