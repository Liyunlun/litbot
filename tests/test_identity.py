"""Tests for the Canonical Paper Identity Layer."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Adjust path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.init_db import init_db
from scripts.paper_identity import (
    PaperRecord,
    get_or_create_paper,
    is_already_pushed,
    normalize_title,
    record_interaction,
    record_push,
)


@pytest.fixture
def db():
    """Create a temporary in-memory database for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = init_db(db_path)
        yield conn
        conn.close()


class TestNormalizeTitle:
    def test_basic(self):
        assert normalize_title("Hello World") == "hello world"

    def test_punctuation(self):
        assert normalize_title("Attention Is All You Need!") == "attention is all you need"

    def test_extra_spaces(self):
        assert normalize_title("  Multiple   Spaces  ") == "multiple spaces"

    def test_mixed_case(self):
        assert normalize_title("BERT: Pre-training") == "bert pretraining"


class TestGetOrCreatePaper:
    def test_create_new_by_doi(self, db):
        paper = get_or_create_paper(
            conn=db,
            doi="10.1234/test.2025.001",
            title="Test Paper",
            authors=["Smith, John"],
            year=2025,
        )
        assert paper.pid.startswith("p_")
        assert paper.doi == "10.1234/test.2025.001"
        assert paper.title == "Test Paper"
        assert paper.is_new is True

    def test_find_existing_by_doi(self, db):
        p1 = get_or_create_paper(
            conn=db, doi="10.1234/test.001", title="Paper A", year=2025
        )
        p2 = get_or_create_paper(
            conn=db, doi="10.1234/test.001", title="Paper A", year=2025
        )
        assert p1.pid == p2.pid
        assert p2.is_new is False

    def test_merge_arxiv_into_existing_doi(self, db):
        p1 = get_or_create_paper(
            conn=db, doi="10.1234/test.002", title="Paper B", year=2025
        )
        assert p1.arxiv_id is None

        p2 = get_or_create_paper(
            conn=db,
            doi="10.1234/test.002",
            arxiv_id="2501.00001",
            title="Paper B",
            year=2025,
        )
        assert p2.pid == p1.pid
        assert p2.arxiv_id == "2501.00001"

    def test_find_by_arxiv_id(self, db):
        p1 = get_or_create_paper(
            conn=db, arxiv_id="2501.12345", title="ArXiv Paper", year=2025
        )
        p2 = get_or_create_paper(
            conn=db, arxiv_id="2501.12345", title="ArXiv Paper", year=2025
        )
        assert p1.pid == p2.pid

    def test_find_by_s2_id(self, db):
        p1 = get_or_create_paper(
            conn=db, s2_id="abc123def456", title="S2 Paper", year=2025
        )
        p2 = get_or_create_paper(
            conn=db, s2_id="abc123def456", title="S2 Paper", year=2025
        )
        assert p1.pid == p2.pid

    def test_find_by_openalex_id(self, db):
        p1 = get_or_create_paper(
            conn=db, openalex_id="W12345", title="OA Paper", year=2025
        )
        p2 = get_or_create_paper(
            conn=db, openalex_id="W12345", title="OA Paper", year=2025
        )
        assert p1.pid == p2.pid

    def test_fuzzy_title_match(self, db):
        p1 = get_or_create_paper(
            conn=db,
            title="Attention Is All You Need",
            authors=["Vaswani, Ashish"],
            year=2017,
        )
        p2 = get_or_create_paper(
            conn=db,
            title="Attention Is All You Need",
            authors=["Vaswani, Ashish"],
            year=2017,
            doi="10.48550/arXiv.1706.03762",
        )
        assert p1.pid == p2.pid
        assert p2.doi == "10.48550/arXiv.1706.03762"

    def test_no_title_raises(self, db):
        with pytest.raises(ValueError, match="Cannot create paper without title"):
            get_or_create_paper(conn=db, doi="10.1234/no-title")

    def test_never_overwrite_existing_id(self, db):
        p1 = get_or_create_paper(
            conn=db,
            doi="10.1234/original",
            s2_id="original_s2",
            title="Original",
            year=2025,
        )
        # Try to "update" s2_id via same DOI — should NOT overwrite
        p2 = get_or_create_paper(
            conn=db,
            doi="10.1234/original",
            s2_id="different_s2",
            title="Original",
            year=2025,
        )
        assert p2.s2_id == "original_s2"  # not overwritten

    def test_merge_enriches_abstract(self, db):
        p1 = get_or_create_paper(
            conn=db, doi="10.1234/enrich", title="Enrich Test", year=2025
        )
        assert p1.abstract is None

        p2 = get_or_create_paper(
            conn=db,
            doi="10.1234/enrich",
            title="Enrich Test",
            abstract="This is the abstract.",
            year=2025,
        )
        assert p2.abstract == "This is the abstract."


class TestPushTracking:
    def test_push_and_check(self, db):
        paper = get_or_create_paper(
            conn=db, doi="10.1234/push.001", title="Push Test", year=2025
        )
        assert not is_already_pushed(db, paper.pid, "F1")
        record_push(db, paper.pid, "F1", "msg_001")
        assert is_already_pushed(db, paper.pid, "F1")
        assert not is_already_pushed(db, paper.pid, "F2")

    def test_push_idempotent(self, db):
        paper = get_or_create_paper(
            conn=db, doi="10.1234/push.002", title="Idempotent", year=2025
        )
        record_push(db, paper.pid, "F1")
        record_push(db, paper.pid, "F1")  # should not raise
        count = db.execute(
            "SELECT COUNT(*) FROM pushes WHERE pid = ?", (paper.pid,)
        ).fetchone()[0]
        assert count == 1


class TestInteractions:
    def test_record_interaction(self, db):
        paper = get_or_create_paper(
            conn=db, doi="10.1234/int.001", title="Interact Test", year=2025
        )
        record_interaction(db, paper.pid, "save", "F1")
        record_interaction(db, paper.pid, "click", "F1")

        rows = db.execute(
            "SELECT action FROM interactions WHERE pid = ? ORDER BY id",
            (paper.pid,),
        ).fetchall()
        assert [r[0] for r in rows] == ["save", "click"]
