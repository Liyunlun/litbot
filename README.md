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
# Clone into your bot's working directory
cd <bot_working_directory>
git clone https://github.com/Liyunlun/litbot.git litbot

# Create virtual environment and install dependencies (Python 3.10+)
cd litbot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize database
python -m scripts.init_db

# Configure profile (interactive wizard)
python -m scripts.setup_profile
cd ..

# Copy skills to .claude/skills/
cp -r litbot/skills/* .claude/skills/
```

### Method 2: One-Click Setup

```bash
cd <bot_working_directory>
git clone https://github.com/Liyunlun/litbot.git litbot
cd litbot && bash setup.sh && cd ..
cp -r litbot/skills/* .claude/skills/
```

The script will:
1. Check Python version (3.10+)
2. Install dependencies
3. Initialize SQLite database
4. Launch interactive profile configuration wizard
5. Verify installation

### Method 3: MetaBot Installation (install to an existing bot)

Send this to MetaBot:

```
Install LitBot to <bot_name> from https://github.com/Liyunlun/litbot.git
```

MetaBot will:
1. Clone the repo into `<bot_dir>/litbot/`
2. Copy skills to `<bot_dir>/.claude/skills/`
3. Initialize database
4. Ask profile configuration questions in chat
5. Register scheduled tasks (daily digest at 08:00, collision alert hourly)

#### For Bot Developers: Installation Instructions

When installing LitBot into an existing bot, follow these steps:

```bash
BOT_DIR=<bot_working_directory>

# 1. Copy litbot into the bot directory
cd $BOT_DIR
git clone https://github.com/Liyunlun/litbot.git litbot

# 2. Install dependencies + initialize DB
pip install -r litbot/requirements.txt
cd litbot && python -m scripts.init_db && cd ..

# 3. Copy skill definitions so the bot recognizes /lit-* commands
cp -r litbot/skills/* .claude/skills/

# 4. Profile setup — ask user these questions in chat:
#    - Research areas (required): comma-separated list
#    - Active project names + keywords (optional)
#    - Tier 1/Tier 2 venues, blacklisted venues
#    - Language preference: zh/en
#    - Semantic Scholar ID (optional)
#    Then write litbot/data/profile.yaml using scripts/config.py:
#
#    cd litbot && python -c "
#    from scripts.config import Profile, ActiveProject, VenueTiers, Preferences, save_profile
#    profile = Profile(
#        name='User Name',
#        research_areas=['area1', 'area2'],
#        active_projects=[ActiveProject(name='...', keywords=['...'], venues=['...'])],
#        venue_tiers=VenueTiers(tier1=['...'], blacklist=['...']),
#        preferences=Preferences(language='zh'),
#    )
#    save_profile(profile)
#    " && cd ..

# 5. Register scheduled tasks via MetaBot
#    mb schedule cron <bot_name> <chat_id> '0 8 * * *' '执行 /lit-daily'
#    mb schedule cron <bot_name> <chat_id> '0 * * * *' '执行 /lit-alert'

# 6. Verify
cd litbot && python -c "
from scripts.init_db import get_db
from scripts.config import load_profile
conn = get_db()
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
profile = load_profile()
print(f'OK: {len(tables)} tables, {len(profile.research_areas)} areas, privacy={profile.privacy_level}')
conn.close()
" && cd ..
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
- **preferences**: language, max papers, diversity ratio, quiet hours, Unpaywall email

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
├── LICENSE                    # MIT License
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
