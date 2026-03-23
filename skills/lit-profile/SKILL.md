# /lit-profile — Profile Management

View, edit, and manage the user's research profile.

## Trigger
- **Manual**: `/lit-profile` or "我的资料"

## Commands

### View Profile
`/lit-profile` or `/lit-profile show`

Display current profile summary:
```
📋 LitBot Profile
━━━━━━━━━━━━━━━━━━━━━
Privacy level: {level}
Research areas: {areas}
Active projects: {N}
  - {project_1}: {keywords} → {venues}
  - ...
Venue tiers: {tier1_count} tier1, {tier2_count} tier2, {blacklist_count} blocked
Preferences: {language}, max {max_papers}/day, diversity {diversity}%
Bootstrap: {mode} ({save_count}/5 saves)
Ranking weights: sim={w_sim}, kw={w_kw}, venue={w_venue}, recent={w_recent}
```

### Edit Research Areas
`/lit-profile areas <area1>, <area2>, ...`

Replace research_areas list. Requires at least one area.

### Add/Remove Project
`/lit-profile add-project <name> --keywords <kw1,kw2> --venues <v1,v2>`
`/lit-profile remove-project <name>`

### Edit Venue Tiers
`/lit-profile tier1 add <venue>`
`/lit-profile tier1 remove <venue>`
`/lit-profile blacklist add <venue>`
`/lit-profile blacklist remove <venue>`

### Edit Preferences
`/lit-profile set language en`
`/lit-profile set max_papers 15`
`/lit-profile set diversity 0.3`

### Re-run Setup Wizard
`/lit-profile setup`

Launches the interactive setup wizard (same as initial setup).

## Implementation

```python
from scripts.config import load_profile, save_profile

profile = load_profile()
# Parse subcommand and modify profile
# Always confirm changes before saving
save_profile(profile)
```

## Rules
- Always show the user what will change BEFORE saving
- Never modify profile.yaml without user confirmation
- After any change, re-display the affected section
- If bootstrap mode is active, remind user about the 👍/👎 buttons
