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


def _input_int(prompt: str, default: int) -> int:
    """Input an integer with validation."""
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print(f"  ⚠ Please enter a valid integer.")


def _input_float(prompt: str, default: float) -> float:
    """Input a float with validation."""
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print(f"  ⚠ Please enter a valid number.")


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
    print("── Step 1/7: Identity (optional, improves matching) ──")
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
    print("── Step 2/7: Research Areas (required) ──")
    print()
    research_areas: list[str] = []
    while not research_areas:
        research_areas = _input_list(
            "Your research areas",
            "e.g.: machine learning, natural language processing"
        )
        if not research_areas:
            print("  ⚠ At least one research area is required.")
    print()

    # === Step 3: Active Projects ===
    print("── Step 3/7: Active Projects ──")
    print()
    projects: list[ActiveProject] = []
    if _yes_no("Do you have active research projects to track?", default=True):
        while True:
            print(f"  Project #{len(projects) + 1}:")
            proj_name = _input("    Project name", "")
            if not proj_name:
                break
            proj_keywords = _input_list("    Keywords", "e.g.: LLM reasoning, in-context learning")
            proj_venues = _input_list("    Target venues", "e.g.: NeurIPS, ICML, ACL")
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
    print("── Step 4/7: Venue Tiers ──")
    print()
    print("  Tier 1: top venues (always include, boost weight)")
    tier1 = _input_list("  Tier 1 venues", "e.g.: Nature, NeurIPS, ACL, ICML")
    print("  Tier 2: good venues (include, neutral weight)")
    tier2 = _input_list("  Tier 2 venues", "e.g.: AAAI, EMNLP")
    print("  Blacklist: venues to always exclude")
    blacklist = _input_list("  Blacklisted venues", "e.g.: MDPI journals")
    print()

    # === Step 5: Preferences ===
    print("── Step 5/7: Preferences ──")
    print()
    language = _input("Push language", "zh")
    digest_time = _input("Daily digest time (HH:MM)", "08:00")
    max_papers = _input_int("Max papers per daily digest", 10)
    diversity = _input_float("Diversity ratio (0-1, portion reserved for exploratory papers)", 0.2)
    min_cite = _input_int("Minimum citation count to highlight", 10)
    print()

    # === Step 6: API Settings ===
    print("── Step 6/7: API Settings ──")
    print()
    print("  Unpaywall API requires a contact email (their TOS).")
    print("  You can use the default (litbot@example.com) or provide your own.")
    unpaywall_email = _input("Unpaywall contact email", "litbot@example.com")
    print()

    # === Step 7: Feishu Configuration ===
    print("── Step 7/7: Feishu Configuration ──")
    print()
    print("  LitBot pushes paper cards via Feishu bot.")
    print("  If you don't have a Feishu bot yet, create one at:")
    print("    https://open.feishu.cn/app")
    print("  See docs/feishu-setup.md for detailed instructions.")
    print()
    app_id = _input("Feishu App ID (e.g. cli_xxx, or press Enter to skip)", "")
    app_secret = ""
    chat_id = ""
    if app_id:
        app_secret = _input("Feishu App Secret", "")
    if app_id and app_secret:
        print()
        print("  Now let's find the chat to push papers to.")
        print("  1. Send a message to the bot in Feishu (any text is fine)")
        print("  2. If using MetaBot, restart it first so the bot can receive messages")
        print("  Then press Enter to auto-detect chats...")
        input()
        try:
            from .feishu_auth import get_tenant_token, list_bot_chats
            token = get_tenant_token(app_id, app_secret)
            chats = list_bot_chats(token)
            if not chats:
                print("  ⚠ No chats found. Make sure you've messaged the bot first.")
                chat_id = _input("Enter chat ID manually (oc_xxx)", "")
            elif len(chats) == 1:
                chat_id = chats[0]["chat_id"]
                print(f"  Found 1 chat: {chats[0]['name']} ({chat_id})")
                print(f"  ✅ Using this chat.")
            else:
                print(f"  Found {len(chats)} chats:")
                for i, c in enumerate(chats):
                    label = "P2P" if c["chat_type"] == "p2p" else "Group"
                    print(f"    [{i + 1}] {c['name']} ({label}) — {c['chat_id']}")
                while True:
                    choice = _input(f"Select chat (1-{len(chats)})", "1")
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(chats):
                            chat_id = chats[idx]["chat_id"]
                            break
                    except ValueError:
                        pass
                    print("  ⚠ Invalid choice.")
                print(f"  ✅ Selected: {chat_id}")
        except Exception as e:
            print(f"  ⚠ Auto-detect failed: {e}")
            chat_id = _input("Enter chat ID manually (oc_xxx)", "")
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
            digest_time=digest_time,
            max_daily_papers=max_papers,
            diversity_ratio=diversity,
            unpaywall_email=unpaywall_email,
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

    if app_id:
        env_path = path.parent / ".env"
        env_vars: dict[str, str] = {}
        # Preserve existing env vars
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip()
        env_vars["LITBOT_FEISHU_APP_ID"] = app_id
        if app_secret:
            env_vars["LITBOT_FEISHU_APP_SECRET"] = app_secret
        if chat_id:
            env_vars["LITBOT_FEISHU_CHAT_ID"] = chat_id
        env_path.write_text(
            "\n".join(f"{k}={v}" for k, v in env_vars.items()) + "\n"
        )
        print(f"  Feishu credentials saved to {env_path}")

    return profile


def run_from_yaml(source: str, target: Path | None = None) -> Profile:
    """Load profile from a YAML file (non-interactive).

    Args:
        source: Path to a pre-filled YAML file.
        target: Where to save (default: data/profile.yaml).
    """
    import yaml as _yaml

    path = target or PROFILE_PATH
    src = Path(source)
    if not src.exists():
        print(f"❌ Source file not found: {src}")
        sys.exit(1)

    with open(src) as f:
        raw = _yaml.safe_load(f)

    # Build profile from raw YAML (same structure as profile.example.yaml)
    from .config import load_profile as _load, save_profile as _save

    # Copy source to target, then load via the standard loader
    path.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(src, path)

    profile = _load(path)
    print(f"✅ Profile loaded from {src} → saved to {path}")
    print(f"   Privacy level: {profile.privacy_level}")
    print(f"   Research areas: {', '.join(profile.research_areas)}")
    print(f"   Active projects: {len(profile.active_projects)}")
    return profile


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--from-yaml":
        if len(sys.argv) < 3:
            print("Usage: python -m scripts.setup_profile --from-yaml <path>")
            sys.exit(1)
        run_from_yaml(sys.argv[2])
    else:
        run_setup()
