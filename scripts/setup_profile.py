#!/usr/bin/env python3
"""Interactive profile setup wizard for LitBot.

Guides user through configuring their research profile step by step.
Can be run standalone or called from setup.sh.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import (
    ActiveProject,
    Preferences,
    Profile,
    RetryPolicy,
    VenueTiers,
    save_profile,
)

PROFILE_PATH = Path(__file__).parent.parent / "data" / "profile.yaml"


def _input(prompt: str, default: str = "") -> str:
    """Input with default value display."""
    if default:
        raw = input(f"{prompt} [{default}]: ").strip()
        return raw or default
    return input(f"{prompt}: ").strip()


def _input_list(prompt: str, hint: str = "comma-separated") -> list[str]:
    """Input a comma-separated list."""
    raw = input(f"{prompt} ({hint}): ").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = input(prompt + suffix + ": ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def run_setup(profile_path: Path | None = None) -> Profile:
    """Run interactive profile setup."""
    path = profile_path or PROFILE_PATH

    print()
    print("=" * 50)
    print("  LitBot Profile Setup Wizard")
    print("=" * 50)
    print()
    print("This wizard will help you configure your research profile.")
    print("All identity fields are optional (press Enter to skip).")
    print()

    # === Step 1: Identity (optional) ===
    print("── Step 1/6: Identity (optional, improves matching) ──")
    print()
    name = _input("Your name (for display only, not sent to LLM)", "")
    s2_id = _input("Semantic Scholar author ID (enables auto paper pull)", "")
    print()

    my_papers: list[str] = []
    if _yes_no("Do you want to provide DOIs of your papers for embedding warm-start?"):
        print("  Enter DOIs one per line (empty line to finish):")
        while True:
            doi = input("  DOI: ").strip()
            if not doi:
                break
            my_papers.append(doi)
    print()

    # === Step 2: Research Areas (required) ===
    print("── Step 2/6: Research Areas (required) ──")
    print()
    research_areas: list[str] = []
    while not research_areas:
        research_areas = _input_list(
            "Your research areas",
            "e.g.: speech emotion recognition, affective computing"
        )
        if not research_areas:
            print("  ⚠ At least one research area is required.")
    print()

    # === Step 3: Active Projects ===
    print("── Step 3/6: Active Projects ──")
    print()
    projects: list[ActiveProject] = []
    if _yes_no("Do you have active research projects to track?", default=True):
        while True:
            print(f"  Project #{len(projects) + 1}:")
            proj_name = _input("    Project name", "")
            if not proj_name:
                break
            proj_keywords = _input_list("    Keywords", "e.g.: speech emotion, LLM, in-context learning")
            proj_venues = _input_list("    Target venues", "e.g.: INTERSPEECH, ICASSP, ACL")
            projects.append(ActiveProject(
                name=proj_name,
                keywords=proj_keywords,
                venues=proj_venues,
            ))
            print()
            if not _yes_no("  Add another project?"):
                break
    print()

    # === Step 4: Venue Tiers ===
    print("── Step 4/6: Venue Tiers ──")
    print()
    print("  Tier 1: top venues (always include, boost weight)")
    tier1 = _input_list("  Tier 1 venues", "e.g.: Nature, NeurIPS, ACL, ICML")
    print("  Tier 2: good venues (include, neutral weight)")
    tier2 = _input_list("  Tier 2 venues", "e.g.: AAAI, EMNLP")
    print("  Blacklist: venues to always exclude")
    blacklist = _input_list("  Blacklisted venues", "e.g.: MDPI journals")
    print()

    # === Step 5: Preferences ===
    print("── Step 5/6: Preferences ──")
    print()
    language = _input("Push language", "zh")
    max_papers = int(_input("Max papers per daily digest", "10"))
    diversity = float(_input("Diversity ratio (0-1, portion reserved for exploratory papers)", "0.2"))
    min_cite = int(_input("Minimum citation count to highlight", "10"))
    print()

    quiet_start = int(_input("Quiet hours start (0-23)", "22"))
    quiet_end = int(_input("Quiet hours end (0-23)", "8"))
    print()

    # === Step 6: Feishu Configuration ===
    print("── Step 6/6: Feishu Configuration ──")
    print()
    print("  You'll need a Feishu bot with:")
    print("  - Bot webhook URL (for pushing cards)")
    print("  - Event callback URL (for button interactions)")
    print("  See docs/feishu-setup.md for detailed instructions.")
    print()
    webhook = _input("Feishu webhook URL (or set LITBOT_FEISHU_WEBHOOK env var later)", "")
    print()

    # === Build and save profile ===
    profile = Profile(
        name=name,
        semantic_scholar_id=s2_id,
        my_papers=my_papers,
        research_areas=research_areas,
        active_projects=projects,
        venue_tiers=VenueTiers(tier1=tier1, tier2=tier2, blacklist=blacklist),
        preferences=Preferences(
            min_citation_highlight=min_cite,
            language=language,
            quiet_hours=[quiet_start, quiet_end],
            max_daily_papers=max_papers,
            diversity_ratio=diversity,
        ),
        retry_policy=RetryPolicy(),
    )

    save_profile(profile, path)

    print("=" * 50)
    print(f"  ✅ Profile saved to {path}")
    print(f"  Privacy level: {profile.privacy_level}")
    print(f"  Research areas: {', '.join(research_areas)}")
    print(f"  Active projects: {len(projects)}")
    print(f"  Ranking weights: {profile.ranking_weights}")
    print("=" * 50)
    print()

    if webhook:
        # Save webhook to a separate env file
        env_path = path.parent / ".env"
        with open(env_path, "a") as f:
            f.write(f"\nLITBOT_FEISHU_WEBHOOK={webhook}\n")
        print(f"  Webhook saved to {env_path}")

    return profile


if __name__ == "__main__":
    run_setup()
