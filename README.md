# LitBot — Literature Intelligence Agent

An autonomous literature monitoring and analysis agent for frontline researchers. Discovers papers, detects competition, and pushes insights via Feishu — no browser needed.

```
You ←→ Feishu Cards ←→ LitBot (Claude Code Bot)
                            │
            ┌───────────────┼───────────────┐
            │               │               │
       Push Engine     Analysis Engine  Personalization
       F1/F2/F3       F3β/F4/F5/F6     Profile+Ranking
            │               │               │
            └───────┬───────┘               │
                    │                       │
              Retrieval ←───────────────────┘
           ┌──┬──┬──┬──┐
           OA S2 CR Ar UW
```

## Features

| Function | Description | Phase |
|----------|-------------|-------|
| **F1 Daily Digest** | Ranked paper recommendations, pushed at 8:00 | MVP |
| **F2 Collision Alert** | Detects competing papers in your research areas | MVP |
| **F3 Trend Burst** | Z-score anomaly detection on field concepts | MVP |
| **F3β Review Assist** | Reference gap detection + novelty analysis (beta) | v2 |
| **F4 Citation Network** | 2-hop citation graph with edge classification | v2 |
| **F6 Comparison Table** | Structured multi-paper comparison with source labels | v2 |
| **F5 Field Panorama** | Taxonomy-based field overview with concept maps | v3 |

## Data Sources

| API | Role | Coverage |
|-----|------|----------|
| [OpenAlex](https://openalex.org/) | Primary metadata | 200M+ papers, all disciplines |
| [Semantic Scholar](https://www.semanticscholar.org/) | Embeddings + similarity | SPECTER2 vectors |
| [Crossref](https://www.crossref.org/) | New paper detection | All DOI journals |
| [arXiv](https://arxiv.org/) | CS preprint supplement | CS, Math, Physics |
| [Unpaywall](https://unpaywall.org/) | OA PDF access | DOI-based OA links |

---

## Installation

### Method 1: Manual Setup

```bash
# Clone
git clone https://github.com/YourOrg/litbot.git
cd litbot

# Python 3.10+ required
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize database
python -m scripts.init_db

# Configure profile (interactive wizard)
python -m scripts.setup_profile

# Set up Feishu (see docs/feishu-setup.md)
```

### Method 2: One-Click Setup

```bash
git clone https://github.com/YourOrg/litbot.git
cd litbot
bash setup.sh
```

The script will:
1. Check Python version (3.10+)
2. Create virtual environment
3. Install dependencies
4. Initialize SQLite database
5. Launch interactive profile configuration wizard
6. Verify installation

### Method 3: MetaBot Installation

Send this message to MetaBot in Feishu:

```
Install LitBot from https://github.com/YourOrg/litbot.git
```

MetaBot will:
1. Clone the repo to `Research/shared/litbot/`
2. Run `setup.sh` automatically
3. Ask you profile configuration questions in chat
4. Register LitBot as a bot with scheduled skills

#### MetaBot Bot Registration

After installation, MetaBot registers LitBot with these scheduled tasks:

```
# Daily digest at 8:00
mb schedule add litbot <chat_id> "0 8 * * *" "/lit-daily"

# Collision alert every hour
mb schedule add litbot <chat_id> "0 * * * *" "/lit-alert"
```

#### For Bot Developers: Installation Instructions

When MetaBot installs LitBot, it should follow these steps:

```bash
# 1. Clone to shared directory
cd ~/lyl/Research/shared
git clone <repo_url> litbot
cd litbot

# 2. Run one-click setup (non-interactive mode for dependencies)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m scripts.init_db

# 3. Profile setup — ask user these questions in chat:
#    - Research areas (required): comma-separated list
#    - Active project names + keywords (optional)
#    - Tier 1 venues (optional)
#    - Blacklisted venues (optional)
#    - Language preference: zh/en
#    - Semantic Scholar ID (optional)
#    Then write data/profile.yaml using scripts/config.py:
#
#    python -c "
#    from scripts.config import Profile, ActiveProject, VenueTiers, Preferences, save_profile
#    profile = Profile(
#        research_areas=['area1', 'area2'],
#        active_projects=[ActiveProject(name='...', keywords=['...'], venues=['...'])],
#        venue_tiers=VenueTiers(tier1=['...'], blacklist=['...']),
#        preferences=Preferences(language='zh'),
#    )
#    save_profile(profile)
#    "

# 4. Register scheduled tasks
#    mb schedule add litbot <chat_id> "0 8 * * *" "/lit-daily"
#    mb schedule add litbot <chat_id> "0 * * * *" "/lit-alert"

# 5. Verify
python -c "
from scripts.init_db import get_db
from scripts.config import load_profile
conn = get_db()
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
profile = load_profile()
print(f'OK: {len(tables)} tables, {len(profile.research_areas)} areas')
conn.close()
"
```

---

## Configuration

### Profile (`data/profile.yaml`)

See [`data/profile.example.yaml`](data/profile.example.yaml) for a full example.

Key sections:
- **identity** (optional): name, Semantic Scholar ID, your paper DOIs
- **research_areas** (required): your research topics
- **active_projects**: projects to track for collision detection
- **venue_tiers**: tier1 (boost), tier2 (neutral), blacklist (exclude)
- **preferences**: language, max papers, diversity ratio, quiet hours

### Privacy Levels

| Level | What you provide | Matching quality |
|-------|-----------------|-----------------|
| Full | S2 ID + name + papers | Best (embedding + citation + keyword) |
| Semi-public | Paper DOIs only | Good (embedding + keyword) |
| Keywords | Research areas + projects | Basic (keyword + venue) |
| Anonymous | Research areas only | Minimal (keyword only) |

Privacy is auto-detected from which fields you fill in.

---

## Project Structure

```
litbot/
├── CLAUDE.md                  # Bot behavior instructions
├── README.md                  # This file
├── setup.sh                   # One-click setup
├── requirements.txt           # Python dependencies
├── scripts/
│   ├── init_db.py             # SQLite schema (8 tables)
│   ├── paper_identity.py      # Canonical paper ID layer
│   ├── config.py              # Profile loader/saver
│   ├── fetch_papers.py        # 5 API clients with retry
│   ├── ranking.py             # Composite scoring formula
│   ├── collision.py           # F2 two-stage detection
│   ├── trend.py               # F3 burst detection
│   ├── feishu_cards.py        # Card builder + callbacks
│   ├── observability.py       # Logging + health reports
│   └── setup_profile.py       # Interactive profile wizard
├── skills/
│   ├── lit-daily/SKILL.md     # F1 + F3
│   ├── lit-alert/SKILL.md     # F2
│   ├── lit-review/SKILL.md    # F3β (beta)
│   ├── lit-network/SKILL.md   # F4
│   ├── lit-compare/SKILL.md   # F6
│   ├── lit-panorama/SKILL.md  # F5
│   └── lit-profile/SKILL.md   # Profile management
├── data/
│   ├── profile.example.yaml   # Example profile (copy to profile.yaml)
│   └── litbot.db              # SQLite database (auto-created)
├── docs/
│   └── feishu-setup.md        # Feishu bot configuration guide
└── tests/
    ├── test_identity.py       # Paper ID resolution tests
    ├── test_ranking.py        # Ranking formula tests
    └── test_collision.py      # Collision detection tests
```

## Key Design Decisions

- **Paper Identity Layer**: Unified ID mapping (DOI ↔ arXiv ↔ S2 ↔ OpenAlex) prevents duplicate recommendations
- **SQLite + WAL**: Single-file database with concurrent read support, no external DB needed
- **Privacy-first**: All identity fields optional; LLM calls never receive user identity
- **Bootstrap mode**: Cold-start handling with keyword seeds and explicit feedback buttons
- **Circuit breakers**: API failures auto-degrade gracefully instead of crashing
- **Diversity ratio**: 20% of daily slots reserved for exploratory papers to prevent filter bubbles

## License

MIT
