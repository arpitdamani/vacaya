"""Plan history in a local SQLite file. Ephemeral inside the Hugging Face Spaces container (resets on rebuild) - fine for a demo."""
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).with_name("plans.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS plans (id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "title TEXT, request TEXT, itinerary TEXT, total REAL, currency TEXT)"
    )
    return c


def save(title: str, request: dict, itinerary: str, total: float, currency: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO plans (title, request, itinerary, total, currency) VALUES (?, ?, ?, ?, ?)",
            (title, json.dumps(request, default=str), itinerary, total, currency),
        )
        return cur.lastrowid


def list_plans() -> list[tuple]:
    with _conn() as c:
        return c.execute("SELECT id, created_at, title, total, currency FROM plans ORDER BY id DESC").fetchall()


def load(plan_id: int) -> str:
    with _conn() as c:
        return c.execute("SELECT itinerary FROM plans WHERE id = ?", (plan_id,)).fetchone()[0]
