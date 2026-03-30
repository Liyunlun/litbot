#!/usr/bin/env bash
# LitBot one-click setup script
# Usage: bash setup.sh          # interactive mode
#        bash setup.sh --auto   # non-interactive (for MetaBot / CI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Parse flags
AUTO=false
for arg in "$@"; do
    case "$arg" in
        --auto) AUTO=true ;;
    esac
done

echo "================================================"
echo "  LitBot — Literature Intelligence Agent Setup"
if [ "$AUTO" = true ]; then
    echo "  (non-interactive mode)"
fi
echo "================================================"
echo

# Step 1: Check Python
echo "── Step 1: Checking Python ──"
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "❌ Python 3.10+ is required but not found."
    echo "   Install: https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Found Python $PY_VERSION"

PY_OK=$($PYTHON -c "import sys; print(int(sys.version_info >= (3, 10)))")
if [ "$PY_OK" != "1" ]; then
    echo "❌ Python 3.10+ required, found $PY_VERSION"
    exit 1
fi
echo "  ✅ Python version OK"
echo

# Step 2: Environment detection
echo "── Step 2: Checking environment ──"
USE_VENV=true
DEPS_OK=$($PYTHON -c "
try:
    import httpx, yaml, numpy
    print('yes')
except ImportError:
    print('no')
" 2>/dev/null)

if [ "$DEPS_OK" = "yes" ]; then
    echo "  Dependencies (httpx, pyyaml, numpy) already available globally."
    if [ "$AUTO" = true ]; then
        # Auto mode: skip venv if deps already available
        USE_VENV=false
        echo "  ✅ Using existing environment (no venv)"
    else
        read -p "  Skip venv and use existing environment? [Y/n]: " SKIP_VENV
        if [[ ! "$SKIP_VENV" =~ ^[Nn] ]]; then
            USE_VENV=false
            echo "  ✅ Using existing environment (no venv)"
        fi
    fi
fi

if [ "$USE_VENV" = true ]; then
    echo "  Setting up virtual environment..."
    if [ ! -d "venv" ]; then
        $PYTHON -m venv venv
        echo "  Created venv/"
    else
        echo "  venv/ already exists, reusing"
    fi
    source venv/bin/activate
    echo "  ✅ Virtual environment activated"
    echo

    echo "── Step 3: Installing dependencies ──"
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    echo "  ✅ Dependencies installed"
else
    echo "  Skipping venv creation and pip install."
fi
echo

# Step 4: Initialize database
echo "── Step 4: Initializing database ──"
$PYTHON -m scripts.init_db
echo "  ✅ Database ready"
echo

# Step 5: Profile setup
echo "── Step 5: Profile configuration ──"
if [ "$AUTO" = true ]; then
    # Auto mode: skip interactive wizard
    if [ -f "data/profile.yaml" ]; then
        echo "  Profile already exists. Keeping it."
    else
        echo "  ⏭ No profile yet — skipping interactive wizard in auto mode."
        echo "  Configure later via: python -m scripts.setup_profile"
        echo "  Or use the Python API:"
        echo "    from scripts.config import Profile, save_profile"
        echo "    save_profile(Profile(research_areas=['your', 'areas']))"
    fi
else
    if [ -f "data/profile.yaml" ]; then
        echo "  Profile already exists at data/profile.yaml"
        read -p "  Overwrite with new profile? [y/N]: " OVERWRITE
        if [[ "$OVERWRITE" =~ ^[Yy] ]]; then
            $PYTHON -m scripts.setup_profile
        else
            echo "  Keeping existing profile."
        fi
    else
        echo "  No profile found. Starting setup wizard..."
        echo
        $PYTHON -m scripts.setup_profile
    fi
fi
echo

# Step 6: Verify installation
echo "── Step 6: Verification ──"
if [ -f "data/profile.yaml" ]; then
    $PYTHON -c "
from scripts.init_db import get_db
from scripts.config import load_profile
conn = get_db()
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
conn.close()
profile = load_profile()
print(f'  Database: {len(tables)} tables')
print(f'  Profile: privacy_level={profile.privacy_level}, areas={len(profile.research_areas)}')
print(f'  Projects: {len(profile.active_projects)}')
"
    echo "  ✅ All checks passed"
else
    $PYTHON -c "
from scripts.init_db import get_db
conn = get_db()
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
conn.close()
print(f'  Database: {len(tables)} tables')
"
    echo "  ✅ Database OK (profile pending)"
fi
echo

# Step 7: Copy skills to parent .claude/skills/
echo "── Step 7: Install skills ──"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
SKILLS_TARGET="$PARENT_DIR/.claude/skills"

if [ -d "$SCRIPT_DIR/skills" ]; then
    if [ "$AUTO" = true ]; then
        # Auto mode: copy without asking
        mkdir -p "$SKILLS_TARGET"
        cp -r "$SCRIPT_DIR/skills"/* "$SKILLS_TARGET/"
        COPIED=$(ls -1 "$SCRIPT_DIR/skills" | wc -l)
        echo "  ✅ Copied $COPIED skills → $SKILLS_TARGET/"
    else
        echo "  LitBot skills need to be in $SKILLS_TARGET/"
        echo "  Skills found: $(ls -1 "$SCRIPT_DIR/skills" | tr '\n' ' ')"
        read -p "  Copy skills now? [Y/n]: " COPY_SKILLS
        if [[ "$COPY_SKILLS" =~ ^[Nn] ]]; then
            echo "  Skipped. Copy manually:"
            echo "    cp -r $SCRIPT_DIR/skills/* $SKILLS_TARGET/"
        else
            mkdir -p "$SKILLS_TARGET"
            cp -r "$SCRIPT_DIR/skills"/* "$SKILLS_TARGET/"
            COPIED=$(ls -1 "$SCRIPT_DIR/skills" | wc -l)
            echo "  ✅ Copied $COPIED skills → $SKILLS_TARGET/"
        fi
    fi
else
    echo "  ⚠ No skills/ directory found. Skipping."
fi
echo

# Step 8: Schedule daily digest (cron)
echo "── Step 8: Daily digest scheduling ──"

if [ "$AUTO" = true ]; then
    # Auto mode: skip cron, bot will configure later
    echo "  ⏭ Skipping cron setup in auto mode."
    echo "  Configure after profile is ready:"
    echo "    mb schedule cron <bot> <chatId> '0 8 * * *' '<prompt>'"
else
    # Read digest_time from profile.yaml
    DIGEST_TIME=$($PYTHON -c "
from scripts.config import load_profile
p = load_profile()
print(p.preferences.digest_time)
" 2>/dev/null || echo "08:00")
    DIGEST_HOUR="${DIGEST_TIME%%:*}"
    DIGEST_MIN="${DIGEST_TIME##*:}"
    # Strip leading zeros for cron
    DIGEST_HOUR=$((10#$DIGEST_HOUR))
    DIGEST_MIN=$((10#$DIGEST_MIN))

    if command -v mb &>/dev/null; then
        # MetaBot environment — auto-register
        echo "  MetaBot detected. Setting up scheduled daily digest."
        echo

        # Detect bot name from environment or ask
        BOT_NAME="${METABOT_BOT_NAME:-}"
        if [ -z "$BOT_NAME" ]; then
            read -p "  Bot name running LitBot (e.g. reader): " BOT_NAME
        fi

        # Detect chat ID from profile .env or ask
        CHAT_ID=""
        if [ -f "data/.env" ]; then
            CHAT_ID=$(grep '^LITBOT_FEISHU_CHAT_ID=' data/.env 2>/dev/null | cut -d= -f2)
        fi
        if [ -z "$CHAT_ID" ]; then
            read -p "  Chat ID for daily digest (oc_xxx): " CHAT_ID
        fi

        if [ -n "$BOT_NAME" ] && [ -n "$CHAT_ID" ]; then
            CRON_EXPR="$DIGEST_MIN $DIGEST_HOUR * * *"
            CRON_PROMPT="执行 /lit-daily。使用 litbot/data/profile.yaml 配置，从 arXiv 和 Crossref 抓取最新论文，通过 paper_identity 去重，ranking 排序后，输出每日论文推荐（中文），包含标题、来源、分数和一句话推荐理由。"

            echo "  Will register daily digest cron:"
            echo "    Schedule : $CRON_EXPR (daily at $DIGEST_TIME)"
            echo "    Bot      : $BOT_NAME"
            echo "    Chat     : $CHAT_ID"
            echo
            read -p "  Register this cron job? [Y/n]: " CONFIRM_CRON
            if [[ "$CONFIRM_CRON" =~ ^[Nn] ]]; then
                echo "  Skipped. You can register manually later:"
                echo "    mb schedule cron $BOT_NAME $CHAT_ID '$CRON_EXPR' '<prompt>'"
            else

            RESULT=$(mb schedule cron "$BOT_NAME" "$CHAT_ID" "$CRON_EXPR" "$CRON_PROMPT" 2>&1) || true
            if echo "$RESULT" | grep -qi 'error\|fail'; then
                echo "  ⚠ Cron registration failed: $RESULT"
                echo "  You can set it up manually later:"
                echo "    mb schedule cron $BOT_NAME $CHAT_ID '$CRON_EXPR' '<prompt>'"
            else
                echo "  ✅ Daily digest scheduled at $DIGEST_TIME"
            fi

            fi
        else
            echo "  ⚠ Missing bot name or chat ID. Skipping cron setup."
            echo "  Set up manually:"
            echo "    mb schedule cron <bot> <chatId> '$DIGEST_MIN $DIGEST_HOUR * * *' '<prompt>'"
        fi
    else
        # Non-MetaBot environment — show instructions
        echo "  MetaBot not detected. To schedule daily digests, set up a cron job:"
        echo
        echo "  Option A — system crontab:"
        echo "    crontab -e"
        echo "    $DIGEST_MIN $DIGEST_HOUR * * * cd $SCRIPT_DIR && ${USE_VENV:+source venv/bin/activate && }python -m scripts.daily_pipeline"
        echo
        echo "  Option B — if you install MetaBot later:"
        echo "    mb schedule cron <bot> <chatId> '$DIGEST_MIN $DIGEST_HOUR * * *' '/lit-daily'"
    fi
fi
echo

echo "================================================"
if [ "$AUTO" = true ]; then
    echo "  ✅ LitBot auto-setup complete!"
    echo "================================================"
    echo
    echo "  What was done:"
    echo "  ✔ Python checked, dependencies installed"
    echo "  ✔ Database initialized"
    echo "  ✔ Skills copied to .claude/skills/"
    echo
    echo "  Still needed:"
    if [ ! -f "data/profile.yaml" ]; then
        echo "  → Configure profile (research areas, projects, venues)"
    fi
    echo "  → Schedule daily digest cron"
    echo
    echo "  The bot can now use /lit-* skills after profile setup."
else
    echo "  ✅ LitBot setup complete!"
    echo "================================================"
    echo
    echo "  Next steps:"
    echo "  1. Edit data/profile.yaml to fine-tune your profile"
    echo "  2. Set up Feishu bot (see docs/feishu-setup.md)"
    echo "  3. If using MetaBot: the bot will auto-discover skills"
    echo
    echo "  Quick test:"
    if [ "$USE_VENV" = true ]; then
        echo "    source venv/bin/activate"
    fi
    echo "    python -c 'from scripts.config import load_profile; p = load_profile(); print(p.research_areas)'"
fi
echo
