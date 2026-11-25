from collections import deque
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time

from .protocol import dumps


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db_lock = Lock()
        self._state_lock = Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS samples (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, t REAL NOT NULL, body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS samples_kind_time ON samples(kind, t);
            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, t REAL NOT NULL, body TEXT NOT NULL
            );
        """)
        self._latest = {}
        for kind in ("motion", "heart", "sleep"):
            row = self._db.execute(
                "SELECT body FROM samples WHERE kind=? ORDER BY t DESC LIMIT 1", (kind,)
            ).fetchone()
            if row:
                self._latest[kind] = json.loads(row[0])
        self._pending = deque(maxlen=4096)
        self._received_at = None

    def append(self, samples, now):
        added = []
        with self._db_lock, self._db:
            for sample in samples:
                inserted = self._db.execute("""
                    INSERT INTO samples VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING
                """, (sample["id"], sample["kind"], sample["t"], dumps(sample)))
                if inserted.rowcount:
                    added.append(sample)
        with self._state_lock:
            for sample in added:
                current = self._latest.get(sample["kind"])
                if current is None or sample["t"] > current["t"]:
                    self._latest[sample["kind"]] = sample
            self._pending.extend(added)
            self._received_at = now
        return len(added)

    def take(self, limit=96):
        with self._state_lock:
            return {
                "v": 1, "latest": self._latest.copy(),
                "samples": [self._pending.popleft() for _ in range(min(limit, len(self._pending)))],
            }

    def record_action(self, command, status, detail=""):
        event = {**command, "status": status, "detail": detail, "result_t": time.time()}
        with self._db_lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO actions VALUES (?, ?, ?)",
                             (command["id"], event["result_t"], dumps(event)))

    def status(self, now, goal_hours):
        with self._state_lock:
            latest, last_received = self._latest.copy(), self._received_at
        with self._db_lock:
            stages = self._db.execute(
                "SELECT body FROM samples WHERE kind='sleep' AND t>? AND t<=?",
                (now - 86_400, now + 5),
            ).fetchall()
            actions = self._db.execute("SELECT body FROM actions ORDER BY t DESC LIMIT 12").fetchall()
        return {
            "v": 1, "now": now, "last_received": last_received, "latest": latest,
            "sleep": sleep_summary([json.loads(row[0]) for row in stages], now, goal_hours),
            "actions": [json.loads(row[0]) for row in actions],
        }

    def close(self):
        self._db.close()


def sleep_summary(stages, now, goal_hours):
    start = now - 86_400
    asleep = sorted((max(start, row["start"]), min(now, row["t"]))
                    for row in stages if row["stage"] in {1, 3, 4, 5} and row["start"] < now)
    end, duration = start, 0.0
    for left, right in asleep:
        duration += max(0, right - max(end, left))
        end = max(end, right)
    priority = "none" if not duration else "focus" if duration >= goal_hours * 3600 else "light"
    return {"hours": round(duration / 3600, 2) if duration else None,
            "goal_hours": goal_hours, "priority": priority}
