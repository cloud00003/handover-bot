# Handover Bot

Telegram bot for recording and confirming physical item handovers.
Specification: [_docs/plan_handover-bot.md](_docs/plan_handover-bot.md).
Implementation order: [backlog.md](backlog.md).

Task 1 provides configuration, polling, safe logging, common Russian error
responses, and offline tests. Registration, menus, orders, database models,
migrations, and reset are later tasks. At this stage `/start` has no reply.

## Local setup

Requires Python 3.11+ and network access to install dependencies. From the
repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

On Linux/macOS use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`.
The manifest declares aiogram 3.x, SQLAlchemy 2.x, aiosqlite, Alembic,
python-dotenv, pytest, and pytest-asyncio. Database packages are reserved for task 2;
there is no migration command to run yet.

## Configuration

Create a bot through Telegram's BotFather and obtain its token. Configure its
name, username, and Russian description there. Copy `.env.example` to `.env`
only if `.env` does not already exist, then replace the placeholder locally:

```dotenv
BOT_TOKEN=replace_with_your_bot_token
```

`BOT_TOKEN` is the only setting currently required. The application reads `.env`
from the working directory; an existing environment variable takes precedence.
Missing, blank, or malformed tokens stop startup with exit code 1 and a safe
configuration message. Format validation does not verify Telegram credentials.
Never commit `.env` or share its contents. Tests use synthetic tokens and
temporary directories and do not read the local `.env`.

## Run

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

The bot uses long polling and requires Telegram connectivity and a valid token.
Use one polling process for the token; an existing webhook must be removed before
polling. Stop with Ctrl+C. The HTTP session closes on shutdown or polling failure.
The lifecycle uses aiogram's [polling API](https://docs.aiogram.dev/en/latest/dispatcher/dispatcher.html).
Logs go to the console with token redaction. User-facing errors are in Russian;
update failures log the update ID and exception type without message bodies.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip check
```

Tests cover configuration loading/validation, safe secret representation and
logging, polling cleanup, Russian error responses, and stale callbacks. Telegram
requests are mocked; these checks do not verify live credentials or connectivity.
