#!/usr/bin/env python3
"""Canonical Paper Identity Layer.

Resolves papers across multiple data sources (DOI, arXiv, S2, OpenAlex)
into a single internal ID (pid). Handles deduplication and cross-source
enrichment.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field

from .init_db import get_db

# nanoid-style ID generation (no external dependency)
import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits
def _nanoid(size: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(size))


@dataclass
class PaperRecord:
    pid: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    s2_id: str | None = None
    openalex_id: str | None = None
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    embedding: bytes | None = None
    concepts: list[str] = field(default_factory=list)
    citation_count: int = 0
    pdf_url: str | None = None
    is_new: bool = False  # True if just created


def normalize_title(title: str) -> str:
    """Normalize title for fuzzy matching: lowercase, strip punctuation, collapse whitespace."""
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _first_author_last(authors: list[str]) -> str | None:
    """Extract last name of first author for fuzzy matching."""
    if not authors:
        return None
    first = authors[0]
    # Handle "Last, First" format
    if "," in first:
        return first.split(",")[0].strip().lower()
    # Handle "First Last" format
    parts = first.strip().split()
    return parts[-1].lower() if parts else None


def get_or_create_paper(
    conn: sqlite3.Connection | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    s2_id: str | None = None,
    openalex_id: str | None = None,
    title: str | None = None,
    authors: list[str] | None = None,
    year: int | None = None,
    venue: str | None = None,
    abstract: str | None = None,
    embedding: bytes | None = None,
    concepts: list[str] | None = None,
    citation_count: int = 0,
    pdf_url: str | None = None,
) -> PaperRecord:
    """Resolve a paper to its canonical internal ID.

    Resolution order: DOI → arXiv → S2 → OpenAlex → title+author fuzzy.
    If found, merges any new external IDs into the existing record.
    If not found, creates a new record.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_db()

    # Whitelist of columns safe for dynamic UPDATE
    _SAFE_COLUMNS = frozenset({
        "doi", "arxiv_id", "s2_id", "openalex_id",
        "abstract", "embedding", "concepts", "citation_count", "pdf_url",
    })

    try:
        # Use BEGIN IMMEDIATE to prevent concurrent duplicate inserts
        conn.execute("BEGIN IMMEDIATE")
        row = None

        # Step 1-4: Exact ID match
        for col, val in [
            ("doi", doi),
            ("arxiv_id", arxiv_id),
            ("s2_id", s2_id),
            ("openalex_id", openalex_id),
        ]:
            if val:
                row = conn.execute(
                    f"SELECT * FROM papers WHERE {col} = ?", (val,)
                ).fetchone()
                if row:
                    break

        # Step 5: Fuzzy title + first author match
        if not row and title:
            norm = normalize_title(title)
            author_last = _first_author_last(authors or [])
            candidates = conn.execute(
                "SELECT * FROM papers WHERE year = ? OR year IS NULL",
                (year,) if year else (None,),
            ).fetchall()

            for c in candidates:
                c_title = normalize_title(c[5])  # title is column 5
                # Simple character-level distance check (Levenshtein proxy)
                if abs(len(norm) - len(c_title)) > 10:
                    continue
                # Check if titles are similar enough
                if norm == c_title or (
                    len(norm) > 20 and norm[:20] == c_title[:20]
                ):
                    if author_last:
                        c_authors = json.loads(c[6]) if c[6] else []
                        c_author_last = _first_author_last(c_authors)
                        if c_author_last and c_author_last == author_last:
                            row = c
                            break
                    else:
                        row = c
                        break

        if row:
            # Found existing paper — merge missing IDs
            pid = row[0]
            updates = {}
            col_map = {
                "doi": (1, doi),
                "arxiv_id": (2, arxiv_id),
                "s2_id": (3, s2_id),
                "openalex_id": (4, openalex_id),
            }
            for col_name, (idx, new_val) in col_map.items():
                if new_val and row[idx] is None:
                    updates[col_name] = new_val

            # Also update nullable fields if we have better data
            if abstract and not row[9]:
                updates["abstract"] = abstract
            if embedding and not row[10]:
                updates["embedding"] = embedding
            if concepts and not row[11]:
                updates["concepts"] = json.dumps(concepts)
            if citation_count and (not row[12] or citation_count > row[12]):
                updates["citation_count"] = citation_count
            if pdf_url and not row[13]:
                updates["pdf_url"] = pdf_url

            if updates:
                # Validate all column names against whitelist
                updates = {k: v for k, v in updates.items() if k in _SAFE_COLUMNS}
                if updates:
                    set_clause = ", ".join(f"{k} = ?" for k in updates)
                    vals = list(updates.values())
                    conn.execute(
                        f"UPDATE papers SET {set_clause}, updated_at = datetime('now') WHERE pid = ?",
                        vals + [pid],
                    )
            conn.commit()

            return PaperRecord(
                pid=pid,
                doi=row[1] or doi,
                arxiv_id=row[2] or arxiv_id,
                s2_id=row[3] or s2_id,
                openalex_id=row[4] or openalex_id,
                title=row[5],
                authors=json.loads(row[6]) if row[6] else [],
                year=row[7],
                venue=row[8],
                abstract=row[9] or abstract,
                embedding=row[10] or embedding,
                concepts=json.loads(row[11]) if row[11] else (concepts or []),
                citation_count=row[12] or citation_count,
                pdf_url=row[13] or pdf_url,
                is_new=False,
            )

        # Step 6: Create new paper
        if not title:
            conn.rollback()
            raise ValueError("Cannot create paper without title")

        pid = f"p_{_nanoid()}"
        try:
            conn.execute(
                """INSERT INTO papers
                   (pid, doi, arxiv_id, s2_id, openalex_id, title, authors,
                    year, venue, abstract, embedding, concepts, citation_count, pdf_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    doi,
                    arxiv_id,
                    s2_id,
                    openalex_id,
                    title,
                    json.dumps(authors or []),
                    year,
                    venue,
                    abstract,
                    embedding,
                    json.dumps(concepts or []),
                    citation_count,
                    pdf_url,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Race: another worker inserted the same paper — retry lookup
            conn.rollback()
            return get_or_create_paper(
                conn=conn, doi=doi, arxiv_id=arxiv_id, s2_id=s2_id,
                openalex_id=openalex_id, title=title, authors=authors,
                year=year, venue=venue, abstract=abstract, embedding=embedding,
                concepts=concepts, citation_count=citation_count, pdf_url=pdf_url,
            )

        return PaperRecord(
            pid=pid,
            doi=doi,
            arxiv_id=arxiv_id,
            s2_id=s2_id,
            openalex_id=openalex_id,
            title=title,
            authors=authors or [],
            year=year,
            venue=venue,
            abstract=abstract,
            embedding=embedding,
            concepts=concepts or [],
            citation_count=citation_count,
            pdf_url=pdf_url,
            is_new=True,
        )
    finally:
        if own_conn:
            conn.close()


def is_already_pushed(
    conn: sqlite3.Connection, pid: str, function: str, user_id: str = "default"
) -> bool:
    """Check if a paper has already been pushed for a given function."""
    row = conn.execute(
        "SELECT 1 FROM pushes WHERE pid = ? AND function = ? AND user_id = ?",
        (pid, function, user_id),
    ).fetchone()
    return row is not None


def record_push(
    conn: sqlite3.Connection,
    pid: str,
    function: str,
    message_id: str | None = None,
    user_id: str = "default",
) -> None:
    """Record that a paper was pushed."""
    conn.execute(
        "INSERT OR IGNORE INTO pushes (pid, function, message_id, user_id) VALUES (?, ?, ?, ?)",
        (pid, function, message_id, user_id),
    )
    conn.commit()


def record_interaction(
    conn: sqlite3.Connection,
    pid: str,
    action: str,
    context: str | None = None,
    user_id: str = "default",
) -> None:
    """Record a user interaction with a paper."""
    conn.execute(
        "INSERT INTO interactions (pid, action, context, user_id) VALUES (?, ?, ?, ?)",
        (pid, action, context, user_id),
    )
    conn.commit()
