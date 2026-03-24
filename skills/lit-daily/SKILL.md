# /lit-daily — Daily Paper Digest + Trend Detection

Runs the F1 daily digest pipeline and F3 trend burst detection.

## Trigger
- **Scheduled**: cron daily at 08:00
- **Manual**: user sends `/lit-daily` or "今日论文"

## Pipeline

### Step 1: Fetch New Papers
```bash
cd litbot
```

```python
from scripts.init_db import get_db
from scripts.fetch_papers import fetch_crossref_new, fetch_arxiv_new, enrich_papers
from scripts.paper_identity import get_or_create_paper, is_already_pushed, record_push
from scripts.config import load_profile
from scripts.ranking import rank_papers, is_in_bootstrap_mode
from scripts.trend import update_trend_stats, detect_bursts
from scripts.feishu_cards import build_daily_digest_card
from scripts.observability import generate_health_report, log_op
from datetime import datetime, timedelta

conn = get_db()
profile = load_profile()
yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

# Fetch from Crossref + arXiv
crossref_papers = fetch_crossref_new(conn, from_date=yesterday)
arxiv_papers = fetch_arxiv_new(conn, date=yesterday)
all_raw = crossref_papers + arxiv_papers
```

### Step 2: Deduplicate via Paper Identity Layer
```python
from scripts.paper_identity import PaperRecord

papers = []
for raw in all_raw:
    record = get_or_create_paper(
        conn=conn,
        doi=raw.get("doi"),
        arxiv_id=raw.get("arxiv_id"),
        title=raw.get("title", ""),
        authors=raw.get("authors", []),
        year=raw.get("year"),
        venue=raw.get("venue"),
        abstract=raw.get("abstract"),
    )
    if not is_already_pushed(conn, record.pid, "F1"):
        papers.append(record)
```

### Step 3: Enrich
```python
enriched = enrich_papers(conn, [{"doi": p.doi, "arxiv_id": p.arxiv_id, "title": p.title} for p in papers])
# Merge enrichment back into PaperRecords (update DB via get_or_create_paper)
```

### Step 4: Rank
```python
ranked = rank_papers(papers, profile, conn)
# ranked is list of (PaperRecord, score)
```

### Step 5: Generate One-liners
For the top N papers, use LLM to generate a one-line recommendation in the user's language.

**LLM prompt** (batch all papers in one call):
```
You are a research paper recommendation assistant.
For each paper below, write ONE sentence (≤30 words) explaining why a researcher
in [{research_areas}] would find it interesting.
Language: {profile.preferences.language}

Papers:
1. Title: {title}  Abstract: {abstract[:200]}
2. ...

Output format: numbered list matching input order.
```

**Privacy**: Only send paper titles/abstracts (public info). Never send user name or project names.

### Step 6: Trend Detection
```python
update_trend_stats(conn, [{"concepts": p.concepts} for p, _ in ranked])
bursts = detect_bursts(conn)
```

If bursts found, use LLM to summarize (same privacy rules).

### Step 7: Build & Push Card
```python
bootstrap = is_in_bootstrap_mode(conn)
card = build_daily_digest_card(
    papers=[(p, score, one_liner) for (p, score), one_liner in zip(ranked, one_liners)],
    date=datetime.now().strftime("%Y-%m-%d"),
    total_scanned=len(all_raw),
    total_matched=len(ranked),
    trend_bursts=bursts if bursts else None,
    language=profile.preferences.language,
)
```

Push via Feishu webhook. Record each push:
```python
for p, _ in ranked:
    record_push(conn, p.pid, "F1", message_id=msg_id)
```

### Step 8: Health Report
At 09:00 (or after F1 completes), generate and push health card:
```python
report = generate_health_report(conn)
```

## Output
- Feishu card with top N papers + trend section
- Health report card (if configured)

## Error Handling
- If Crossref fails: continue with arXiv only, log warning
- If S2 enrichment fails: rank without embeddings (keyword-only)
- If LLM fails: push papers without one-liners, use title as fallback
- If Feishu push fails: retry 3x, then log error for manual retry
