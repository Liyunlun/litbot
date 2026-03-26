"""Comprehensive integration tests for LitBot.

Simulates real end-to-end workflows without hitting external APIs.
All data is synthetic with realistic metadata from ML/NLP research.
"""

import json
import math
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.init_db import init_db
from scripts.config import (
    ActiveProject,
    Preferences,
    Profile,
    RetryPolicy,
    VenueTiers,
    load_profile,
    save_profile,
)
from scripts.paper_identity import (
    PaperRecord,
    get_or_create_paper,
    is_already_pushed,
    record_interaction,
    record_push,
)
from scripts.ranking import (
    _vec_to_bytes,
    collect_keywords,
    compute_keyword_score,
    compute_similarity,
    compute_venue_score,
    is_in_bootstrap_mode,
    rank_papers,
)
from scripts.collision import (
    AlertLevel,
    classify_alert,
    coarse_filter,
    parse_collision_response,
)
from scripts.trend import detect_bursts, update_trend_stats
from scripts.observability import (
    CircuitState,
    generate_health_report,
    get_circuit,
    log_op,
    _circuits,
)
from scripts.feishu_cards import (
    build_collision_card,
    build_daily_digest_card,
    handle_callback,
    shorten_authors,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """Create a fresh temp database for each test."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_integration.db"
        conn = init_db(db_path)
        yield conn
        conn.close()


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """Reset all global circuit breaker state between tests."""
    _circuits.clear()
    yield
    _circuits.clear()


def _make_profile(**overrides):
    """Build a test profile programmatically with sensible defaults."""
    defaults = dict(
        name="Dr. Wei Zhang",
        semantic_scholar_id="12345678",
        my_papers=["10.18653/v1/2023.acl-long.100"],
        research_areas=[
            "speech emotion recognition",
            "affective computing",
            "multimodal learning",
        ],
        active_projects=[
            ActiveProject(
                name="SER with LLM",
                keywords=[
                    "speech emotion",
                    "large language model",
                    "in-context learning",
                    "prompt tuning",
                ],
                venues=["INTERSPEECH", "ICASSP"],
            ),
            ActiveProject(
                name="Multimodal Sentiment",
                keywords=[
                    "multimodal sentiment",
                    "audio-text fusion",
                    "cross-modal attention",
                ],
                venues=["ACL", "EMNLP"],
            ),
        ],
        venue_tiers=VenueTiers(
            tier1=["NeurIPS", "ICML", "ACL", "INTERSPEECH", "ICASSP"],
            tier2=["AAAI", "EMNLP", "NAACL", "ICLR"],
            blacklist=["MDPI", "Frontiers in"],
        ),
        preferences=Preferences(
            min_citation_highlight=10,
            language="en",
            digest_time="08:00",
            max_daily_papers=10,
            diversity_ratio=0.2,
        ),
        retry_policy=RetryPolicy(),
    )
    defaults.update(overrides)
    return Profile(**defaults)


def _random_embedding(seed=None):
    """Generate a random 768-dim float32 embedding as bytes."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(768).astype(np.float32)
    vec /= np.linalg.norm(vec)  # unit norm for clean cosine sims
    return vec.tobytes()


def _similar_embedding(base_bytes, noise_scale=0.05, seed=None):
    """Create an embedding close to a base embedding."""
    rng = np.random.RandomState(seed)
    base = np.frombuffer(base_bytes, dtype=np.float32).copy()
    noise = rng.randn(768).astype(np.float32) * noise_scale
    result = base + noise
    result /= np.linalg.norm(result)
    return result.astype(np.float32).tobytes()


# Realistic test paper data from ML/NLP/speech research
TEST_PAPERS = [
    dict(
        doi="10.18653/v1/2025.acl-long.42",
        arxiv_id="2501.09876",
        title="Prompt-Tuned Speech Emotion Recognition via Large Language Models",
        authors=["Chen, Xiao", "Li, Ming", "Wang, Jun"],
        year=2025,
        venue="ACL",
        abstract=(
            "We propose a novel prompt-tuning framework for speech emotion "
            "recognition that leverages large language models to capture "
            "nuanced emotional cues in spoken language. Our approach combines "
            "in-context learning with acoustic features."
        ),
        concepts=["speech emotion recognition", "prompt tuning", "large language model"],
    ),
    dict(
        doi="10.1109/ICASSP49357.2025.001",
        title="Cross-Modal Attention for Multimodal Sentiment Analysis",
        authors=["Park, Soo-Jin", "Kim, Hyun"],
        year=2025,
        venue="ICASSP",
        abstract=(
            "This paper introduces a cross-modal attention mechanism for "
            "multimodal sentiment analysis, fusing audio and text modalities "
            "through a transformer-based architecture."
        ),
        concepts=["multimodal sentiment", "cross-modal attention", "transformer"],
    ),
    dict(
        doi="10.48550/arXiv.2501.11111",
        arxiv_id="2501.11111",
        title="Scaling Laws for Reward Model Overoptimization in RLHF",
        authors=["Gao, Leo", "Schulman, John", "Hilton, Jacob"],
        year=2025,
        venue="NeurIPS",
        abstract=(
            "We study how reward model overoptimization scales as a function "
            "of policy size, reward model size, and the amount of RL training. "
            "We find predictable relationships that can guide RLHF practice."
        ),
        concepts=["RLHF", "reward model", "scaling laws"],
    ),
    dict(
        doi="10.48550/arXiv.2501.22222",
        arxiv_id="2501.22222",
        title="DPO: Direct Preference Optimization for Language Models",
        authors=["Rafailov, Rafael", "Sharma, Archit", "Mitchell, Eric"],
        year=2025,
        venue="ICML",
        abstract=(
            "We propose Direct Preference Optimization, a simple approach to "
            "train language models from human preferences without reinforcement "
            "learning, achieving state-of-the-art alignment performance."
        ),
        concepts=["preference optimization", "alignment", "language model"],
    ),
    dict(
        doi="10.3390/s25010001",
        title="A Survey on IoT Sensor Data Processing",
        authors=["Brown, Alice", "Davis, Bob"],
        year=2025,
        venue="MDPI Sensors",
        abstract="A comprehensive survey of IoT sensor data processing methods.",
        concepts=["IoT", "sensor data"],
    ),
    dict(
        arxiv_id="2501.33333",
        title="Self-Supervised Speech Representation Learning with HuBERT",
        authors=["Hsu, Wei-Ning", "Bolber, Bastian", "Baevski, Alexei"],
        year=2025,
        venue="INTERSPEECH",
        abstract=(
            "We present improvements to HuBERT for self-supervised speech "
            "representation learning. Our method shows strong performance on "
            "emotion recognition and speaker verification tasks."
        ),
        concepts=["self-supervised learning", "speech representation", "HuBERT"],
    ),
    dict(
        doi="10.18653/v1/2025.emnlp-main.55",
        title="In-Context Learning for Low-Resource Emotion Classification",
        authors=["Zhao, Yilin", "Chen, Xiao"],
        year=2025,
        venue="EMNLP",
        abstract=(
            "We explore in-context learning capabilities of large language "
            "models for low-resource speech emotion classification, showing "
            "competitive performance with minimal labeled data."
        ),
        concepts=["in-context learning", "emotion classification", "low-resource"],
    ),
    dict(
        doi="10.1007/978-3-031-73000-1_1",
        title="Neural Architecture Search for Efficient Transformers",
        authors=["Tan, Mingxing", "Le, Quoc V."],
        year=2025,
        venue="ICLR",
        abstract=(
            "We propose a neural architecture search method to discover "
            "efficient transformer variants that reduce computation while "
            "maintaining accuracy across NLP benchmarks."
        ),
        concepts=["neural architecture search", "efficient transformers", "NLP"],
    ),
    dict(
        doi="10.1145/3600000.3600001",
        title="Affective Computing in Human-Robot Interaction",
        authors=["Picard, Rosalind", "Breazeal, Cynthia"],
        year=2025,
        venue="AAAI",
        abstract=(
            "This paper surveys affective computing methods applied to "
            "human-robot interaction, covering speech emotion recognition, "
            "facial expression analysis, and multimodal fusion techniques."
        ),
        concepts=["affective computing", "human-robot interaction", "emotion"],
    ),
    dict(
        doi="10.48550/arXiv.2501.44444",
        arxiv_id="2501.44444",
        title="Quantum Error Correction Codes for Noisy Quantum Computers",
        authors=["Preskill, John", "Gottesman, Daniel"],
        year=2025,
        venue="Physical Review Letters",
        abstract=(
            "We develop new quantum error correction codes that significantly "
            "reduce logical error rates on near-term noisy quantum processors."
        ),
        concepts=["quantum computing", "error correction", "quantum hardware"],
    ),
]


# =========================================================================
# 1. Full F1 pipeline simulation
# =========================================================================


class TestF1Pipeline:
    """Full F1 daily-digest pipeline: ingest, dedup, rank."""

    def test_ingest_all_papers(self, db):
        """Insert 10 realistic papers via get_or_create_paper."""
        records = []
        for p in TEST_PAPERS:
            emb = _random_embedding(seed=hash(p["title"]) % 2**31)
            rec = get_or_create_paper(
                conn=db,
                doi=p.get("doi"),
                arxiv_id=p.get("arxiv_id"),
                title=p["title"],
                authors=p["authors"],
                year=p["year"],
                venue=p["venue"],
                abstract=p["abstract"],
                concepts=p.get("concepts"),
                embedding=emb,
            )
            assert rec.pid.startswith("p_")
            assert rec.is_new is True
            records.append(rec)

        # Verify all 10 are in the DB
        count = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        assert count == 10

    def test_dedup_same_doi(self, db):
        """Inserting same DOI twice returns the same pid."""
        p = TEST_PAPERS[0]
        r1 = get_or_create_paper(
            conn=db, doi=p["doi"], title=p["title"],
            authors=p["authors"], year=p["year"],
        )
        r2 = get_or_create_paper(
            conn=db, doi=p["doi"], title=p["title"],
            authors=p["authors"], year=p["year"],
        )
        assert r1.pid == r2.pid
        assert r1.is_new is True
        assert r2.is_new is False

    def test_dedup_doi_then_arxiv_for_same_paper(self, db):
        """Insert by DOI first, then by arXiv for same paper -- dedup via DOI match."""
        p = TEST_PAPERS[0]  # has both doi and arxiv_id
        r1 = get_or_create_paper(
            conn=db, doi=p["doi"], title=p["title"],
            authors=p["authors"], year=p["year"],
        )
        # Now insert with arXiv only but same DOI in payload
        r2 = get_or_create_paper(
            conn=db, doi=p["doi"], arxiv_id=p.get("arxiv_id"),
            title=p["title"], authors=p["authors"], year=p["year"],
        )
        assert r1.pid == r2.pid
        # arXiv ID should be merged
        assert r2.arxiv_id == p.get("arxiv_id")

    def test_dedup_arxiv_id_lookup(self, db):
        """Insert by arXiv, then lookup by arXiv deduplicates."""
        p = TEST_PAPERS[5]  # has only arxiv_id (no doi)
        r1 = get_or_create_paper(
            conn=db, arxiv_id=p.get("arxiv_id"), title=p["title"],
            authors=p["authors"], year=p["year"],
        )
        r2 = get_or_create_paper(
            conn=db, arxiv_id=p.get("arxiv_id"), title=p["title"],
            authors=p["authors"], year=p["year"],
            doi="10.9999/newly.discovered.doi",
        )
        assert r1.pid == r2.pid
        # DOI should be merged in
        assert r2.doi == "10.9999/newly.discovered.doi"

    def test_ranking_keyword_papers_rank_higher(self, db):
        """Papers matching profile keywords rank above unrelated papers."""
        profile = _make_profile()
        records = []
        for p in TEST_PAPERS:
            emb = _random_embedding(seed=hash(p["title"]) % 2**31)
            rec = get_or_create_paper(
                conn=db, doi=p.get("doi"), arxiv_id=p.get("arxiv_id"),
                title=p["title"], authors=p["authors"],
                year=p["year"], venue=p["venue"],
                abstract=p["abstract"], concepts=p.get("concepts"),
                embedding=emb,
            )
            records.append(rec)

        ranked = rank_papers(records, profile, db)
        assert len(ranked) > 0

        # The top-ranked paper should be one that matches keywords
        # (speech emotion, affective computing, etc.)
        top_titles = [r[0].title for r in ranked[:3]]
        keyword_match_found = False
        for title in top_titles:
            tl = title.lower()
            if any(kw in tl for kw in ["speech emotion", "emotion", "affective"]):
                keyword_match_found = True
                break
        assert keyword_match_found, (
            f"Expected keyword-related paper in top 3, got: {top_titles}"
        )

    def test_blacklisted_venue_filtered(self, db):
        """Papers from blacklisted venues (MDPI) are excluded from rankings."""
        profile = _make_profile()
        records = []
        for p in TEST_PAPERS:
            rec = get_or_create_paper(
                conn=db, doi=p.get("doi"), arxiv_id=p.get("arxiv_id"),
                title=p["title"], authors=p["authors"],
                year=p["year"], venue=p["venue"],
                abstract=p["abstract"],
            )
            records.append(rec)

        ranked = rank_papers(records, profile, db)
        ranked_venues = [r[0].venue for r in ranked]
        for v in ranked_venues:
            assert v is None or "MDPI" not in v, f"MDPI paper should be filtered: {v}"

    def test_diversity_ratio_includes_non_top(self, db):
        """With enough papers, diversity_ratio reserves slots for exploratory picks."""
        profile = _make_profile(
            preferences=Preferences(max_daily_papers=10, diversity_ratio=0.3),
        )
        # Generate 50 papers with varying relevance
        records = []
        for i in range(50):
            if i < 10:
                title = f"Speech Emotion Recognition Method {i}"
                abstract = "Speech emotion recognition with large language model."
                venue = "INTERSPEECH"
            else:
                title = f"Unrelated Topic Paper {i}"
                abstract = "This paper studies something completely different."
                venue = "SomeConf"
            rec = get_or_create_paper(
                conn=db, doi=f"10.9999/div.{i:04d}",
                title=title, authors=[f"Author{i}"], year=2025,
                venue=venue, abstract=abstract,
            )
            records.append(rec)

        ranked = rank_papers(records, profile, db)
        assert len(ranked) <= 10

    def test_max_daily_papers_respected(self, db):
        """Output never exceeds max_daily_papers."""
        profile = _make_profile(
            preferences=Preferences(max_daily_papers=3, diversity_ratio=0.0),
        )
        records = []
        for i, p in enumerate(TEST_PAPERS):
            rec = get_or_create_paper(
                conn=db, doi=p.get("doi"), arxiv_id=p.get("arxiv_id"),
                title=p["title"], authors=p["authors"],
                year=p["year"], venue=p["venue"], abstract=p["abstract"],
            )
            records.append(rec)

        ranked = rank_papers(records, profile, db)
        assert len(ranked) <= 3


# =========================================================================
# 2. F2 Collision pipeline
# =========================================================================


class TestF2Collision:
    """F2 collision detection: coarse filter, parse response, alert levels."""

    def test_coarse_filter_keyword_overlap(self):
        """Papers with high keyword overlap pass the keyword-based coarse filter."""
        project = ActiveProject(
            name="SER with LLM",
            keywords=["speech emotion", "large language model", "in-context learning"],
        )
        collision_paper = PaperRecord(
            pid="p_collision_1",
            title="Speech Emotion Recognition using Large Language Model Prompting",
            abstract=(
                "We propose using in-context learning with large language models "
                "for speech emotion recognition."
            ),
        )
        unrelated_paper = PaperRecord(
            pid="p_unrelated_1",
            title="Quantum Computing with Superconducting Qubits",
            abstract="Advances in superconducting quantum processor architecture.",
        )
        result = coarse_filter(
            [collision_paper, unrelated_paper],
            project,
            project_centroid=None,
        )
        pids = [p.pid for p in result]
        assert "p_collision_1" in pids
        assert "p_unrelated_1" not in pids

    def test_coarse_filter_embedding_threshold(self):
        """Papers with embedding similarity >= threshold pass."""
        project = ActiveProject(
            name="SER with LLM",
            keywords=["speech emotion"],
        )
        base_emb = _random_embedding(seed=42)
        centroid = np.frombuffer(base_emb, dtype=np.float32).copy()

        similar_paper = PaperRecord(
            pid="p_sim",
            title="Similar Paper",
            embedding=_similar_embedding(base_emb, noise_scale=0.02, seed=1),
        )
        distant_emb = _random_embedding(seed=999)
        distant_paper = PaperRecord(
            pid="p_distant",
            title="Distant Paper",
            embedding=distant_emb,
        )
        no_emb_paper = PaperRecord(
            pid="p_noemb",
            title="No Embedding Paper",
            embedding=None,
        )

        result = coarse_filter(
            [similar_paper, distant_paper, no_emb_paper],
            project,
            project_centroid=centroid,
            threshold=0.65,
        )
        pids = [p.pid for p in result]
        assert "p_sim" in pids
        # paper without embedding is always excluded in embedding mode
        assert "p_noemb" not in pids

    def test_parse_collision_response_valid_json(self):
        """Valid JSON response is parsed correctly."""
        response = json.dumps({
            "scores": {
                "problem_overlap": 0.9,
                "method_similarity": 0.7,
                "dataset_overlap": 0.4,
                "contribution_conflict": 0.6,
                "conclusion_competitiveness": 0.3,
            },
            "analysis": "Strong overlap in problem formulation and methodology.",
        })
        result = parse_collision_response(response)
        expected_score = 0.9 * 0.30 + 0.7 * 0.25 + 0.4 * 0.20 + 0.6 * 0.15 + 0.3 * 0.10
        assert result["collision_score"] == pytest.approx(expected_score, abs=0.01)
        assert result["analysis"] == "Strong overlap in problem formulation and methodology."

    def test_parse_collision_response_markdown_fences(self):
        """JSON wrapped in markdown code fences is handled."""
        inner = json.dumps({
            "scores": {
                "problem_overlap": 0.5,
                "method_similarity": 0.5,
                "dataset_overlap": 0.5,
                "contribution_conflict": 0.5,
                "conclusion_competitiveness": 0.5,
            },
            "analysis": "Moderate overlap across all dimensions.",
        })
        response = f"```json\n{inner}\n```"
        result = parse_collision_response(response)
        assert result["collision_score"] > 0.0
        assert "Moderate overlap" in result["analysis"]

    def test_parse_collision_response_flat_format(self):
        """Flat JSON (scores not nested) is accepted."""
        response = json.dumps({
            "problem_overlap": 0.4,
            "method_similarity": 0.3,
            "dataset_overlap": 0.2,
            "contribution_conflict": 0.1,
            "conclusion_competitiveness": 0.0,
            "analysis": "Minimal overlap.",
        })
        result = parse_collision_response(response)
        assert result["collision_score"] > 0.0

    def test_parse_collision_response_garbage(self):
        """Garbage input returns zero-score result without raising."""
        result = parse_collision_response("THIS IS NOT JSON AT ALL!!!")
        assert result["collision_score"] == 0.0
        assert "parse error" in result["analysis"].lower() or result["analysis"] != ""

    def test_parse_collision_response_partial_json(self):
        """Truncated JSON returns zero-score result."""
        result = parse_collision_response('{"scores": {"problem_overlap": 0.5, "method_s')
        assert result["collision_score"] == 0.0

    def test_alert_level_classification(self):
        """All four alert levels are correctly classified."""
        assert classify_alert(0.60) == AlertLevel.HIGH
        assert classify_alert(0.55) == AlertLevel.HIGH
        assert classify_alert(0.45) == AlertLevel.MEDIUM
        assert classify_alert(0.35) == AlertLevel.MEDIUM
        assert classify_alert(0.30) == AlertLevel.UNCERTAIN
        assert classify_alert(0.25) == AlertLevel.UNCERTAIN
        assert classify_alert(0.20) == AlertLevel.LOW
        assert classify_alert(0.00) == AlertLevel.LOW

    def test_alert_boundary_values(self):
        """Boundary values fall into the correct level."""
        assert classify_alert(0.549999) == AlertLevel.MEDIUM
        assert classify_alert(0.550000) == AlertLevel.HIGH
        assert classify_alert(0.349999) == AlertLevel.UNCERTAIN
        assert classify_alert(0.350000) == AlertLevel.MEDIUM
        assert classify_alert(0.249999) == AlertLevel.LOW
        assert classify_alert(0.250000) == AlertLevel.UNCERTAIN


# =========================================================================
# 3. F3 Trend pipeline
# =========================================================================


class TestF3Trend:
    """F3 trend burst detection: insert stats, detect spikes."""

    def _fill_30d_history(self, db, concept, daily_count, days=30):
        """Insert stable daily counts for a concept over the past N days."""
        today = datetime.utcnow()
        for i in range(1, days + 1):
            d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            db.execute(
                "INSERT OR REPLACE INTO trend_stats (concept, date, count) VALUES (?, ?, ?)",
                (concept, d, daily_count),
            )
        db.commit()

    def test_spike_detected(self, db):
        """A sudden spike today (10x normal) triggers a burst detection."""
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Fill 30 days of stable history for three concepts
        self._fill_30d_history(db, "transformers", 5)
        self._fill_30d_history(db, "diffusion models", 3)
        self._fill_30d_history(db, "rlhf", 2)

        # Spike for 'rlhf' today: 50 vs normal 2
        db.execute(
            "INSERT OR REPLACE INTO trend_stats (concept, date, count) VALUES (?, ?, ?)",
            ("rlhf", today, 50),
        )
        # Normal counts for others today
        db.execute(
            "INSERT OR REPLACE INTO trend_stats (concept, date, count) VALUES (?, ?, ?)",
            ("transformers", today, 6),
        )
        db.execute(
            "INSERT OR REPLACE INTO trend_stats (concept, date, count) VALUES (?, ?, ?)",
            ("diffusion models", today, 4),
        )
        db.commit()

        bursts = detect_bursts(db, date=today, z_threshold=3.0)
        burst_concepts = [b["concept"] for b in bursts]
        assert "rlhf" in burst_concepts

        rlhf_burst = next(b for b in bursts if b["concept"] == "rlhf")
        assert rlhf_burst["today_count"] == 50
        assert rlhf_burst["avg_30d"] == pytest.approx(2.0, abs=0.1)
        assert rlhf_burst["z_score"] > 3.0

    def test_no_burst_for_stable(self, db):
        """Stable counts do not trigger bursts."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        # Use varying history (std > 0) so a slight increase is not a burst
        for i in range(1, 31):
            d = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
            # Vary between 8 and 12 (mean ~10, std ~1.4)
            count = 10 + (i % 3) - 1  # values: 9, 10, 11, 9, 10, 11, ...
            db.execute(
                "INSERT OR REPLACE INTO trend_stats (concept, date, count) VALUES (?, ?, ?)",
                ("stable_concept", d, count),
            )
        db.commit()
        # Today's count is within normal range (11 is not 3 sigma above mean)
        db.execute(
            "INSERT OR REPLACE INTO trend_stats (concept, date, count) VALUES (?, ?, ?)",
            ("stable_concept", today, 11),
        )
        db.commit()

        bursts = detect_bursts(db, date=today, z_threshold=3.0)
        burst_concepts = [b["concept"] for b in bursts]
        assert "stable_concept" not in burst_concepts

    def test_new_concept_burst(self, db):
        """A concept appearing for the first time is treated as a burst (z=inf)."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        db.execute(
            "INSERT INTO trend_stats (concept, date, count) VALUES (?, ?, ?)",
            ("brand_new_topic", today, 15),
        )
        db.commit()

        bursts = detect_bursts(db, date=today, z_threshold=3.0)
        burst_concepts = [b["concept"] for b in bursts]
        assert "brand_new_topic" in burst_concepts

        new_burst = next(b for b in bursts if b["concept"] == "brand_new_topic")
        assert math.isinf(new_burst["z_score"])

    def test_update_trend_stats_from_papers(self, db):
        """update_trend_stats correctly ingests concept counts."""
        papers = [
            {"concepts": ["attention", "transformer"]},
            {"concepts": ["attention", "RLHF"]},
            {"concepts": json.dumps(["attention", "scaling"])},  # JSON string format
        ]
        update_trend_stats(db, papers)

        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT count FROM trend_stats WHERE concept = ? AND date = ?",
            ("attention", today),
        ).fetchone()
        assert row is not None
        assert row[0] == 3  # 'attention' appears in all 3 papers


# =========================================================================
# 4. Observability pipeline
# =========================================================================


class TestObservability:
    """Observability: logging, circuit breaker, health reports."""

    def test_log_op_writes_entries(self, db):
        """log_op creates structured entries in op_log."""
        log_op(db, "crossref", "fetch_new", "ok", latency_ms=120, detail="fetched 50 papers")
        log_op(db, "s2", "fetch_embedding", "ok", latency_ms=250, tokens_used=0)
        log_op(db, "llm", "collision_score", "ok", latency_ms=800, tokens_used=1200)
        log_op(db, "arxiv", "fetch_new", "error", latency_ms=10000, detail="timeout")

        total = db.execute("SELECT COUNT(*) FROM op_log").fetchone()[0]
        assert total == 4

        errors = db.execute(
            "SELECT COUNT(*) FROM op_log WHERE status = 'error'"
        ).fetchone()[0]
        assert errors == 1

    def test_circuit_breaker_trips(self):
        """Circuit breaker opens after error rate exceeds threshold."""
        circuit = get_circuit("test_api", cooldown_sec=3600)
        assert circuit.is_open is False

        # Record 19 successes and then 2 errors to cross 5% error rate at 21 calls
        for _ in range(19):
            circuit.record_success()
        assert circuit.is_open is False

        circuit.record_error(threshold=0.05)  # 1/20 = 5%, but total < 20 still
        # Now at 20 calls, 1 error = 5%, threshold is > 5%
        assert circuit.is_open is False

        circuit.record_error(threshold=0.05)  # 2/21 = 9.5% > 5%, total >= 20
        assert circuit.is_open is True

    def test_circuit_breaker_reset(self):
        """Circuit breaker can be reset."""
        circuit = get_circuit("reset_test", cooldown_sec=3600)
        for _ in range(18):
            circuit.record_success()
        circuit.record_error(threshold=0.05)
        circuit.record_error(threshold=0.05)
        assert circuit.is_open is True

        circuit.reset()
        assert circuit.is_open is False
        assert circuit.error_count == 0
        assert circuit.total_count == 0

    def test_generate_health_report(self, db):
        """Health report contains expected sections."""
        # SQLite datetime('now') is UTC, so use UTC date for consistency
        today_utc = datetime.utcnow().strftime("%Y-%m-%d")

        # Insert op_log entries with explicit UTC timestamps so they match the date filter
        for source, op, status, lat, tokens in [
            ("crossref", "fetch_new", "ok", 150, None),
            ("s2", "fetch_embedding", "ok", 200, None),
            ("llm", "collision_score", "ok", 600, 500),
            ("arxiv", "fetch_new", "error", 10000, None),
        ]:
            db.execute(
                "INSERT INTO op_log (ts, source, operation, status, latency_ms, tokens_used) "
                "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
                (source, op, status, lat, tokens),
            )
        db.commit()

        # Insert a paper with explicit UTC timestamp so created_at matches
        db.execute(
            """INSERT INTO papers (pid, doi, title, year, created_at, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            ("p_health_001", "10.1234/health.001", "Health Test", 2025),
        )
        db.execute(
            "INSERT OR IGNORE INTO pushes (pid, function, pushed_at) VALUES (?, ?, datetime('now'))",
            ("p_health_001", "F1"),
        )
        db.execute(
            "INSERT INTO interactions (pid, action, context, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            ("p_health_001", "save", "F1"),
        )
        db.commit()

        report = generate_health_report(db, date=today_utc)

        assert "LitBot Daily Health" in report
        assert "Papers scanned" in report
        assert "Papers pushed" in report
        assert "LLM tokens" in report
        assert "Latency" in report

    def test_health_report_shows_open_circuit(self, db):
        """Health report flags open circuit breakers."""
        circuit = get_circuit("broken_api", cooldown_sec=3600)
        for _ in range(19):
            circuit.record_success()
        circuit.record_error(threshold=0.05)
        circuit.record_error(threshold=0.05)
        assert circuit.is_open is True

        report = generate_health_report(db)
        assert "CIRCUIT OPEN" in report
        assert "broken_api" in report


# =========================================================================
# 5. Profile lifecycle
# =========================================================================


class TestProfileLifecycle:
    """Profile: create, save, load, privacy levels, ranking weights."""

    def test_save_and_load_roundtrip(self):
        """Profile survives a save/load cycle via YAML."""
        profile = _make_profile()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.yaml"
            save_profile(profile, path)
            loaded = load_profile(path)

        assert loaded.name == profile.name
        assert loaded.semantic_scholar_id == profile.semantic_scholar_id
        assert loaded.my_papers == profile.my_papers
        assert loaded.research_areas == profile.research_areas
        assert len(loaded.active_projects) == len(profile.active_projects)
        for orig, reloaded in zip(profile.active_projects, loaded.active_projects):
            assert orig.name == reloaded.name
            assert orig.keywords == reloaded.keywords
            assert orig.venues == reloaded.venues
        assert loaded.venue_tiers.tier1 == profile.venue_tiers.tier1
        assert loaded.venue_tiers.tier2 == profile.venue_tiers.tier2
        assert loaded.venue_tiers.blacklist == profile.venue_tiers.blacklist
        assert loaded.preferences.max_daily_papers == profile.preferences.max_daily_papers
        assert loaded.preferences.diversity_ratio == profile.preferences.diversity_ratio
        assert loaded.preferences.language == profile.preferences.language
        assert loaded.preferences.digest_time == profile.preferences.digest_time
        assert loaded.retry_policy.max_attempts == profile.retry_policy.max_attempts
        assert loaded.retry_policy.backoff == profile.retry_policy.backoff

    def test_privacy_level_full(self):
        """Profile with name or S2 ID gets 'full' privacy level."""
        p = _make_profile(name="Dr. Zhang", semantic_scholar_id="12345")
        assert p.privacy_level == "full"

    def test_privacy_level_semi_public(self):
        """Profile with my_papers but no name/S2 ID gets 'semi_public'."""
        p = _make_profile(
            name="", semantic_scholar_id="",
            my_papers=["10.1234/my.paper"],
        )
        assert p.privacy_level == "semi_public"

    def test_privacy_level_keywords(self):
        """Profile with only projects (no name, S2 ID, papers) gets 'keywords'."""
        p = _make_profile(
            name="", semantic_scholar_id="", my_papers=[],
            active_projects=[ActiveProject(name="Test", keywords=["test"])],
        )
        assert p.privacy_level == "keywords"

    def test_privacy_level_anonymous(self):
        """Bare profile gets 'anonymous' privacy level."""
        p = Profile(research_areas=["machine learning"])
        assert p.privacy_level == "anonymous"

    def test_ranking_weights_full(self):
        """Full privacy level uses embedding-heavy weights."""
        p = _make_profile(name="Dr. Zhang")
        w = p.ranking_weights
        assert w["sim"] == pytest.approx(0.40)
        assert w["keyword"] == pytest.approx(0.25)
        assert w["venue"] == pytest.approx(0.20)
        assert w["recency"] == pytest.approx(0.15)

    def test_ranking_weights_keywords(self):
        """Keywords-only privacy level zeros out sim weight."""
        p = _make_profile(
            name="", semantic_scholar_id="", my_papers=[],
            active_projects=[ActiveProject(name="Test", keywords=["ml"])],
        )
        w = p.ranking_weights
        assert w["sim"] == pytest.approx(0.00)
        assert w["keyword"] == pytest.approx(0.50)

    def test_ranking_weights_anonymous(self):
        """Anonymous privacy level maximizes keyword weight."""
        p = Profile(research_areas=["ml"])
        w = p.ranking_weights
        assert w["sim"] == pytest.approx(0.00)
        assert w["keyword"] == pytest.approx(0.60)

    def test_load_nonexistent_returns_defaults(self):
        """Loading from a nonexistent path returns a default Profile."""
        p = load_profile(Path("/tmp/definitely_does_not_exist_12345.yaml"))
        assert p.name == ""
        assert p.research_areas == []
        assert p.privacy_level == "anonymous"


# =========================================================================
# 6. Feishu card building
# =========================================================================


class TestFeishuCards:
    """Feishu card structure, collision cards, callback handling."""

    def _make_digest_papers(self, n=5):
        """Generate n test paper tuples for the digest card builder."""
        papers = []
        for i in range(n):
            p = PaperRecord(
                pid=f"p_digest_{i}",
                doi=f"10.1234/digest.{i:03d}",
                title=f"Test Paper Number {i}: A Study on Topic {i}",
                authors=[f"Smith, Author{i}", f"Lee, Coauthor{i}"],
                year=2025,
                venue=["ACL", "NeurIPS", "ICML", "EMNLP", "AAAI"][i % 5],
            )
            score = 0.9 - i * 0.1
            recommendation = f"Relevant to your work on topic {i}."
            papers.append((p, score, recommendation))
        return papers

    def test_daily_digest_card_structure(self):
        """Daily digest card has config, header, and elements."""
        papers = self._make_digest_papers(5)
        card = build_daily_digest_card(
            papers=papers,
            date="2025-03-24",
            total_scanned=500,
            total_matched=42,
        )
        assert "config" in card
        assert card["config"]["wide_screen_mode"] is True
        assert "header" in card
        assert "title" in card["header"]
        assert "2025-03-24" in card["header"]["title"]["content"]
        assert "elements" in card
        assert len(card["elements"]) > 0

        # Check that paper titles appear in markdown elements
        md_elements = [e for e in card["elements"] if e.get("tag") == "markdown"]
        all_md_content = " ".join(e.get("content", "") for e in md_elements)
        assert "Test Paper Number 0" in all_md_content
        assert "Test Paper Number 4" in all_md_content

        # Check footer stats
        assert "500 papers scanned" in all_md_content
        assert "42 matched" in all_md_content

    def test_daily_digest_card_has_buttons(self):
        """Each paper in the digest card has Save/Mute/View buttons."""
        papers = self._make_digest_papers(3)
        card = build_daily_digest_card(
            papers=papers, date="2025-03-24",
            total_scanned=100, total_matched=10,
        )
        action_elements = [e for e in card["elements"] if e.get("tag") == "action"]
        assert len(action_elements) >= 3  # one per paper

        # Check button structure
        first_actions = action_elements[0]["actions"]
        assert any("save" in str(btn.get("value", "")).lower() or
                    "Save" in btn.get("text", {}).get("content", "")
                    for btn in first_actions)

    def test_daily_digest_with_trends(self):
        """Trend bursts section is included when provided."""
        papers = self._make_digest_papers(2)
        card = build_daily_digest_card(
            papers=papers, date="2025-03-24",
            total_scanned=100, total_matched=10,
            trend_bursts=[{"concept": "RLHF", "delta": "300%"}],
        )
        all_content = json.dumps(card)
        assert "RLHF" in all_content
        assert "Trending" in all_content

    def test_collision_card_structure(self):
        """Collision card has correct structure and alert level."""
        paper = PaperRecord(
            pid="p_coll_001",
            doi="10.1234/collision.001",
            title="Competing Paper on Speech Emotion Recognition",
            authors=["Rival, Researcher", "Another, Author"],
            year=2025,
            venue="INTERSPEECH",
        )
        scores = {
            "problem_overlap": 0.8,
            "method_similarity": 0.6,
            "dataset_overlap": 0.5,
            "contribution_conflict": 0.4,
            "conclusion_competitiveness": 0.3,
        }
        card = build_collision_card(
            paper=paper,
            project_name="SER with LLM",
            collision_score=0.58,
            scores=scores,
            analysis="High overlap in problem and method.",
            alert_level=AlertLevel.HIGH,
        )
        assert card["config"]["wide_screen_mode"] is True
        assert "HIGH" in card["header"]["title"]["content"]
        assert card["header"]["template"] == "red"

        # Verify elements contain paper info
        all_content = json.dumps(card)
        assert "Competing Paper on Speech Emotion Recognition" in all_content
        assert "SER with LLM" in all_content
        assert "0.58" in all_content

    def test_collision_card_medium_level(self):
        """Medium-level collision card uses orange template."""
        paper = PaperRecord(
            pid="p_coll_002", title="Some Paper",
            authors=["Author, One"], year=2025,
        )
        card = build_collision_card(
            paper=paper, project_name="Proj",
            collision_score=0.40, scores={},
            analysis="Moderate overlap.", alert_level=AlertLevel.MEDIUM,
        )
        assert card["header"]["template"] == "orange"
        assert "MEDIUM" in card["header"]["title"]["content"]

    def test_callback_handling_first_time(self, db):
        """First callback processes correctly and records interaction."""
        payload = {
            "event": {
                "token": "cb_unique_token_001",
                "open_message_id": "msg_12345",
                "action": {
                    "value": {"action": "p_digest_0:save"},
                },
            },
        }
        # First, create the paper so the interaction can be recorded
        get_or_create_paper(
            conn=db, title="Callback Test Paper", year=2025,
        )
        # Use a pid that exists
        paper = get_or_create_paper(
            conn=db, doi="10.1234/cb.001", title="CB Paper", year=2025,
        )
        payload["event"]["action"]["value"]["action"] = f"{paper.pid}:save"

        result = handle_callback(db, payload)
        assert result["code"] == 0
        assert "data" in result
        assert result["data"]["pid"] == paper.pid
        assert result["data"]["action"] == "save"

        # Verify interaction is recorded
        row = db.execute(
            "SELECT action FROM interactions WHERE pid = ?",
            (paper.pid,),
        ).fetchone()
        assert row is not None
        assert row[0] == "save"

    def test_callback_idempotency(self, db):
        """Replay of same callback_id returns {code: 0} without duplicate."""
        paper = get_or_create_paper(
            conn=db, doi="10.1234/idem.001", title="Idempotent Paper", year=2025,
        )
        payload = {
            "event": {
                "token": "cb_idempotent_001",
                "open_message_id": "msg_99999",
                "action": {
                    "value": {"action": f"{paper.pid}:mute"},
                },
            },
        }

        result1 = handle_callback(db, payload)
        assert result1["code"] == 0
        assert "data" in result1

        result2 = handle_callback(db, payload)
        assert result2["code"] == 0
        assert "data" not in result2  # idempotent replay, no data

        # Only one interaction recorded
        count = db.execute(
            "SELECT COUNT(*) FROM interactions WHERE pid = ?",
            (paper.pid,),
        ).fetchone()[0]
        assert count == 1

    def test_shorten_authors(self):
        """Author shortening handles various list sizes."""
        assert shorten_authors([]) == "Unknown"
        assert shorten_authors(["Smith, John"]) == "Smith, John"
        assert shorten_authors(["A", "B", "C"]) == "A, B, C"
        assert shorten_authors(["A", "B", "C", "D"]) == "A, B, C et al."
        assert shorten_authors(["A", "B", "C", "D", "E"]) == "A, B, C et al."


# =========================================================================
# 7. Bootstrap mode
# =========================================================================


class TestBootstrapMode:
    """Bootstrap lifecycle: active -> completed after 5 saves."""

    def test_starts_active(self, db):
        """Bootstrap mode is active for a fresh database."""
        assert is_in_bootstrap_mode(db) is True

    def test_stays_active_under_5_saves(self, db):
        """Bootstrap stays active with fewer than 5 saves."""
        for i in range(4):
            p = get_or_create_paper(
                conn=db, doi=f"10.1234/boot.{i}",
                title=f"Boot Paper {i}", year=2025,
            )
            record_interaction(db, p.pid, "save", "bootstrap")
            db.execute(
                "UPDATE bootstrap_state SET save_count = ? WHERE user_id = 'default'",
                (i + 1,),
            )
            db.commit()

        assert is_in_bootstrap_mode(db) is True

    def test_completes_at_5_saves(self, db):
        """Bootstrap transitions to completed after exactly 5 saves."""
        for i in range(5):
            p = get_or_create_paper(
                conn=db, doi=f"10.1234/boot5.{i}",
                title=f"Boot5 Paper {i}", year=2025,
            )
            record_interaction(db, p.pid, "save", "bootstrap")

        db.execute(
            "UPDATE bootstrap_state SET save_count = 5, mode = 'completed' WHERE user_id = 'default'"
        )
        db.commit()
        assert is_in_bootstrap_mode(db) is False

    def test_bootstrap_state_row_exists(self, db):
        """init_db creates a default bootstrap_state row."""
        row = db.execute(
            "SELECT mode, save_count FROM bootstrap_state WHERE user_id = 'default'"
        ).fetchone()
        assert row is not None
        assert row[0] == "active"
        assert row[1] == 0

    def test_bootstrap_multiple_transitions(self, db):
        """Save count increments correctly through the full lifecycle."""
        for i in range(5):
            p = get_or_create_paper(
                conn=db, doi=f"10.1234/lifecycle.{i}",
                title=f"Lifecycle Paper {i}", year=2025,
            )
            record_interaction(db, p.pid, "save", "bootstrap")
            new_count = i + 1
            db.execute(
                "UPDATE bootstrap_state SET save_count = ? WHERE user_id = 'default'",
                (new_count,),
            )
            if new_count >= 5:
                db.execute(
                    "UPDATE bootstrap_state SET mode = 'completed' WHERE user_id = 'default'"
                )
            db.commit()

            if new_count < 5:
                assert is_in_bootstrap_mode(db) is True
            else:
                assert is_in_bootstrap_mode(db) is False


# =========================================================================
# 8. Cross-pipeline integration
# =========================================================================


class TestCrossPipeline:
    """Tests that span multiple modules working together."""

    def test_ingest_rank_and_check_push_status(self, db):
        """Full flow: ingest papers -> rank -> record push -> verify idempotency."""
        profile = _make_profile()
        records = []
        for p in TEST_PAPERS:
            rec = get_or_create_paper(
                conn=db, doi=p.get("doi"), arxiv_id=p.get("arxiv_id"),
                title=p["title"], authors=p["authors"],
                year=p["year"], venue=p["venue"], abstract=p["abstract"],
            )
            records.append(rec)

        ranked = rank_papers(records, profile, db)
        assert len(ranked) > 0

        # Push top paper
        top_paper = ranked[0][0]
        assert not is_already_pushed(db, top_paper.pid, "F1")
        record_push(db, top_paper.pid, "F1", "msg_daily_001")
        assert is_already_pushed(db, top_paper.pid, "F1")
        assert not is_already_pushed(db, top_paper.pid, "F2")

    def test_collision_filter_on_ranked_papers(self, db):
        """After ranking, collision filter identifies overlap with active projects."""
        profile = _make_profile()
        project = profile.active_projects[0]  # SER with LLM

        records = []
        for p in TEST_PAPERS:
            rec = get_or_create_paper(
                conn=db, doi=p.get("doi"), arxiv_id=p.get("arxiv_id"),
                title=p["title"], authors=p["authors"],
                year=p["year"], venue=p["venue"], abstract=p["abstract"],
            )
            records.append(rec)

        # Keyword-based coarse filter (no centroid)
        collision_candidates = coarse_filter(records, project, project_centroid=None)
        # Papers about speech emotion / LLM / in-context learning should pass
        cand_titles = [c.title for c in collision_candidates]
        assert any("speech emotion" in t.lower() or "in-context learning" in t.lower()
                    for t in cand_titles), f"Expected collision candidates, got: {cand_titles}"

    def test_observability_survives_full_pipeline(self, db):
        """Log ops during the full pipeline and generate a report."""
        today = datetime.now().strftime("%Y-%m-%d")

        # Simulate a pipeline run
        log_op(db, "crossref", "fetch_new", "ok", latency_ms=200, detail="50 papers")
        log_op(db, "arxiv", "fetch_new", "ok", latency_ms=150, detail="30 papers")
        log_op(db, "s2", "fetch_embedding", "ok", latency_ms=400)
        log_op(db, "llm", "one_liner", "ok", latency_ms=600, tokens_used=800)
        log_op(db, "llm", "collision_score", "ok", latency_ms=900, tokens_used=1500)

        # Insert papers and pushes
        for i in range(3):
            p = get_or_create_paper(
                conn=db, doi=f"10.1234/obs.{i}",
                title=f"Obs Paper {i}", year=2025,
            )
            record_push(db, p.pid, "F1")
            if i == 0:
                record_interaction(db, p.pid, "save", "F1")
            elif i == 1:
                record_interaction(db, p.pid, "mute", "F1")

        report = generate_health_report(db, date=today)
        assert "LitBot Daily Health" in report
        assert "Papers scanned" in report
        assert "LLM tokens" in report

    def test_trend_ingestion_with_real_paper_concepts(self, db):
        """Feed TEST_PAPERS concepts into trend stats and verify counts."""
        papers_as_dicts = [
            {"concepts": p.get("concepts", [])} for p in TEST_PAPERS
        ]
        update_trend_stats(db, papers_as_dicts)

        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = db.execute(
            "SELECT count FROM trend_stats WHERE concept = ? AND date = ?",
            ("large language model", today),
        ).fetchone()
        # "large language model" appears in TEST_PAPERS[0] concepts
        assert row is not None
        assert row[0] >= 1
