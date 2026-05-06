# sosunov-mcp (MyDay MCP)

[MCP](https://modelcontextprotocol.io/)‑сервис «Мой день»: геймификация, цепочки челленджей, задачи на экономию. Данные по транзакциям берутся из отдельного **tx-agent** по HTTP (**по умолчанию** `http://127.0.0.1:9200/mcp`). Состояние хранится в **PostgreSQL**.

| Компонент | Порт | Назначение |
|-----------|------|------------|
| `server.py` (этот проект) | **9101** | MCP over HTTP (`streamable-http`) |
| tx-agent | **9200** | RPC `tools/call` → `getTransactionsForPeriod`, … |

В коде упомянут WS proxy на **9100** — это внешняя обвязка, в репозитории только приложение на **9101**.

---

## Структура репозитория

| Файл | Назначение |
|------|------------|
| `server.py` | FastMCP приложение, инструменты, `_init_db()` |
| `db_config.py` | Сборка DSN PostgreSQL из переменных окружения |
| `mock_tx_agent.py` | Локальный мок tx-agent для тестов без боевого агента |
| `requirements.txt` | Зависимости Python |
| `.env.example` | Шаблон подключения к БД |

---

## Требования

- **Python 3.10+** (используются типы вида `list[int] | None`)
- **PostgreSQL** (локально или удалённо)
- Для продакшена — реальный **tx-agent** на порту из `TX_AGENT_URL` в `server.py` (или прокси)

---

## Установка

```bash
cd sosunov-mcp
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env под свой PostgreSQL
chmod 600 .env
```

> Если виртуальное окружение лежит в другом месте (например, общий `~/Documents/.venv`), подставьте свой путь к `python` вместо `./.venv/bin/python`.

---

## PostgreSQL

Параметры задаются переменными окружения (**см. `db_config.py`**):

| Переменная | По умолчанию |
|------------|----------------|
| `POSTGRES_HOST` | `127.0.0.1` |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_DB` | `myday` |
| `POSTGRES_USER` | `myday` |
| `POSTGRES_PASSWORD` | `myday` |
| `POSTGRES_SSLMODE` | `prefer` |

Один раз создайте роль и базу (от суперпользователя Postgres), например:

```sql
CREATE ROLE myday LOGIN PASSWORD 'myday';
CREATE DATABASE myday OWNER myday;
```

При первом импорте/запуске `server.py` выполняется **`_init_db()`** — создаются таблицы ниже, если их ещё нет.

Смотреть данные нужно в базе **`myday`**, схема **`public`**, а не в системной базе `postgres`.

### Модель данных

Типы дат/времени в схеме сейчас хранятся как **TEXT** (ISO-строки или `YYYY-MM-DD`), а JSON — как **TEXT** с сериализованным содержимым. Идентификаторы — строковые префиксы вроде `chain_…`, `plan_…`.

#### `clients`

Профиль клиента (заполняется внешним потоком или вручную; в `server.py` нет tool для вставки).

| Поле | Назначение |
|------|------------|
| `client_id` | Первичный ключ, внешний идентификатор клиента |
| `name` | Отображаемое имя |
| `authorized` | Флаг `0/1`: доступ/согласие (зарезервировано под авторизацию) |

#### `optimization_plans`

Подтверждённый план оптимизации трат — основа для построения цепочки челленджей.

| Поле | Назначение |
|------|------------|
| `plan_id` | Первичный ключ плана |
| `client_id` | Владелец плана |
| `selected_categories` | JSON-массив **кодов категорий** (числа), например `[5411, 5812]` — ротация по слотам челленджа |
| `target_reduction_pct` | Целевой **процент сокращения трат** по плану |
| `monthly_target_savings_rub` | **Месячная цель экономии** в рублях |
| `category_forecasts` | JSON-объект: оценка базы трат по категории (ключ — строка с кодом категории); для лимитов в задачах |
| `confirmed_at` | Отметка времени подтверждения плана (текстовая; сортировка «последний план» в `buildPredictedChain`) |

#### `challenge_chains`

Цепочка челленджей на календарный месяц (4 недельных слота и общий план).

| Поле | Назначение |
|------|------------|
| `chain_id` | Первичный ключ цепочки |
| `client_id` | Клиент |
| `plan_id` | Ссылка на `optimization_plans` |
| `month` | Месяц в формате **`YYYY-MM`** |
| `status` | Жизненный цикл: например **`ACTIVE`** (текущая цепочка), **`pending_activation`** (предрасчёт на следующий месяц) |
| `created_at` | Время создания (ISO + смещение) |

#### `challenges`

Один **слот** внутри цепочки (недельный интервал внутри месяца).

| Поле | Назначение |
|------|------------|
| `challenge_id` | Первичный ключ челленджа |
| `chain_id` | Родительская цепочка |
| `slot_number` | Номер слота **1–4** (логика недель месяца в `buildChallengeChain`) |
| `start_date` | Начало периода **`YYYY-MM-DD`** |
| `end_date` | Конец периода **`YYYY-MM-DD`** |
| `target_savings_rub` | **Целевая экономия** в рублях на этот слот |
| `status` | **`PENDING`** (ожидает), **`ACTIVE`** (текущий), **`COMPLETED`** / **`FAILED`** |
| `fact_savings_rub` | Зафиксированная экономия по факту завершения слота (проставляется при автозавершении) |

#### `tasks`

Конкретное задание пользователю в рамках челленджа (шаблон «ШБ-xx», лимит по категории и т.д.).

| Поле | Назначение |
|------|------------|
| `task_id` | Первичный ключ задачи |
| `challenge_id` | Челлендж-«неделя» |
| `template_id` | Код шаблона из `TASK_TEMPLATES` (например `ШБ-01`) |
| `action` | Логический тип действия: `SPEND`, `COUNT`, `LIMIT`, … |
| `category_code` | **Код MCC/PFM-категории** для задачи |
| `target_amount_rub` | **Лимит или целевой объём** в рублях (доля прогресса считается от него) |
| `saving_amount_rub` | Связанная **целевая экономия** в ₽ для отображения/логики |
| `period_start`, `period_end` | Интервал действия задачи |
| `status` | **`ACTIVE`**, **`PENDING`**, **`COMPLETED`** |
| `human_text` | Текст задачи для пользователя |
| `actual_amount_rub` | Факт трат по категории за период (из tx-agent при проверке) |
| `progress_pct` | Процент выполнения **0–100** |

#### `scheduler_jobs`

Отложенные проверки и переключение слотов (внешний планировщик должен вызывать `scheduledProgressCheck` с нужным `job_id`).

| Поле | Назначение |
|------|------------|
| `job_id` | Первичный ключ job |
| `client_id` | Клиент |
| `chain_id` | Цепочка |
| `job_type` | **`DAILY_CHECK`** (ежедневный прогресс), **`CHALLENGE_END`**, **`MONTH_END`** |
| `challenge_id` | Для `CHALLENGE_END` — какой челлендж закрывается; для `DAILY_CHECK`/`MONTH_END` может быть пустым |
| `check_time_local` | Локальное время проверки (строка, напр. `10:00`) |
| `timezone` | Часовой пояс (напр. `Europe/Moscow`) |
| `scheduled_at` | Когда job был «назначен» (ISO) |
| `next_run_at` | Следующий запуск (используется в `getStatus`) |
| `last_run_at` | Время последнего успешного прогона |
| `status` | Например **`pending`**, **`succeeded`** |
| `retry_count` | Счётчик повторов (зарезервировано) |

#### `progress_log`

Аудит пересчёта прогресса по задачам.

| Поле | Назначение |
|------|------------|
| `log_id` | Первичный ключ записи |
| `task_id` | Задача |
| `checked_at` | Время проверки |
| `old_status`, `new_status` | Статус до/после |
| `actual_amount_rub` | Учтённая сумма на момент проверки |
| `scheduler_job_id` | Какой job инициировал проверку |

#### `control_tasks`

Контроль **аномалий** трат/«доходов» по порогу в рамках периода челленджа.

| Поле | Назначение |
|------|------------|
| `control_task_id` | Первичный ключ |
| `challenge_id` | Челлендж |
| `type` | **`SPEND_ANOMALY`** — всплеск трат; **`INCOME_ANOMALY`** — порог по сумме (в коде сравнивается с фактом по категориям из tx-agent) |
| `x_threshold_rub` | Порог срабатывания в рублях |
| `category_codes_watched` | JSON-массив кодов категорий, по которым считается факт |
| `is_visible_to_client` | `0/1` — показывать ли клиенту (сейчас чаще `0`) |
| `is_triggered` | `0/1` — сработало ли условие |
| `trigger_txn_id` | Зарезервировано под ID транзакции-триггера |
| `trigger_amount_rub` | Сумма, на которой сработало правило |
| `alert_dismissed_at` | Когда алерт снят (`acknowledgeAnomaly`); пока не `NULL`, `getStatus` не показывает в `open_anomaly_alert` |
| `period_start`, `period_end` | Окно наблюдения |

#### `consents`

Заготовка под хранение согласий (в текущем `server.py` не заполняется инструментами).

| Поле | Назначение |
|------|------------|
| `consent_id` | Идентификатор согласия |
| `client_id` | Клиент |
| `scope` | Область согласия |
| `timestamp` | Время |
| `version` | Версия текста согласия |

#### `budget_plans`

Заготовка под бюджеты по месяцам (в текущем `server.py` не заполняется инструментами).

| Поле | Назначение |
|------|------------|
| `budget_plan_id` | Первичный ключ |
| `client_id` | Клиент |
| `month` | Месяц **`YYYY-MM`** |
| `status` | Статус плана |
| `plan_data` | Сериализованные данные плана |
| `created_at`, `updated_at` | Метки времени |

Связи по смыслу: **`optimization_plans`** → **`challenge_chains`** → **`challenges`** → **`tasks`** / **`control_tasks`**; **`scheduler_jobs`** и **`progress_log`** завязаны на цепочку и задачи.

---

## Запуск

**1. (Опционально для локальных тестов)** мок tx-agent:

```bash
set -a; source .env; set +a
PYTHONPATH="$(pwd)" .venv/bin/python mock_tx_agent.py
```

**2. MCP‑сервер:**

```bash
set -a; source .env; set +a
PYTHONPATH="$(pwd)" .venv/bin/python server.py
```

Ожидаемый лог: инициализация схемы и `Uvicorn running on http://127.0.0.1:9101`.

Остановка процесса, слушающего порт:

```bash
kill -9 "$(lsof -tiTCP:9101 -sTCP:LISTEN)"
```

Аналогично для мока: порт **9200**.

---

## MCP по HTTP

Эндпоинт: `http://127.0.0.1:9101/mcp`.

1. **`initialize`** — в ответе приходит заголовок **`mcp-session-id`** (обязательно сохранять).
2. Дальнейшие запросы (например **`tools/call`**) отправляются с заголовком **`mcp-session-id`** и заголовком **`Authorization: Bearer <токен>`**, где токен должен совпасть с **`GATEWAY_TOKEN`** в `server.py`.

Формат тела — JSON-RPC 2.0; ответ может прийти как SSE (`event: message` + `data: {...}`).

---

## Инструменты (tools)

Кратко по назначению (полный текст в `server.py` / prompt сервера):

- `buildChallengeChain` — построить цепочку челленджей на месяц
- `saveChallengeChain` — сохранить цепочку и зарегистрировать scheduler-jobs
- `scheduledProgressCheck` — опрос tx-agent и обновление прогресса в БД
- `getStatus` — статус челленджей и задач клиента
- `recalculateChain` — пересчёт отложенных слотов
- `acknowledgeAnomaly` — снять алерт аномалии
- `buildChallengeDigest` — дайджест завершённого челленджа
- `buildPredictedChain` — предрасчёт цепочки на следующий месяц

Без живого **tx-agent** вызов **`scheduledProgressCheck`** завершится ошибкой сети; с **`mock_tx_agent.py`** можно прогнать сценарий локально.

---

## Безопасность

- **`GATEWAY_TOKEN`** сейчас захардкожен в `server.py` — для любой среды кроме локальной тестовой лучше вынести в переменную окружения и ротировать.
- Файл **`.env`** с паролем БД не коммитить; права `chmod 600`.

---

## Про репозиторий `spimex-instruments`

Подробный **README** для выгрузки SPIMEX лежит в **другом** репозитории, потому что запрос был явно на путь `repo/spimex-instruments/README.md`:

`Documents/repo/spimex-instruments/README.md`

Текущий репозиторий **`sosunov-mcp`** с ним не смешивается — это отдельный сервис (MCP + Postgres + tx-agent).
