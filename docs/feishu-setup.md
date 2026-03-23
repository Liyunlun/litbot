# Feishu Bot Setup Guide

LitBot pushes cards to Feishu (飞书). You need a Feishu custom bot with webhook and event callback capabilities.

## Step 1: Create Feishu App

1. Go to [Feishu Open Platform](https://open.feishu.cn/app)
2. Click "Create Custom App"
3. App name: `LitBot`
4. App description: "Literature Intelligence Agent"

## Step 2: Configure Bot

1. Navigate to **Bot** in the left menu
2. Enable the bot capability
3. Note down the **App ID** and **App Secret**

## Step 3: Set Up Webhook (for pushing cards)

### Option A: Incoming Webhook (simpler)
1. In your Feishu group chat, click Settings → Bots → Add Bot
2. Select "Custom Bot" → get the webhook URL
3. Set `LITBOT_FEISHU_WEBHOOK` in `data/.env`

### Option B: App Bot (for interactive cards)
1. In the app config, go to **Event Subscriptions**
2. Set Request URL to your callback endpoint (e.g., `https://your-server/litbot/callback`)
3. Note the **Encrypt Key** and **Verification Token**
4. Subscribe to events:
   - `im.message.receive_v1` (receive messages)
   - `card.action.trigger` (button clicks)

## Step 4: Permissions

Add these permissions in **Permissions & Scopes**:
- `im:message:send_as_bot` — send messages
- `im:message:update` — update cards (for button state changes)
- `im:chat:readonly` — read chat info

## Step 5: Configure LitBot

Create `data/.env`:
```bash
LITBOT_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
LITBOT_FEISHU_APP_ID=cli_xxx
LITBOT_FEISHU_APP_SECRET=xxx
LITBOT_FEISHU_ENCRYPT_KEY=xxx
LITBOT_FEISHU_CHAT_ID=oc_xxx
```

## Step 6: Test

```bash
# Test webhook push
curl -X POST "$LITBOT_FEISHU_WEBHOOK" \
  -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"LitBot test message"}}'
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
| Webhook returns 400 | Check URL format and Content-Type header |
| Buttons don't work | Ensure event subscription is active and callback URL is reachable |
| Card not updating | Check `im:message:update` permission |
| Duplicate callbacks | Normal — LitBot handles idempotency automatically |
