#!/usr/bin/env python3
"""MCP MyDay — геймификация, челленджи.
Порт 9101 (internal). WS proxy на порту 9100.
Читает данные о транзакциях через внутренний HTTP API от tx-agent (порт 9200).
"""
import sys
import json
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, timezone
from typing import Any
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.middleware.bearer_auth import AccessToken
from mcp.server.auth.settings import AuthSettings
from db_config import get_postgres_dsn

GATEWAY_TOKEN = "Ew88G9YHkI1Br8nT1Kk7FfH2RZFSEzU_iTVAxHow3bo"
TX_AGENT_URL = "http://127.0.0.1:9200/mcp"


class _Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token == GATEWAY_TOKEN:
            return AccessToken(token=token, client_id="myday-gateway", scopes=["mcp:tools"])
        return None


TASK_TEMPLATES = [
    {"id": "ШБ-01", "action": "SPEND", "object": "MCC", "params": ["X_rub", "T_deadline", "Y_pfm_category"]},
    {"id": "ШБ-02", "action": "SPEND", "object": "MCC", "params": ["X_rub", "P_period", "Y_pfm_category"]},
    {"id": "ШБ-03", "action": "SPEND", "object": "MCC", "params": ["X_rub", "SP_weekend", "Y_pfm_category"]},
    {"id": "ШБ-05", "action": "SPEND", "object": "MERCHANT", "params": ["X_rub", "T_deadline", "M_merchant"]},
    {"id": "ШБ-06", "action": "SPEND", "object": "MERCHANT", "params": ["X_rub", "P_period", "M_merchant"]},
    {"id": "ШБ-09", "action": "COUNT", "object": "MCC", "params": ["N_count", "T_deadline", "Y_pfm_category"]},
    {"id": "ШБ-10", "action": "COUNT", "object": "MERCHANT", "params": ["N_count", "P_period", "M_merchant"]},
    {"id": "ШБ-11", "action": "LIMIT", "object": "MCC", "params": ["X_limit_rub", "P_period", "Y_pfm_category"], "primary": True},
    {"id": "ШБ-12", "action": "LIMIT", "object": "MCC", "params": ["X_limit_rub", "SP_weekend", "Y_pfm_category"]},
    {"id": "ШБ-13", "action": "LIMIT", "object": "MERCHANT", "params": ["X_limit_rub", "P_period", "M_merchant"]},
    {"id": "ШБ-14", "action": "COMBO", "object": "2_MCC", "params": ["X_target_y1", "X_limit_y2", "P_period", "Y1", "Y2"]},
    {"id": "ШБ-15", "action": "SERIES", "object": "MCC", "params": ["X_daily_rub", "N_days", "P_period", "Y_pfm_category"]},
]


def _log(msg: str):
    print(msg, file=sys.stderr)


def _parse_json(val: Any) -> Any:
    if isinstance(val, str):
        return json.loads(val)
    return val


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=3))).isoformat()


def _today() -> str:
    return datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")


class PostgresDB:
    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _adapt_query(query: str) -> str:
        # Existing code uses sqlite placeholders ("?"), convert them for PostgreSQL.
        return query.replace("?", "%s")

    def execute(self, query: str, params: tuple | None = None):
        cur = self.conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(self._adapt_query(query), params or ())
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def _init_db():
    conn = psycopg2.connect(get_postgres_dsn())
    db = PostgresDB(conn)
    schema_sql = """
        CREATE TABLE IF NOT EXISTS consents (
            consent_id TEXT PRIMARY KEY, client_id TEXT, scope TEXT, timestamp TEXT, version TEXT
        );
        CREATE TABLE IF NOT EXISTS optimization_plans (
            plan_id TEXT PRIMARY KEY, client_id TEXT, selected_categories TEXT,
            target_reduction_pct REAL, monthly_target_savings_rub REAL,
            category_forecasts TEXT, confirmed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS challenge_chains (
            chain_id TEXT PRIMARY KEY, client_id TEXT, plan_id TEXT,
            month TEXT, status TEXT DEFAULT 'ACTIVE', created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS challenges (
            challenge_id TEXT PRIMARY KEY, chain_id TEXT, slot_number INTEGER,
            start_date TEXT, end_date TEXT, target_savings_rub REAL,
            status TEXT DEFAULT 'PENDING', fact_savings_rub REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY, challenge_id TEXT, template_id TEXT,
            action TEXT, category_code INTEGER, target_amount_rub REAL,
            saving_amount_rub REAL, period_start TEXT, period_end TEXT,
            status TEXT DEFAULT 'ACTIVE', human_text TEXT, actual_amount_rub REAL DEFAULT 0, progress_pct REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS scheduler_jobs (
            job_id TEXT PRIMARY KEY, client_id TEXT, chain_id TEXT,
            job_type TEXT DEFAULT 'DAILY_CHECK', challenge_id TEXT,
            check_time_local TEXT DEFAULT '10:00', timezone TEXT DEFAULT 'Europe/Moscow',
            scheduled_at TEXT, next_run_at TEXT, last_run_at TEXT,
            status TEXT DEFAULT 'pending', retry_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS progress_log (
            log_id TEXT PRIMARY KEY, task_id TEXT, checked_at TEXT,
            old_status TEXT, new_status TEXT, actual_amount_rub REAL,
            scheduler_job_id TEXT
        );
        CREATE TABLE IF NOT EXISTS control_tasks (
            control_task_id TEXT PRIMARY KEY, challenge_id TEXT,
            type TEXT, x_threshold_rub REAL, category_codes_watched TEXT,
            is_visible_to_client INTEGER DEFAULT 0, is_triggered INTEGER DEFAULT 0,
            trigger_txn_id TEXT, trigger_amount_rub REAL,
            alert_dismissed_at TEXT,
            period_start TEXT, period_end TEXT
        );
        CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY, name TEXT, authorized INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS budget_plans (
            budget_plan_id TEXT PRIMARY KEY, client_id TEXT, month TEXT,
            status TEXT, plan_data TEXT, created_at TEXT, updated_at TEXT
        );
    """
    for stmt in (s.strip() for s in schema_sql.split(";")):
        if stmt:
            db.execute(stmt)
    db.commit()
    db.close()
    _log("MyDay PostgreSQL schema initialized")


def _get_db():
    conn = psycopg2.connect(get_postgres_dsn(), connect_timeout=30)
    return PostgresDB(conn)


async def _call_tx_agent(method: str, arguments: dict) -> dict:
    """Внутренний вызов к tx-agent для получения данных по транзакциям."""
    import httpx
    rpc_id = uuid.uuid4().hex[:8]
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {GATEWAY_TOKEN}",
    }
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": rpc_id,
        "params": {"name": method, "arguments": arguments},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(TX_AGENT_URL, json=payload, headers=headers, timeout=60)
        body = resp.text
        if body.startswith("event:"):
            for line in body.split("\n"):
                if line.startswith("data: "):
                    body = line[6:]
                    break
        data = json.loads(body)
        content = data.get("result", {}).get("content", [])
        if content:
            return json.loads(content[0].get("text", "{}"))
        return {}


MYDAY_PROMPT = """Мой День — персональный финансовый помощник в системе Рататуй. Геймификация, челленджи по экономии.

Данные по транзакциям предоставляет отдельный tx-agent (порт 9200).

== ИНСТРУМЕНТЫ (8) ==
buildChallengeChain — построить цепочку челленджей на месяц (4 слота по неделям)
saveChallengeChain — сохранить цепочку и зарегистрировать scheduler-jobs
scheduledProgressCheck — проверка прогресса по расписанию, обновляет БД
getStatus — текущий статус всех челленджей, заданий, аномалий
recalculateChain — пересчитать незапущенные челленджи при изменении категорий/процента
acknowledgeAnomaly — снять алерт по аномалии без пересчёта цепочки
buildChallengeDigest — дайджест завершённого челленджа с результатами
buildPredictedChain — предрасчёт цепочки на следующий месяц"""


mcp = FastMCP(
    "moy_den",
    host="127.0.0.1",
    port=9101,
    instructions=MYDAY_PROMPT,
    auth=AuthSettings(
        issuer_url="https://myday.local",
        resource_server_url="https://myday.local",
    ),
    token_verifier=_Verifier(),
)


@mcp.tool()
async def buildChallengeChain(plan_id: str, entry_date: str) -> str:
    """Построить цепочку челленджей на месяц по правилу 4 слотов. На вход принимает plan_id и дату входа (YYYY-MM-DD). Создаёт цепочку с 4 недельными слотами, каждому слоту назначает задачу из шаблона с категорией трат и целевой суммой экономии. Создаёт контрольные задачи (SPEND_ANOMALY, INCOME_ANOMALY)."""
    db = _get_db()
    plan = db.execute("SELECT * FROM optimization_plans WHERE plan_id=?", (plan_id,)).fetchone()
    if not plan:
        db.close()
        return json.dumps({"error": "404", "message": "План не найден"}, ensure_ascii=False)
    selected = json.loads(plan["selected_categories"])
    pct = plan["target_reduction_pct"]
    monthly_target = plan["monthly_target_savings_rub"]
    client_id = plan["client_id"]
    cat_forecasts = json.loads(plan["category_forecasts"]) if plan["category_forecasts"] else {}

    existing = db.execute("SELECT chain_id FROM challenge_chains WHERE client_id=? AND status='ACTIVE'", (client_id,)).fetchone()
    if existing:
        db.close()
        return json.dumps({"error": "409", "message": "У клиента уже есть активная цепочка"}, ensure_ascii=False)

    entry = datetime.strptime(entry_date, "%Y-%m-%d")
    days_in_month = (entry.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    days_in_month = days_in_month.day
    entry_day = entry.day
    days_remaining = days_in_month - entry_day + 1
    period_target = monthly_target * (days_remaining / days_in_month)

    slot_defs = [
        {"slot": 1, "start": 1, "end": 7},
        {"slot": 2, "start": 8, "end": 14},
        {"slot": 3, "start": 15, "end": 21},
        {"slot": 4, "start": 22, "end": days_in_month},
    ]
    available = [s for s in slot_defs if entry_day <= s["end"]]
    total_days = sum(s["end"] - max(entry_day if s["slot"] == available[0]["slot"] else s["start"], s["start"]) + 1 for s in available)
    total_days = max(total_days, 1)

    chain_id = _gen_id("chain")
    month_str = entry.strftime("%Y-%m")
    month_end_date = f"{entry.year}-{entry.month:02d}-{days_in_month}"
    db.execute("INSERT INTO challenge_chains (chain_id,client_id,plan_id,month,status,created_at) VALUES (?,?,?,?,?,?)",
               (chain_id, client_id, plan_id, month_str, "ACTIVE", _now_iso()))

    challenges = []
    for i, sd in enumerate(available):
        s = max(entry_day, sd["start"]) if i == 0 else sd["start"]
        e = sd["end"]
        dur = e - s + 1
        target_savings = round(period_target * (dur / total_days))
        ch_id = _gen_id("ch")
        status = "ACTIVE" if i == 0 else "PENDING"
        is_activation = sd["slot"] == 4 and entry_day >= (days_in_month - 2)
        db.execute(
            "INSERT INTO challenges (challenge_id,chain_id,slot_number,start_date,end_date,target_savings_rub,status) VALUES (?,?,?,?,?,?,?)",
            (ch_id, chain_id, sd["slot"], f"{entry.year}-{entry.month:02d}-{s:02d}", f"{entry.year}-{entry.month:02d}-{e:02d}", target_savings, status),
        )

        cat_code = selected[i % len(selected)]
        forecast_median = cat_forecasts.get(str(cat_code), 5000)
        limit_rub = round(forecast_median * (1 - pct / 100) * (dur / 30))
        saving_rub = round(target_savings * 0.7)

        tmpl = TASK_TEMPLATES[i % len(TASK_TEMPLATES)]
        tmpl_id = tmpl["id"]
        tmpl_action = tmpl["action"]

        action_map = {
            "SPEND": f"Ограничить траты до {limit_rub:,} ₽ (категория {cat_code}) с {s} по {e}",
            "COUNT": f"Не более 3 покупок (категория {cat_code}) с {s} по {e}",
            "LIMIT": f"Не тратить более {limit_rub:,} ₽ (категория {cat_code}) с {s} по {e}",
            "COMBO": f"Сократить траты в 2 категориях одновременно с {s} по {e}",
            "SERIES": f"Серия дней без трат (категория {cat_code}) с {s} по {e}",
        }
        human = action_map.get(tmpl_action, action_map["LIMIT"])
        if is_activation:
            human = f"⚡ ФИНАЛЬНЫЙ РЫВОК! {human}"
        t_id = _gen_id("t")
        db.execute(
            "INSERT INTO tasks (task_id,challenge_id,template_id,action,category_code,target_amount_rub,saving_amount_rub,period_start,period_end,status,human_text) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (t_id, ch_id, tmpl_id, tmpl_action, cat_code, limit_rub, saving_rub,
             f"{entry.year}-{entry.month:02d}-{s:02d}", f"{entry.year}-{entry.month:02d}-{e:02d}",
             "ACTIVE", human),
        )

        tasks = [{
            "task_id": t_id, "template_id": tmpl_id, "action": tmpl_action,
            "category_code": cat_code,
            "target_amount_rub": limit_rub,
            "saving_amount_rub": saving_rub, "status": "ACTIVE",
            "human_text": human,
        }]

        challenges.append({
            "challenge_id": ch_id, "slot_number": sd["slot"],
            "start_date": f"{entry.year}-{entry.month:02d}-{s:02d}",
            "end_date": f"{entry.year}-{entry.month:02d}-{e:02d}",
            "duration_days": dur, "target_savings_rub": target_savings,
            "status": status, "is_activation": is_activation, "tasks": tasks,
            "control_tasks": [],
        })

    for i, ch in enumerate(challenges):
        challenge_id = ch["challenge_id"]
        ch_s = ch["start_date"]
        ch_e = ch["end_date"]
        target = challenges[i].get("target_savings_rub", 0)
        cat_codes = selected
        x_spend = min(round(5000 * 3), round(target * 0.5))
        ct_spend_id = _gen_id("ct")
        db.execute(
            "INSERT INTO control_tasks (control_task_id,challenge_id,type,x_threshold_rub,category_codes_watched,is_visible_to_client,period_start,period_end) VALUES (?,?,?,?,?,?,?,?)",
            (ct_spend_id, challenge_id, "SPEND_ANOMALY", x_spend, json.dumps(cat_codes), 0, ch_s, ch_e),
        )
        x_income = 120000
        ct_income_id = _gen_id("ct")
        db.execute(
            "INSERT INTO control_tasks (control_task_id,challenge_id,type,x_threshold_rub,category_codes_watched,is_visible_to_client,period_start,period_end) VALUES (?,?,?,?,?,?,?,?)",
            (ct_income_id, challenge_id, "INCOME_ANOMALY", x_income, json.dumps(cat_codes), 0, ch_s, ch_e),
        )
        challenges[i]["control_tasks"] = [
            {"control_task_id": ct_spend_id, "type": "SPEND_ANOMALY", "x_threshold_rub": x_spend,
             "category_codes_watched": cat_codes, "is_visible_to_client": False,
             "period_start": ch_s, "period_end": ch_e},
            {"control_task_id": ct_income_id, "type": "INCOME_ANOMALY", "x_threshold_rub": x_income,
             "category_codes_watched": cat_codes, "is_visible_to_client": False,
             "period_start": ch_s, "period_end": ch_e},
        ]

    db.commit()
    db.close()
    return json.dumps({
        "challenge_chain_id": chain_id, "month": month_str,
        "month_end_date": month_end_date, "challenges": challenges,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def saveChallengeChain(challenge_chain_id: str, check_time_local: str = "10:00", timezone_str: str = "Europe/Moscow") -> str:
    """Сохранить цепочку челленджей и зарегистрировать scheduler-jobs: один DAILY_CHECK (ежедневная проверка прогресса), по одному CHALLENGE_END на каждый слот и один MONTH_END на конец месяца."""
    db = _get_db()
    chain = db.execute("SELECT * FROM challenge_chains WHERE chain_id=?", (challenge_chain_id,)).fetchone()
    if not chain:
        db.close()
        return json.dumps({"error": "404"}, ensure_ascii=False)

    jobs = []
    tomorrow = (datetime.now(timezone(timedelta(hours=3))) + timedelta(days=1)).strftime("%Y-%m-%d")

    daily_id = _gen_id("job")
    daily_at = f"{tomorrow}T{check_time_local}:00+03:00"
    db.execute(
        "INSERT INTO scheduler_jobs (job_id,client_id,chain_id,job_type,check_time_local,timezone,scheduled_at,next_run_at,status) VALUES (?,?,?,?,?,?,?,?,?)",
        (daily_id, chain["client_id"], challenge_chain_id, "DAILY_CHECK", check_time_local, timezone_str, daily_at, daily_at, "pending"),
    )
    jobs.append({"job_id": daily_id, "type": "DAILY_CHECK", "scheduled_at": daily_at})

    challenges = db.execute("SELECT * FROM challenges WHERE chain_id=?", (challenge_chain_id,)).fetchall()
    for ch in challenges:
        ce_id = _gen_id("job")
        ce_at = f"{ch['end_date']}T23:30:00+03:00"
        db.execute(
            "INSERT INTO scheduler_jobs (job_id,client_id,chain_id,job_type,challenge_id,check_time_local,timezone,scheduled_at,next_run_at,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ce_id, chain["client_id"], challenge_chain_id, "CHALLENGE_END", ch["challenge_id"], "23:30", timezone_str, ce_at, ce_at, "pending"),
        )
        jobs.append({"job_id": ce_id, "type": "CHALLENGE_END", "scheduled_at": ce_at})

    month_end = db.execute("SELECT end_date FROM challenges WHERE chain_id=? ORDER BY slot_number DESC LIMIT 1", (challenge_chain_id,)).fetchone()
    if month_end:
        me_id = _gen_id("job")
        me_at = f"{month_end['end_date']}T23:30:00+03:00"
        db.execute(
            "INSERT INTO scheduler_jobs (job_id,client_id,chain_id,job_type,check_time_local,timezone,scheduled_at,next_run_at,status) VALUES (?,?,?,?,?,?,?,?,?)",
            (me_id, chain["client_id"], challenge_chain_id, "MONTH_END", "23:30", timezone_str, me_at, me_at, "pending"),
        )
        jobs.append({"job_id": me_id, "type": "MONTH_END", "scheduled_at": me_at})

    db.commit()
    db.close()
    return json.dumps({"saved": True, "scheduler_jobs": jobs}, ensure_ascii=False, indent=2)


@mcp.tool()
async def scheduledProgressCheck(scheduler_job_id: str, client_id: str, challenge_chain_id: str,
                                  job_type: str = "DAILY_CHECK") -> str:
    """Проверка прогресса по расписанию. Запрашивает tx-agent для получения фактических трат за период, обновляет прогресс задач, выявляет аномалии. job_type: DAILY_CHECK | CHALLENGE_END | MONTH_END."""
    db = _get_db()
    chain = db.execute("SELECT * FROM challenge_chains WHERE chain_id=?", (challenge_chain_id,)).fetchone()

    challenges = db.execute("SELECT * FROM challenges WHERE chain_id=? AND status='ACTIVE'", (challenge_chain_id,)).fetchall()
    active_ch = challenges[0] if challenges else None

    from_date = active_ch["start_date"] if active_ch else _today()[:8] + "01"
    to_date = min(_today(), active_ch["end_date"]) if active_ch else _today()

    tx_data = await _call_tx_agent("getTransactionsForPeriod", {
        "client_id": client_id, "from_date": from_date, "to_date": to_date,
    })
    fact_by_cat = tx_data.get("fact_by_category", {})
    fact_by_cat_int = {int(k): v for k, v in fact_by_cat.items()}

    updated = []
    completed_challenges = []
    for ch in challenges:
        tasks = db.execute("SELECT * FROM tasks WHERE challenge_id=? AND status='ACTIVE'", (ch["challenge_id"],)).fetchall()
        all_done = True
        for t in tasks:
            target = t["target_amount_rub"]
            cat_code = t["category_code"]
            actual = round(fact_by_cat_int.get(cat_code, 0))
            progress = min(100, round(actual / target * 100)) if target > 0 else 0
            new_status = "COMPLETED" if progress >= 100 else "ACTIVE"
            db.execute("UPDATE tasks SET status=?, actual_amount_rub=?, progress_pct=? WHERE task_id=?",
                       (new_status, actual, progress, t["task_id"]))
            log_id = _gen_id("log")
            db.execute("INSERT INTO progress_log (log_id,task_id,checked_at,old_status,new_status,actual_amount_rub,scheduler_job_id) VALUES (?,?,?,?,?,?,?)",
                       (log_id, t["task_id"], _now_iso(), "ACTIVE", new_status, actual, scheduler_job_id))
            updated.append({"task_id": t["task_id"], "old_status": "ACTIVE", "new_status": new_status,
                           "progress_pct": progress, "actual_amount_rub": actual})
            if new_status != "COMPLETED":
                all_done = False
        if all_done and tasks:
            db.execute("UPDATE challenges SET status='COMPLETED', fact_savings_rub=? WHERE challenge_id=?",
                       (ch["target_savings_rub"], ch["challenge_id"]))
            completed_challenges.append(ch["challenge_id"])
            next_pending = db.execute("SELECT challenge_id FROM challenges WHERE chain_id=? AND status='PENDING' LIMIT 1",
                                      (challenge_chain_id,)).fetchone()
            if next_pending:
                db.execute("UPDATE challenges SET status='ACTIVE' WHERE challenge_id=?", (next_pending["challenge_id"],))

    anomalies_data = []
    ctrl_tasks = db.execute("SELECT * FROM control_tasks WHERE is_triggered=0").fetchall()
    for ct in ctrl_tasks:
        cats_watched = json.loads(ct["category_codes_watched"]) if ct["category_codes_watched"] else []
        for cc in cats_watched:
            actual = fact_by_cat_int.get(cc, 0)
            if ct["type"] == "SPEND_ANOMALY" and actual >= ct["x_threshold_rub"]:
                db.execute("UPDATE control_tasks SET is_triggered=1, trigger_amount_rub=? WHERE control_task_id=?",
                           (actual, ct["control_task_id"]))
                anomalies_data.append({"control_task_id": ct["control_task_id"], "type": "SPEND_ANOMALY",
                                        "trigger_amount_rub": actual, "category_code": cc})
            elif ct["type"] == "INCOME_ANOMALY" and actual >= ct["x_threshold_rub"]:
                db.execute("UPDATE control_tasks SET is_triggered=1, trigger_amount_rub=? WHERE control_task_id=?",
                           (actual, ct["control_task_id"]))
                anomalies_data.append({"control_task_id": ct["control_task_id"], "type": "INCOME_ANOMALY",
                                        "trigger_amount_rub": actual, "category_code": cc})

    db.execute("UPDATE scheduler_jobs SET last_run_at=?, status='succeeded' WHERE job_id=?",
               (_now_iso(), scheduler_job_id))
    db.commit()
    db.close()
    return json.dumps({"checked_at": _now_iso(), "updated_tasks": updated,
                       "completed_challenges": completed_challenges,
                       "anomalies_detected": anomalies_data}, ensure_ascii=False, indent=2)


@mcp.tool()
async def getStatus(client_id: str, include_completed: bool = True) -> str:
    """Текущий статус всех челленджей и заданий клиента. Возвращает активный челлендж, список завершённых, активные задачи, накопленную экономию, информацию о следующей проверке и открытые алерты аномалий."""
    db = _get_db()
    chain = db.execute("SELECT * FROM challenge_chains WHERE client_id=? AND status='ACTIVE'", (client_id,)).fetchone()
    if not chain:
        db.close()
        return json.dumps({"active_challenge": None, "completed_challenges": [], "active_tasks": [], "saved_so_far_rub": 0, "next_check_at": None}, ensure_ascii=False)

    challenges = db.execute("SELECT * FROM challenges WHERE chain_id=?", (chain["chain_id"],)).fetchall()
    result_ch = []
    saved = 0
    for ch in challenges:
        if ch["status"] in ("COMPLETED", "FAILED") and not include_completed:
            continue
        tasks = db.execute("SELECT * FROM tasks WHERE challenge_id=?", (ch["challenge_id"],)).fetchall()
        saved += ch["fact_savings_rub"] or 0
        result_ch.append({
            "challenge_id": ch["challenge_id"], "slot_number": ch["slot_number"],
            "start_date": ch["start_date"], "end_date": ch["end_date"],
            "target_savings_rub": ch["target_savings_rub"], "status": ch["status"],
            "tasks": [dict(t) for t in tasks],
        })

    anomaly = db.execute("SELECT ct.*, ch.start_date, ch.end_date FROM control_tasks ct JOIN challenges ch ON ct.challenge_id=ch.challenge_id JOIN challenge_chains cc ON ch.chain_id=cc.chain_id WHERE cc.client_id=? AND ct.is_triggered=1 AND ct.alert_dismissed_at IS NULL", (client_id,)).fetchone()
    open_anomaly = None
    if anomaly:
        open_anomaly = {"control_task_id": anomaly["control_task_id"], "type": anomaly["type"],
                        "x_threshold_rub": anomaly["x_threshold_rub"], "trigger_amount_rub": anomaly["trigger_amount_rub"]}

    job = db.execute("SELECT next_run_at FROM scheduler_jobs WHERE chain_id=?", (chain["chain_id"],)).fetchone()
    db.close()
    return json.dumps({
        "active_challenge": result_ch[0] if result_ch else None,
        "completed_challenges": [c for c in result_ch if c["status"] == "COMPLETED"],
        "active_tasks": [t for c in result_ch for t in c["tasks"] if t["status"] == "ACTIVE"],
        "saved_so_far_rub": saved,
        "next_check_at": job["next_run_at"] if job else None,
        "open_anomaly_alert": open_anomaly,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def recalculateChain(client_id: str, challenge_chain_id: str,
                           new_selected_categories: list[int] | None = None,
                           new_target_reduction_pct: float | None = None,
                           trigger_source: str = "USER_CALCULATOR") -> str:
    """Пересчитать незапущенные (PENDING) челленджи в цепочке. Завершённые и активные слоты сохраняются, остальные пересоздаются с новыми категориями и процентом экономии. Позволяет пользователю скорректировать план на оставшийся месяц."""
    db = _get_db()
    chain = db.execute("SELECT * FROM challenge_chains WHERE chain_id=?", (challenge_chain_id,)).fetchone()
    if not chain:
        db.close()
        return json.dumps({"error": "404"}, ensure_ascii=False)
    plan = db.execute("SELECT * FROM optimization_plans WHERE plan_id=?", (chain["plan_id"],)).fetchone()
    if not plan:
        db.close()
        return json.dumps({"error": "404"}, ensure_ascii=False)

    selected = new_selected_categories if new_selected_categories else json.loads(plan["selected_categories"])
    pct = new_target_reduction_pct if new_target_reduction_pct else plan["target_reduction_pct"]
    pct = max(5, min(25, pct))
    cat_forecasts = json.loads(plan["category_forecasts"]) if plan["category_forecasts"] else {}

    preserved = []
    rebuilt = []
    challenges = db.execute("SELECT * FROM challenges WHERE chain_id=?", (challenge_chain_id,)).fetchall()
    for ch in challenges:
        if ch["status"] in ("COMPLETED", "ACTIVE"):
            preserved.append({"challenge_id": ch["challenge_id"], "slot_number": ch["slot_number"], "status": ch["status"]})
            continue
        db.execute("DELETE FROM tasks WHERE challenge_id=?", (ch["challenge_id"],))
        db.execute("DELETE FROM control_tasks WHERE challenge_id=?", (ch["challenge_id"],))

        sd = ch["slot_number"]
        s = int(ch["start_date"][-2:])
        e = int(ch["end_date"][-2:])
        dur = e - s + 1
        cat_code = selected[(sd - 1) % len(selected)]
        forecast_median = cat_forecasts.get(str(cat_code), 5000)
        limit_rub = round(forecast_median * (1 - pct / 100) * (dur / 30))
        target_savings = round(ch["target_savings_rub"])
        saving_rub = round(target_savings * 0.7)
        tmpl = TASK_TEMPLATES[(sd - 1) % len(TASK_TEMPLATES)]

        human = f"Не тратить более {limit_rub:,} ₽ (категория {cat_code})"
        t_id = _gen_id("t")
        db.execute("INSERT INTO tasks (task_id,challenge_id,template_id,action,category_code,target_amount_rub,saving_amount_rub,period_start,period_end,status,human_text) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (t_id, ch["challenge_id"], tmpl["id"], tmpl["action"], cat_code, limit_rub, saving_rub,
                    ch["start_date"], ch["end_date"], "PENDING", human))
        rebuilt.append({"challenge_id": ch["challenge_id"], "slot_number": sd, "status": "rebuilt"})

    db.commit()
    db.close()
    return json.dumps({"recalculated_at": _now_iso(), "preserved_challenges": preserved,
                       "rebuilt_challenges": rebuilt, "control_tasks_recalculated": True,
                       "alert_dismissed": trigger_source != "ANOMALY"}, ensure_ascii=False, indent=2)


@mcp.tool()
async def acknowledgeAnomaly(control_task_id: str) -> str:
    """Закрыть алерт аномалии без пересчёта цепочки челленджей. Помечает control_task как dismissed, после чего getStatus перестаёт показывать его в open_anomaly_alert."""
    db = _get_db()
    db.execute("UPDATE control_tasks SET alert_dismissed_at=? WHERE control_task_id=?", (_now_iso(), control_task_id))
    db.commit()
    db.close()
    return json.dumps({"alert_dismissed": True, "dismissed_at": _now_iso()}, ensure_ascii=False, indent=2)


@mcp.tool()
async def buildChallengeDigest(client_id: str, challenge_id: str) -> str:
    """Собрать дайджест завершённого челленджа. Возвращает количество выполненных задач, фактическую экономию и признак успеха."""
    db = _get_db()
    ch = db.execute("SELECT * FROM challenges WHERE challenge_id=?", (challenge_id,)).fetchone()
    if not ch:
        db.close()
        return json.dumps({"error": "404"}, ensure_ascii=False)
    tasks = db.execute("SELECT * FROM tasks WHERE challenge_id=?", (challenge_id,)).fetchall()
    tasks_done = sum(1 for t in tasks if t["status"] == "COMPLETED")
    is_success = ch["status"] == "COMPLETED"
    db.close()
    return json.dumps({"digest_id": _gen_id("dig"), "tasks_done": f"{tasks_done} из {len(tasks)}",
                       "challenge_fact_savings_rub": ch["fact_savings_rub"] or 0,
                       "is_success": is_success}, ensure_ascii=False, indent=2)


@mcp.tool()
async def buildPredictedChain(client_id: str, next_month: str) -> str:
    """Создать предсказанную цепочку челленджей на следующий месяц (YYYY-MM) на основе последнего подтверждённого плана оптимизации. Цепочка создаётся в статусе pending_activation и автоматически активируется 1 числа месяца."""
    db = _get_db()
    plan = db.execute("SELECT * FROM optimization_plans WHERE client_id=? ORDER BY confirmed_at DESC LIMIT 1", (client_id,)).fetchone()
    if not plan:
        db.close()
        return json.dumps({"error": "404", "message": "Нет подтверждённого плана"}, ensure_ascii=False)
    existing = db.execute("SELECT chain_id FROM challenge_chains WHERE client_id=? AND month=?", (client_id, next_month)).fetchone()
    if existing:
        db.close()
        return json.dumps({"error": "409", "message": "Цепочка на этот месяц уже есть"}, ensure_ascii=False)

    predicted_id = _gen_id("pchain")
    auto_date = f"{next_month}-01"
    db.execute("INSERT INTO challenge_chains (chain_id,client_id,plan_id,month,status,created_at) VALUES (?,?,?,?,?,?)",
               (predicted_id, client_id, plan["plan_id"], next_month, "pending_activation", _now_iso()))
    db.commit()
    db.close()
    return json.dumps({"predicted_chain_id": predicted_id, "status": "pending_activation",
                       "auto_activation_date": auto_date}, ensure_ascii=False, indent=2)


_init_db()

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
