# sosunov-mcp (MyDay MCP)

[MCP](https://modelcontextprotocol.io/)‑сервис «Мой день»: геймификация, цепочки челленджей, задачи на экономию. Данные по транзакциям берутся из отдельного **tx-agent** по HTTP (**по умолчанию** `http://127.0.0.1:9200/mcp`). Состояние хранится в **PostgreSQL**.

| Компонент | Порт | Назначение |
|-----------|------|------------|
| приложение в `python/server.py` | **9101** | MCP over HTTP (`streamable-http`), хост задаётся **`MCP_HOST`** (в кластере обычно `0.0.0.0`) |
| tx-agent | **9200** | RPC `tools/call` → `getTransactionsForPeriod`, URL — **`TX_AGENT_URL`** |

В коде упомянут WS proxy на **9100** — это внешняя обвязка, в репозитории только приложение на **9101**.

Структура выровнена под корпоративный Python-шаблон для **OpenShift** (каталог приложения `python/`, `cfg/`, Helm под `openshift/`, `Dockerfile` в корне).

---

## Структура репозитория

| Путь | Назначение |
|------|------------|
| `python/server.py` | FastMCP: инструменты, `_init_db()` |
| `python/db.py` | DSN PostgreSQL из переменных окружения |
| `python/config.py` | `GATEWAY_TOKEN`, `TX_AGENT_URL`, `MCP_HOST` / `MCP_PORT`, issuer URLs |
| `python/common_utils.py` | Общие утилиты (расширение под стандарты компании) |
| `python/controller.py`, `python/fill_template.py` | Заготовки под шаблон пайплайнов (логика MCP в `server.py`) |
| `python/mock_tx_agent.py` | Локальный мок tx-agent |
| `cfg/application.yml` | Пример ключей конфигурации (ожидаются env в рантайме) |
| `cfg/default.txt` | Краткое описание сервиса |
| `openshift/charts/` | Helm chart: Deployment, Service, ConfigMap, Secret (опц.), VirtualService (опц.) |
| `openshift/chart-values.yaml` | Пример переопределений для окружений / CI |
| `Dockerfile` | Сборка образа (non-root `1001`, `PYTHONPATH=/opt/app-root/python`) |
| `pip.conf` | Корпоративный PyPI при необходимости |
| `requirements.txt` | Зависимости Python |
| `.env.example` | Шаблон переменных для локального запуска |

---

## Требования

- **Python 3.10+** (используются типы вида `list[int] | None`)
- **PostgreSQL**
- **tx-agent** доступен по **`TX_AGENT_URL`** (в кластере — DNS сервиса, не `127.0.0.1`)

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

Параметры БД задаются переменными окружения (**см. `python/db.py`**); остальные настройки приложения — **`python/config.py`** и `.env.example`:

| Переменная | По умолчанию |
|------------|----------------|
| `POSTGRES_HOST` | `127.0.0.1` |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_DB` | `myday` |
| `POSTGRES_USER` | `myday` |
| `POSTGRES_PASSWORD` | `myday` |
| `POSTGRES_SSLMODE` | `prefer` |

Дополнительно (см. `.env.example`): `MCP_HOST`, `MCP_PORT`, `TX_AGENT_URL`, `GATEWAY_TOKEN`, `MCP_ISSUER_URL`, `MCP_RESOURCE_SERVER_URL`.

Один раз создайте роль и базу (от суперпользователя Postgres), например:

```sql
CREATE ROLE myday LOGIN PASSWORD 'myday';
CREATE DATABASE myday OWNER myday;
```

При первом импорте/запуске `python/server.py` выполняется **`_init_db()`** — создаются таблицы ниже, если их ещё нет.

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

## Запуск (локально)

Каталог с репозиторием должен быть в `PYTHONPATH`, чтобы импортировать пакет `python/` как модули верхнего уровня (`config`, `db`).

**1. (Опционально)** мок tx-agent:

```bash
set -a; source .env; set +a
PYTHONPATH="$(pwd)/python" .venv/bin/python "$(pwd)/python/mock_tx_agent.py"
```

**2. MCP‑сервер:**

```bash
set -a; source .env; set +a
PYTHONPATH="$(pwd)/python" .venv/bin/python "$(pwd)/python/server.py"
```

Для локали в `.env` можно оставить `MCP_HOST=127.0.0.1`. В OpenShift в ConfigMap задано **`MCP_HOST=0.0.0.0`**.

Ожидаемый лог: инициализация схемы и `Uvicorn running on http://127.0.0.1:9101`.

Остановка процесса, слушающего порт:

```bash
kill -9 "$(lsof -tiTCP:9101 -sTCP:LISTEN)"
```

Аналогично для мока: порт **9200**

---

## OpenShift / Helm

Образ собирается из корня репозитория:

```bash
podman build -t sosunov-mcp:latest .
# или docker build ...
```

Публикация в реестр проекта и ссылка на образ в `openshift/chart-values.yaml` / `values.yaml`.

Установка чарта (после создания Secret с ключами `postgres-password` и `gateway-token`, если `secrets.generate: false`):

```bash
helm upgrade --install sosunov-mcp ./openshift/charts \
  -f openshift/chart-values.yaml \
  -n YOUR_NAMESPACE
```

Для отладки в dev можно включить генерацию Secret в values: `secrets.generate: true` (не для продакшена).

Включение Istio **VirtualService**: `istio.enabled: true` и корректный `istio.gateway`; хосты — в `istio.hosts`.

Подробнее — комментарии в `openshift/charts/values.yaml` и вывод **`helm upgrade ... --dry-run`** / `helm template`.

---

## MCP по HTTP

Эндпоинт: `http://127.0.0.1:9101/mcp`.

1. **`initialize`** — в ответе приходит заголовок **`mcp-session-id`** (обязательно сохранять).
2. Дальнейшие запросы (например **`tools/call`**) отправляются с заголовком **`mcp-session-id`** и заголовком **`Authorization: Bearer <токен>`**, где токен должен совпасть с переменной **`GATEWAY_TOKEN`** (`python/config.py`).

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

Без живого **tx-agent** вызов **`scheduledProgressCheck`** завершится ошибкой сети; с **`python/mock_tx_agent.py`** можно прогнать сценарий локально.

---

## Безопасность

- **`GATEWAY_TOKEN`** задаётся через окружение (в Helm — из Secret); дефолт в `config.py` только для локальной разработки.
- Файл **`.env`** с паролем БД не коммитить; права `chmod 600`.

---

## Про репозиторий `spimex-instruments`

Подробный **README** для выгрузки SPIMEX лежит в **другом** репозитории, потому что запрос был явно на путь `repo/spimex-instruments/README.md`:

`Documents/repo/spimex-instruments/README.md`

Текущий репозиторий **`sosunov-mcp`** с ним не смешивается — это отдельный сервис (MCP + Postgres + tx-agent).
