#!/usr/bin/env python3
"""Feishu authentication helper for LitBot setup.

Provides tenant_access_token retrieval and chat discovery
using App ID + App Secret.
"""
from __future__ import annotations

import httpx

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_CHATS_URL = "https://open.feishu.cn/open-apis/im/v1/chats"


def get_tenant_token(app_id: str, app_secret: str) -> str:
    """Get tenant_access_token from Feishu."""
    resp = httpx.post(FEISHU_TOKEN_URL, json={
        "app_id": app_id,
        "app_secret": app_secret,
    }, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {data.get('msg', 'unknown error')}")
    return data["tenant_access_token"]


def list_bot_chats(token: str) -> list[dict]:
    """List all chats the bot is a member of."""
    headers = {"Authorization": f"Bearer {token}"}
    chats: list[dict] = []
    page_token = ""

    while True:
        params: dict[str, str] = {"page_size": "50"}
        if page_token:
            params["page_token"] = page_token

        resp = httpx.get(FEISHU_CHATS_URL, headers=headers, params=params, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu API error: {data.get('msg', 'unknown error')}")

        items = data.get("data", {}).get("items", [])
        for item in items:
            chats.append({
                "chat_id": item.get("chat_id", ""),
                "name": item.get("name", "(unnamed)"),
                "chat_type": item.get("chat_type", ""),
            })

        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token", "")

    return chats
