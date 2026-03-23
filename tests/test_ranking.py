"""Tests for the ranking formula."""

import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.init_db import init_db
from scripts.config import ActiveProject, Preferences, Profile, VenueTiers
from scripts.paper_identity import PaperRecord, get_or_create_paper, record_interaction
from scripts.ranking import (
    collect_keywords,
    compute_feedback_adjustment,
    compute_keyword_score,
    compute_recency_score,
    compute_similarity,
    compute_venue_score,
    get_profile_centroid,
    is_in_bootstrap_mode,
    rank_papers,
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = init_db(db_path)
        yield conn
        conn.close()


@pytest.fixture
def profile():
    return Profile(
        research_areas=["speech emotion recognition", "affective computing"],
        active_projects=[
            ActiveProject(
                name="SER with LLM",
                keywords=["speech emotion", "large language model", "in-context learning"],
                venues=["INTERSPEECH", "ICASSP"],
            )
        ],
        venue_tiers=VenueTiers(
            tier1=["NeurIPS", "ICML", "ACL", "INTERSPEECH"],
            tier2=["AAAI", "EMNLP"],
            blacklist=["MDPI"],
        ),
        preferences=Preferences(max_daily_papers=5, diversity_ratio=0.2),
    )


class TestComputeSimilarity:
    def test_identical_vectors(self):
        vec = np.random.randn(768).astype(np.float32)
        centroid = vec.copy()
        assert compute_similarity(vec.tobytes(), centroid) == pytest.approx(1.0, abs=0.01)

    def test_orthogonal_vectors(self):
        a = np.zeros(768, dtype=np.float32)
        a[0] = 1.0
        b = np.zeros(768, dtype=np.float32)
        b[1] = 1.0
        assert compute_similarity(a.tobytes(), b) == pytest.approx(0.0, abs=0.01)

    def test_no_embedding(self):
        centroid = np.random.randn(768).astype(np.float32)
        assert compute_similarity(None, centroid) == 0.0

    def test_no_centroid(self):
        vec = np.random.randn(768).astype(np.float32)
        assert compute_similarity(vec.tobytes(), None) == 0.0


class TestComputeKeywordScore:
    def test_all_match(self):
        score = compute_keyword_score(
            "Speech Emotion Recognition with LLM",
            "We use in-context learning for speech emotion recognition.",
            ["speech emotion", "in-context learning"],
        )
        assert score == pytest.approx(1.0)

    def test_partial_match(self):
        score = compute_keyword_score(
            "Speech Emotion Recognition",
            "A study on emotions.",
            ["speech emotion", "large language model", "in-context learning"],
        )
        assert 0.0 < score < 1.0

    def test_no_match(self):
        score = compute_keyword_score(
            "Quantum Computing",
            "Qubits and entanglement.",
            ["speech emotion", "large language model"],
        )
        assert score == 0.0

    def test_empty_keywords(self):
        score = compute_keyword_score("Any Title", "Any abstract", [])
        assert score == 0.0


class TestComputeVenueScore:
    def test_tier1(self):
        tiers = VenueTiers(tier1=["NeurIPS", "ICML"], tier2=["AAAI"], blacklist=["MDPI"])
        assert compute_venue_score("NeurIPS", tiers) == 1.0

    def test_tier2(self):
        tiers = VenueTiers(tier1=["NeurIPS"], tier2=["AAAI"], blacklist=["MDPI"])
        assert compute_venue_score("AAAI", tiers) == 0.5

    def test_unknown(self):
        tiers = VenueTiers(tier1=["NeurIPS"], tier2=["AAAI"], blacklist=["MDPI"])
        assert compute_venue_score("SomeConf", tiers) == 0.3

    def test_blacklisted(self):
        tiers = VenueTiers(tier1=[], tier2=[], blacklist=["MDPI"])
        assert compute_venue_score("MDPI", tiers) == float("-inf")

    def test_none_venue(self):
        tiers = VenueTiers()
        assert compute_venue_score(None, tiers) == 0.3


class TestComputeRecencyScore:
    def test_today(self):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert compute_recency_score(None, today) == pytest.approx(1.0, abs=0.05)

    def test_30_days_old(self):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        assert compute_recency_score(None, old) == pytest.approx(0.0, abs=0.05)

    def test_15_days_old(self):
        from datetime import datetime, timedelta
        mid = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        assert compute_recency_score(None, mid) == pytest.approx(0.5, abs=0.05)

    def test_year_only_fallback(self):
        from datetime import datetime
        current_year = datetime.now().year
        score = compute_recency_score(current_year, None)
        assert score >= 0.0


class TestCollectKeywords:
    def test_dedup_and_lowercase(self, profile):
        kws = collect_keywords(profile)
        assert len(kws) == len(set(kws))
        for kw in kws:
            assert kw == kw.lower()

    def test_includes_research_areas(self, profile):
        kws = collect_keywords(profile)
        assert "speech emotion recognition" in kws

    def test_includes_project_keywords(self, profile):
        kws = collect_keywords(profile)
        assert "large language model" in kws


class TestBootstrapMode:
    def test_active_by_default(self, db):
        assert is_in_bootstrap_mode(db) is True

    def test_completed_after_5_saves(self, db):
        for i in range(5):
            p = get_or_create_paper(
                conn=db, doi=f"10.1234/boot.{i}", title=f"Paper {i}", year=2025
            )
            record_interaction(db, p.pid, "save", "F1")

        db.execute(
            "UPDATE bootstrap_state SET save_count = 5, mode = 'completed' WHERE user_id = 'default'"
        )
        db.commit()
        assert is_in_bootstrap_mode(db) is False


class TestRankPapers:
    def test_basic_ranking(self, db, profile):
        papers = []
        for i in range(3):
            p = get_or_create_paper(
                conn=db,
                doi=f"10.1234/rank.{i}",
                title=f"Speech Emotion Paper {i}",
                authors=[f"Author{i}"],
                year=2025,
                venue="INTERSPEECH" if i == 0 else "Unknown",
                abstract="Speech emotion recognition with large language model." if i == 0 else "Other topic.",
            )
            papers.append(PaperRecord(
                pid=p.pid,
                doi=p.doi,
                title=p.title,
                authors=p.authors,
                year=p.year,
                venue=p.venue,
                abstract=p.abstract,
            ))

        ranked = rank_papers(papers, profile, db)
        assert len(ranked) <= profile.preferences.max_daily_papers
        # First paper should rank highest (best keyword match + tier1 venue)
        assert ranked[0][0].doi == "10.1234/rank.0"

    def test_blacklisted_filtered(self, db, profile):
        p = get_or_create_paper(
            conn=db,
            doi="10.1234/mdpi.001",
            title="MDPI Paper",
            year=2025,
            venue="MDPI Sensors",
        )
        papers = [PaperRecord(
            pid=p.pid, doi=p.doi, title=p.title, year=p.year,
            venue=p.venue, abstract="",
        )]
        ranked = rank_papers(papers, profile, db)
        assert len(ranked) == 0
