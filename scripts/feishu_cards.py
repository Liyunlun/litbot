#!/usr/bin/env python3
"""Feishu (Lark) Interactive Message Card builder and callback handler for LitBot.

Builds v2 interactive cards for daily digest (F1), collision alerts (F2),
and health reports.  Handles button callback verification and idempotent
action recording.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import urllib.parse

from .paper_identity import PaperRecord, record_interaction
from .collision import AlertLevel
from .init_db import get_db
from .observability import log_op


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def shorten_authors(authors: list[str], max_display: int = 3) -> str:
    """Format an author list, truncating with 'et al.' if needed."""
    if not authors:
        return "Unknown"
    if len(authors) <= max_display:
        return ", ".join(authors)
    return ", ".join(authors[:max_display]) + " et al."


def paper_url(paper: PaperRecord) -> str:
    """Best available URL for a paper."""
    if paper.doi:
        return f"https://doi.org/{paper.doi}"
    if paper.arxiv_id:
        return f"https://arxiv.org/abs/{paper.arxiv_id}"
    if paper.s2_id:
        return f"https://www.semanticscholar.org/paper/{paper.s2_id}"
    # Fallback: Google Scholar title search
    query = urllib.parse.quote_plus(paper.title)
    return f"https://scholar.google.com/scholar?q={query}"


def _is_bootstrap_mode(conn: sqlite3.Connection, user_id: str = "default") -> bool:
    """Check whether the user is still in bootstrap mode."""
    row = conn.execute(
        "SELECT mode FROM bootstrap_state WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is not None and row[0] == "active"


# ---------------------------------------------------------------------------
# Card Builders
# ---------------------------------------------------------------------------


def _action_button(text: str, value: str) -> dict:
    """Create a single Feishu action button element."""
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "default",
        "value": {"action": value},
    }


def build_daily_digest_card(
    papers: list[tuple[PaperRecord, float, str]],
    date: str,
    total_scanned: int,
    total_matched: int,
    trend_bursts: list[dict] | None = None,
    language: str = "zh",
) -> dict:
    """Build a Feishu v2 interactive card for the daily paper digest (F1).

    Args:
        papers:         List of (PaperRecord, relevance_score, one_line_recommendation).
        date:           ISO date string shown in the header.
        total_scanned:  Total papers scanned today.
        total_matched:  Papers that passed the ranking threshold.
        trend_bursts:   Optional list of trending-concept dicts from the trend module.
        language:       Display language hint (currently unused, reserved).

    Returns:
        A dict serializable as a Feishu Interactive Message Card (v2).
    """
    elements: list[dict] = []

    # Detect bootstrap mode (best-effort, non-critical)
    bootstrap = False
    try:
        conn = get_db()
        bootstrap = _is_bootstrap_mode(conn)
        conn.close()
    except Exception:
        pass

    for i, (paper, score, recommendation) in enumerate(papers, 1):
        authors_short = shorten_authors(paper.authors)
        venue = paper.venue or "Preprint"
        year = paper.year or "?"
        url = paper_url(paper)

        md_text = (
            f"**{i}. {paper.title}**\n"
            f"{authors_short} \u00b7 {venue} \u00b7 {year}\n"
            f"\U0001f4a1 {recommendation}"
        )
        elements.append({
            "tag": "markdown",
            "content": md_text,
        })

        # Action buttons
        buttons: list[dict] = [
            _action_button("Save \U0001f4cc", f"{paper.pid}:save"),
            _action_button("Mute \U0001f507", f"{paper.pid}:mute"),
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "View \U0001f517"},
                "type": "primary",
                "url": url,
            },
        ]

        if bootstrap:
            buttons.extend([
                _action_button("\U0001f44d Relevant", f"{paper.pid}:thumbs_up"),
                _action_button("\U0001f44e Not relevant", f"{paper.pid}:thumbs_down"),
            ])

        elements.append({
            "tag": "action",
            "actions": buttons,
        })

        # Divider between papers (except after the last one)
        if i < len(papers):
            elements.append({"tag": "hr"})

    # Trending section
    if trend_bursts:
        elements.append({"tag": "hr"})
        trend_lines = ["\U0001f525 **Trending**"]
        for burst in trend_bursts:
            name = burst.get("concept", burst.get("name", "?"))
            delta = burst.get("delta", burst.get("growth", ""))
            trend_lines.append(f"- {name} {f'(+{delta})' if delta else ''}")
        elements.append({
            "tag": "markdown",
            "content": "\n".join(trend_lines),
        })

    # Footer stats
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": (
            f"\U0001f4ca Today: {total_scanned} papers scanned, "
            f"{total_matched} matched"
        ),
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"\U0001f4c4 Daily Paper Digest \u2014 {date}",
            },
            "template": "blue",
        },
        "elements": elements,
    }


def build_collision_card(
    paper: PaperRecord,
    project_name: str,
    collision_score: float,
    scores: dict,
    analysis: str,
    alert_level: AlertLevel,
) -> dict:
    """Build a Feishu v2 interactive card for a collision alert (F2).

    Args:
        paper:           The competing paper.
        project_name:    Name of the user's project that is affected.
        collision_score: Weighted overall collision score.
        scores:          Per-dimension score dict (problem, method, ...).
        analysis:        LLM-generated natural-language explanation.
        alert_level:     Severity classification.

    Returns:
        A dict serializable as a Feishu Interactive Message Card (v2).
    """
    is_high = alert_level == AlertLevel.HIGH
    template = "red" if is_high else "orange"

    authors_short = shorten_authors(paper.authors)
    venue = paper.venue or "Preprint"
    year = paper.year or "?"
    url = paper_url(paper)

    # Format per-dimension scores compactly
    dim_abbrev = {
        "problem_overlap": "problem",
        "method_similarity": "method",
        "dataset_overlap": "dataset",
        "contribution_conflict": "contribution",
        "conclusion_competitiveness": "conclusion",
    }
    if scores:
        score_parts = ", ".join(
            f"{dim_abbrev.get(k, k)}: {v:.2f}" for k, v in scores.items()
        )
    else:
        score_parts = "n/a"

    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"**{paper.title}**\n"
                f"{authors_short} \u00b7 {venue} \u00b7 {year}"
            ),
        },
        {
            "tag": "markdown",
            "content": f"\U0001f3af Related project: **{project_name}**",
        },
        {
            "tag": "markdown",
            "content": (
                f"\u26a1 Score: **{collision_score:.2f}** "
                f"({score_parts})"
            ),
        },
        {
            "tag": "markdown",
            "content": analysis,
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                _action_button("Save \U0001f4cc", f"{paper.pid}:save"),
                _action_button("Dismiss \u270b", f"{paper.pid}:dismiss"),
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "View \U0001f517"},
                    "type": "primary",
                    "url": url,
                },
            ],
        },
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": (
                    f"\U0001f6a8 Collision Alert \u2014 "
                    f"{alert_level.value.upper()}"
                ),
            },
            "template": template,
        },
        "elements": elements,
    }


def build_health_card(report_text: str) -> dict:
    """Build a simple Feishu v2 card for a health report.

    Args:
        report_text: Pre-formatted health report string (from observability).

    Returns:
        A dict serializable as a Feishu Interactive Message Card (v2).
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "\U0001f3e5 LitBot Health Report",
            },
            "template": "green",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": report_text,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------


def verify_signature(
    timestamp: str, nonce: str, body: str, encrypt_key: str
) -> str:
    """Compute the Feishu webhook verification signature.

    Formula: SHA256(timestamp + nonce + encrypt_key + body)

    Returns:
        Hex-encoded SHA-256 digest.
    """
    payload = (timestamp + nonce + encrypt_key + body).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def handle_callback(
    conn: sqlite3.Connection,
    payload: dict,
    encrypt_key: str = "",
) -> dict:
    """Process a Feishu button-click callback idempotently.

    Steps:
        1. Extract callback_id and action_value from the payload.
        2. If callback_id already processed, short-circuit with ``{code: 0}``.
        3. Parse ``pid`` and ``action`` from the action_value string.
        4. Insert the callback row (processed=0).
        5. Record the interaction via ``record_interaction``.
        6. Mark callback as processed.
        7. Return result dict.

    Args:
        conn:        Active database connection.
        payload:     Parsed JSON body from Feishu's callback request.
        encrypt_key: App encrypt key (currently unused in body, reserved).

    Returns:
        ``{"code": 0}`` on idempotent replay, or
        ``{"code": 0, "data": {"pid": ..., "action": ...}}`` on first processing.
    """
    # Extract identifiers from the Feishu callback payload
    event = payload.get("event", {})
    action_obj = event.get("action", {})
    action_value_raw = action_obj.get("value", {})

    # action_value is either a dict {"action": "pid:act"} or a plain string
    if isinstance(action_value_raw, dict):
        action_value: str = action_value_raw.get("action", "")
    else:
        action_value = str(action_value_raw)

    # Derive a stable callback_id
    callback_id = event.get("token", "")
    if not callback_id:
        # Fallback: hash the action + timestamp to create an idempotency key
        ts = str(event.get("ts", time.time()))
        callback_id = hashlib.sha256(
            (action_value + ts).encode("utf-8")
        ).hexdigest()[:24]

    message_id = event.get("open_message_id", "")

    # Idempotency check
    existing = conn.execute(
        "SELECT processed FROM callbacks WHERE callback_id = ?",
        (callback_id,),
    ).fetchone()
    if existing is not None and existing[0] == 1:
        return {"code": 0}

    # Parse pid:action
    if ":" not in action_value:
        return {"code": 0}

    pid, action = action_value.split(":", 1)

    # Insert callback row (processed=0)
    conn.execute(
        "INSERT OR IGNORE INTO callbacks (callback_id, message_id, action) "
        "VALUES (?, ?, ?)",
        (callback_id, message_id, action),
    )
    conn.commit()

    # Record the user interaction
    context = event.get("context", None)
    record_interaction(conn, pid, action, context=context)

    # Mark processed
    conn.execute(
        "UPDATE callbacks SET processed = 1 WHERE callback_id = ?",
        (callback_id,),
    )
    conn.commit()

    log_op(conn, "feishu", "callback", "ok", detail=f"{pid}:{action}")

    return {"code": 0, "data": {"pid": pid, "action": action}}


# ---------------------------------------------------------------------------
# Card update (post-callback)
# ---------------------------------------------------------------------------

_ACTION_LABELS: dict[str, str] = {
    "save": "\u2705 Saved",
    "mute": "\U0001f6ab Muted",
    "dismiss": "\u2705 Dismissed",
    "thumbs_up": "\u2705 Marked relevant",
    "thumbs_down": "\u2705 Marked irrelevant",
}


def build_card_update(original_card: dict, pid: str, action: str) -> dict:
    """Return a copy of *original_card* with the clicked button disabled.

    Finds the button whose action value matches ``pid:action`` and replaces
    its label with a confirmation string (e.g. "Save" -> "Saved").  The
    button is set to ``"disabled"`` type so Feishu greys it out.

    Args:
        original_card: The card dict that was originally sent.
        pid:           Paper ID that was acted on.
        action:        The action string (save, mute, dismiss, ...).

    Returns:
        Updated card dict (deep-copied with modifications).
    """
    import copy

    card = copy.deepcopy(original_card)
    target_value = f"{pid}:{action}"
    new_label = _ACTION_LABELS.get(action, f"\u2705 {action.capitalize()}d")

    for element in card.get("elements", []):
        if element.get("tag") != "action":
            continue
        for btn in element.get("actions", []):
            btn_value = btn.get("value", {})
            if isinstance(btn_value, dict) and btn_value.get("action") == target_value:
                btn["text"]["content"] = new_label
                btn["type"] = "default"
                btn["disabled"] = True
            elif isinstance(btn_value, str) and btn_value == target_value:
                btn["text"]["content"] = new_label
                btn["type"] = "default"
                btn["disabled"] = True

    return card
