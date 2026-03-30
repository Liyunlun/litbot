# MetaBot Installation Guide

When a user asks you to install LitBot, follow this guide exactly. **Do not skip steps. Do not use default values without showing them to the user.**

## Prerequisites

- You are a Claude Code bot running inside MetaBot
- You have access to Bash, file read/write, and `mb` CLI
- The LitBot repo URL: `https://github.com/Liyunlun/litbot.git`

---

## Phase 1: Determine Installation Target

Ask the user which bot to install LitBot on.

**If the user already specified a bot** (e.g., "安装到 reader"), confirm and proceed.

**If the user did not specify**, list available bots:
```
我来帮你安装 LitBot。你想安装到哪个 bot？

可用的 bot：
- reader (文献/阅读)
- ...其他 bot...

或者我可以帮你创建一个新的 bot。
```

**If the user wants a new bot**, you need:
1. Bot name (e.g., `reader`)
2. Feishu App ID (`cli_xxx`) — guide user to create at https://open.feishu.cn/app if needed
3. Feishu App Secret

Then register via `mb` CLI. See docs/feishu-setup.md for Feishu app creation steps.

After confirming the target bot, identify its working directory (e.g., `/path/to/reader-bot/`).

---

## Phase 2: Automated Installation

Run these commands and show the user the progress:

```bash
cd <bot_working_directory>
git clone https://github.com/Liyunlun/litbot.git litbot
cd litbot && bash setup.sh --auto
```

`setup.sh --auto` will:
- Check Python 3.10+
- Detect existing dependencies or create venv
- Install dependencies
- Initialize SQLite database (9 tables)
- Copy 7 skills to `../.claude/skills/`
- Skip profile setup and cron (handled in Phase 3-5)

Show the user a summary:
```
✔ LitBot 基础环境安装完成：
  - Python 3.10 ✓
  - 依赖已安装 ✓
  - 数据库初始化（9 张表）✓
  - 7 个 Skills 已复制 ✓

接下来我需要了解你的研究方向，来配置个性化推荐。
```

---

## Phase 3: Profile Configuration

Guide the user through profile setup **one question at a time** via chat. Do NOT run the interactive `setup_profile.py` wizard (it requires terminal stdin). Instead, collect answers in chat, then use the Python API to write the profile.

### Q1: Research Areas (REQUIRED)

```
你的研究领域是什么？（必填，可以多个，逗号分隔）

例如：task and motion planning, embodied AI, robot manipulation
```

**Do not proceed until the user answers this question.**

### Q2: Active Projects (optional)

```
你有在跟的研究项目吗？如果有，请告诉我：
- 项目名称
- 关键词（用于匹配相关论文）
- 目标投稿会议

例如：
  项目：Robot Grasping
  关键词：grasp planning, dexterous manipulation
  会议：RSS, CoRL, ICRA

可以添加多个项目。没有的话回复"跳过"。
```

### Q3: Paper Warm Start (optional, significantly improves recommendations)

```
想要更精准的论文推荐吗？你可以提供你的已发表论文信息：

方式一：提供 Semantic Scholar ID（自动拉取全部论文）
  → 在 semanticscholar.org 搜索你的名字，主页 URL 中的数字就是 ID

方式二：手动提供几篇代表论文的 DOI
  → 例如：10.1234/example.2025.001

这会启用 embedding warm-start，让推荐从第一天起就很精准。
不提供也可以，后续通过 /lit-profile 随时补充。
```

**Privacy implications to explain if user asks:**
| 提供信息 | 隐私级别 | 匹配质量 |
|---------|---------|---------|
| S2 ID + 论文 | full | 最佳（embedding + citation + keyword）|
| 只提供 DOI | semi-public | 好（embedding + keyword）|
| 只填研究领域 | keywords | 基础（keyword + venue）|

### Q4: Venue Preferences (optional)

```
你的顶刊/顶会偏好？

Tier 1（必看，权重加成）：例如 NeurIPS, ICML, ACL, Nature
黑名单（永远屏蔽）：例如 MDPI journals

没有特别偏好回复"跳过"，将使用默认设置。
```

### Q5: Push Preferences

```
最后几个推送设置：

- 推送语言？中文 / English（默认：中文）
- 每天几点推送？（默认：08:00）
- 每日最多推几篇论文？（默认：10 篇）

可以直接回复"默认"使用以上默认值，或逐个指定。
```

### Write Profile

After collecting all answers, use the Python API to write the profile:

```python
cd <bot_dir>/litbot && python3 -c "
from scripts.config import Profile, ActiveProject, VenueTiers, Preferences, save_profile

profile = Profile(
    name='<user_name_if_provided>',
    semantic_scholar_id='<s2_id_if_provided>',
    my_papers=[<doi_list_if_provided>],
    research_areas=[<areas_from_Q1>],
    active_projects=[
        ActiveProject(name='<name>', keywords=[<kws>], venues=[<venues>]),
        ...
    ],
    venue_tiers=VenueTiers(
        tier1=[<tier1_venues>],
        blacklist=[<blacklisted_venues>],
    ),
    preferences=Preferences(
        language='<zh_or_en>',
        digest_time='<HH:MM>',
        max_daily_papers=<N>,
        diversity_ratio=0.2,
    ),
)
save_profile(profile)
"
```

Show the user the configured profile summary:
```
✅ Profile 配置完成：

  研究领域：task and motion planning, embodied AI
  跟踪项目：Robot Grasping (grasp, manipulation → RSS, CoRL)
  隐私级别：full（已提供 S2 ID + 论文）
  Tier 1 会议：RSS, CoRL, ICRA, NeurIPS
  推送：每天 08:00，中文，最多 10 篇
```

---

## Phase 4: Feishu Configuration

If the bot already has Feishu credentials configured (check bots.json or existing `.env`), skip to Chat ID detection.

Otherwise:

```
LitBot 通过飞书推送每日论文。

你已经有飞书机器人了吗？
- 有 → 请提供 App ID (cli_xxx) 和 App Secret
- 没有 → 参考 docs/feishu-setup.md 创建，或回复"跳过"之后再配置
```

### Chat ID Auto-Detection

If Feishu credentials are available:

```python
cd <bot_dir>/litbot && python3 -c "
from scripts.feishu_auth import get_tenant_token, list_bot_chats
token = get_tenant_token('<app_id>', '<app_secret>')
chats = list_bot_chats(token)
for i, c in enumerate(chats):
    print(f'[{i+1}] {c[\"name\"]} ({c[\"chat_type\"]}) — {c[\"chat_id\"]}')
"
```

Show the user and let them pick:
```
找到以下飞书会话：
  [1] 张三 (P2P) — oc_xxx
  [2] 论文讨论群 (group) — oc_yyy

推送到哪个会话？
```

Write the selection to `data/.env`:
```
LITBOT_FEISHU_APP_ID=cli_xxx
LITBOT_FEISHU_APP_SECRET=xxx
LITBOT_FEISHU_CHAT_ID=oc_xxx
```

---

## Phase 5: Register Daily Push

Show the full cron configuration and ask for confirmation:

```
是否注册每日论文推送？

  时间：每天 08:00
  Bot：reader
  会话：oc_xxx
  内容：自动抓取论文 → 去重 → 排序 → 推送中文摘要

确认注册？(Y/n)
```

If confirmed:
```bash
mb schedule cron <bot_name> <chat_id> '<MIN> <HOUR> * * *' '执行 /lit-daily。使用 litbot/data/profile.yaml 配置，从 arXiv 和 Crossref 抓取最新论文，通过 paper_identity 去重，ranking 排序后，输出每日论文推荐（中文），包含标题、来源、分数和一句话推荐理由。'
```

---

## Phase 6: Installation Complete

Show the final summary:

```
✅ LitBot 安装完成！

已安装到：reader (/path/to/reader-bot/)
研究领域：<areas>
每日推送：<time>，<language>
隐私级别：<level>

可用命令：
  /lit-daily    — 手动触发今日论文推荐
  /lit-alert    — 竞争论文检测
  /lit-review   — 审稿辅助（beta）
  /lit-network  — 引用网络分析
  /lit-compare  — 多论文对比表
  /lit-panorama — 领域全景图
  /lit-profile  — 修改研究 profile

如需调整配置，随时使用 /lit-profile。
```

---

## Error Handling

| Situation | Action |
|-----------|--------|
| Python < 3.10 | Tell user to upgrade, provide install link |
| `setup.sh --auto` fails | Show error output, suggest manual steps |
| No Feishu credentials | Skip Phase 4, remind user to configure later |
| `mb schedule cron` fails | Show manual command for user to retry |
| User wants to change profile later | Point to `/lit-profile` skill |

---

## Important Notes

- **Never run `setup_profile.py` directly** — it requires terminal stdin. Always use the Python API.
- **Never use default values silently** — always show defaults and get user confirmation.
- **Ask one question at a time** — don't dump all questions in one message.
- **Show progress** — users should see what's happening at each step.
- **Profile privacy** — never send user identity (name, S2 ID, paper DOIs) to external LLMs.
