# LitBot 使用指南

> 从零开始：通过 MetaBot 对话完成 LitBot 的安装、配置和日常使用。

---

## 目录

1. [概述](#概述)
2. [安装流程（通过 MetaBot 对话）](#安装流程)
3. [与 LitBot 对话](#与-litbot-对话)
4. [命令参考](#命令参考)
5. [日常使用场景](#日常使用场景)
6. [常见问题与排查](#常见问题与排查)

---

## 概述

LitBot 是一个文献监控 Agent，作为独立 bot 运行在 MetaBot 平台上。你在飞书中与它对话，它帮你：

- **每日推送**论文推荐（按你的研究方向排名）
- **碰撞检测**竞争论文（与你的项目重叠的新论文）
- **按需分析**引用网络、论文对比、领域全景

```
你 ←→ 飞书 ←→ LitBot (独立 bot)
                   ↓
              Claude Code + Python 脚本
                   ↓
         OpenAlex / S2 / Crossref / arXiv / Unpaywall
```

### 功能一览

| 命令 | 功能 | 阶段 |
|------|------|------|
| `/lit-daily` | 每日论文推送 + 趋势检测 | MVP |
| `/lit-alert` | 竞争论文碰撞检测 | MVP |
| `/lit-review <doi>` | 审稿辅助（引用缺失 + 新颖性分析） | v2 Beta |
| `/lit-network <doi>` | 引用网络分析（2跳图谱） | v2 |
| `/lit-compare <doi1> <doi2> ...` | 多论文对比表格 | v2 |
| `/lit-panorama <topic>` | 领域全景（分类树 + 概念图） | v3 |
| `/lit-profile` | 个人资料管理 | MVP |

---

## 安装流程

默认流程：通过与 MetaBot 对话，创建一个新的飞书 bot，安装 LitBot，配置 profile，设置定时推送。

### 第一步：在飞书开放平台创建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)
2. 点击 **创建企业自建应用**
3. 填写信息：
   - 应用名称：`LitBot`（或你喜欢的名字）
   - 应用描述：`文献追踪助手`
4. 进入应用后，点左侧菜单 **添加应用能力** → 选择 **机器人**
5. 记下 **App ID**（格式：`cli_xxx`）和 **App Secret**
6. 在应用的 **权限管理** 页面，搜索并开通以下权限：

| 权限 | 用途 |
|------|------|
| `im:message:send_as_bot` | 发送消息（推送论文卡片） |
| `im:message:update` | 更新卡片（按钮状态变化） |
| `im:chat:readonly` | 读取对话列表（获取 Chat ID） |

> **常见问题：找不到"机器人"选项**
> 确保你选择的是"企业自建应用"而不是"应用商店应用"。只有自建应用才能添加机器人能力。

> **常见问题：权限搜索不到**
> 部分权限需要管理员审批。如果搜索不到 `im:message:update`，尝试搜 `im:message` 然后在结果中找。

### 第二步：配置事件订阅并发布

1. 在应用管理页面，找到 **事件与回调** → **长连接**
2. 启用 **长连接模式**（WebSocket）
3. 添加事件：搜索并添加 `im.message.receive_v1`（接收消息）
4. 点击 **版本管理与发布** → **创建版本** → 填写版本号和更新说明
5. 提交发布申请（如果你是管理员，直接审批通过）

> **关键：必须添加 `im.message.receive_v1` 事件，否则 bot 收不到用户消息。**

> **常见问题：长连接 vs 请求地址**
> MetaBot 使用 **长连接**（WebSocket）模式接收消息，不是传统的回调 URL 模式。选择"长连接"，不需要配置请求地址。

> **常见问题：发布后飞书搜不到 bot**
> 检查 **可用范围** 是否包含你自己。测试阶段可以在"可用范围"中添加个人。

### 第三步：在 MetaBot 中注册 bot

在 MetaBot 对话中发送：

```
创建一个新 bot，名字叫 litbot，飞书 App ID 是 cli_xxx，Secret 是 xxx
```

MetaBot 会将 bot 注册到 `bots.json` 并自动激活。

注册完成后，**重启 MetaBot** 使新 bot 生效：

```
重启 metabot
```

或在终端执行 `pm2 restart metabot`。

> **验证：** 重启后在飞书中给新 bot 发一条消息（如"你好"），确认收到回复。

> **常见问题：bot 注册后发消息没回应**
> 1. 确认已重启 MetaBot → `pm2 status` 检查是否 online
> 2. 确认 App ID / Secret 正确 → 对比飞书开放平台和 bots.json
> 3. 确认已添加 `im.message.receive_v1` 事件（第二步）
> 4. 确认应用已发布且自己在可用范围内

### 第四步：获取 Chat ID

Chat ID 是你与 bot 的对话标识（格式：`oc_xxx`），后续配置定时推送时需要。

**方法 A：让 MetaBot 帮你查（推荐）**

1. 先在飞书中给 LitBot 发一条任意消息（如"你好"）
2. 在 MetaBot 中发送：

```
帮我查一下飞书 bot 的 chat ID，App ID 是 cli_xxx，Secret 是 xxx
```

MetaBot 会调用飞书 API 列出所有 chat，找到你的对话 ID。

**方法 B：手动调 API 查**

```bash
# 1. 获取 tenant token
TOKEN=$(curl -s -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
  -H "Content-Type: application/json" \
  -d '{"app_id":"cli_xxx","app_secret":"xxx"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['tenant_access_token'])")

# 2. 列出对话
curl -s https://open.feishu.cn/open-apis/im/v1/chats \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

在返回结果中找到 `chat_id`（以 `oc_` 开头）。

> **常见问题：返回空列表 / 没有 chat**
> 你必须 **先** 在飞书里给 bot 发一条消息，之后 API 才能看到这个对话。如果是群聊，需要先把 bot 加入群。

> **常见问题：Auth failed / token 获取失败**
> 检查 App ID 和 App Secret 是否正确复制（注意不要多复制空格）。确认应用已发布。

> **关键区分：** 每个 bot 有自己独立的 Chat ID。LitBot 的 Chat ID 和 MetaBot 的不一样，不能混用。

### 第五步：安装 LitBot 代码

在 MetaBot 对话中发送：

```
帮 LitBot 从 https://github.com/Liyunlun/litbot.git 安装 litbot，并按照 Readme 中的步骤安装
```

MetaBot 会执行：
1. `git clone` 到 bot 工作目录下的 `litbot/`
2. `bash setup.sh --auto` 安装依赖 + 初始化数据库
3. 复制 skills 到 `.claude/skills/`

> **常见问题：setup.sh 报错**
>
> | 错误信息 | 解决方案 |
> |---------|---------|
> | `Python 3.10+ required` | `conda activate <env>` 或 `pyenv install 3.10` |
> | `pip install` 超时 | `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
> | `Permission denied` | `chmod +x setup.sh` 或 `bash setup.sh` |
> | `sqlite3.OperationalError` | 检查 `litbot/data/` 目录写权限 |

### 第六步：配置 Profile

> 如果 MetaBot 在安装时已自动引导配置，可跳过此步。

在 LitBot 对话窗口发送：

```
请使用 /lit-profile 配置 litbot
```

LitBot 会交互式询问以下信息：

| 步骤 | 问题 | 是否必填 | 说明 |
|------|------|---------|------|
| 1 | 姓名 | 可选 | 仅显示用，不发送给 LLM |
| 2 | Semantic Scholar ID | 可选 | 有的话能大幅提升推荐质量 |
| 3 | 你的论文 DOI 列表 | 可选 | 用于 embedding 热启动 |
| 4 | **研究领域** | **必填** | 如 "symbolic reasoning, agent learning" |
| 5 | 活跃项目 | 可选 | 名称 + 关键词 + 目标期刊（碰撞检测用） |
| 6 | 期刊分级 | 可选 | tier1（加权）/ blacklist（排除） |
| 7 | 推送偏好 | 可选 | 语言、时间、每日上限、探索性比例 |

配置保存在 `litbot/data/profile.yaml`。

> **关键：至少填写"研究领域"，否则 LitBot 无法工作。**

> **常见问题：配置后想修改某项**
> 不需要重新 setup，直接用命令修改：
> ```
> 使用 /lit-profile areas NLP, Agent, Symbolic Reasoning
> 使用 /lit-profile set max_papers 5
> 使用 /lit-profile add-project MyProject --keywords kw1,kw2 --venues ICML,NeurIPS
> ```

> **隐私说明：** 你的姓名、机构、S2 ID、项目名称 **永远不会发送给外部 LLM**。仅用于本地匹配和 embedding 计算。

### 第七步：配置定时推送

> 如果 MetaBot 在安装时已自动引导配置，可跳过此步。

在 MetaBot 对话中发送：

```
给 litbot 配置每天早上 8 点在 oc_xxx 执行 /lit-daily
```

MetaBot 会注册 cron 任务：
```bash
mb schedule cron litbot oc_xxx '0 8 * * *' '执行 /lit-daily'
```

> **常见问题：cron 配置了但没收到推送**
> 排查清单：
> 1. `mb schedule list` — 确认任务存在且状态为 active
> 2. `pm2 status` — 确认 metabot 进程 online
> 3. 确认 Chat ID（`oc_xxx`）正确 — 必须是 LitBot 自己的对话 ID，不是 MetaBot 的
> 4. 确认 profile.yaml 已配置 — 在 LitBot 中发 `使用 /lit-profile show` 检查
> 5. 手动触发测试 — 在 LitBot 中发 `使用 /lit-daily` 看是否正常工作

> **常见问题：cron 中的 Chat ID 用错了**
> 每个 bot 有自己独立的对话，Chat ID 不能混用。定时推送需要用 LitBot 自己的 Chat ID（第四步获取的那个）。

### 第八步：验证安装

在 LitBot 对话中发送：

```
请使用 /lit-daily
```

如果一切正常，你会收到一张论文推荐卡片。

**完整验证清单：**

| 检查项 | 方法 | 预期结果 |
|--------|------|---------|
| Bot 响应 | 在飞书发任意消息给 LitBot | 收到回复 |
| Skills 加载 | 发 `检查 /lit-profile` | 显示你的 profile |
| 数据库 | 发 `使用 /lit-daily` | 推送论文卡片 |
| 定时任务 | `mb schedule list` | 看到 litbot 的 cron 条目 |

> **常见问题：发消息后报错或无响应**
> 1. Skills 未安装 — 检查 `.claude/skills/lit-daily/SKILL.md` 是否存在
> 2. Python 依赖缺失 — `cd litbot && pip install -r requirements.txt`
> 3. 数据库未初始化 — `cd litbot && python -m scripts.init_db`
> 4. Profile 未配置 — 先执行 `请使用 /lit-profile setup`

---

## 与 LitBot 对话

### 直接对话（推荐）

在飞书中找到 LitBot 的对话窗口，直接发消息：

```
使用 /lit-daily
今日论文
碰撞检测
引用网络 2301.07041
```

### 通过 MetaBot 委派

如果你在 MetaBot 的对话中：

```
让 litbot 执行 /lit-daily
```

### 自然语言

| 你说的 | 等价命令 |
|--------|----------|
| "今日论文" | `/lit-daily` |
| "碰撞检测" | `/lit-alert` |
| "审稿辅助 2301.07041" | `/lit-review 2301.07041` |
| "引用网络 2301.07041" | `/lit-network 2301.07041` |
| "论文对比 doi1 doi2" | `/lit-compare doi1 doi2` |
| "领域全景 agent learning" | `/lit-panorama agent learning` |
| "我的资料" | `/lit-profile` |

---

## 命令参考

### /lit-daily — 每日论文推送

**触发：** 定时（cron）或手动发送 `使用 /lit-daily`

**输出卡片包含：**
- Top N 篇推荐论文（标题 + 一句话摘要 + 评分）
- 碰撞预警（如果有高风险竞争论文）
- 趋势突变（如果有概念突增 Z > 3σ）

**冷启动：** 保存论文 < 5 篇时，会显示额外按钮帮助快速收集偏好。

### /lit-alert — 碰撞检测

**触发：** `使用 /lit-alert` 或 "碰撞检测"

**两阶段检测：**
1. 粗筛：SPECTER2 embedding 余弦相似度 ≥ 0.65
2. 精筛：LLM 5 维度打分（问题重叠 0.30 + 方法相似 0.25 + 数据集重叠 0.20 + 贡献冲突 0.15 + 结论竞争 0.10）

**报警级别：**

| 级别 | 阈值 | 行为 |
|------|------|------|
| HIGH | ≥ 0.55 | 立即推送红色卡片 |
| MEDIUM | 0.35–0.55 | 在每日推送中标记 |
| UNCERTAIN | 0.25–0.35 | "可能相关，请确认" + 按钮 |
| LOW | < 0.25 | 仅记录日志 |

### /lit-review \<doi\> — 审稿辅助（Beta）

**触发：** `使用 /lit-review 2301.07041`

**输出（每条结论带置信度标签）：**
- 引用缺失分析
- 作者轨迹分析
- 新颖性评估（HIGH / MEDIUM / LOW）

> Beta 功能，所有结论需独立验证。

### /lit-network \<doi\> — 引用网络

**触发：** `使用 /lit-network 2301.07041`

**输出：**
- Mermaid 图谱（黄=种子、绿=后续、蓝=应用、红=方法分支）
- Top 5 影响力节点 + 关键洞察

### /lit-compare \<doi1\> \<doi2\> [...] — 论文对比

**触发：** `使用 /lit-compare doi1 doi2`（2-10 篇）

**输出：** 结构化对比表（问题、方法、数据集、结果、新颖性、局限），每字段标注来源可信度。

### /lit-panorama \<topic\> — 领域全景

**触发：** `使用 /lit-panorama agent learning`

**输出：** 概念层次树 + 分支代表论文 + 活跃作者 + 趋势 + 你的项目定位。

### /lit-profile — 个人资料

```
使用 /lit-profile                # 显示当前资料
使用 /lit-profile setup          # 重新配置向导
使用 /lit-profile areas A, B, C  # 修改研究领域
使用 /lit-profile add-project X --keywords kw1,kw2 --venues V1,V2
使用 /lit-profile remove-project X
使用 /lit-profile tier1 add NeurIPS
使用 /lit-profile blacklist add MDPI
使用 /lit-profile set language zh
使用 /lit-profile set max_papers 5
使用 /lit-profile set diversity 0.3
```

---

## 日常使用场景

### 场景 1：每天早上收到论文推送

安装完成后自动运行。每天定时收到飞书卡片。

### 场景 2：发现竞争论文

推送卡片中碰撞预警区域显示红色警告：
```
碰撞预警 — 项目: SymbolicAgent
论文: "Symbolic Predicate Learning for Autonomous Agents"
问题重叠: 0.85  方法相似: 0.72
```
→ 回复 `使用 /lit-network <doi>` 深入分析引用关系

### 场景 3：写论文前文献调研

```
使用 /lit-panorama symbolic reasoning for agents    ← 先看全局
使用 /lit-compare doi1 doi2 doi3                    ← 再对比关键论文
```

### 场景 4：审稿快速检查

```
使用 /lit-review 2401.12345    ← 引用完整性 + 新颖性评估
```

### 场景 5：调整偏好

```
使用 /lit-profile set max_papers 5        ← 减少每日推送数量
使用 /lit-profile set diversity 0.4       ← 增加跨领域探索
使用 /lit-profile areas A, B, C, D        ← 扩展研究领域
```

---

## 常见问题与排查

### 安装阶段

**Q: 飞书开放平台创建应用后，在飞书客户端搜不到 bot**
- 确认应用已 **发布**（开放平台 → 应用发布 → 创建版本 → 申请上线）
- 如果是测试阶段，需要在 **可用范围** 中添加自己

**Q: 给 bot 发消息没有回应**
按顺序排查：
1. MetaBot 是否在运行？→ `pm2 status`
2. bot 是否已注册？→ 在 MetaBot 中发 `mb bots` 看列表
3. App ID / Secret 是否正确？→ 对比飞书开放平台和 bots.json
4. 是否添加了 `im.message.receive_v1` 事件？→ 检查飞书开放平台事件订阅
5. 应用是否已发布？→ 检查版本管理页面
6. 是否在可用范围内？→ 检查应用可用范围设置

**Q: Chat ID 怎么也查不到**
- 你必须 **先** 在飞书里给 bot 发一条消息，之后 API 才能看到这个对话
- 如果是群聊场景，需要先把 bot 拉入群，然后查到的是群的 `oc_xxx`
- 注意区分：每个 bot 有自己独立的 Chat ID，LitBot 的和 MetaBot 的不一样

**Q: setup.sh 报错**

| 错误信息 | 解决方案 |
|---------|---------|
| `Python 3.10+ required` | `conda activate <env>` 或 `pyenv install 3.10` |
| `pip install` 超时 | `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| `Permission denied` | `chmod +x setup.sh` 或 `bash setup.sh` |
| `sqlite3.OperationalError` | 检查 `litbot/data/` 目录写权限 |

### 配置阶段

**Q: Profile 配置后想修改怎么办**
不需要重新 setup，直接用 `使用 /lit-profile` 子命令修改即可。所有修改即时生效。

**Q: 不知道自己的 Semantic Scholar ID**
去 [semanticscholar.org](https://www.semanticscholar.org/) 搜索你的名字，进入作者页面，URL 中的数字就是你的 ID。没有也没关系，只是推荐质量会降低一些。

**Q: 研究领域怎么填效果最好**
- 3-5 个为佳，太少会遗漏，太多会不精准
- 用英文关键词（论文标题/摘要中常出现的词）
- 例：`symbolic reasoning, neuro-symbolic AI` 比 `AI` 好

### 定时推送阶段

**Q: cron 配了但不推送**
排查清单：
1. `mb schedule list` — 任务是否存在、状态是否 active
2. Chat ID 是否正确 — 必须是 LitBot 自己的对话 ID
3. `pm2 status` — metabot 是否 online
4. Profile 是否已配置 — `使用 /lit-profile show`
5. 手动测试 — 在 LitBot 中发 `使用 /lit-daily`

**Q: cron 的 Chat ID 填了 MetaBot 的 ID**
这是常见错误。每个 bot 有自己独立的对话，Chat ID 不能混用。定时推送需要用 LitBot 自己的 Chat ID。

**Q: 想改推送时间**
```
# 先删旧任务
mb schedule list          ← 找到 task_id
mb schedule remove <id>

# 再加新任务
mb schedule cron litbot oc_xxx '0 9 * * *' '执行 /lit-daily'
```

### 使用阶段

**Q: 推送论文不相关**
逐步调优（按优先级）：
1. 精确化 `research_areas`
2. 添加 `active_projects` 关键词
3. 提供 `my_papers` DOI（提升 embedding 质量）
4. 降低 `diversity_ratio`（减少探索性论文）

**Q: 碰撞检测误报太多 / 漏报**
前 2 周是 shadow mode（观察期）。之后系统会请你标注论文校准。也可以通过点击 UNCERTAIN 级别预警的确认按钮帮助校准。

**Q: 卡片内容被截断**
飞书卡片有 28KB 限制。降低 `max_papers` 即可。

### 数据相关

**Q: 备份数据**
```bash
cp litbot/data/litbot.db litbot/data/litbot.db.backup
cp litbot/data/profile.yaml litbot/data/profile.yaml.backup
```

**Q: 重置（从头开始）**
```bash
rm litbot/data/litbot.db
cd litbot && python -m scripts.init_db && cd ..
```
会清除推送记录和反馈，Profile 不受影响。

---

## 快速参考卡

```
安装:  1. 飞书开放平台创建应用 → 拿到 App ID + Secret
       2. 配置长连接 + im.message.receive_v1 事件 → 发布应用
       3. 告诉 MetaBot → "创建 litbot bot，App ID cli_xxx，Secret xxx"
       4. 重启 MetaBot → 给 LitBot 发消息验证
       5. 获取 Chat ID（oc_xxx）
       6. 告诉 MetaBot → "帮 LitBot 安装 litbot"
       7. 在 LitBot 中 → "请使用 /lit-profile 配置 litbot"
       8. 告诉 MetaBot → "给 litbot 配 cron 每天 8:00 在 oc_xxx 推送"

日常:  使用 /lit-daily          每日推送
       使用 /lit-alert          碰撞检测
       使用 /lit-review <doi>   审稿辅助
       使用 /lit-network <doi>  引用网络
       使用 /lit-compare <dois> 论文对比
       使用 /lit-panorama <topic> 领域全景
       使用 /lit-profile        管理资料

调优:  使用 /lit-profile areas A, B, C
       使用 /lit-profile set max_papers 5
       使用 /lit-profile set diversity 0.3
```
