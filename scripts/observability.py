#!/usr/bin/env python3
"""Observability: structured logging, health reports, circuit breaker."""

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from .init_db import get_db


@dataclass
class CircuitState:
    """Per-source circuit breaker state."""
    source: str
    error_count: int = 0
    total_count: int = 0
    tripped_at: float | None = None
    cooldown_sec: int = 3600

    @property
    def is_open(self) -> bool:
        if self.tripped_at is None:
            return False
        return (time.time() - self.tripped_at) < self.cooldown_sec

    @property
    def error_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.error_count / self.total_count

    def record_success(self) -> None:
        self.total_count += 1

    def record_error(self, threshold: float = 0.05) -> None:
        self.total_count += 1
        self.error_count += 1
        if self.total_count >= 20 and self.error_rate > threshold:
            self.tripped_at = time.time()

    def reset(self) -> None:
        self.error_count = 0
        self.total_count = 0
        self.tripped_at = None


# Global circuit breakers per source
_circuits: dict[str, CircuitState] = {}


def get_circuit(source: str, cooldown_sec: int = 3600) -> CircuitState:
    if source not in _circuits:
        _circuits[source] = CircuitState(source=source, cooldown_sec=cooldown_sec)
    return _circuits[source]


def log_op(
    conn: sqlite3.Connection,
    source: str,
    operation: str,
    status: str,
    latency_ms: int = 0,
    tokens_used: int | None = None,
    detail: str | None = None,
) -> None:
    """Write a structured log entry to op_log."""
    conn.execute(
        """INSERT INTO op_log (source, operation, status, latency_ms, tokens_used, detail)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (source, operation, status, latency_ms, tokens_used, detail),
    )
    conn.commit()


@contextmanager
def timed_op(conn: sqlite3.Connection, source: str, operation: str):
    """Context manager that times an operation and logs it.

    Usage:
        with timed_op(conn, "s2", "fetch_embedding") as op:
            result = call_api(...)
            op["tokens"] = 0
            op["detail"] = "fetched 10 papers"
    """
    op: dict = {"tokens": None, "detail": None}
    start = time.time()
    try:
        yield op
        elapsed = int((time.time() - start) * 1000)
        circuit = get_circuit(source)
        circuit.record_success()
        log_op(conn, source, operation, "ok", elapsed, op["tokens"], op["detail"])
    except TimeoutError:
        elapsed = int((time.time() - start) * 1000)
        circuit = get_circuit(source)
        circuit.record_error()
        log_op(conn, source, operation, "timeout", elapsed, detail=str(op.get("detail")))
        raise
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        circuit = get_circuit(source)
        circuit.record_error()
        log_op(conn, source, operation, "error", elapsed, detail=str(e))
        raise


def generate_health_report(conn: sqlite3.Connection, date: str | None = None) -> str:
    """Generate daily health report text.

    Args:
        date: ISO date string (YYYY-MM-DD). Defaults to today.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # Paper scan stats
    papers_today = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE created_at LIKE ?", (f"{date}%",)
    ).fetchone()[0]

    merged_today = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE updated_at LIKE ? AND created_at NOT LIKE ?",
        (f"{date}%", f"{date}%"),
    ).fetchone()[0]

    # Push stats
    pushes_today = conn.execute(
        "SELECT COUNT(*) FROM pushes WHERE pushed_at LIKE ?", (f"{date}%",)
    ).fetchone()[0]

    # Interaction stats
    saves = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE action='save' AND created_at LIKE ?",
        (f"{date}%",),
    ).fetchone()[0]
    mutes = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE action='mute' AND created_at LIKE ?",
        (f"{date}%",),
    ).fetchone()[0]
    clicks = conn.execute(
        "SELECT COUNT(*) FROM interactions WHERE action='click' AND created_at LIKE ?",
        (f"{date}%",),
    ).fetchone()[0]

    # API error stats
    api_stats = conn.execute(
        """SELECT source,
                  COUNT(*) as total,
                  SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) as errors
           FROM op_log
           WHERE ts LIKE ?
           GROUP BY source""",
        (f"{date}%",),
    ).fetchall()

    # LLM token stats
    token_row = conn.execute(
        "SELECT COALESCE(SUM(tokens_used), 0) FROM op_log WHERE ts LIKE ? AND source = 'llm'",
        (f"{date}%",),
    ).fetchone()
    total_tokens = token_row[0]

    # Latency stats
    latency_stats = conn.execute(
        """SELECT operation, AVG(latency_ms), MAX(latency_ms)
           FROM op_log WHERE ts LIKE ? AND status = 'ok'
           GROUP BY operation""",
        (f"{date}%",),
    ).fetchall()

    # Format report
    lines = [
        f"📊 LitBot Daily Health — {date}",
        "━" * 40,
        f"Papers scanned: {papers_today} (merged: {merged_today})",
        f"Papers pushed: {pushes_today} (saves: {saves}, mutes: {mutes}, clicks: {clicks})",
    ]

    if api_stats:
        api_line = ", ".join(
            f"{src} {errs}/{tot}" for src, tot, errs in api_stats
        )
        lines.append(f"API calls (errors/total): {api_line}")

    lines.append(f"LLM tokens: {total_tokens:,}")

    if latency_stats:
        lat_line = ", ".join(
            f"{op}: avg {int(avg)}ms max {int(mx)}ms" for op, avg, mx in latency_stats
        )
        lines.append(f"Latency: {lat_line}")

    # Circuit breaker status
    for source, circuit in _circuits.items():
        if circuit.is_open:
            lines.append(f"⚠️ CIRCUIT OPEN: {source} (error rate: {circuit.error_rate:.1%})")

    return "\n".join(lines)
