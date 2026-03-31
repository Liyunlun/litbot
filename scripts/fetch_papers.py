#!/usr/bin/env python3
"""Async-capable API clients for academic data sources.

Fetches papers from Crossref, arXiv, OpenAlex, Semantic Scholar, and Unpaywall.
All clients use httpx with exponential-backoff retries and circuit breakers.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from .config import RetryPolicy
from .observability import get_circuit, log_op, timed_op

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    retry_policy: RetryPolicy,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an HTTP request with exponential-backoff retries.

    Backoff schedule: 1s, 2s, 4s (up to retry_policy.max_attempts).
    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(retry_policy.max_attempts):
        try:
            response = await client.request(
                method,
                url,
                timeout=retry_policy.timeout_per_request,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < retry_policy.max_attempts - 1:
                # Use longer backoff for 429 Rate Limit responses
                is_rate_limit = (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code == 429
                )
                if is_rate_limit:
                    retry_after = exc.response.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and retry_after.isdigit() else 30
                else:
                    delay = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "Retry %d/%d for %s %s: %s (backoff %.0fs)",
                    attempt + 1,
                    retry_policy.max_attempts,
                    method,
                    url,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 1. Crossref
# ---------------------------------------------------------------------------

async def fetch_crossref_new(
    conn: sqlite3.Connection,
    from_date: str,
    rows: int = 100,
    *,
    query_keywords: list[str] | None = None,
    retry_policy: RetryPolicy | None = None,
) -> list[dict[str, Any]]:
    """Fetch recently indexed works from Crossref.

    Args:
        conn: SQLite connection for observability logging.
        from_date: ISO date string (YYYY-MM-DD) for the index-date filter.
        rows: Maximum number of results to return.
        query_keywords: Profile keywords to pre-filter results. When provided,
            Crossref ``query`` parameter narrows results to relevant papers
            instead of returning globally random recent papers.
        retry_policy: Override default retry settings.

    Returns:
        List of dicts with keys: doi, title, authors, year, venue.
    """
    policy = retry_policy or RetryPolicy()
    source = "crossref"
    circuit = get_circuit(source, cooldown_sec=policy.circuit_breaker_cooldown)

    if circuit.is_open:
        logger.warning("Circuit open for %s — skipping", source)
        return []

    # Build query with keyword pre-filtering.
    # Without keywords, Crossref returns globally random recent papers which
    # is useless for niche fields (100 random papers won't contain any match).
    url = (
        f"https://api.crossref.org/works"
        f"?filter=from-index-date:{from_date}"
        f"&rows={rows}&sort=relevance&order=desc"
    )
    if query_keywords:
        # Use top 5 keywords to keep the query focused
        query_terms = "+".join(query_keywords[:5])
        url += f"&query={query_terms}"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        with timed_op(conn, source, "fetch_new") as op:
            resp = await _request_with_retry(client, "GET", url, policy)
            data = resp.json()

            items = data.get("message", {}).get("items", [])
            papers: list[dict[str, Any]] = []
            for item in items:
                # Extract published year
                date_parts = (
                    item.get("published-print", {}).get("date-parts")
                    or item.get("published-online", {}).get("date-parts")
                    or item.get("issued", {}).get("date-parts")
                    or [[None]]
                )
                year = date_parts[0][0] if date_parts and date_parts[0] else None

                # Extract authors
                authors: list[str] = []
                for a in item.get("author", []):
                    family = a.get("family", "")
                    given = a.get("given", "")
                    if family:
                        name = f"{family}, {given}" if given else family
                        authors.append(name)

                # Extract venue (container-title)
                container = item.get("container-title", [])
                venue = container[0] if container else None

                # Extract title
                title_list = item.get("title", [])
                title = title_list[0] if title_list else ""

                papers.append({
                    "doi": item.get("DOI"),
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "venue": venue,
                })

            op["detail"] = f"fetched {len(papers)} papers from crossref"
            return papers


# ---------------------------------------------------------------------------
# 2. arXiv
# ---------------------------------------------------------------------------

_ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

_DEFAULT_CATEGORIES = ["cs.CL", "cs.AI", "cs.LG", "cs.SD", "eess.AS"]

# Mapping from common research area keywords to arXiv categories.
# Used when profile doesn't specify explicit arXiv categories.
_AREA_TO_ARXIV: dict[str, list[str]] = {
    "psychology": ["q-bio.NC", "cs.CY", "cs.HC"],
    "social": ["cs.SI", "cs.CY", "physics.soc-ph"],
    "neuroscience": ["q-bio.NC", "cs.NE"],
    "biology": ["q-bio.GN", "q-bio.MN", "q-bio.PE"],
    "physics": ["physics.gen-ph", "cond-mat", "hep-ph"],
    "math": ["math.CO", "math.OC", "math.ST"],
    "economics": ["econ.GN", "econ.TH"],
    "statistics": ["stat.ML", "stat.ME", "stat.AP"],
    "medical": ["q-bio.QM", "cs.CE"],
    "robotics": ["cs.RO", "cs.SY"],
    "nlp": ["cs.CL"],
    "vision": ["cs.CV"],
    "machine learning": ["cs.LG", "stat.ML"],
    "agent": ["cs.AI", "cs.MA"],
}


def _infer_arxiv_categories(research_areas: list[str]) -> list[str]:
    """Infer arXiv categories from profile research areas."""
    cats: list[str] = []
    for area in research_areas:
        area_lower = area.lower()
        for keyword, arxiv_cats in _AREA_TO_ARXIV.items():
            if keyword in area_lower:
                for c in arxiv_cats:
                    if c not in cats:
                        cats.append(c)
    return cats if cats else _DEFAULT_CATEGORIES


async def fetch_arxiv_new(
    conn: sqlite3.Connection,
    date: str,
    categories: list[str] | None = None,
    max_results: int = 200,
    *,
    query_keywords: list[str] | None = None,
    retry_policy: RetryPolicy | None = None,
) -> list[dict[str, Any]]:
    """Fetch papers submitted on a given date from arXiv.

    Args:
        conn: SQLite connection for observability logging.
        date: Date string in YYYYMMDD format for the submittedDate range.
        categories: arXiv category codes to filter. Defaults to CS/audio categories.
        max_results: Maximum entries to retrieve.
        query_keywords: Profile keywords used to infer categories when none
            are explicitly provided. Also adds keyword terms to the query
            for better relevance.
        retry_policy: Override default retry settings.

    Returns:
        List of dicts with keys: arxiv_id, title, authors, year, abstract, categories.
    """
    policy = retry_policy or RetryPolicy()
    source = "arxiv"
    circuit = get_circuit(source, cooldown_sec=policy.circuit_breaker_cooldown)

    if circuit.is_open:
        logger.warning("Circuit open for %s — skipping", source)
        return []

    # Infer categories from profile keywords if not explicitly provided
    if categories:
        cats = categories
    elif query_keywords:
        cats = _infer_arxiv_categories(query_keywords)
    else:
        cats = _DEFAULT_CATEGORIES

    cat_query = "+OR+".join(f"cat:{c}" for c in cats)
    # Add keyword terms to improve relevance within matched categories
    if query_keywords and not categories:
        kw_query = "+OR+".join(f"all:{kw}" for kw in query_keywords[:3])
        search_query = f"submittedDate:[{date}+TO+{date}]+AND+(({cat_query})+OR+({kw_query}))"
    else:
        search_query = f"submittedDate:[{date}+TO+{date}]+AND+({cat_query})"
    url = (
        f"https://export.arxiv.org/api/query"
        f"?search_query={search_query}"
        f"&max_results={max_results}"
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:
        with timed_op(conn, source, "fetch_new") as op:
            resp = await _request_with_retry(client, "GET", url, policy)
            root = ET.fromstring(resp.text)

            papers: list[dict[str, Any]] = []
            for entry in root.findall("atom:entry", _ARXIV_NS):
                # arxiv_id from entry id URL (e.g. http://arxiv.org/abs/2401.12345v1)
                entry_id = entry.findtext("atom:id", "", _ARXIV_NS)
                arxiv_id = entry_id.rsplit("/abs/", 1)[-1] if "/abs/" in entry_id else entry_id
                # Strip version suffix for canonical ID
                if arxiv_id and "v" in arxiv_id:
                    arxiv_id = arxiv_id.rsplit("v", 1)[0]

                title_text = entry.findtext("atom:title", "", _ARXIV_NS)
                title = " ".join(title_text.split())  # collapse whitespace

                authors: list[str] = []
                for author_el in entry.findall("atom:author", _ARXIV_NS):
                    name = author_el.findtext("atom:name", "", _ARXIV_NS).strip()
                    if name:
                        authors.append(name)

                summary = entry.findtext("atom:summary", "", _ARXIV_NS).strip()

                # Published year
                published = entry.findtext("atom:published", "", _ARXIV_NS)
                year = int(published[:4]) if published and len(published) >= 4 else None

                # Categories
                entry_cats: list[str] = []
                for cat_el in entry.findall("atom:category", _ARXIV_NS):
                    term = cat_el.get("term", "")
                    if term:
                        entry_cats.append(term)
                # Also check arxiv namespace
                for cat_el in entry.findall("arxiv:primary_category", _ARXIV_NS):
                    term = cat_el.get("term", "")
                    if term and term not in entry_cats:
                        entry_cats.insert(0, term)

                papers.append({
                    "arxiv_id": arxiv_id,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "abstract": summary,
                    "categories": entry_cats,
                })

            op["detail"] = f"fetched {len(papers)} papers from arxiv"
            return papers


# ---------------------------------------------------------------------------
# 3. OpenAlex
# ---------------------------------------------------------------------------

def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct abstract text from OpenAlex abstract_inverted_index."""
    if not inverted_index:
        return None
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in word_positions) if word_positions else None


async def fetch_openalex_by_dois(
    conn: sqlite3.Connection,
    dois: list[str],
    *,
    retry_policy: RetryPolicy | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch paper metadata from OpenAlex by DOIs.

    Args:
        conn: SQLite connection for observability logging.
        dois: List of DOI strings to look up.
        retry_policy: Override default retry settings.

    Returns:
        Dict keyed by DOI with enrichment data: openalex_id, title, abstract,
        concepts, cited_by_count, venue, arxiv_id.
    """
    policy = retry_policy or RetryPolicy()
    source = "openalex"
    circuit = get_circuit(source, cooldown_sec=policy.circuit_breaker_cooldown)

    if circuit.is_open:
        logger.warning("Circuit open for %s — skipping", source)
        return {}

    if not dois:
        return {}

    results: dict[str, dict[str, Any]] = {}
    # Batch in groups of 50
    batch_size = 50

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for i in range(0, len(dois), batch_size):
            batch = dois[i : i + batch_size]
            doi_filter = "|".join(batch)
            url = f"https://api.openalex.org/works?filter=doi:{doi_filter}"

            with timed_op(conn, source, "fetch_by_dois") as op:
                resp = await _request_with_retry(client, "GET", url, policy)
                data = resp.json()

                for work in data.get("results", []):
                    # Extract DOI from work (normalized)
                    work_doi = work.get("doi", "") or ""
                    # OpenAlex returns full URL: https://doi.org/10.xxx
                    if work_doi.startswith("https://doi.org/"):
                        work_doi = work_doi[len("https://doi.org/"):]

                    # OpenAlex ID from URL
                    oa_id_url = work.get("id", "")
                    openalex_id = (
                        oa_id_url.rsplit("/", 1)[-1]
                        if "/" in oa_id_url
                        else oa_id_url
                    )

                    # Abstract
                    abstract = _reconstruct_abstract(
                        work.get("abstract_inverted_index")
                    )

                    # Concepts
                    concepts: list[str] = [
                        c.get("display_name", "")
                        for c in work.get("concepts", [])
                        if c.get("display_name")
                    ]

                    # Venue from primary_location.source.display_name
                    venue: str | None = None
                    primary_loc = work.get("primary_location") or {}
                    source_info = primary_loc.get("source") or {}
                    venue = source_info.get("display_name")

                    # arXiv cross-link
                    ids = work.get("ids", {})
                    arxiv_url = ids.get("arxiv", "")
                    arxiv_id: str | None = None
                    if arxiv_url:
                        # Format: https://arxiv.org/abs/2401.12345
                        arxiv_id = (
                            arxiv_url.rsplit("/", 1)[-1]
                            if "/" in arxiv_url
                            else arxiv_url
                        )

                    entry = {
                        "openalex_id": openalex_id,
                        "title": work.get("title", ""),
                        "abstract": abstract,
                        "concepts": concepts,
                        "cited_by_count": work.get("cited_by_count", 0),
                        "venue": venue,
                        "arxiv_id": arxiv_id,
                    }

                    results[work_doi] = entry

                op["detail"] = f"batch {i // batch_size + 1}: {len(batch)} dois"

    return results


# ---------------------------------------------------------------------------
# 3b. OpenAlex keyword search (additional source for niche fields)
# ---------------------------------------------------------------------------


async def fetch_openalex_by_keywords(
    conn: sqlite3.Connection,
    keywords: list[str],
    from_date: str,
    rows: int = 50,
    *,
    retry_policy: RetryPolicy | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent papers from OpenAlex matching profile keywords.

    This supplements Crossref for niche fields where Crossref's 100 random
    papers are unlikely to contain relevant results. OpenAlex supports
    concept-based and keyword search with date filtering.

    Args:
        conn: SQLite connection for observability logging.
        keywords: Profile keywords to search for.
        from_date: ISO date string (YYYY-MM-DD) for the date filter.
        rows: Maximum number of results.
        retry_policy: Override default retry settings.

    Returns:
        List of dicts with keys: doi, title, authors, year, venue, abstract,
        openalex_id, concepts, cited_by_count.
    """
    policy = retry_policy or RetryPolicy()
    source = "openalex"
    circuit = get_circuit(source, cooldown_sec=policy.circuit_breaker_cooldown)

    if circuit.is_open:
        logger.warning("Circuit open for %s — skipping keyword search", source)
        return []

    if not keywords:
        return []

    # Build search query: use top keywords joined by OR
    search_terms = "|".join(keywords[:5])
    url = (
        f"https://api.openalex.org/works"
        f"?filter=from_publication_date:{from_date},"
        f"default.search:{search_terms}"
        f"&per_page={rows}&sort=relevance_score:desc"
    )

    papers: list[dict[str, Any]] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        with timed_op(conn, source, "fetch_by_keywords") as op:
            try:
                resp = await _request_with_retry(client, "GET", url, policy)
                data = resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                logger.warning("OpenAlex keyword search failed: %s", exc)
                op["detail"] = f"keyword search failed: {exc}"
                return []

            for work in data.get("results", []):
                work_doi = work.get("doi", "") or ""
                if work_doi.startswith("https://doi.org/"):
                    work_doi = work_doi[len("https://doi.org/"):]

                if not work_doi:
                    continue  # skip papers without DOI

                oa_id_url = work.get("id", "")
                openalex_id = (
                    oa_id_url.rsplit("/", 1)[-1]
                    if "/" in oa_id_url
                    else oa_id_url
                )

                abstract = _reconstruct_abstract(
                    work.get("abstract_inverted_index")
                )

                concepts = [
                    c.get("display_name", "")
                    for c in work.get("concepts", [])
                    if c.get("display_name")
                ]

                # Authors
                authors: list[str] = []
                for authorship in work.get("authorships", []):
                    author_info = authorship.get("author", {})
                    name = author_info.get("display_name", "")
                    if name:
                        authors.append(name)

                year = work.get("publication_year")

                venue: str | None = None
                primary_loc = work.get("primary_location") or {}
                source_info = primary_loc.get("source") or {}
                venue = source_info.get("display_name")

                title = work.get("title", "")

                papers.append({
                    "doi": work_doi,
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "venue": venue,
                    "abstract": abstract,
                    "openalex_id": openalex_id,
                    "concepts": concepts,
                    "cited_by_count": work.get("cited_by_count", 0),
                })

            op["detail"] = f"keyword search: {len(papers)} papers"

    return papers


# ---------------------------------------------------------------------------
# 4. Semantic Scholar
# ---------------------------------------------------------------------------

_S2_FIELDS = (
    "paperId,externalIds,title,abstract,"
    "embedding.vector,tldr,citationCount,venue,year"
)


async def fetch_s2_by_ids(
    conn: sqlite3.Connection,
    paper_ids: list[str],
    id_type: str = "DOI",
    *,
    retry_policy: RetryPolicy | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch paper data from Semantic Scholar by IDs.

    Args:
        conn: SQLite connection for observability logging.
        paper_ids: List of paper identifiers.
        id_type: Identifier type prefix (e.g. "DOI", "ArXiv", "CorpusId").
        retry_policy: Override default retry settings.

    Returns:
        Dict keyed by input ID with S2 data: s2_id, title, abstract,
        embedding, tldr, citation_count, venue, year, external_ids.
    """
    policy = retry_policy or RetryPolicy()
    source = "s2"
    circuit = get_circuit(source, cooldown_sec=policy.circuit_breaker_cooldown)

    if circuit.is_open:
        logger.warning("Circuit open for %s — skipping", source)
        return {}

    if not paper_ids:
        return {}

    results: dict[str, dict[str, Any]] = {}
    batch_size = 500
    url = f"https://api.semanticscholar.org/graph/v1/paper/batch?fields={_S2_FIELDS}"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for i in range(0, len(paper_ids), batch_size):
            batch = paper_ids[i : i + batch_size]
            # Prefix IDs with type for the batch endpoint
            prefixed = [f"{id_type}:{pid}" for pid in batch]

            with timed_op(conn, source, "fetch_by_ids") as op:
                resp = await _request_with_retry(
                    client, "POST", url, policy, json={"ids": prefixed},
                )
                data = resp.json()

                for original_id, paper in zip(batch, data):
                    if paper is None:
                        # S2 returns null for unresolved IDs
                        continue

                    entry: dict[str, Any] = {
                        "s2_id": paper.get("paperId"),
                        "title": paper.get("title", ""),
                        "abstract": paper.get("abstract"),
                        "embedding": None,
                        "tldr": None,
                        "citation_count": paper.get("citationCount", 0),
                        "venue": paper.get("venue", ""),
                        "year": paper.get("year"),
                        "external_ids": paper.get("externalIds", {}),
                    }

                    # Embedding vector
                    emb_data = paper.get("embedding")
                    if emb_data and isinstance(emb_data, dict):
                        entry["embedding"] = emb_data.get("vector")

                    # TLDR
                    tldr_data = paper.get("tldr")
                    if tldr_data and isinstance(tldr_data, dict):
                        entry["tldr"] = tldr_data.get("text")

                    results[original_id] = entry

                op["detail"] = f"batch {i // batch_size + 1}: {len(batch)} ids"

    return results


# ---------------------------------------------------------------------------
# 5. Unpaywall
# ---------------------------------------------------------------------------

async def fetch_unpaywall(
    conn: sqlite3.Connection,
    doi: str,
    email: str | None = None,
    *,
    retry_policy: RetryPolicy | None = None,
) -> str | None:
    """Look up the best open-access PDF URL for a DOI via Unpaywall.

    Args:
        conn: SQLite connection for observability logging.
        doi: The DOI to look up.
        email: Contact email (required by Unpaywall API TOS).
            Falls back to profile preferences or 'litbot@example.com'.
        retry_policy: Override default retry settings.

    Returns:
        URL string of the best OA PDF, or None if not available.
    """
    if email is None:
        from .config import load_profile
        email = load_profile().preferences.unpaywall_email

    policy = retry_policy or RetryPolicy()
    source = "unpaywall"
    circuit = get_circuit(source, cooldown_sec=policy.circuit_breaker_cooldown)

    if circuit.is_open:
        logger.warning("Circuit open for %s — skipping", source)
        return None

    url = f"https://api.unpaywall.org/v2/{doi}?email={email}"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        with timed_op(conn, source, "fetch_pdf_url") as op:
            try:
                resp = await _request_with_retry(client, "GET", url, policy)
                data = resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                logger.info("Unpaywall lookup failed for %s: %s", doi, exc)
                op["detail"] = f"no result for {doi}"
                return None

            best_oa = data.get("best_oa_location") or {}
            pdf_url = best_oa.get("url_for_pdf") or best_oa.get("url")
            op["detail"] = f"doi={doi} pdf={'yes' if pdf_url else 'no'}"
            return pdf_url


# ---------------------------------------------------------------------------
# Enrichment helper
# ---------------------------------------------------------------------------

async def enrich_papers(
    conn: sqlite3.Connection,
    papers: list[dict[str, Any]],
    *,
    retry_policy: RetryPolicy | None = None,
) -> list[dict[str, Any]]:
    """Enrich a list of paper dicts with OpenAlex and Semantic Scholar data.

    Takes papers from fetch_crossref_new or fetch_arxiv_new and batches their
    DOIs to the enrichment APIs. Merges results back into each paper dict.

    Args:
        conn: SQLite connection for observability logging.
        papers: List of paper dicts (must have 'doi' and/or 'arxiv_id' keys).
        retry_policy: Override default retry settings.

    Returns:
        The same list with additional keys merged from OpenAlex and S2.
    """
    if not papers:
        return papers

    # Collect DOIs for batch lookup
    doi_papers: dict[str, list[int]] = {}  # doi -> list of indices
    for idx, p in enumerate(papers):
        doi = p.get("doi")
        if doi:
            doi_papers.setdefault(doi, []).append(idx)

    all_dois = list(doi_papers.keys())

    # Fetch from both sources concurrently
    oa_data: dict[str, dict[str, Any]] = {}
    s2_data: dict[str, dict[str, Any]] = {}

    if all_dois:
        oa_result, s2_result = await asyncio.gather(
            fetch_openalex_by_dois(conn, all_dois, retry_policy=retry_policy),
            fetch_s2_by_ids(conn, all_dois, id_type="DOI", retry_policy=retry_policy),
            return_exceptions=True,
        )
        if isinstance(oa_result, dict):
            oa_data = oa_result
        elif isinstance(oa_result, BaseException):
            logger.error("OpenAlex enrichment failed: %s", oa_result)
            log_op(conn, "openalex", "enrich", "error", detail=str(oa_result))

        if isinstance(s2_result, dict):
            s2_data = s2_result
        elif isinstance(s2_result, BaseException):
            logger.error("S2 enrichment failed: %s", s2_result)
            log_op(conn, "s2", "enrich", "error", detail=str(s2_result))

    # Merge enrichment data into papers.
    # Use _merge_field() instead of setdefault() to handle None values correctly:
    # setdefault("abstract", None) sets the key to None, then a later source with
    # a real value can't overwrite it. This caused abstract fallback to silently fail.
    def _merge_field(target: dict, key: str, value: Any) -> None:
        """Set field only if current value is missing or None."""
        if value is not None and not target.get(key):
            target[key] = value

    for doi, indices in doi_papers.items():
        oa = oa_data.get(doi, {})
        s2 = s2_data.get(doi, {})

        for idx in indices:
            paper = papers[idx]

            # OpenAlex fields
            if oa:
                _merge_field(paper, "openalex_id", oa.get("openalex_id"))
                _merge_field(paper, "abstract", oa.get("abstract"))
                _merge_field(paper, "concepts", oa.get("concepts"))
                _merge_field(paper, "cited_by_count", oa.get("cited_by_count"))
                if oa.get("venue") and not paper.get("venue"):
                    paper["venue"] = oa["venue"]
                if oa.get("arxiv_id") and not paper.get("arxiv_id"):
                    paper["arxiv_id"] = oa["arxiv_id"]

            # Semantic Scholar fields
            if s2:
                _merge_field(paper, "s2_id", s2.get("s2_id"))
                _merge_field(paper, "abstract", s2.get("abstract"))
                _merge_field(paper, "embedding", s2.get("embedding"))
                _merge_field(paper, "tldr", s2.get("tldr"))
                _merge_field(paper, "citation_count", s2.get("citation_count"))
                _merge_field(paper, "external_ids", s2.get("external_ids"))
                if s2.get("venue") and not paper.get("venue"):
                    paper["venue"] = s2["venue"]

    return papers
