# Handover Bot — Version 1 Backlog

Source: [_docs/plan_handover-bot.md](_docs/plan_handover-bot.md), sections 1–36.

Repository inspected: only the specification, a minimal README, and `.gitignore` are present as project files. There is no application, dependency manifest, database setup, or test suite. A local `.env` exists; its contents were not read. `.gitignore` already excludes `.env`, virtual environments, Python caches, and SQLite `.sqlite3` files.

Tasks 1–3 and 5–6 are complete; task 4 is in progress pending replacement-policy clarification; tasks 7–12 are pending. Task 5 was explicitly requested while Task 4 removal remained pending; it uses existing invitations and validates active bindings without implementing removal or reassignment policy. Implement one requested task at a time; dependencies name prerequisite tasks, including their transitive dependencies. This backlog does not authorize implementation.

## Shared acceptance criteria

- Every Telegram menu, button, prompt, validation/error message, status, notification, confirmation, and empty state is in Russian, using the specification's wording where supplied. Navigation is button-driven and normal screens do not require technical IDs.
- Each protected action checks the current active role using Telegram user ID and, where applicable, assignment to the order. Callback data alone never grants access.
- Inputs are validated; duplicate actions and unknown/stale callbacks are handled safely. Secrets and sensitive code details never appear in logs or unintended messages; technical errors stay out of Telegram replies.
- Each behavior includes relevant automated tests and passing checks before its task is complete. Update setup documentation when commands or configuration change.
- Use the preferred simple Python/aiogram/SQLite stack. Keep business rules outside handlers, centralize transitions and Russian text where practical, and add nothing excluded by specification section 33.

## 1. Project scaffold and configuration

**Dependencies:** None. **Specification:** 25–26, 29–32.

**Status:** Complete. Added the dependency manifest, configuration loader, polling entry point with session cleanup, safe console logging, shared Russian error handling, `.env.example`, offline tests, and setup/run documentation. Verification: 15 tests passed; `pip check` passed. Live Telegram connectivity was not tested. No later task behavior was implemented.

**Acceptance criteria:**

- Python 3.11+, aiogram 3.x, SQLAlchemy 2.x, Alembic, configuration loading, pytest, and async test support are declared in a dependency manifest.
- A minimal application entry point loads `BOT_TOKEN` from `.env`, starts the bot, and closes resources cleanly; missing configuration fails clearly without revealing secrets.
- `.env.example` contains placeholders only; existing secret exclusions are preserved. Basic logging, Russian error handling, and test scaffolding are available.
- README documents installation, environment setup, local startup, and test commands; configuration checks pass.

## 2. Persistence, state machine, and event foundation

**Dependencies:** 1. **Specification:** 10, 18–24, 27.

**Status:** Complete. Implemented the initial Alembic migration, async SQLite sessions, historical binding records, constraints and single-use record operations, UTC timestamp storage, structural state transitions, and atomic immutable audit events. Applied the user's clarified rules: Asia/Bishkek local scheduling/display, waiting defaults copied at order creation, and only completion or dispute after successful code verification. Task 2 introduced no Telegram workflows.

**Verification:** 66 automated tests passed, including clean migration/upgrade/downgrade, schema comparison, persistence, concurrency, rollback, audit immutability, Bishkek/UTC date-boundary conversion, waiting snapshots, and post-verification transition restrictions. `pip check` passed. Tests used temporary databases; the local `.env` and Telegram bot were not changed.

**Acceptance criteria:**

- Migrations create persistent users/role bindings, invitations, orders, pickup-code data, defaults, and events with all fields required by sections 19 and 21–24.
- Database constraints and transactions support one active role per Telegram account, single-use invitations/codes, and consistent order changes with immutable audit events.
- A centralized state machine covers the specified lifecycle and rejects invalid transitions; handlers cannot arbitrarily assign statuses.
- Events retain order, type, server timestamp, actor identity/role, and relevant structured metadata. Historical references survive participant unbinding.
- Waiting defaults to 15 minutes; pickup-code lifetime is 20 minutes from generation, independently of waiting. Critical timestamps use server time.
- Tests verify clean database migration, persistence, constraints, invalid transitions, and atomic state/event recording. Clarify applicable timing rules listed below before encoding them.

## 3. First administrator and authorization

**Dependencies:** 2. **Specification:** 3–5, 21, 25–27.

**Status:** Complete. Clean-system `/start` uses the exact Russian confirmation wording and one confirmation button. Atomic first-administrator registration persists Telegram identity and role; stale, repeated, and concurrent confirmations cannot register another administrator. Each protected menu action checks the current active role by Telegram user ID. The menu contains exactly `Заказы`, `Участники`, `История`, and `Настройки`; section workflows remain pending in later tasks. Existing Task 1 and Task 2 business rules are unchanged.

**Verification:** 35 targeted registration/runtime tests passed; the full suite passed all 95 tests. `pip check` and `git diff --check` passed (line-ending warnings only). Tests use temporary migrated databases and mocked Telegram requests; no live registration was performed.

**Acceptance criteria:**

- On a clean system, `/start` shows only `Подтвердить роль администратора`; confirmation saves identity/profile data and initializes the system.
- After initialization, other users cannot self-register as administrator, including through stale or concurrent confirmations; no secret command grants access.
- Administrator menu contains `Заказы`, `Участники`, `История`, and `Настройки`.
- Reusable authorization checks reject unknown/inactive users and wrong roles on every protected action. Tests cover first/second registration, concurrent confirmation, and unauthorized access; later handlers extend this coverage.

## 4. Participant invitations and removal

**Dependencies:** 3. **Specification:** 6–7, 21–22, 25, 27.

**Status:** In progress. Implemented administrator name-entry flows, secure hashed one-time invitation tokens/deep links, atomic role binding/redemption, current-role authorization, and participant lists/profiles in Russian. Binding removal remains pending clarification of its effects on active orders, defaults, and replacement assignments.

**Verification so far:** 25 invitation tests and all 120 full-suite tests passed. `pip check` and `git diff --check` passed (line-ending warnings only). No live Telegram messages were sent; the local `.env` was unchanged.

**Acceptance criteria:**

- Administrator can create courier/customer invitations after entering a friendly name; secure random single-use deep links are shown for the administrator to forward.
- Redemption validates unused/non-revoked tokens, binds the Telegram account to the intended role, saves profile/registration data and assigned name, and consumes the invitation atomically.
- Participant lists/details display assigned names and roles. Confirmed `Удалить привязку` removes active access, preserves history, and invalidates related active invitations where applicable.
- A removed account can be invited again, including to another role, without simultaneously holding two active roles.
- Tests cover both roles, malformed/reused/revoked tokens, concurrent redemption, role conflicts, unbinding, and re-invitation. Replacement effects on existing orders follow clarification below.

## 5. Operational defaults and order creation

**Dependencies:** 4. **Specification:** 8–10, 23–24, 27.

**Status:** Complete. Added Russian operational settings, active participant selection, and step-by-step order drafts with current defaults, individual overrides, optional description, summary/edit/cancel, and final atomic creation/event recording. Uses Asia/Bishkek scheduling and existing per-order waiting snapshots. Fresh form callback markers and the existing per-conversation event isolation reject stale/repeated confirmation. No schema migration, removal policy, or Task 6+ workflow was added.

**Verification:** All 190 full-suite tests pass; targeted Task 5, registration, and invitation tests pass (122 tests), including invalid fields, missing/inactive defaults, all editable draft fields, settings persistence, role guards, concurrent confirmations, event rollback, and Telegram delivery failure after successful creation. `pip check` and `git diff --check` pass. Tests used temporary migrated databases and mocked Telegram requests.

**Acceptance criteria:**

- `Настройки` persists default courier, customer, pickup address, and configurable waiting minutes (initially 15); secrets are never displayed.
- `Заказы` → `Создать заказ` collects required name, date, time, address, courier, and customer, plus an optional description with `Пропустить`.
- Date/time formats are explained and validated. Defaults are prefilled and individually replaceable; missing or inactive participant defaults cannot silently produce an invalid assignment.
- Summary includes every creation field and `Создать заказ`, `Изменить`, `Отмена`; only final creation confirmation persists a `Запланировано` order and its event.
- Tests cover required-field validation, defaults, overrides, optional description, editing/cancelling the draft, and duplicate creation confirmation.

## 6. Administrator order views and cancellation

**Dependencies:** 5. **Specification:** 17, 19–20, 27.

**Status:** Complete. Added Russian paginated active/history lists, order details and milestone timestamps, recorded photo/location viewing, safe code-expiry display, and paginated event timelines. Administrator cancellation requires confirmation and current authorization, rejects verified/terminal handovers, and atomically records status, cancellation time, audit event, and unused-code invalidation. Participants are notified after commit; delivery failures are reported without undoing cancellation. No migration or Task 7+ workflow was added.

**Verification:** All 58 Task 6 tests and all 248 full-suite tests passed. Coverage includes every lifecycle state, empty/missing data, historical names, evidence and long-text pagination, secret suppression, cancellation abort/confirm, duplicate/concurrent confirmation, verification after prompting, rollback, current-role guards, and notification failure. `pip check` and `git diff --check` passed. Tests used temporary migrated databases and mocked Telegram requests; the running bot was not restarted.

**Acceptance criteria:**

- Active list shows name, scheduled date/time, named courier/customer, and Russian status; order details render all specified fields, available evidence, milestone times, code expiry, and timeline without exposing the code/hash.
- `История` includes completed, cancelled, no-show, and disputed orders with individual timelines; absent data and empty lists have clear Russian rendering.
- Confirmed `Отменить заказ` changes an active order before successful code verification to `Отменено`, records the event/time, blocks further handover actions, and notifies its courier and customer. Cancellation after code verification is forbidden by the clarified transition rules.
- Tests use representative lifecycle fixtures to cover all views, cancellation confirmation/abort, duplicates, and forbidden actions after cancellation. Later tasks verify newly generated evidence appears here.

## 7. Courier arrival, location, and product photo

**Dependencies:** 6; resolve arrival/current-order questions below. **Specification:** 11, 18–19, 27.

**Acceptance criteria:**

- Courier `/start` displays the assigned current order with name, date/time, address, customer name, and status, or a Russian empty state.
- `Я на месте` is available according to the clarified handover window; arrival timestamp/event is recorded once, followed by the location prompt.
- Telegram coordinates or a text containing a valid 2GIS link are accepted and stored with server timestamp; the raw URL is preserved. No distance enforcement or external map API is added.
- After location, a Telegram photo is required; its file ID, order reference, and server timestamp are stored before showing `Товар готов к выдаче`.
- Tests cover both location types, invalid input, photo storage, required sequence, duplicate arrival, stale actions, and another courier attempting to act on the order.

## 8. Readiness and one-time pickup-code handover

**Dependencies:** 7; clarify replacement trigger below before exposing it. **Specification:** 12–14, 19, 25–27.

**Acceptance criteria:**

- Readiness requires the previous steps, records status/time/event, generates a secure random order-bound code, and notifies the assigned customer with order name, address, readiness time, photo, and `Ваш код получения: XXXXX`.
- Store a secure hash-based verifier where practical. Code validity is exactly 20 minutes from server generation time; replacement invalidates the preceding active code, and duplicate readiness does not unintentionally regenerate it.
- `Подтвердить передачу` prompts `Введите код заказчика.`; validation checks order, allowed state, expiry, and unused status.
- Success consumes the code atomically, records confirmation time/event, transitions to `Ожидается подтверждение заказчика`, and notifies the customer. Invalid/expired/reused codes produce clear Russian errors.
- Tests cover valid/wrong/expired/used/wrong-order codes, the 20-minute boundary, replacement invalidation, readiness prerequisites, and duplicate/concurrent validation.

## 9. Customer receipt confirmation and dispute

**Dependencies:** 8. **Specification:** 15, 18–19, 27.

**Acceptance criteria:**

- After valid code confirmation, only the assigned customer can answer `Подтверждаете получение товара?` using `Да, товар получил` or `Нет, товар не получил`.
- Acceptance records receipt/completion timestamps and final events, sets `Завершено`, and prevents further handover actions.
- Denial records the dispute time/event, sets `Спорная ситуация`, and notifies the administrator without completing the order or introducing dispute resolution.
- Tests cover both outcomes, wrong actor/state, repeated/concurrent responses, and a single completion record.

## 10. Waiting extension and no-show

**Dependencies:** 9; resolve waiting questions below. **Specification:** 10, 16, 18–19, 27.

**Acceptance criteria:**

- Waiting eligibility uses scheduled time plus the configured waiting window and server time, independently of pickup-code expiry.
- Courier sees `Продлить ожидание` and `Завершить ожидание` in the clarified eligible states; extension behavior follows the clarified duration/limits and records the significant action.
- Ending eligible waiting without successful handover records a no-show status/time/event and notifies the administrator.
- Completed orders cannot become no-show; invalid, stale, and repeated actions cannot overwrite an incompatible outcome.
- Tests cover deadline boundaries, configured duration, extension, no-show notification/event, and conflicts with completion/cancellation/dispute under the agreed transition rules.

## 11. Local developer reset

**Dependencies:** 10. **Specification:** 27–28.

**Acceptance criteria:**

- A local/server-only script requires typing `RESET`; declining confirmation changes nothing.
- Confirmed reset clears administrator, participants/bindings, invitations, orders/codes, events, and customized defaults to restore first-run state.
- The next `/start` can register the first administrator again; no reset button or command is reachable through Telegram.
- Automated tests run against an isolated test database and verify both refusal and full reset. README explains the command and destructive effect.

## 12. Integrated verification and operational handover

**Dependencies:** 11 and completion of all earlier acceptance criteria. **Specification:** 25–27, 29, 34.

**Acceptance criteria:**

- Full automated suite passes and covers every case in section 27, including wrong-role access, revoked bindings, invalid transitions, duplicate actions, and all terminal outcomes. Tests are added incrementally, not deferred to this task.
- README covers BotFather creation/name/username/description, environment variables, installation/migrations, local run, tests, and reset with no credentials.
- With configured Telegram test accounts, verify registration/invitations, defaults/order creation, both location inputs, photo/code delivery, receipt/dispute, expiry/reuse rejection, no-show, cancellation, history, and reset; record actual results and any external setup blockers.
- Review every Telegram surface for Russian wording, including errors/empty states; verify secrets are excluded and runtime state survives restart.
- Section 34's definition of done is checked against evidence; no section 33 features are introduced. Finish with documented run instructions and deployment preparation for the specified simple stack.

## Confirmed timing and transition rules

The user clarified these rules for task 2:

- Scheduled and displayed local date/time use `Asia/Bishkek`; persisted server timestamps use UTC.
- Waiting defaults are copied into each order at creation. Later default changes do not affect existing orders.
- After successful pickup-code verification, the order can only proceed from pending customer confirmation to completed or disputed. No-show and cancellation are not allowed.

## Clarifications required before affected implementation

These are unresolved specification details, not new features or assumed business rules. Ask the user when the affected task is requested; planning can be completed without deciding them.

- **Task 7:** How early/late is `Я на месте` available, and which order is current if a courier has multiple active orders?
- **Task 10:** How long does `Продлить ожидание` extend waiting, and are repeated extensions allowed?
- **Tasks 4–5:** After unbinding, what happens to active orders and defaults referencing that participant? Does a replacement inherit existing assignments, or only become selectable for new orders? Historical records must remain intact in every case.
- **Task 8:** Who may request a replacement pickup code, and through which action? Implement the specified invalidation guarantee without inventing an additional Telegram workflow.
