# /lit-review — Review Assistance (F3β)

⚠️ **BETA** — Every output MUST carry this disclaimer:
> ⚠️ Beta — AI analysis may contain errors. All conclusions should be verified independently.

Analyzes a paper's references, identifies gaps, and assesses novelty.

## Trigger
- **Manual**: `/lit-review <doi_or_arxiv_id>` or "审稿辅助 <doi>"

## Pipeline

```bash
cd /home/user/lyl/Research/shared/litbot
source venv/bin/activate
```

### Step 1: Resolve Paper
```python
from scripts.init_db import get_db
from scripts.paper_identity import get_or_create_paper
from scripts.fetch_papers import fetch_s2_by_ids

conn = get_db()
# Resolve input (DOI or arXiv ID) to canonical paper
paper = get_or_create_paper(conn=conn, doi=input_doi, arxiv_id=input_arxiv)
```

### Step 2: Fetch References
```python
# S2 API: get paper's reference list
# GET /paper/{s2_id}/references?fields=paperId,title,citationCount,isInfluential,year
references = fetch_references(conn, paper.s2_id)
```

### Step 3: Identify Missing Citations
Use S2 recommendations API to find related papers NOT in the reference list:
```python
# GET /recommendations/v1/papers/forpaper/{s2_id}?fields=...&limit=20
recommended = fetch_recommendations(conn, paper.s2_id)
missing = [r for r in recommended if r["paperId"] not in ref_s2_ids]
```

Flag:
- Highly-cited papers (top 10% by citation in the concept cluster) not cited
- Recent papers (< 1 year) in the same area not cited
- Self-citation ratio: count(self_citations) / total_references

### Step 4: Author Trajectory Analysis
```python
# For each author: fetch recent 5 papers from S2
# Identify research trajectory and expertise areas
```

### Step 5: LLM Novelty Analysis
**Prompt** (privacy-safe: only public paper data):
```
You are an academic reviewer analyzing a paper's novelty.

Paper under review:
  Title: {title}
  Abstract: {abstract}

The paper claims these contributions:
  [extracted from abstract/introduction]

For each claimed contribution, I found these potentially overlapping prior works:
  [list of similar papers with titles and abstracts]

For each contribution, assess:
1. Novelty level: HIGH / MEDIUM / LOW
2. Evidence: cite specific prior work that overlaps
3. What is genuinely new vs incremental improvement

IMPORTANT: Label every conclusion with confidence:
- [HIGH confidence]: supported by multiple matching papers
- [MEDIUM confidence]: based on abstract similarity, may differ in details
- [LOW confidence]: speculative, would need full paper reading to confirm
```

### Step 6: Generate Report
Output a structured Feishu card:

```
⚠️ Beta — AI analysis may contain errors. Verify independently.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📄 Review Analysis: {paper.title}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📚 Reference Analysis
  Total references: {N}
  Self-citation ratio: {X}%
  Potentially missing citations: {M}
    - {missing_paper_1} (cited {C} times) [HIGH confidence]
    - {missing_paper_2} ...

🔬 Novelty Assessment
  Contribution 1: "{claim}" → {MEDIUM} [MEDIUM confidence]
    Similar work: {paper_title} (2024)
  Contribution 2: ...

👤 Author Context
  First author: {name}, recent focus on {topic}
  Lab trajectory: {summary}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Confidence Labels

EVERY conclusion must have a confidence tag:
- **[HIGH confidence]**: Multiple independent evidence sources agree
- **[MEDIUM confidence]**: Based on abstract/metadata similarity
- **[LOW confidence]**: Speculative, needs full paper verification

## Error Handling
- If S2 can't find the paper: try OpenAlex, report partial analysis
- If reference list unavailable: skip missing citation analysis, do novelty only
- If LLM response is malformed: report raw findings without interpretation
