"""Tests for F2 collision detection."""

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.init_db import init_db
from scripts.config import ActiveProject, Profile
from scripts.paper_identity import PaperRecord
from scripts.collision import (
    AlertLevel,
    build_collision_prompt,
    classify_alert,
    coarse_filter,
    parse_collision_response,
)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = init_db(db_path)
        yield conn
        conn.close()


@pytest.fixture
def project():
    return ActiveProject(
        name="SER with LLM",
        keywords=["speech emotion", "large language model", "in-context learning"],
        venues=["INTERSPEECH", "ICASSP"],
    )


def _make_paper(title="Test Paper", abstract="A test abstract.", embedding=None, **kwargs):
    return PaperRecord(
        pid=f"p_test_{hash(title) % 10000}",
        title=title,
        abstract=abstract,
        embedding=embedding,
        **kwargs,
    )


def _random_embedding(seed=42):
    rng = np.random.RandomState(seed)
    vec = rng.randn(768).astype(np.float32)
    return vec.tobytes()


class TestClassifyAlert:
    def test_high(self):
        assert classify_alert(0.60) == AlertLevel.HIGH
        assert classify_alert(0.55) == AlertLevel.HIGH

    def test_medium(self):
        assert classify_alert(0.45) == AlertLevel.MEDIUM
        assert classify_alert(0.35) == AlertLevel.MEDIUM

    def test_uncertain(self):
        assert classify_alert(0.30) == AlertLevel.UNCERTAIN
        assert classify_alert(0.25) == AlertLevel.UNCERTAIN

    def test_low(self):
        assert classify_alert(0.20) == AlertLevel.LOW
        assert classify_alert(0.0) == AlertLevel.LOW

    def test_boundary_high(self):
        assert classify_alert(0.55) == AlertLevel.HIGH
        assert classify_alert(0.549) == AlertLevel.MEDIUM

    def test_boundary_medium(self):
        assert classify_alert(0.35) == AlertLevel.MEDIUM
        assert classify_alert(0.349) == AlertLevel.UNCERTAIN

    def test_boundary_uncertain(self):
        assert classify_alert(0.25) == AlertLevel.UNCERTAIN
        assert classify_alert(0.249) == AlertLevel.LOW


class TestCoarseFilter:
    def test_similar_papers_pass(self, project):
        centroid = np.random.randn(768).astype(np.float32)
        # Create a paper with embedding similar to centroid
        similar_emb = (centroid + 0.1 * np.random.randn(768).astype(np.float32))
        similar_emb = similar_emb.astype(np.float32)

        papers = [
            _make_paper("Similar Paper", embedding=similar_emb.tobytes()),
            _make_paper("Different Paper", embedding=_random_embedding(seed=99)),
        ]
        result = coarse_filter(papers, project, centroid, threshold=0.5)
        # The similar paper should pass; the random one likely won't
        assert any(p.title == "Similar Paper" for p in result)

    def test_no_centroid_keyword_fallback(self, project):
        papers = [
            _make_paper(
                "Speech Emotion Recognition with Large Language Model",
                abstract="We use in-context learning for speech emotion.",
            ),
            _make_paper("Quantum Computing Basics", abstract="Qubits and gates."),
        ]
        result = coarse_filter(papers, project, project_centroid=None, threshold=0.65)
        assert len(result) >= 1
        assert result[0].title.startswith("Speech Emotion")

    def test_empty_papers(self, project):
        centroid = np.random.randn(768).astype(np.float32)
        result = coarse_filter([], project, centroid)
        assert result == []


class TestBuildCollisionPrompt:
    def test_contains_paper_info(self, project):
        paper = _make_paper(
            "SER using GPT-4",
            abstract="We apply GPT-4 to speech emotion recognition.",
        )
        prompt = build_collision_prompt(paper, project)
        assert "SER using GPT-4" in prompt
        assert "speech emotion" in prompt.lower()

    def test_contains_dimensions(self, project):
        paper = _make_paper("Test", abstract="Test abstract")
        prompt = build_collision_prompt(paper, project)
        assert "problem_overlap" in prompt
        assert "method_similarity" in prompt


class TestParseCollisionResponse:
    def test_valid_json(self):
        response = json.dumps({
            "problem_overlap": 0.8,
            "method_similarity": 0.6,
            "dataset_overlap": 0.5,
            "contribution_conflict": 0.3,
            "conclusion_competitiveness": 0.2,
            "analysis": "Significant overlap in problem formulation.",
        })
        result = parse_collision_response(response)
        assert "collision_score" in result
        assert "scores" in result
        assert "analysis" in result
        expected = 0.8 * 0.30 + 0.6 * 0.25 + 0.5 * 0.20 + 0.3 * 0.15 + 0.2 * 0.10
        assert result["collision_score"] == pytest.approx(expected, abs=0.01)

    def test_scores_clamped(self):
        response = json.dumps({
            "problem_overlap": 1.5,  # > 1, should clamp
            "method_similarity": -0.2,  # < 0, should clamp
            "dataset_overlap": 0.5,
            "contribution_conflict": 0.5,
            "conclusion_competitiveness": 0.5,
            "analysis": "Test",
        })
        result = parse_collision_response(response)
        assert result["scores"]["problem_overlap"] == 1.0
        assert result["scores"]["method_similarity"] == 0.0

    def test_missing_fields_default_zero(self):
        response = json.dumps({
            "problem_overlap": 0.5,
            "analysis": "Partial response",
        })
        result = parse_collision_response(response)
        assert result["scores"]["method_similarity"] == 0.0
        assert result["collision_score"] >= 0.0

    def test_malformed_json(self):
        result = parse_collision_response("not json at all")
        assert result["collision_score"] == 0.0
        assert "parse error" in result["analysis"].lower() or result["analysis"] != ""
