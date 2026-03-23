# /lit-compare — Comparison Table Generation (F6)

Generates a structured comparison table across multiple papers.

## Trigger
- **Manual**: `/lit-compare <doi1> <doi2> [doi3] ...` or "论文对比 <doi1> <doi2>"

## Pipeline

### Step 1: Resolve Papers
```python
from scripts.init_db import get_db
from scripts.paper_identity import get_or_create_paper
from scripts.fetch_papers import fetch_s2_by_ids, fetch_openalex_by_dois, fetch_unpaywall

conn = get_db()
papers = []
for identifier in input_ids:
    p = get_or_create_paper(conn=conn, doi=identifier, arxiv_id=identifier)
    papers.append(p)
```

### Step 2: Gather Information

For each paper, collect:
1. **S2 TLDR** (preferred — concise, structured)
2. **Abstract** (fallback)
3. **PDF via Unpaywall** (for table/result extraction if needed)

```python
# S2 batch: get TLDR, method description
# OpenAlex: get concepts, venue, citation count
# Unpaywall: get OA PDF URL
```

### Step 3: Extract Comparison Dimensions

Use LLM to extract structured fields from each paper:

**Prompt**:
```
You are building a paper comparison table. For each paper, extract:

1. Problem: What problem does it solve? (1 sentence)
2. Method: Core approach/technique (1-2 sentences)
3. Dataset: What data/benchmarks are used?
4. Key Result: Main quantitative result (if available)
5. Novelty: What's new compared to prior work? (1 sentence)
6. Limitation: Key limitation acknowledged (1 sentence)

Papers:
{for each paper: title, abstract, TLDR}

Output JSON array with one object per paper.

IMPORTANT: For each field, label the source:
- [verified: from abstract] — directly stated in abstract
- [verified: from TLDR] — from Semantic Scholar TLDR
- [inferred: from abstract] — reasoned from abstract, not explicitly stated
- [unverified: needs full paper] — cannot determine from available metadata
```

### Step 4: Build Table

| Dimension | Paper 1 | Paper 2 | Paper 3 |
|-----------|---------|---------|---------|
| Problem | ... [verified] | ... [verified] | ... |
| Method | ... | ... | ... |
| Dataset | ... | ... | ... |
| Key Result | ... | ... [inferred] | ... |
| Novelty | ... | ... | ... |
| Limitation | ... [unverified] | ... | ... |

### Step 5: Output

1. **Feishu card**: Markdown table (for quick viewing)
2. **Excel file**: Full table with source labels, paper metadata, and URLs
   - Output to `/tmp/metabot-outputs/{chat_id}/comparison_table.xlsx`

### PDF Table Extraction (optional, if results available)

If PDF is available and user requests detailed results:
```python
# Extract tables from PDF (best-effort)
# Label extraction confidence:
#   high: table clearly structured, values parsed correctly
#   low: table partially parsed, some values may be wrong
#   failed: could not extract table
```

Fall back to S2 TLDR + abstract if extraction fails.

## Error Handling
- If a paper can't be found: skip it, note in output
- If S2 TLDR unavailable: use abstract only
- If PDF extraction fails: mark affected cells as [unverified: needs full paper]
- Minimum 2 papers required; maximum 10 papers per comparison
