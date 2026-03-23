#!/usr/bin/env bash
# LitBot one-click setup script
# Usage: bash setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================"
echo "  LitBot — Literature Intelligence Agent Setup"
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

# Step 2: Create virtual environment
echo "── Step 2: Setting up virtual environment ──"
if [ ! -d "venv" ]; then
    $PYTHON -m venv venv
    echo "  Created venv/"
else
    echo "  venv/ already exists, reusing"
fi

source venv/bin/activate
echo "  ✅ Virtual environment activated"
echo

# Step 3: Install dependencies
echo "── Step 3: Installing dependencies ──"
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "  ✅ Dependencies installed"
echo

# Step 4: Initialize database
echo "── Step 4: Initializing database ──"
$PYTHON -m scripts.init_db
echo "  ✅ Database ready"
echo

# Step 5: Profile setup
echo "── Step 5: Profile configuration ──"
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
echo

# Step 6: Verify installation
echo "── Step 6: Verification ──"
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
echo

echo "================================================"
echo "  ✅ LitBot setup complete!"
echo "================================================"
echo
echo "  Next steps:"
echo "  1. Edit data/profile.yaml to fine-tune your profile"
echo "  2. Set up Feishu bot (see docs/feishu-setup.md)"
echo "  3. If using MetaBot: the bot will auto-discover skills"
echo
echo "  Quick test:"
echo "    source venv/bin/activate"
echo "    python -c 'from scripts.config import load_profile; p = load_profile(); print(p.research_areas)'"
echo
