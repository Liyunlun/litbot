#!/usr/bin/env python3
"""Profile loader and configuration management."""

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROFILE_PATH = Path(__file__).parent.parent / "data" / "profile.yaml"


@dataclass
class ActiveProject:
    name: str
    keywords: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)


@dataclass
class VenueTiers:
    tier1: list[str] = field(default_factory=list)
    tier2: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)


@dataclass
class Preferences:
    min_citation_highlight: int = 10
    language: str = "zh"
    quiet_hours: list[int] = field(default_factory=lambda: [22, 8])
    max_daily_papers: int = 10
    diversity_ratio: float = 0.2


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff: str = "exponential"       # 1s → 2s → 4s
    timeout_per_request: int = 10      # seconds
    circuit_breaker_threshold: float = 0.05   # 5%
    circuit_breaker_window: int = 3600        # 1 hour
    circuit_breaker_cooldown: int = 3600      # 1 hour


@dataclass
class Profile:
    # Identity (all optional)
    name: str = ""
    semantic_scholar_id: str = ""
    my_papers: list[str] = field(default_factory=list)  # DOIs

    # Required
    research_areas: list[str] = field(default_factory=list)

    # Projects
    active_projects: list[ActiveProject] = field(default_factory=list)

    # Venue tiers
    venue_tiers: VenueTiers = field(default_factory=VenueTiers)

    # Preferences
    preferences: Preferences = field(default_factory=Preferences)

    # Retry policy
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    @property
    def privacy_level(self) -> str:
        """Determine privacy level based on filled fields."""
        if self.semantic_scholar_id or self.name:
            return "full"
        if self.my_papers:
            return "semi_public"
        if self.active_projects:
            return "keywords"
        return "anonymous"

    @property
    def ranking_weights(self) -> dict[str, float]:
        """Return ranking weights based on privacy level."""
        level = self.privacy_level
        if level in ("full", "semi_public"):
            return {"sim": 0.40, "keyword": 0.25, "venue": 0.20, "recency": 0.15}
        if level == "keywords":
            return {"sim": 0.00, "keyword": 0.50, "venue": 0.30, "recency": 0.20}
        return {"sim": 0.00, "keyword": 0.60, "venue": 0.20, "recency": 0.20}


def load_profile(path: Path | None = None) -> Profile:
    """Load profile from YAML file."""
    p = path or PROFILE_PATH
    if not p.exists():
        return Profile()

    with open(p) as f:
        data = yaml.safe_load(f) or {}

    identity = data.get("identity", {})
    prefs_data = data.get("preferences", {})
    tiers_data = data.get("venue_tiers", {})
    retry_data = data.get("retry_policy", {})

    projects = []
    for proj in data.get("active_projects", []):
        projects.append(ActiveProject(
            name=proj.get("name", ""),
            keywords=proj.get("keywords", []),
            venues=proj.get("venues", []),
        ))

    return Profile(
        name=identity.get("name", ""),
        semantic_scholar_id=identity.get("semantic_scholar_id", ""),
        my_papers=identity.get("my_papers", []),
        research_areas=data.get("research_areas", []),
        active_projects=projects,
        venue_tiers=VenueTiers(
            tier1=tiers_data.get("tier1", []),
            tier2=tiers_data.get("tier2", []),
            blacklist=tiers_data.get("blacklist", []),
        ),
        preferences=Preferences(
            min_citation_highlight=prefs_data.get("min_citation_highlight", 10),
            language=prefs_data.get("language", "zh"),
            quiet_hours=prefs_data.get("quiet_hours", [22, 8]),
            max_daily_papers=prefs_data.get("max_daily_papers", 10),
            diversity_ratio=prefs_data.get("diversity_ratio", 0.2),
        ),
        retry_policy=RetryPolicy(
            max_attempts=retry_data.get("max_attempts", 3),
            backoff=retry_data.get("backoff", "exponential"),
            timeout_per_request=retry_data.get("timeout_per_request", 10),
            circuit_breaker_threshold=retry_data.get("circuit_breaker_threshold", 0.05),
            circuit_breaker_window=retry_data.get("circuit_breaker_window", 3600),
            circuit_breaker_cooldown=retry_data.get("circuit_breaker_cooldown", 3600),
        ),
    )


def save_profile(profile: Profile, path: Path | None = None) -> None:
    """Save profile to YAML file."""
    p = path or PROFILE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}

    # Identity
    identity: dict[str, Any] = {}
    if profile.name:
        identity["name"] = profile.name
    if profile.semantic_scholar_id:
        identity["semantic_scholar_id"] = profile.semantic_scholar_id
    if profile.my_papers:
        identity["my_papers"] = profile.my_papers
    if identity:
        data["identity"] = identity

    # Research areas
    data["research_areas"] = profile.research_areas

    # Active projects
    if profile.active_projects:
        data["active_projects"] = [
            {"name": p.name, "keywords": p.keywords, "venues": p.venues}
            for p in profile.active_projects
        ]

    # Venue tiers
    data["venue_tiers"] = {
        "tier1": profile.venue_tiers.tier1,
        "tier2": profile.venue_tiers.tier2,
        "blacklist": profile.venue_tiers.blacklist,
    }

    # Preferences
    data["preferences"] = {
        "min_citation_highlight": profile.preferences.min_citation_highlight,
        "language": profile.preferences.language,
        "quiet_hours": profile.preferences.quiet_hours,
        "max_daily_papers": profile.preferences.max_daily_papers,
        "diversity_ratio": profile.preferences.diversity_ratio,
    }

    # Retry policy
    rp = profile.retry_policy
    data["retry_policy"] = {
        "max_attempts": rp.max_attempts,
        "backoff": rp.backoff,
        "timeout_per_request": rp.timeout_per_request,
        "circuit_breaker_threshold": rp.circuit_breaker_threshold,
        "circuit_breaker_window": rp.circuit_breaker_window,
        "circuit_breaker_cooldown": rp.circuit_breaker_cooldown,
    }

    with open(p, "w") as f:
        yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
