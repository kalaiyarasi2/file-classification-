"""
monitor_db.py - Centralized SQLite monitoring database for all three agents.

Tracks:
  - pipeline_runs       → each start_flow.py execution cycle
  - email_events        → Outlook/Gmail email fetch results
  - file_events         → per-file classification results (Classifier Agent)
  - gpu_jobs            → GPU extraction job results (Gpu_server)
  - agent_heartbeats    → live health snapshots for each agent

Database location: .sessions/monitor.db  (beside pipeline_process.json)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── DB Path ───────────────────────────────────────────────────────────────────
DB_DIR  = Path(__file__).parent / ".sessions"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "monitor.db"

_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────
_SCHEMA = """
-- ── Pipeline Runs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    UNIQUE NOT NULL,          -- uuid
    provider        TEXT    NOT NULL,                 -- 'outlook' | 'gmail'
    status          TEXT    NOT NULL DEFAULT 'running',  -- running | completed | stopped | error
    started_at      REAL    NOT NULL,                 -- Unix timestamp
    ended_at        REAL,
    emails_found    INTEGER DEFAULT 0,
    attachments     INTEGER DEFAULT 0,
    files_classified INTEGER DEFAULT 0,
    files_others    INTEGER DEFAULT 0,
    files_gpu       INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON pipeline_runs(started_at);

-- ── Email Events ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    ts              REAL    NOT NULL,
    provider        TEXT,                             -- 'outlook' | 'gmail'
    email_subject   TEXT,
    sender          TEXT,
    attachment_name TEXT,
    action          TEXT,                             -- 'fetched' | 'skipped_dup' | 'no_pdf' | 'downloaded' | 'error'
    detail          TEXT,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_email_run ON email_events(run_id);
CREATE INDEX IF NOT EXISTS idx_email_ts  ON email_events(ts);

-- ── File Events (Classifier) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS file_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    ts              REAL    NOT NULL,
    filename        TEXT    NOT NULL,
    file_size       INTEGER,
    pdf_type        TEXT,                             -- 'digital' | 'scanned'
    category        TEXT,                             -- winning category or 'Others'
    score           REAL,
    processing_ms   INTEGER,
    sent_to_gpu     INTEGER DEFAULT 0,                -- 1 = yes, 0 = no (Others)
    error           TEXT,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_file_run      ON file_events(run_id);
CREATE INDEX IF NOT EXISTS idx_file_category ON file_events(category);

-- ── GPU Jobs ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gpu_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    ts              REAL    NOT NULL,
    filename        TEXT    NOT NULL,
    status          TEXT,                             -- 'pending'|'processing'|'completed'|'failed'
    ocr_layer       TEXT,                             -- 'gpu_ocr'|'tesseract'|'vision'|'digital'
    processing_ms   INTEGER,
    output_file     TEXT,
    math_verified   INTEGER,                          -- 1=pass, 0=fail, NULL=n/a
    error           TEXT,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_gpu_run    ON gpu_jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_gpu_status ON gpu_jobs(status);

-- ── Agent Heartbeats ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_heartbeats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL,
    agent           TEXT    NOT NULL,                 -- 'classifier'|'outlook'|'gpu'
    status          TEXT    NOT NULL,                 -- 'online'|'offline'|'error'
    pid             INTEGER,
    detail          TEXT                              -- JSON blob of extra info
);
CREATE INDEX IF NOT EXISTS idx_hb_agent ON agent_heartbeats(agent);
CREATE INDEX IF NOT EXISTS idx_hb_ts    ON agent_heartbeats(ts);
"""

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads + writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _now() -> float:
    return time.time()

def _dt(ts: Optional[float]) -> Optional[str]:
    """Unix float → ISO string for display."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    """Create all tables. Safe to call on every startup."""
    with _lock:
        conn = _connect()
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

# Run schema on import
init_db()

# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Runs
# ─────────────────────────────────────────────────────────────────────────────
def run_start(run_id: str, provider: str) -> None:
    """Record that a new pipeline run has started."""
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_runs (run_id, provider, status, started_at) "
            "VALUES (?, ?, 'running', ?)",
            (run_id, provider, _now())
        )
        conn.commit()
        conn.close()

def run_update(run_id: str, **kwargs) -> None:
    """Update counters / status on an existing run. Accepts any column kwarg."""
    allowed = {"status", "ended_at", "emails_found", "attachments",
               "files_classified", "files_others", "files_gpu", "errors", "notes"}
    cols = {k: v for k, v in kwargs.items() if k in allowed}
    if not cols:
        return
    sql  = "UPDATE pipeline_runs SET " + ", ".join(f"{k}=?" for k in cols)
    sql += " WHERE run_id=?"
    vals = list(cols.values()) + [run_id]
    with _lock:
        conn = _connect()
        conn.execute(sql, vals)
        conn.commit()
        conn.close()

def run_finish(run_id: str, status: str = "completed", **kwargs) -> None:
    """Mark a pipeline run as finished."""
    kwargs["status"]   = status
    kwargs["ended_at"] = _now()
    run_update(run_id, **kwargs)

def get_runs(limit: int = 20) -> List[Dict]:
    """Return the most recent pipeline runs."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_active_run() -> Optional[Dict]:
    """Return the currently running pipeline run, if any."""
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM pipeline_runs WHERE status='running' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None

# ─────────────────────────────────────────────────────────────────────────────
# Email Events
# ─────────────────────────────────────────────────────────────────────────────
def log_email(
    run_id: str,
    action: str,
    provider: str = None,
    email_subject: str = None,
    sender: str = None,
    attachment_name: str = None,
    detail: str = None,
) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO email_events "
            "(run_id, ts, provider, email_subject, sender, attachment_name, action, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, _now(), provider, email_subject, sender, attachment_name, action, detail)
        )
        conn.commit()
        conn.close()

def get_email_events(run_id: str = None, limit: int = 50) -> List[Dict]:
    conn = _connect()
    if run_id:
        rows = conn.execute(
            "SELECT * FROM email_events WHERE run_id=? ORDER BY ts DESC LIMIT ?",
            (run_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# File Events (Classifier)
# ─────────────────────────────────────────────────────────────────────────────
def log_file(
    run_id: str,
    filename: str,
    category: str,
    pdf_type: str = None,
    score: float = None,
    file_size: int = None,
    processing_ms: int = None,
    sent_to_gpu: bool = False,
    error: str = None,
) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO file_events "
            "(run_id, ts, filename, file_size, pdf_type, category, score, "
            " processing_ms, sent_to_gpu, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, _now(), filename, file_size, pdf_type, category, score,
             processing_ms, 1 if sent_to_gpu else 0, error)
        )
        conn.commit()
        conn.close()

def get_file_events(run_id: str = None, limit: int = 100) -> List[Dict]:
    conn = _connect()
    if run_id:
        rows = conn.execute(
            "SELECT * FROM file_events WHERE run_id=? ORDER BY ts DESC LIMIT ?",
            (run_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM file_events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_category_stats(run_id: str = None) -> List[Dict]:
    """Count files per category, optionally filtered to a run."""
    conn = _connect()
    q = "SELECT category, COUNT(*) as count, AVG(score) as avg_score FROM file_events"
    if run_id:
        q += f" WHERE run_id='{run_id}'"
    q += " GROUP BY category ORDER BY count DESC"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# GPU Jobs
# ─────────────────────────────────────────────────────────────────────────────
def log_gpu_job(
    run_id: str,
    filename: str,
    status: str,
    ocr_layer: str = None,
    processing_ms: int = None,
    output_file: str = None,
    math_verified: Optional[bool] = None,
    error: str = None,
) -> None:
    math_val = None if math_verified is None else (1 if math_verified else 0)
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO gpu_jobs "
            "(run_id, ts, filename, status, ocr_layer, processing_ms, "
            " output_file, math_verified, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, _now(), filename, status, ocr_layer, processing_ms,
             output_file, math_val, error)
        )
        conn.commit()
        conn.close()

def get_gpu_jobs(run_id: str = None, limit: int = 100) -> List[Dict]:
    conn = _connect()
    if run_id:
        rows = conn.execute(
            "SELECT * FROM gpu_jobs WHERE run_id=? ORDER BY ts DESC LIMIT ?",
            (run_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM gpu_jobs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# Agent Heartbeats
# ─────────────────────────────────────────────────────────────────────────────
def heartbeat(agent: str, status: str, pid: int = None, **extra) -> None:
    """Record a health snapshot for an agent. Call every ~30s from the pipeline."""
    detail = json.dumps(extra) if extra else None
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO agent_heartbeats (ts, agent, status, pid, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now(), agent, status, pid, detail)
        )
        conn.commit()
        conn.close()

def get_latest_heartbeats() -> Dict[str, Dict]:
    """Return the most recent heartbeat for each agent."""
    conn = _connect()
    rows = conn.execute(
        "SELECT h.* FROM agent_heartbeats h "
        "INNER JOIN ("
        "  SELECT agent, MAX(ts) AS max_ts FROM agent_heartbeats GROUP BY agent"
        ") latest ON h.agent = latest.agent AND h.ts = latest.max_ts"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        d = dict(r)
        d["ts_str"] = _dt(d["ts"])
        d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
        result[d["agent"]] = d
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Summary
# ─────────────────────────────────────────────────────────────────────────────
def get_dashboard_summary() -> Dict[str, Any]:
    """Single call to get everything the dashboard needs."""
    conn = _connect()

    # Active run
    active = conn.execute(
        "SELECT * FROM pipeline_runs WHERE status='running' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    # Last 5 runs
    recent_runs = conn.execute(
        "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 5"
    ).fetchall()

    # Today's totals
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).timestamp()
    totals = conn.execute(
        "SELECT "
        "  SUM(emails_found) as emails, "
        "  SUM(attachments)  as attachments, "
        "  SUM(files_classified) as classified, "
        "  SUM(files_others) as others, "
        "  SUM(files_gpu)    as gpu_jobs, "
        "  SUM(errors)       as errors "
        "FROM pipeline_runs WHERE started_at >= ?",
        (today_start,)
    ).fetchone()

    # Category breakdown (today)
    cats = conn.execute(
        "SELECT fe.category, COUNT(*) as count "
        "FROM file_events fe "
        "JOIN pipeline_runs pr ON fe.run_id = pr.run_id "
        "WHERE pr.started_at >= ? "
        "GROUP BY fe.category ORDER BY count DESC",
        (today_start,)
    ).fetchall()

    # GPU success rate (today)
    gpu_stats = conn.execute(
        "SELECT "
        "  COUNT(*) as total, "
        "  SUM(CASE WHEN gj.status='completed' THEN 1 ELSE 0 END) as success, "
        "  SUM(CASE WHEN gj.status='failed'    THEN 1 ELSE 0 END) as failed "
        "FROM gpu_jobs gj "
        "JOIN pipeline_runs pr ON gj.run_id = pr.run_id "
        "WHERE pr.started_at >= ?",
        (today_start,)
    ).fetchone()

    conn.close()

    return {
        "active_run":   dict(active)  if active  else None,
        "recent_runs":  [dict(r) for r in recent_runs],
        "today_totals": dict(totals)  if totals  else {},
        "categories":   [dict(c) for c in cats],
        "gpu_stats":    dict(gpu_stats) if gpu_stats else {},
        "heartbeats":   get_latest_heartbeats(),
    }

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────
def cleanup(days: int = 30) -> int:
    """Delete records older than N days. Returns rows deleted."""
    cutoff = _now() - days * 86400
    with _lock:
        conn = _connect()
        old_runs = conn.execute(
            "SELECT run_id FROM pipeline_runs WHERE started_at < ?", (cutoff,)
        ).fetchall()
        run_ids = [r[0] for r in old_runs]
        deleted = 0
        if run_ids:
            placeholders = ",".join("?" * len(run_ids))
            for tbl in ("email_events", "file_events", "gpu_jobs"):
                cur = conn.execute(f"DELETE FROM {tbl} WHERE run_id IN ({placeholders})", run_ids)
                deleted += cur.rowcount
            cur = conn.execute(f"DELETE FROM pipeline_runs WHERE run_id IN ({placeholders})", run_ids)
            deleted += cur.rowcount
        # also purge old heartbeats
        cur = conn.execute("DELETE FROM agent_heartbeats WHERE ts < ?", (cutoff,))
        deleted += cur.rowcount
        conn.commit()
        conn.close()
    return deleted
