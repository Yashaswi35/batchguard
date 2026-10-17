"""Append-only audit trail in SQLite. Database triggers reject UPDATE and DELETE on audit tables."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ts TEXT NOT NULL, node TEXT NOT NULL,
  input_hash TEXT, output_json TEXT, model TEXT, prompt_version TEXT);
CREATE TABLE IF NOT EXISTS investigations (
  run_id TEXT PRIMARY KEY, batch_id INTEGER, status TEXT, result_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, reviewer TEXT NOT NULL,
  decision TEXT NOT NULL, comment TEXT, ts TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals
  BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
  BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditLog:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.executescript(SCHEMA)

    def log(self, run_id, node, inputs, outputs, model=None, prompt_version=None):
        h = hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]
        self.con.execute("INSERT INTO audit_log (run_id, ts, node, input_hash, output_json, model, prompt_version) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (run_id, now(), node, h, json.dumps(outputs, default=str), model, prompt_version))
        self.con.commit()

    def save_investigation(self, inv):
        self.con.execute("INSERT INTO investigations VALUES (?,?,?,?,?)",
                         (inv.run_id, inv.batch_id, inv.status, inv.model_dump_json(), inv.created_at))
        self.con.commit()

    def get_investigation(self, run_id):
        r = self.con.execute("SELECT result_json FROM investigations WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(r[0]) if r else None

    def trail(self, run_id):
        cur = self.con.execute("SELECT ts, node, input_hash, output_json, model, prompt_version FROM audit_log "
                               "WHERE run_id=? ORDER BY id", (run_id,))
        return [dict(ts=t, node=n, input_hash=h, output=json.loads(o), model=m, prompt_version=p)
                for t, n, h, o, m, p in cur.fetchall()]

    def approve(self, run_id, reviewer, decision, comment):
        self.con.execute("INSERT INTO approvals (run_id, reviewer, decision, comment, ts) VALUES (?,?,?,?,?)",
                         (run_id, reviewer, decision, comment, now()))
        self.con.commit()

    def approvals(self, run_id):
        cur = self.con.execute("SELECT reviewer, decision, comment, ts FROM approvals WHERE run_id=? ORDER BY id",
                               (run_id,))
        return [dict(reviewer=r, decision=d, comment=c, ts=t) for r, d, c, t in cur.fetchall()]
