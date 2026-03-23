#!/usr/bin/env python3
"""F2 Collision Alert — two-stage detection for papers that compete with
the user's active research projects.

Stage 1 (coarse): fast embedding cosine-similarity filter with keyword fallback.
Stage 2 (fine):   LLM-scored structured analysis across five competition
                  dimensions, producing a weighted collision score.
"""
from __future__ import annotations

import json
import sqlite3
from enum import Enum

import numpy as np

from .config import Profile, ActiveProject
from .paper_identity import PaperRecord
from .ranking import _bytes_to_vec, get_profile_centroid
from .observability import timed_op, log_op


# ---------------------------------------------------------------------------
# Stage 2 — LLM scoring dimensions and weights
# ---------------------------------------------------------------------------

COLLISION_DIMENSIONS: dict[str, float] = {
    "problem_overlap": 0.30,
    "method_similarity": 0.25,
    "dataset_overlap": 0.20,
    "contribution_conflict": 0.15,
    "conclusion_competitiveness": 0.10,
}


# ---------------------------------------------------------------------------
# Alert classification
# ---------------------------------------------------------------------------


class AlertLevel(Enum):
    """Severity of a collision alert."""

    HIGH = "high"            # collision_score >= 0.55
    MEDIUM = "medium"        # 0.35 <= score < 0.55
    UNCERTAIN = "uncertain"  # 0.25 <= score < 0.35
    LOW = "low"              # score < 0.25


def classify_alert(collision_score: float) -> AlertLevel:
    """Map a numeric collision score to an alert level.

    Thresholds:
        >= 0.55  ->  HIGH
        >= 0.35  ->  MEDIUM
        >= 0.25  ->  UNCERTAIN
        <  0.25  ->  LOW
    """
    if collision_score >= 0.55:
        return AlertLevel.HIGH
    if collision_score >= 0.35:
        return AlertLevel.MEDIUM
    if collision_score >= 0.25:
        return AlertLevel.UNCERTAIN
    return AlertLevel.LOW


# ---------------------------------------------------------------------------
# Stage 1 — Coarse filter
# ---------------------------------------------------------------------------


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors, returned in [0, 1]."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(max(0.0, np.dot(a, b) / (norm_a * norm_b)))


def _keyword_overlap(paper: PaperRecord, keywords: list[str]) -> int:
    """Count how many *keywords* appear in the paper's title + abstract."""
    text = paper.title.lower()
    if paper.abstract:
        text += " " + paper.abstract.lower()
    return sum(1 for kw in keywords if kw.lower() in text)


def coarse_filter(
    papers: list[PaperRecord],
    project: ActiveProject,
    project_centroid: np.ndarray | None,
    threshold: float = 0.65,
) -> list[PaperRecord]:
    """Stage 1: fast pre-filter to select collision candidates.

    Primary strategy — embedding cosine similarity:
        Compute similarity between each paper's embedding and the
        *project_centroid*.  Papers with sim >= *threshold* pass.

    Fallback — keyword overlap (used when no centroid is available):
        Papers whose title+abstract contain >= 2 of the project's
        keywords pass the filter.

    Args:
        papers:            Candidate papers to screen.
        project:           The active project to check against.
        project_centroid:  Mean embedding for papers related to *project*.
                           ``None`` triggers the keyword fallback.
        threshold:         Cosine similarity cutoff (default 0.65).

    Returns:
        Subset of *papers* that survive the coarse filter.
    """
    if project_centroid is not None:
        # Embedding-based filtering
        result: list[PaperRecord] = []
        for paper in papers:
            if paper.embedding is None:
                continue
            vec = _bytes_to_vec(paper.embedding)
            sim = _cosine_similarity(vec, project_centroid)
            if sim >= threshold:
                result.append(paper)
        return result

    # Fallback: keyword overlap (>= 2 keywords match)
    return [
        p for p in papers
        if _keyword_overlap(p, project.keywords) >= 2
    ]


# ---------------------------------------------------------------------------
# Stage 2 — Fine filter (LLM structured scoring)
# ---------------------------------------------------------------------------


def build_collision_prompt(paper: PaperRecord, project: ActiveProject) -> str:
    """Build a prompt that asks an LLM to score a paper across the five
    collision dimensions.

    The prompt includes the paper title, abstract, and the project's
    keywords, and requests a JSON response with per-dimension scores
    (each on a [0, 1] scale) plus a brief natural-language analysis.
    """
    dimensions_desc = "\n".join(
        f"  - {dim} (weight {w:.0%}): score from 0 (no overlap) to 1 (direct conflict)"
        for dim, w in COLLISION_DIMENSIONS.items()
    )

    abstract_text = paper.abstract or "(no abstract available)"
    keywords_text = ", ".join(project.keywords) if project.keywords else "(none)"

    return (
        "You are a research competition analyst.  Given a newly published paper "
        "and a researcher's active project, score the degree of competition on "
        "five dimensions.  Each score is a float in [0, 1].\n\n"
        f"## Paper\n"
        f"Title: {paper.title}\n"
        f"Abstract: {abstract_text}\n\n"
        f"## Active Project\n"
        f"Name: {project.name}\n"
        f"Keywords: {keywords_text}\n\n"
        f"## Dimensions to score\n"
        f"{dimensions_desc}\n\n"
        "Respond ONLY with a JSON object in this exact shape (no markdown fences):\n"
        "{\n"
        '  "scores": {\n'
        '    "problem_overlap": <float>,\n'
        '    "method_similarity": <float>,\n'
        '    "dataset_overlap": <float>,\n'
        '    "contribution_conflict": <float>,\n'
        '    "conclusion_competitiveness": <float>\n'
        "  },\n"
        '  "analysis": "<one-paragraph natural-language explanation>"\n'
        "}"
    )


def parse_collision_response(response: str) -> dict:
    """Parse the LLM JSON response and compute the weighted collision score.

    Expects the LLM to return a JSON object with ``"scores"`` (a dict of
    the five dimension names to floats in [0, 1]) and ``"analysis"`` (a
    string).

    Returns:
        {
            "scores": {<dim>: <float>, ...},
            "collision_score": <float>,   # weighted sum
            "analysis": <str>,
        }

    Returns a zero-score result on parse failure (tolerant of malformed LLM output).
    """
    try:
        # Strip potential markdown code fences
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        data = json.loads(text)

        # Accept both {"scores": {"dim": val}} and flat {"dim": val} formats
        raw_scores = data.get("scores", {})
        if not raw_scores:
            raw_scores = data

        scores: dict[str, float] = {}
        for dim in COLLISION_DIMENSIONS:
            val = raw_scores.get(dim, 0.0)
            try:
                scores[dim] = float(max(0.0, min(1.0, float(val))))
            except (TypeError, ValueError):
                scores[dim] = 0.0

        analysis = str(data.get("analysis", ""))

        collision_score = sum(
            scores[dim] * weight for dim, weight in COLLISION_DIMENSIONS.items()
        )

        return {
            "scores": scores,
            "collision_score": round(collision_score, 4),
            "analysis": analysis,
        }
    except (json.JSONDecodeError, AttributeError, KeyError):
        return {
            "scores": {dim: 0.0 for dim in COLLISION_DIMENSIONS},
            "collision_score": 0.0,
            "analysis": "Parse error: could not interpret LLM response",
        }


# ---------------------------------------------------------------------------
# Project centroid helper
# ---------------------------------------------------------------------------


def get_project_centroid(
    conn: sqlite3.Connection,
    project: ActiveProject,
    user_id: str = "default",
) -> np.ndarray | None:
    """Compute the mean embedding of saved papers whose keywords overlap
    with the given project.

    Searches for papers (via the interactions table, action='save') whose
    concept list or title contains at least one of the project's keywords.
    Returns the mean of their embeddings, or ``None`` if no matching
    papers with embeddings are found.
    """
    if not project.keywords:
        return get_profile_centroid(conn, user_id)

    rows = conn.execute(
        """
        SELECT p.embedding, p.title, p.concepts
        FROM interactions i
        JOIN papers p ON p.pid = i.pid
        WHERE i.action = 'save'
          AND i.user_id = ?
          AND p.embedding IS NOT NULL
        """,
        (user_id,),
    ).fetchall()

    kw_lower = {kw.lower() for kw in project.keywords}
    embeddings: list[np.ndarray] = []

    for embedding_blob, title, concepts_json in rows:
        if embedding_blob is None:
            continue
        # Check keyword match against title
        title_lower = (title or "").lower()
        concepts = json.loads(concepts_json) if concepts_json else []
        concepts_lower = {c.lower() for c in concepts}

        if kw_lower & concepts_lower or any(kw in title_lower for kw in kw_lower):
            embeddings.append(_bytes_to_vec(embedding_blob))

    if embeddings:
        return np.mean(embeddings, axis=0).astype(np.float32)

    # Fall back to the global profile centroid
    return get_profile_centroid(conn, user_id)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def detect_collisions(
    papers: list[PaperRecord],
    profile: Profile,
    conn: sqlite3.Connection,
    llm_call=None,
) -> list[dict]:
    """Run the full two-stage collision detection pipeline.

    For every active project in *profile*:

    1. Compute the project centroid via :func:`get_project_centroid`.
    2. Run :func:`coarse_filter` to find candidate papers.
    3. Deduplicate candidates across projects.
    4. If *llm_call* is provided (a callable ``str -> str``), run Stage 2
       (LLM scoring) for each candidate.
    5. Otherwise, fall back to using the coarse cosine similarity as a
       proxy collision score.

    Args:
        papers:    Today's fetched papers.
        profile:   User profile with active projects.
        conn:      Database connection.
        llm_call:  Optional callable ``(prompt: str) -> str`` for LLM
                   evaluation.  Pass ``None`` to skip Stage 2.

    Returns:
        List of result dicts sorted by ``collision_score`` descending::

            {
                "paper": PaperRecord,
                "project": str,
                "collision_score": float,
                "alert_level": AlertLevel,
                "analysis": str,
                "scores": dict,
            }
    """
    if not profile.active_projects:
        return []

    # Collect coarse candidates per project, tracking best project match
    seen_pids: dict[str, dict] = {}  # pid -> best candidate record

    for project in profile.active_projects:
        centroid = get_project_centroid(conn, project)
        candidates = coarse_filter(papers, project, centroid)

        for paper in candidates:
            # Compute coarse similarity for proxy scoring
            if centroid is not None and paper.embedding is not None:
                vec = _bytes_to_vec(paper.embedding)
                coarse_sim = _cosine_similarity(vec, centroid)
            else:
                # Keyword fallback: use normalised keyword overlap as proxy
                overlap = _keyword_overlap(paper, project.keywords)
                max_kw = max(len(project.keywords), 1)
                coarse_sim = min(1.0, overlap / max_kw)

            # Keep the highest-scoring project association per paper
            if paper.pid not in seen_pids or coarse_sim > seen_pids[paper.pid]["coarse_sim"]:
                seen_pids[paper.pid] = {
                    "paper": paper,
                    "project": project.name,
                    "coarse_sim": coarse_sim,
                }

    # Stage 2: fine scoring (or proxy)
    results: list[dict] = []

    for entry in seen_pids.values():
        paper: PaperRecord = entry["paper"]
        project_name: str = entry["project"]
        coarse_sim: float = entry["coarse_sim"]

        # Find the ActiveProject object for prompt building
        project_obj = next(
            (p for p in profile.active_projects if p.name == project_name),
            None,
        )

        if llm_call is not None and project_obj is not None:
            # Stage 2 — LLM structured scoring
            prompt = build_collision_prompt(paper, project_obj)
            try:
                with timed_op(conn, "llm", "collision_score") as op:
                    raw_response = llm_call(prompt)
                    op["detail"] = f"collision scored: {paper.pid}"

                parsed = parse_collision_response(raw_response)
                collision_score = parsed["collision_score"]
                analysis = parsed["analysis"]
                scores = parsed["scores"]
            except Exception:
                # LLM or parse failure — fall back to coarse proxy
                log_op(conn, "llm", "collision_score", "error",
                       detail=f"fallback to coarse for {paper.pid}")
                collision_score = coarse_sim
                analysis = "LLM scoring failed; using coarse similarity as proxy."
                scores = {}
        else:
            # No LLM available — use coarse similarity as proxy
            collision_score = coarse_sim
            analysis = "No LLM available; score is coarse cosine similarity."
            scores = {}

        results.append({
            "paper": paper,
            "project": project_name,
            "collision_score": round(collision_score, 4),
            "alert_level": classify_alert(collision_score),
            "analysis": analysis,
            "scores": scores,
        })

    # Sort by collision_score descending
    results.sort(key=lambda r: r["collision_score"], reverse=True)
    return results
