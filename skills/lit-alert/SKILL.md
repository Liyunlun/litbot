# /lit-alert — Collision Alert (F2)

Detects papers that compete with user's active research projects.

## Trigger
- **Scheduled**: runs as part of `/lit-daily` digest pipeline
- **Manual**: user sends `/lit-alert` or "碰撞检测"

## Pipeline

```bash
cd ${LITBOT_ROOT:-litbot}
# Activate venv only if it exists (skipped in global-deps environments)
[ -d venv ] && source venv/bin/activate
```

### Step 1: Get Today's Papers with Embeddings
```python
from scripts.init_db import get_db
from scripts.config import load_profile
from scripts.collision import detect_collisions, AlertLevel
from scripts.paper_identity import is_already_pushed, record_push
from scripts.feishu_cards import build_collision_card
from scripts.observability import log_op
from datetime import datetime

conn = get_db()
profile = load_profile()
today = datetime.now().strftime("%Y-%m-%d")

# Fetch today's papers that have embeddings
papers_with_emb = conn.execute(
    "SELECT * FROM papers WHERE created_at LIKE ? AND embedding IS NOT NULL",
    (f"{today}%",)
).fetchall()
# Convert rows to PaperRecord objects
```

### Step 2: Run Two-Stage Detection
```python
def llm_call(prompt: str) -> str:
    """Call LLM for structured collision scoring."""
    # Use Claude or configured LLM
    # Privacy: prompt contains only paper title/abstract + anonymous keywords
    pass

results = detect_collisions(papers, profile, conn, llm_call=llm_call)
```

### Step 3: Alert Based on Level

For each result:
```python
for r in results:
    pid = r["paper"].pid
    level = r["alert_level"]

    if is_already_pushed(conn, pid, "F2"):
        continue

    if level in (AlertLevel.HIGH, AlertLevel.MEDIUM):
        # Include in daily digest collision section
        card = build_collision_card(
            paper=r["paper"],
            project_name=r["project"],
            collision_score=r["collision_score"],
            scores=r["scores"],
            analysis=r["analysis"],
            alert_level=level,
        )
        record_push(conn, pid, "F2", message_id=msg_id)
        log_op(conn, "litbot", f"f2_alert_{level.name.lower()}", "ok", detail=f"{pid}: {r['collision_score']:.2f}")

    elif level == AlertLevel.UNCERTAIN:
        # Push with "please confirm" label
        card = build_collision_card(
            paper=r["paper"],
            project_name=r["project"],
            collision_score=r["collision_score"],
            scores=r["scores"],
            analysis=r["analysis"] + "\n\n❓ 这篇论文可能与您的项目相关，请确认。",
            alert_level=level,
        )
        # Push with confirm/dismiss buttons
        record_push(conn, pid, "F2", message_id=msg_id)

    else:
        # LOW: log only
        log_op(conn, "litbot", "f2_alert_low", "ok", detail=f"{pid}: {r['collision_score']:.2f}")
```

## LLM Prompt for Stage 2

```
You are an academic competition analyst. Given a paper and a research project description,
score the competition level on 5 dimensions (0-1 scale):

Paper:
  Title: {title}
  Abstract: {abstract}

Research keywords: {keywords}  (anonymous — do not infer researcher identity)

Score these dimensions:
1. problem_overlap (0-1): Do they address the same research question?
2. method_similarity (0-1): Do they use similar techniques?
3. dataset_overlap (0-1): Same benchmark/domain/scenario?
4. contribution_conflict (0-1): Would their claims undermine this research?
5. conclusion_competitiveness (0-1): Do they claim SOTA on the same target?

Output JSON:
{"problem_overlap": 0.8, "method_similarity": 0.6, ..., "analysis": "one paragraph explanation"}
```

## Calibration

- **Week 1-2**: Shadow mode — compute scores but don't alert. Log all scores.
- **Week 3**: Review logged scores with user. User labels 20-30 papers.
- **Adjust**: Fit thresholds to maximize precision ≥ 75%, recall ≥ 60%.
- **Ongoing**: Every 30 days, auto-adjust thresholds ±0.05 based on user feedback.

## Error Handling
- If no embeddings available: use keyword-only coarse filter
- If LLM fails: use coarse similarity as proxy score (higher threshold: 0.75 → HIGH)
- If Feishu push fails: retry 3x, log for manual push
