"""
Semantic Scholar paper search module.

Uses S2's relevance search endpoint which provides semantically-ranked results,
meaning natural language queries work well (e.g. "cancer drug response prediction
using genomic data" returns papers ranked by meaning, not just keyword overlap).

Falls back to the bulk search endpoint if the relevance endpoint is rate-limited (429).

DEPRECATED (, 2026-05-19): the Find pipeline's primary search
backend is now ``find.pubmed_search`` (NCBI E-utilities). This module is
retained for two reasons:

  1. Legacy projects created before the deploy timestamp carry
     ``projects.search_backend = 's2'`` and still resume against this module.
  2. ``SEARCH_BACKEND=s2`` is the documented emergency-rollback env override
     in case PubMed is unavailable (NCBI IP block, ESearch outage, etc).

``rank_papers_by_data_availability`` and ``scan_data_availability`` are still
used by both backends — the PubMed module imports them. Do not remove them
without first migrating the helpers into a shared utility module.
"""

import logging
import os
import re
from typing import Optional, List, Dict

import requests

from utils.pipeline_log import pipeline_log
from utils.llm_config import build_openrouter_sync_client, get_openrouter_model
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

RELEVANCE_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
BULK_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
S2_FIELDS = "title,url,abstract,publicationDate,openAccessPdf,externalIds,citationCount,fieldsOfStudy,authors"
PAGE_LIMIT = 100  # Max per-page limit allowed by the relevance API
MAX_TOTAL = 1000  # Hard cap: relevance API returns at most 1000 results

POSITIVE_DATA_PATTERNS = [
    ("geo_accession", re.compile(r"\bGSE\d{3,}\b", re.IGNORECASE), 35),
    ("sra_bioproject", re.compile(r"\bPRJ(?:NA|EB|DB)\d+\b", re.IGNORECASE), 30),
    ("sra_series", re.compile(r"\b[SED]RP\d+\b", re.IGNORECASE), 25),
    ("figshare", re.compile(r"\bfigshare\b", re.IGNORECASE), 25),
    ("zenodo", re.compile(r"\bzenodo\b", re.IGNORECASE), 25),
    ("osf", re.compile(r"\bosf\.io\b|\bopen science framework\b", re.IGNORECASE), 20),
    ("github", re.compile(r"\bgithub\.com\b|\bgithub\b", re.IGNORECASE), 20),
    ("depmap", re.compile(r"\bdepmap\b", re.IGNORECASE), 20),
    ("gdsc", re.compile(r"\bGDSC\b|genomics of drug sensitivity", re.IGNORECASE), 20),
    ("ccle", re.compile(r"\bCCLE\b|cancer cell line encyclopedia", re.IGNORECASE), 20),
    ("ctrp", re.compile(r"\bCTRP\b|cancer therapeutics response portal", re.IGNORECASE), 20),
    ("tcga_gdc", re.compile(r"\bTCGA\b|\bGDC\b|genomic data commons", re.IGNORECASE), 18),
    ("drug_response_quant", re.compile(
        r"\bIC50\b|\bIC[\s-]?50\b|\bEC50\b|\bGI50\b|\bGR50\b|\bAUC\b|"
        r"dose[\s-]?response|drug[\s-]?sensitivity|drug[\s-]?screen(?:ing)?|"
        r"drug[\s-]?response|\bDSS\b score|chemosensitivity",
        re.IGNORECASE,
    ), 22),
    ("supplementary_data", re.compile(
        r"supplementary (?:data|table|file|material)|source data|data availability",
        re.IGNORECASE,
    ), 12),
]

NEGATIVE_DATA_PATTERNS = [
    ("available_upon_request", re.compile(r"available (?:from|upon) request", re.IGNORECASE), -25),
    ("controlled_access", re.compile(r"controlled[- ]access|restricted access", re.IGNORECASE), -25),
    ("dbgap", re.compile(r"\bdbGaP\b", re.IGNORECASE), -20),
    ("ega", re.compile(r"\bEGA\b|european genome-phenome archive", re.IGNORECASE), -20),
    ("approval_required", re.compile(
        r"data access committee|institutional approval|requires approval|application required",
        re.IGNORECASE,
    ), -18),
]


def create_s2_session(api_key: str = None) -> Session:
    """Create a requests Session with the S2-mandated retry/backoff configuration."""
    session = Session()
    # Don't auto-retry 429 — we handle it manually to fall back to bulk search
    session.mount('https://', HTTPAdapter(max_retries=Retry(
        total=6,
        backoff_factor=2.0,
        backoff_jitter=0.5,
        respect_retry_after_header=True,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods={'GET', 'POST'},
    )))
    session.headers.update({
        'User-Agent': 'ai4med-autogen/1.0 (research pipeline)',
    })
    if api_key:
        session.headers['x-api-key'] = api_key
    return session


def _parse_year(date_str: str) -> Optional[str]:
    """Extract 4-digit year from formats like '1950/01/01', '1950-01-01', or '1950'."""
    if not date_str:
        return None
    m = re.search(r'(\d{4})', date_str)
    return m.group(1) if m else None


def _build_filter_params(
    query: str, year_range: str, fields_of_study: str,
    open_access_only: bool, min_citation_count: int,
) -> Dict:
    """Build the shared filter params used by both relevance and bulk endpoints."""
    params: Dict = {
        'query': query,
        'fields': S2_FIELDS,
    }
    if year_range:
        params['year'] = year_range
    if fields_of_study:
        params['fieldsOfStudy'] = fields_of_study
    if open_access_only:
        params['openAccessPdf'] = ''
    if min_citation_count is not None:
        params['minCitationCount'] = min_citation_count
    return params


def _normalize_papers(raw_papers: List[Dict]) -> List[Dict]:
    """Convert S2 API response dicts to the pipeline's expected paper dict format."""
    normalized = []
    for p in raw_papers:
        authors = []
        for a in (p.get('authors') or []):
            name = a.get('name', '').strip()
            if name:
                parts = name.rsplit(' ', 1)
                if len(parts) == 2:
                    authors.append(f"{parts[1]} {parts[0]}")
                else:
                    authors.append(name)

        external_ids = p.get('externalIds') or {}
        doi = external_ids.get('DOI')
        pub_date = p.get('publicationDate') or ''

        paper = {
            'paper_id': p.get('paperId', ''),
            'title': p.get('title', 'Unknown Title'),
            'authors': authors,
            'publication_date': pub_date,
            'doi': doi,
            'abstract': p.get('abstract'),
            'open_access_pdf': p.get('openAccessPdf'),
            'external_ids': external_ids,
            'citation_count': p.get('citationCount', 0),
            'fields_of_study': p.get('fieldsOfStudy') or [],
            'url': p.get('url', ''),
        }
        availability = scan_data_availability(paper)
        paper.update(availability)
        normalized.append(paper)
    return normalized


def _paper_availability_text(paper: Dict) -> str:
    """Build searchable text from metadata fields likely to carry data availability clues."""
    external_ids = paper.get("external_ids") or paper.get("externalIds") or {}
    open_access_pdf = paper.get("open_access_pdf") or paper.get("openAccessPdf") or {}
    parts = [
        paper.get("title") or "",
        paper.get("abstract") or "",
        paper.get("url") or "",
        paper.get("doi") or "",
    ]
    if isinstance(open_access_pdf, dict):
        parts.append(open_access_pdf.get("url") or "")
    if isinstance(external_ids, dict):
        parts.extend(str(value) for value in external_ids.values() if value)
    return "\n".join(parts)


def scan_data_availability(paper: Dict) -> Dict:
    """Score paper metadata for likely downstream dataset availability."""
    text = _paper_availability_text(paper)
    score = 0
    signals = []
    for signal, pattern, weight in POSITIVE_DATA_PATTERNS:
        if pattern.search(text):
            score += weight
            signals.append(signal)
    for signal, pattern, weight in NEGATIVE_DATA_PATTERNS:
        if pattern.search(text):
            score += weight
            signals.append(signal)

    return {
        "data_availability_score": score,
        "data_availability_signals": signals,
    }


def rank_papers_by_data_availability(papers: List[Dict]) -> List[Dict]:
    """Rank papers by dataset availability signals, then citations, preserving ties."""
    enriched = []
    for index, paper in enumerate(papers):
        ranked_paper = dict(paper)
        if (
            "data_availability_score" not in ranked_paper
            or "data_availability_signals" not in ranked_paper
        ):
            ranked_paper.update(scan_data_availability(ranked_paper))
        enriched.append((index, ranked_paper))

    return [
        paper
        for index, paper in sorted(
            enriched,
            key=lambda item: (
                -int(item[1].get("data_availability_score") or 0),
                -int(item[1].get("citation_count") or 0),
                item[0],
            ),
        )
    ]


def _search_relevance(
    session: Session, params: Dict, max_results: int, project_id: Optional[str] = None
) -> List[Dict]:
    """Fetch papers using the relevance (semantic) endpoint with offset/limit pagination."""
    params = {**params, 'limit': min(PAGE_LIMIT, max_results), 'offset': 0}
    all_papers = []

    while len(all_papers) < max_results:
        response = session.get(RELEVANCE_SEARCH_URL, params=params, timeout=30)
        if response.status_code == 429:
            raise _RateLimitError()
        response.raise_for_status()
        data = response.json()

        if params["offset"] == 0:
            total = data.get("total", 0)
            pipeline_log(
                f"S2 strategy=relevance_search endpoint first | total_hint={total} "
                f"year={params.get('year')!r} open_access_only={bool(params.get('openAccessPdf') is not None)}",
                stage="find",
                component="semantic_scholar",
                project_id=project_id,
            )

        batch = data.get("data", [])
        if not batch:
            break
        all_papers.extend(batch)
        pipeline_log(
            f"S2 relevance_search pagination: accumulated {len(all_papers)} raw row(s)",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
        )

        next_offset = data.get('next', None)
        if next_offset is None or len(all_papers) >= max_results:
            break
        params['offset'] = next_offset

    return all_papers


def _transform_query_for_bulk(query: str, project_id: Optional[str] = None) -> str:
    """Transform a natural language query into keywords for the bulk (keyword) endpoint.

    Uses GPT-4.1-mini to extract key scientific search terms. Falls back to simple
    stop-word removal if the OpenAI call fails.
    """
    try:
        client = build_openrouter_sync_client()
        response = client.chat.completions.create(
            model=get_openrouter_model("OPENROUTER_MODEL"),
            messages=[
                {"role": "system", "content": (
                    "Extract the key scientific search terms from this query. "
                    "Return only the important keywords/phrases separated by spaces, "
                    "removing filler words. Keep domain-specific terms intact. "
                    "Return nothing else."
                )},
                {"role": "user", "content": query},
            ],
            max_tokens=100,
            temperature=0,
        )
        keywords = response.choices[0].message.content.strip()
        pipeline_log(
            f"S2 bulk prep: query_transform=openrouter_keywords {query!r} → {keywords!r}",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
        )
        return keywords
    except Exception as e:
        pipeline_log(
            f"S2 bulk prep: query_transform=openrouter_failed ({e}) → fallback=stop_words",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
            level=logging.WARNING,
        )
        stop_words = {
            'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
            'on', 'with', 'at', 'by', 'from', 'as', 'into', 'about', 'between',
            'through', 'after', 'before', 'above', 'below', 'and', 'but', 'or',
            'not', 'no', 'nor', 'so', 'yet', 'both', 'either', 'neither', 'each',
            'every', 'all', 'any', 'few', 'more', 'most', 'some', 'such', 'than',
            'too', 'very', 'just', 'also', 'how', 'what', 'which', 'who', 'whom',
            'this', 'that', 'these', 'those', 'it', 'its', 'i', 'we', 'they',
            'me', 'us', 'them', 'my', 'our', 'their', 'your', 'he', 'she', 'him',
            'her', 'his', 'if', 'then', 'else', 'when', 'where', 'why', 'while',
            'useful', 'using', 'used', 'use', 'paper', 'papers', 'study', 'studies',
            'information', 'data', 'based', 'related',
        }
        words = query.lower().split()
        keywords = ' '.join(w for w in words if w not in stop_words)
        pipeline_log(
            f"S2 bulk prep: query_transform=stop_words {query!r} → {keywords!r}",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
        )
        return keywords or query


def _search_bulk(
    session: Session, params: Dict, max_results: int, project_id: Optional[str] = None
) -> List[Dict]:
    """Fetch papers using the bulk (keyword) endpoint with token-based pagination."""
    params = {**params}  # don't mutate caller's dict
    params["query"] = _transform_query_for_bulk(params["query"], project_id=project_id)
    all_papers = []

    response = session.get(BULK_SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    total = data.get("total", 0)
    pipeline_log(
        f"S2 strategy=bulk_search (keyword) after fallback | query={params['query']!r} total_hint={total}",
        stage="find",
        component="semantic_scholar",
        project_id=project_id,
    )

    while len(all_papers) < max_results:
        batch = data.get('data', [])
        if not batch:
            break
        all_papers.extend(batch)
        pipeline_log(
            f"S2 bulk_search pagination: accumulated {len(all_papers)} raw row(s)",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
        )

        token = data.get('token')
        if not token or len(all_papers) >= max_results:
            break

        params['token'] = token
        response = session.get(BULK_SEARCH_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

    return all_papers


class _RateLimitError(Exception):
    pass


def search_papers(
    query: str,
    max_results: int = 10,
    year_start: str = None,
    year_end: str = None,
    fields_of_study: str = None,
    open_access_only: bool = False,
    min_citation_count: int = None,
    api_key: str = None,
    project_id: Optional[str] = None,
) -> List[Dict]:
    """
    Search Semantic Scholar, preferring the relevance (semantic) endpoint.

    Falls back to the bulk (keyword) endpoint if the relevance endpoint returns 429.

    Args:
        query: Natural language search query.
        max_results: Maximum number of papers to return.
        year_start: Start year (or "YYYY/MM/DD" — year is extracted).
        year_end:   End year (or "YYYY/MM/DD" — year is extracted).
        fields_of_study: Comma-separated S2 field names (e.g. "Medicine,Biology").
        open_access_only: If True, only return papers with open access PDFs.
        min_citation_count: Minimum citation threshold (filters out low-impact papers).
        api_key: Semantic Scholar API key (env var S2_API_KEY). Optional but recommended.
        project_id: Optional web project UUID for structured pipeline logs.

    Returns:
        List of paper dicts, each containing: paper_id, title, authors, publication_date,
        doi, abstract, open_access_pdf, external_ids, citation_count, fields_of_study, url.
    """
    if api_key is None:
        api_key = os.getenv('S2_API_KEY')

    max_results = min(max_results, MAX_TOTAL)

    # Small random start-jitter so concurrent pipeline runs don't hammer S2 in lockstep.
    import random, time as _time
    _time.sleep(random.uniform(0, 1.5))

    session = create_s2_session(api_key)

    start_year = _parse_year(year_start)
    end_year = _parse_year(year_end)
    year_range = None
    if start_year and end_year:
        year_range = f"{start_year}-{end_year}"
    elif start_year:
        year_range = f"{start_year}-"
    elif end_year:
        year_range = f"-{end_year}"

    params = _build_filter_params(query, year_range, fields_of_study, open_access_only, min_citation_count)

    try:
        all_papers = _search_relevance(session, params, max_results, project_id=project_id)

    except _RateLimitError:
        pipeline_log(
            "S2 fallback: relevance_search returned HTTP 429 → switching to bulk_search (keyword) endpoint",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
            level=logging.WARNING,
        )
        try:
            all_papers = _search_bulk(session, params, max_results, project_id=project_id)
        except requests.exceptions.RequestException as e:
            pipeline_log(
                f"S2 bulk_search failed after 429 fallback: {e}",
                stage="find",
                component="semantic_scholar",
                project_id=project_id,
                level=logging.ERROR,
            )
            return []

    except requests.exceptions.RequestException as e:
        pipeline_log(
            f"S2 relevance_search request error (no fallback): {e}",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
            level=logging.ERROR,
        )
        return []
    except Exception as e:
        pipeline_log(
            f"S2 unexpected error: {e}",
            stage="find",
            component="semantic_scholar",
            project_id=project_id,
            level=logging.ERROR,
        )
        return []
    finally:
        session.close()

    result = rank_papers_by_data_availability(_normalize_papers(all_papers[:max_results]))
    pipeline_log(
        f"S2 search done: returning {len(result)} normalized paper(s) (cap max_results={max_results})",
        stage="find",
        component="semantic_scholar",
        project_id=project_id,
    )
    return result


if __name__ == "__main__":
    papers = search_papers(
        query='cancer drug response prediction using genomic data',
        max_results=5,
        year_start="2020",
        open_access_only=True,
        min_citation_count=3,
    )
    print(f"\nFound {len(papers)} papers:")
    for p in papers:
        oa = "OA" if p['open_access_pdf'] else "no OA"
        print(f"  [{p['citation_count']}c] [{oa}] {p['title']} ({p['publication_date'][:4] if p['publication_date'] else 'n/a'}) - DOI: {p['doi']}")
