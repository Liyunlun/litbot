#!/usr/bin/env python3
"""F3 Trend Burst Detection — identifies concepts experiencing abnormal
publication spikes using z-score analysis over a rolling 30-day window.

Workflow:
    1. ``update_trend_stats`` — ingest today's papers and increment
       per-concept counts in the ``trend_stats`` table.
    2. ``detect_bursts`` — for each concept in today's data, compute
       a z-score against its 30-day history; flag those exceeding the
       threshold.
    3. ``build_trend_summary_prompt`` — format detected bursts into an
       LLM prompt for natural-language trend explanation.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta

from .init_db import get_db
from .observability import log_op


# ---------------------------------------------------------------------------
# Trend stat ingestion
# ---------------------------------------------------------------------------


def update_trend_stats(conn: sqlite3.Connection, papers: list[dict]) -> None:
    """Update concept counts in ``trend_stats`` for today.

    For each paper dict, the ``"concepts"`` key is expected to hold a
    list of concept strings (e.g. ``["attention", "RLHF"]``).  Each
    concept's count for today's date is atomically incremented.

    Args:
        conn:    Database connection.
        papers:  List of paper dicts, each containing at least a
                 ``"concepts"`` key with ``list[str]`` values.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    for paper in papers:
        concepts = paper.get("concepts")
        if not concepts:
            continue
        # Handle both list and JSON-string formats
        if isinstance(concepts, str):
            try:
                concepts = json.loads(concepts)
            except (json.JSONDecodeError, TypeError):
                continue

        for concept in concepts:
            concept = concept.strip().lower()
            if not concept:
                continue
            conn.execute(
                """
                INSERT INTO trend_stats (concept, date, count)
                VALUES (?, ?, 1)
                ON CONFLICT(concept, date)
                DO UPDATE SET count = count + 1
                """,
                (concept, today),
            )

    conn.commit()


# ---------------------------------------------------------------------------
# Burst detection
# ---------------------------------------------------------------------------


def detect_bursts(
    conn: sqlite3.Connection,
    date: str | None = None,
    z_threshold: float = 3.0,
) -> list[dict]:
    """Detect concepts whose publication count today is a statistical
    outlier relative to the preceding 30 days.

    For each concept present in today's ``trend_stats``:

    1. Query the last 30 days of daily counts (excluding today).
    2. Compute the rolling mean and standard deviation.
    3. Calculate ``z = (today_count - mean) / std``.
    4. If ``z >= z_threshold``, flag the concept as a burst.

    When ``std == 0`` (constant history), any increase over the mean
    triggers a burst with ``z = inf``.

    Args:
        conn:         Database connection.
        date:         ISO date string (YYYY-MM-DD).  Defaults to today.
        z_threshold:  Minimum z-score to qualify as a burst (default 3.0).

    Returns:
        List of burst dicts sorted by ``z_score`` descending::

            {
                "concept": str,
                "z_score": float,
                "today_count": int,
                "avg_30d": float,
            }
    """
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")

    # Date range for the 30-day lookback (excluding today)
    end_date = datetime.strptime(date, "%Y-%m-%d")
    start_date = end_date - timedelta(days=30)
    start_str = start_date.strftime("%Y-%m-%d")

    # Fetch today's concept counts
    today_rows = conn.execute(
        "SELECT concept, count FROM trend_stats WHERE date = ?",
        (date,),
    ).fetchall()

    if not today_rows:
        return []

    bursts: list[dict] = []

    for concept, today_count in today_rows:
        # Fetch last 30 days of history for this concept (excluding today)
        history_rows = conn.execute(
            """
            SELECT count FROM trend_stats
            WHERE concept = ?
              AND date >= ?
              AND date < ?
            ORDER BY date
            """,
            (concept, start_str, date),
        ).fetchall()

        counts = [row[0] for row in history_rows]

        if not counts:
            # No history at all — treat any occurrence as a burst
            bursts.append({
                "concept": concept,
                "z_score": float("inf"),
                "today_count": today_count,
                "avg_30d": 0.0,
            })
            continue

        n = len(counts)
        mean = sum(counts) / n
        variance = sum((c - mean) ** 2 for c in counts) / n
        std = math.sqrt(variance)

        if std == 0.0:
            # Constant history — any increase above mean is a burst
            if today_count > mean:
                z_score = float("inf")
            else:
                continue
        else:
            z_score = (today_count - mean) / std

        if z_score >= z_threshold:
            bursts.append({
                "concept": concept,
                "z_score": round(z_score, 4) if math.isfinite(z_score) else float("inf"),
                "today_count": today_count,
                "avg_30d": round(mean, 2),
            })

    # Sort by z_score descending (inf first)
    bursts.sort(key=lambda b: b["z_score"], reverse=True)
    return bursts


# ---------------------------------------------------------------------------
# LLM prompt builder
# ---------------------------------------------------------------------------


def build_trend_summary_prompt(bursts: list[dict]) -> str:
    """Build a prompt asking an LLM to summarise why the detected concepts
    are trending.

    The prompt lists each burst concept with its z-score and today's
    count, and asks for a concise natural-language summary suitable for
    a daily research briefing.

    Args:
        bursts: Output of :func:`detect_bursts`.

    Returns:
        A prompt string ready to be passed to an LLM.
    """
    if not bursts:
        return (
            "No trending research concepts were detected today. "
            "Respond with a short note confirming that no unusual activity was found."
        )

    lines: list[str] = []
    for b in bursts:
        z_str = "inf" if math.isinf(b["z_score"]) else f"{b['z_score']:.2f}"
        lines.append(
            f"  - {b['concept']}: z-score = {z_str}, "
            f"today = {b['today_count']}, "
            f"30-day avg = {b['avg_30d']:.1f}"
        )
    concept_lines = "\n".join(lines)

    return (
        "You are a research trend analyst.  The following concepts have been "
        "detected as statistical outliers in today's publication data compared "
        "to their 30-day rolling average.\n\n"
        f"## Trending Concepts\n"
        f"{concept_lines}\n\n"
        "For each concept, briefly explain:\n"
        "1. Why it might be trending (recent events, conferences, breakthroughs).\n"
        "2. What this means for researchers in related fields.\n\n"
        "Keep the summary concise (2-4 sentences per concept) and suitable for "
        "a daily research briefing."
    )
