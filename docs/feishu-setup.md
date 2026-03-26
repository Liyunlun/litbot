# Feishu Bot Setup Guide

LitBot pushes paper cards to Feishu (飞书). This guide walks you through creating a Feishu bot and connecting it to LitBot.

## Step 1: Create Feishu App

1. Go to [Feishu Open Platform](https://open.feishu.cn/app)
2. Click "Create Custom App"
3. App name: `LitBot`
4. App description: "Literature Intelligence Agent"

## Step 2: Configure Bot

1. Navigate to **Bot** in the left menu
2. Enable the bot capability
3. Note down the **App ID** (`cli_xxx`) and **App Secret**

## Step 3: Permissions

Add these permissions in **Permissions & Scopes**:
- `im:message:send_as_bot` — send messages
- `im:message:update` — update cards (for button state changes)
- `im:chat:readonly` — read chat list (used for auto-detecting chat ID)

Then click **Publish** to activate the permissions.

## Step 4: Get Chat ID

You need the chat ID where LitBot will push paper cards.

**Option A: Auto-detect (recommended)**

The setup wizard (`python -m scripts.setup_profile`) will auto-detect after you enter App ID + Secret:
1. Send any message to the bot in Feishu
2. If using [MetaBot](https://github.com/Shiien/metabot), restart it first so the bot can receive messages
3. The wizard calls Feishu API to list available chats
4. Pick the one you want

**Option B: Manual**

1. Open Feishu, send a message to the bot (or add bot to a group)
2. Call Feishu API to list chats:
   ```bash
   # Get tenant_access_token
   TOKEN=$(curl -s -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
     -H "Content-Type: application/json" \
     -d '{"app_id":"cli_xxx","app_secret":"xxx"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['tenant_access_token'])")

   # List chats
   curl -s https://open.feishu.cn/open-apis/im/v1/chats \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
   ```
3. Find your chat's `chat_id` (starts with `oc_`)

## Step 5: Configure LitBot

Create `data/.env` (or let the setup wizard write it):
```bash
LITBOT_FEISHU_APP_ID=cli_xxx
LITBOT_FEISHU_APP_SECRET=xxx
LITBOT_FEISHU_CHAT_ID=oc_xxx
```

Optional (for interactive card buttons):
```bash
LITBOT_FEISHU_ENCRYPT_KEY=xxx      # Event subscription encrypt key
LITBOT_FEISHU_VERIFICATION_TOKEN=xxx  # Event subscription verification token
```

## Step 6: Event Subscriptions (optional, for interactive cards)

If you want button interactions (save/dismiss/feedback) on paper cards:

1. In the app config, go to **Event Subscriptions**
2. Set Request URL to your callback endpoint (e.g., `https://your-server/litbot/callback`)
3. Subscribe to events:
   - `card.action.trigger` (button clicks)

## Step 7: Test

```bash
# Verify credentials work
cd litbot
python3 -c "
from scripts.feishu_auth import get_tenant_token, list_bot_chats
import os
token = get_tenant_token(os.environ.get('LITBOT_FEISHU_APP_ID', 'cli_xxx'), os.environ.get('LITBOT_FEISHU_APP_SECRET', 'xxx'))
chats = list_bot_chats(token)
print(f'Found {len(chats)} chats:')
for c in chats:
    print(f'  {c[\"name\"]} — {c[\"chat_id\"]}')
"
```

## Callback Processing

When a user clicks a button on a LitBot card, Feishu sends a POST to your callback URL:

```json
{
  "event": {
    "token": "callback_unique_id",
    "action": {
      "value": "p_abc123:save"
    }
  }
}
```

LitBot processes this via `handle_callback()` in `scripts/feishu_cards.py`:
1. Verify signature
2. Check idempotency (avoid duplicate processing)
3. Record interaction
4. Update card in-place (button → "✅ Saved")

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Auth failed` when getting token | Check App ID and Secret are correct |
| No chats found | Send a message to the bot in Feishu first |
| `im:chat:readonly` permission error | Publish the app after adding permissions |
| Buttons don't work | Ensure event subscription is active and callback URL is reachable |
| Card not updating | Check `im:message:update` permission |
| Duplicate callbacks | Normal — LitBot handles idempotency automatically |
