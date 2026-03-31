#!/usr/bin/env python3
"""Paper ranking module.

Implements the composite scoring formula:

    score(paper) = w_sim * sim_score
                 + w_kw  * keyword_score
                 + w_venue * venue_score
                 + w_recent * recency_score
                 + feedback_adjustment

Each component is normalised to [0, 1] (except feedback_adjustment which is
clamped to [-0.3, +0.3]).  Weights come from Profile.ranking_weights which
varies by privacy level.
"""
from __future__ import annotations

import json
import logging
import math
import random
import re
import sqlite3
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

import numpy as np

from .config import Profile, VenueTiers, load_profile
from .init_db import get_db
from .paper_identity import PaperRecord

# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

_EMBED_DIM = 768  # SPECTER2 float32


def _bytes_to_vec(blob: bytes) -> np.ndarray:
    """Deserialise a 768-dim float32 embedding stored as raw bytes."""
    return np.frombuffer(blob, dtype=np.float32)


def _vec_to_bytes(vec: np.ndarray) -> bytes:
    """Serialise a numpy vector to raw bytes for SQLite BLOB storage."""
    return vec.astype(np.float32).tobytes()


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------


def compute_similarity(
    paper_embedding: bytes | None,
    centroid: np.ndarray | None,
) -> float:
    """Cosine similarity between a paper embedding and the profile centroid.

    Returns a value in [0, 1].  If either input is None the score is 0.0.
    """
    if paper_embedding is None or centroid is None:
        return 0.0

    vec = _bytes_to_vec(paper_embedding)

    norm_a = np.linalg.norm(vec)
    norm_b = np.linalg.norm(centroid)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    cos = float(np.dot(vec, centroid) / (norm_a * norm_b))
    # Clamp to [0, 1] — negative similarity means unrelated, treat as 0.
    return max(0.0, min(1.0, cos))


def compute_keyword_score(
    title: str,
    abstract: str | None,
    keywords: list[str],
) -> float:
    """Fraction of profile keywords that appear in the paper text.

    Returns matched_keywords / total_keywords, or 0.0 when there are no
    keywords to match against.
    """
    if not keywords:
        return 0.0

    text = title.lower()
    if abstract:
        text += " " + abstract.lower()

    matched = sum(1 for kw in keywords if kw in text)
    return matched / len(keywords)


def compute_venue_score(
    venue: str | None,
    venue_tiers: VenueTiers,
) -> float:
    """Score a venue against the tier lists.

    Returns:
        1.0   for tier-1 venues
        0.5   for tier-2 venues
        -inf  for blacklisted venues (caller should filter before ranking)
        0.3   for everything else (unknown)
    """
    if venue is None:
        return 0.3

    venue_lower = venue.lower()

    for v in venue_tiers.blacklist:
        if v.lower() in venue_lower:
            return float("-inf")

    for v in venue_tiers.tier1:
        if v.lower() in venue_lower:
            return 1.0

    for v in venue_tiers.tier2:
        if v.lower() in venue_lower:
            return 0.5

    return 0.3


def compute_recency_score(
    year: int | None,
    published_date: str | None,
) -> float:
    """Score based on how recently the paper was published.

    Uses ``published_date`` (ISO format YYYY-MM-DD) when available, otherwise
    falls back to ``year`` (approximated as Jan 1 of that year).

    Returns max(0, 1 - days_since_published / 30).
    """
    ref_date: date | None = None

    if published_date:
        # Try ISO date parsing (YYYY-MM-DD or longer)
        try:
            ref_date = datetime.fromisoformat(published_date[:10]).date()
        except (ValueError, TypeError):
            pass

    if ref_date is None and year is not None:
        # If the paper's year is the current year and no exact date is given,
        # assume it's recent (use 7 days ago as a conservative estimate).
        # Otherwise fall back to Jan 1 of that year.
        current_year = date.today().year
        if year >= current_year:
            ref_date = date.today() - timedelta(days=7)
        else:
            ref_date = date(year, 1, 1)

    if ref_date is None:
        return 0.0

    days_since = (date.today() - ref_date).days
    return max(0.0, 1.0 - days_since / 30.0)


def compute_feedback_adjustment(
    conn: sqlite3.Connection,
    paper: PaperRecord,
) -> float:
    """Compute a bonus/penalty from recent user interactions.

    Looks at saved and muted papers in the past 90 days that share an author
    or venue with the candidate paper.  Each matching save adds +0.1, each
    matching mute subtracts 0.1.  The result is clamped to [-0.3, +0.3].
    """
    cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()

    # Fetch recent interactions with paper metadata
    rows = conn.execute(
        """
        SELECT i.action, p.authors, p.venue
        FROM interactions i
        JOIN papers p ON p.pid = i.pid
        WHERE i.action IN ('save', 'mute')
          AND i.created_at >= ?
        """,
        (cutoff,),
    ).fetchall()

    if not rows:
        return 0.0

    paper_authors = {a.lower() for a in paper.authors}
    paper_venue = paper.venue.lower() if paper.venue else None

    adjustment = 0.0

    for action, authors_json, venue in rows:
        match = False

        # Check author overlap
        if authors_json and paper_authors:
            row_authors = json.loads(authors_json)
            row_set = {a.lower() for a in row_authors}
            if paper_authors & row_set:
                match = True

        # Check venue match
        if not match and paper_venue and venue:
            if venue.lower() == paper_venue:
                match = True

        if match:
            if action == "save":
                adjustment += 0.1
            elif action == "mute":
                adjustment -= 0.1

    return max(-0.3, min(0.3, adjustment))


# ---------------------------------------------------------------------------
# Profile centroid
# ---------------------------------------------------------------------------


def get_profile_centroid(
    conn: sqlite3.Connection,
    user_id: str = "default",
) -> np.ndarray | None:
    """Compute or retrieve the user's embedding centroid.

    Strategy:
    1. Fetch embeddings of all saved papers (action='save').
    2. If there are >= 5, return their mean.
    3. If fewer, fall back to bootstrap seed paper embeddings
       (papers with interaction context='bootstrap').
    4. If still nothing, return None.
    """
    # Try saved papers first
    rows = conn.execute(
        """
        SELECT p.embedding
        FROM interactions i
        JOIN papers p ON p.pid = i.pid
        WHERE i.action = 'save'
          AND i.user_id = ?
          AND p.embedding IS NOT NULL
        """,
        (user_id,),
    ).fetchall()

    embeddings = [_bytes_to_vec(r[0]) for r in rows if r[0]]

    if len(embeddings) >= 5:
        return np.mean(embeddings, axis=0).astype(np.float32)

    # Fall back to bootstrap seed embeddings
    seed_rows = conn.execute(
        """
        SELECT p.embedding
        FROM interactions i
        JOIN papers p ON p.pid = i.pid
        WHERE i.context = 'bootstrap'
          AND i.user_id = ?
          AND p.embedding IS NOT NULL
        """,
        (user_id,),
    ).fetchall()

    seed_embeddings = [_bytes_to_vec(r[0]) for r in seed_rows if r[0]]

    if seed_embeddings:
        return np.mean(seed_embeddings, axis=0).astype(np.float32)

    return None


# ---------------------------------------------------------------------------
# Keyword collection
# ---------------------------------------------------------------------------


def collect_keywords(profile: Profile) -> list[str]:
    """Gather deduplicated, lowercased keywords from the profile.

    Combines ``research_areas`` with keywords from every active project.
    """
    seen: set[str] = set()
    result: list[str] = []

    for kw in profile.research_areas:
        lower = kw.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(lower)

    for proj in profile.active_projects:
        for kw in proj.keywords:
            lower = kw.lower()
            if lower not in seen:
                seen.add(lower)
                result.append(lower)

    return result


# ---------------------------------------------------------------------------
# Bootstrap mode check
# ---------------------------------------------------------------------------


def is_in_bootstrap_mode(
    conn: sqlite3.Connection,
    user_id: str = "default",
) -> bool:
    """Return True if the user is still in bootstrap mode.

    Bootstrap mode is active when ``bootstrap_state.mode = 'active'`` and
    ``save_count < 5``.
    """
    row = conn.execute(
        "SELECT mode, save_count FROM bootstrap_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    if row is None:
        return True  # no record → treat as bootstrap

    mode, save_count = row
    return mode == "active" and (save_count or 0) < 5


# ---------------------------------------------------------------------------
# Main ranking pipeline
# ---------------------------------------------------------------------------


def rank_papers(
    papers: list[PaperRecord],
    profile: Profile,
    conn: sqlite3.Connection,
) -> list[tuple[PaperRecord, float]]:
    """Rank candidate papers using the composite scoring formula.

    Steps:
    1. Filter out papers from blacklisted venues.
    2. Compute the four component scores + feedback adjustment.
    3. Combine with profile-dependent weights.
    4. Sort descending by score.
    5. Apply diversity sampling — reserve ``diversity_ratio`` of output slots
       for papers ranked 15-50 (random sample from that band).
    6. Return the top ``max_daily_papers`` as (paper, score) pairs.
    """
    weights = dict(profile.ranking_weights)  # copy so we can modify
    venue_tiers = profile.venue_tiers
    keywords = collect_keywords(profile)
    centroid = get_profile_centroid(conn)
    max_papers = profile.preferences.max_daily_papers
    diversity_ratio = profile.preferences.diversity_ratio

    # --- Step 0: redistribute weights when embedding unavailable ---
    # When S2 is down or no centroid exists, sim weight is dead weight.
    # Redistribute it proportionally to keyword and venue to maintain
    # discriminative power instead of silently losing 40% of signal.
    if centroid is None and weights.get("sim", 0) > 0:
        lost = weights["sim"]
        weights["sim"] = 0.0
        remaining = weights.get("keyword", 0) + weights.get("venue", 0) + weights.get("recency", 0)
        if remaining > 0:
            for k in ("keyword", "venue", "recency"):
                weights[k] = weights.get(k, 0) + lost * (weights.get(k, 0) / remaining)
        logger.info(
            "No embedding centroid — redistributed sim weight: kw=%.2f venue=%.2f rec=%.2f",
            weights.get("keyword", 0), weights.get("venue", 0), weights.get("recency", 0),
        )

    # --- Step 1: filter blacklisted venues ---
    candidates: list[PaperRecord] = []
    for p in papers:
        vs = compute_venue_score(p.venue, venue_tiers)
        if math.isinf(vs) and vs < 0:
            continue
        candidates.append(p)

    # --- Step 2-3: score each candidate ---
    scored: list[tuple[PaperRecord, float]] = []
    for p in candidates:
        sim = compute_similarity(p.embedding, centroid)
        kw = compute_keyword_score(p.title, p.abstract, keywords)
        vs = compute_venue_score(p.venue, venue_tiers)
        rec = compute_recency_score(p.year, None)
        fb = compute_feedback_adjustment(conn, p)

        score = (
            weights.get("sim", 0.0) * sim
            + weights.get("keyword", 0.0) * kw
            + weights.get("venue", 0.0) * vs
            + weights.get("recency", 0.0) * rec
            + fb
        )
        scored.append((p, score))

    # --- Step 4: sort descending ---
    scored.sort(key=lambda x: x[1], reverse=True)

    if not scored:
        return []

    # --- Step 5: diversity sampling ---
    # Reserve diversity_ratio of slots for papers ranked 15-50 to avoid
    # recommending only from the same narrow cluster.
    diversity_slots = max(1, int(max_papers * diversity_ratio))
    primary_slots = max_papers - diversity_slots

    primary = scored[:primary_slots]

    # Diversity band: ranks 15-50 (0-indexed 14-49), excluding those already
    # selected for the primary list.
    primary_pids = {p.pid for p, _ in primary}
    diversity_band = [
        (p, s) for p, s in scored[14:50] if p.pid not in primary_pids
    ]

    if diversity_band and diversity_slots > 0:
        sample_size = min(diversity_slots, len(diversity_band))
        diverse_picks = random.sample(diversity_band, sample_size)
    else:
        diverse_picks = []

    result = primary + diverse_picks
    # Re-sort the final list by score so the output is ordered.
    result.sort(key=lambda x: x[1], reverse=True)

    return result[:max_papers]
