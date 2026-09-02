"""
PubMed E-utilities search module (replacement for Semantic Scholar).

Mirrors the public surface of ``find.semantic_scholar_search.search_papers`` so
``FindTeam.run()`` can switch backends at a single call site. Internally uses
NCBI E-utilities:

  * ESearch — `term=` → PMID list (supports PubMed advanced syntax natively:
    ``[Title]``, ``[MeSH Terms]``, ``[Author]``, ``AND/OR/NOT``, ``"phrase"``,
    ``YYYY/MM/DD:YYYY/MM/DD[dp]``)
  * EFetch — PMIDs → XML metadata (title, authors, abstract, journal, DOI, PMCID)
  * ELink — batch PubMed→PMC reverse lookup, enriches ``external_ids.PMCID``
    so the downstream OA waterfall (Europe PMC step) hits more often.

NCBI E-utilities policy requires every request to send ``tool`` and ``email``;
neither is optional. ``api_key`` raises the rate limit from 3 to 10 req/s but
the path works without it. EFetch / ELink calls chunk PMIDs at ≤200 per
request to stay under NCBI's nginx ~8KB GET URL ceiling.

"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from find.semantic_scholar_search import scan_data_availability
from utils.pipeline_log import pipeline_log

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"

TOOL_NAME = "ai4med-autogen"

# NCBI documents 10000 PMIDs per EFetch (POST mode) but GET URLs are capped by
# their nginx at ~8KB. Each PMID is ~8 digits + separator, so 200 PMIDs leaves
# headroom for the rest of the query string.
#
# Reduced from 200 → 100 because ELink JSON responses for 200 PMIDs are large
# enough to occasionally hit "Response ended prematurely" (mid-response TCP
# disconnects on NCBI's side). Halving the chunk size halves the per-response
# payload, drops the truncation rate, and halves the blast radius of a single
# failed chunk (100 papers lose PMCID enrichment instead of 200). The extra
# NCBI calls are well inside the 10 req/s rate limit with an API key.
NCBI_BATCH_SIZE = 100

REQUEST_TIMEOUT = 30

# MeSH expansion regex guards.
# Skip expansion when the user has already hand-written PubMed advanced syntax —
# expanding would break their query semantics.
# Allow spaces inside field tags so we catch `[MeSH Terms]`, `[Affiliation]`, etc.
_FIELD_TAG_RE = re.compile(r"\[\s*[A-Za-z][A-Za-z\s]*\]")
# UPPERCASE booleans only (lowercase "or" is a search term in PubMed, not an operator).
# Also catches trailing operators (e.g. ending with "AND").
_BOOLEAN_RE = re.compile(r"(?:^|\s)(AND|OR|NOT)(?=\s|$)")

# Cap on how many MeSH entry terms get OR-ed into the expanded query. Bigger
# values cause URL-length blowup against NCBI's nginx 8KB ceiling.
MESH_TOP_N = 10


def _should_skip_mesh(query: str) -> bool:
    """bypass MeSH expansion when the user has used PubMed
    advanced syntax — field tags ``[Title]``/`[MeSH Terms]`/etc. or explicit
    uppercase boolean operators (``AND``/``OR``/``NOT``).
    """
    return bool(_FIELD_TAG_RE.search(query) or _BOOLEAN_RE.search(query))


def _expand_with_mesh(
    session: Session,
    query: str,
    api_key: Optional[str],
    email: str,
    project_id: Optional[str],
    top_n: int = MESH_TOP_N,
) -> tuple:
    """Resolve `query` against NCBI MeSH and return (expanded_query, terms_added, status).

    status is one of: "expanded" | "skipped_advanced_syntax" | "failed" | "no_results"

    Strategy:
      1. ESearch `db=mesh` with the user query → MeSH UID list (first match wins).
      2. ESummary on the top UID → fetch the MeSH entry's term scope notes and
         synonyms via the MeSH record. PubMed exposes ``ds_meshterms`` in
         ESummary.
      3. Build expanded query: ``(original) OR ("term1"[MeSH Terms]) OR ...``.

    Returns the original query unchanged if no expansion happens. Never raises —
    failures degrade gracefully (status="failed").
    """
    if _should_skip_mesh(query):
        return query, [], "skipped_advanced_syntax"

    try:
        # Step 1: MeSH ESearch
        params = _common_params(api_key, email)
        params.update({"db": "mesh", "term": query, "retmode": "json", "retmax": "1"})
        resp = session.get(ESEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        uids = data.get("esearchresult", {}).get("idlist", []) or []
        if not uids:
            pipeline_log(
                f"mesh_expand: no MeSH UID for query={query!r}",
                stage="find", component="pubmed_search", project_id=project_id,
            )
            return query, [], "no_results"

        # Step 2: ESummary for MeSH entry terms
        sum_params = _common_params(api_key, email)
        sum_params.update({"db": "mesh", "id": uids[0], "retmode": "json"})
        sum_resp = session.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params=sum_params, timeout=REQUEST_TIMEOUT,
        )
        sum_resp.raise_for_status()
        sum_data = sum_resp.json()
        result = (sum_data.get("result") or {}).get(uids[0]) or {}
        # MeSH ESummary returns "ds_meshterms" array of synonym strings.
        candidates = result.get("ds_meshterms") or []
        if not candidates:
            # Fallback: use the canonical term name only.
            candidates = [result.get("ds_meshui_name") or result.get("name") or ""]
        terms = [t for t in candidates if t][:top_n]
        if not terms:
            return query, [], "no_results"

        # Step 3: build OR query with explicit field tag — ensures these are
        # treated as MeSH terms, not free text. Quote each term defensively.
        or_parts = [f'("{t}"[MeSH Terms])' for t in terms]
        expanded = f"({query}) OR " + " OR ".join(or_parts)
        return expanded, terms, "expanded"
    except Exception as e:
        pipeline_log(
            f"mesh_expand: failed for query={query!r}: {e}; falling back to original",
            stage="find", component="pubmed_search", project_id=project_id,
            level=logging.WARNING,
        )
        return query, [], "failed"


def _resolve_email(email: Optional[str], project_id: Optional[str]) -> str:
    """Pick the email to send with every NCBI request.

    NCBI ToS requires a real contact email on every request. Falls back to the
    same ``UNPAYWALL_EMAIL`` already configured for the OA downloader if
    ``NCBI_EMAIL`` is missing (logs CRITICAL so ops know to fix it).
    """
    if email:
        return email
    fallback = os.getenv("UNPAYWALL_EMAIL") or "your-email@example.com"  # set UNPAYWALL_EMAIL
    pipeline_log(
        f"NCBI_EMAIL not set, falling back to {fallback!r}; set NCBI_EMAIL in .env",
        stage="find",
        component="pubmed_search",
        project_id=project_id,
        level=logging.CRITICAL,
    )
    return fallback


def _create_pubmed_session() -> Session:
    """requests Session with NCBI-friendly retry/backoff."""
    session = Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3,
        backoff_factor=2.0,
        backoff_jitter=0.5,
        respect_retry_after_header=True,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods={"GET"},
    )))
    session.headers.update({
        "User-Agent": f"{TOOL_NAME}/1.0 (research pipeline)",
    })
    return session


def _common_params(api_key: Optional[str], email: str) -> Dict[str, str]:
    params = {"tool": TOOL_NAME, "email": email}
    if api_key:
        params["api_key"] = api_key
    return params


def _chunked(seq: List[str], n: int) -> List[List[str]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def _esearch(
    session: Session,
    query: str,
    max_results: int,
    year_start: Optional[str],
    year_end: Optional[str],
    api_key: Optional[str],
    email: str,
    project_id: Optional[str],
    retstart: int = 0,
) -> List[str]:
    """Run an ESearch and return the matching PMIDs.

    Date range is passed via ``mindate``/``maxdate`` (NCBI's native parameters)
    rather than appended to the query string, so the user can still write
    ``[dp]`` syntax in the search box if they want — the frontend warns about
    the collision but the backend doesn't enforce.

    ``retstart`` parameter enables PubMed-native pagination
    so the find-stage fill-to-target loop can request successive batches.
    """
    params = _common_params(api_key, email)
    params.update({
        "db": "pubmed",
        "term": query,
        "retmax": str(max_results),
        # ESearch defaults to most-recent, NOT relevance.
        # We need relevance ranking to match the S2 path's behaviour for
        # medical researchers (otherwise they get newest-but-irrelevant
        # papers for high-recall queries).
        "sort": "relevance",
        "datetype": "pdat",
        "retmode": "json",
    })
    if retstart:
        params["retstart"] = str(retstart)
    if year_start:
        params["mindate"] = year_start
    if year_end:
        params["maxdate"] = year_end

    pipeline_log(
        f"pubmed esearch: term={query!r} mindate={year_start} maxdate={year_end} "
        f"sort=relevance retmax={max_results}",
        stage="find",
        component="pubmed_search",
        project_id=project_id,
    )

    resp = session.get(ESEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    pmids: List[str] = data.get("esearchresult", {}).get("idlist", []) or []

    if not pmids:
        pipeline_log(
            f"pubmed esearch: [esearch=0] no results for query={query!r}",
            stage="find",
            component="pubmed_search",
            project_id=project_id,
            level=logging.WARNING,
        )
    else:
        pipeline_log(
            f"pubmed esearch: returned {len(pmids)} PMIDs (first={pmids[0]})",
            stage="find",
            component="pubmed_search",
            project_id=project_id,
        )
    return pmids


def _efetch(
    session: Session,
    pmids: List[str],
    api_key: Optional[str],
    email: str,
    project_id: Optional[str],
) -> Dict[str, Dict]:
    """Fetch full metadata for a list of PMIDs and return a {pmid: paper_dict} map.

    Chunked at NCBI_BATCH_SIZE per request to stay under URL length limits.
    """
    out: Dict[str, Dict] = {}
    if not pmids:
        return out

    chunks = _chunked(pmids, NCBI_BATCH_SIZE)
    for idx, chunk in enumerate(chunks, start=1):
        params = _common_params(api_key, email)
        params.update({
            "db": "pubmed",
            "id": ",".join(chunk),
            "rettype": "abstract",
            "retmode": "xml",
        })
        pipeline_log(
            f"pubmed efetch chunk={idx}/{len(chunks)} size={len(chunk)}",
            stage="find",
            component="pubmed_search",
            project_id=project_id,
        )
        try:
            resp = session.get(EFETCH_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as e:
            pipeline_log(
                f"pubmed efetch chunk {idx} failed: {e}",
                stage="find",
                component="pubmed_search",
                project_id=project_id,
                level=logging.ERROR,
            )
            continue

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            pipeline_log(
                f"pubmed efetch chunk {idx} XML parse failed: {e}",
                stage="find",
                component="pubmed_search",
                project_id=project_id,
                level=logging.ERROR,
            )
            continue

        for article in root.findall(".//PubmedArticle"):
            try:
                paper = _parse_pubmed_article(article)
                if paper:
                    out[paper["paper_id"]] = paper
            except Exception as e:
                # Best-effort per-article — log and continue.
                pmid_node = article.find(".//MedlineCitation/PMID")
                pmid_txt = pmid_node.text if pmid_node is not None else "?"
                pipeline_log(
                    f"pubmed efetch article pmid={pmid_txt} parse error: {e}",
                    stage="find",
                    component="pubmed_search",
                    project_id=project_id,
                    level=logging.ERROR,
                )

    return out


def _elink_pmid_to_pmcid(
    session: Session,
    pmids: List[str],
    api_key: Optional[str],
    email: str,
    project_id: Optional[str],
) -> Dict[str, str]:
    """Batch PubMed→PMC reverse lookup. Returns {pmid: pmcid_with_PMC_prefix}.

    Uses multiple ``&id=`` parameters (NOT CSV) so the response's ``linksets``
    array carries per-input mappings — CSV form returns a single merged LinkSet
    that loses the PMID→PMCID correspondence.
    """
    out: Dict[str, str] = {}
    if not pmids:
        return out

    chunks = _chunked(pmids, NCBI_BATCH_SIZE)
    for chunk in chunks:
        # `requests` serialises a list under one key into repeated `&id=` params,
        # which is exactly what ELink wants for per-input LinkSets.
        params = _common_params(api_key, email)
        params.update({
            "dbfrom": "pubmed",
            "db": "pmc",
            "retmode": "json",
        })
        # requests preserves order; pass id list separately.
        get_params = list(params.items()) + [("id", pmid) for pmid in chunk]
        try:
            resp = session.get(ELINK_URL, params=get_params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            pipeline_log(
                f"pubmed elink chunk failed: {e}",
                stage="find",
                component="pubmed_search",
                project_id=project_id,
                level=logging.WARNING,
            )
            continue

        for linkset in data.get("linksets", []) or []:
            ids = linkset.get("ids") or []
            if not ids:
                continue
            in_pmid = str(ids[0])
            for dbset in linkset.get("linksetdbs", []) or []:
                links = dbset.get("links") or []
                if links:
                    pmc_id = str(links[0])
                    if not pmc_id.startswith("PMC"):
                        pmc_id = f"PMC{pmc_id}"
                    out[in_pmid] = pmc_id
                    break  # first PMC link is canonical

    pipeline_log(
        f"pubmed elink: enriched {len(out)}/{len(pmids)} PMIDs with PMCID",
        stage="find",
        component="pubmed_search",
        project_id=project_id,
    )
    return out


_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")


def _parse_pubmed_article(article: ET.Element) -> Optional[Dict]:
    """Convert one ``<PubmedArticle>`` element to the pipeline's paper dict.

    Returns None if the PMID itself is missing (unparsable article).
    """
    pmid_node = article.find(".//MedlineCitation/PMID")
    if pmid_node is None or not (pmid_node.text or "").strip():
        return None
    pmid = pmid_node.text.strip()

    title_node = article.find(".//MedlineCitation/Article/ArticleTitle")
    title = _xml_text(title_node) or "Unknown Title"

    # Authors: ``LastName FirstName`` — match the S2 normalisation order.
    authors: List[str] = []
    for au in article.findall(".//MedlineCitation/Article/AuthorList/Author"):
        last = (au.findtext("LastName") or "").strip()
        fore = (au.findtext("ForeName") or au.findtext("Initials") or "").strip()
        if last and fore:
            authors.append(f"{last} {fore}")
        elif last:
            authors.append(last)
        else:
            collective = (au.findtext("CollectiveName") or "").strip()
            if collective:
                authors.append(collective)

    # Abstract: PubMed allows multiple <AbstractText> sections (Background /
    # Methods / Results / Conclusions). Join with newlines.
    abstract_parts = [_xml_text(at) for at in article.findall(".//MedlineCitation/Article/Abstract/AbstractText")]
    abstract = "\n".join(p for p in abstract_parts if p) or None

    # Publication date — PubDate may be Year/Month/Day or MedlineDate ("2024 Jan-Mar").
    pub_date = _parse_pub_date(article)

    # Journal name. PubMed exposes it as MedlineCitation/Article/Journal/Title
    # (full journal name, e.g. "Nature Communications") and ISOAbbreviation
    # ("Nat Commun"). Prefer the full title; fall back to abbreviation.
    journal_node = article.find(".//MedlineCitation/Article/Journal/Title")
    journal = _xml_text(journal_node)
    if not journal:
        abbrev_node = article.find(".//MedlineCitation/Article/Journal/ISOAbbreviation")
        journal = _xml_text(abbrev_node)
    journal = journal or None

    # Journal / external IDs from PubmedData/ArticleIdList.
    doi: Optional[str] = None
    pmcid: Optional[str] = None
    for aid in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        id_type = (aid.get("IdType") or "").lower()
        value = (aid.text or "").strip()
        if not value:
            continue
        if id_type == "doi" and _DOI_PATTERN.match(value):
            doi = value
        elif id_type == "pmc":
            pmcid = value if value.startswith("PMC") else f"PMC{value}"

    external_ids: Dict[str, str] = {}
    if doi:
        external_ids["DOI"] = doi
    if pmcid:
        external_ids["PMCID"] = pmcid
    # store PMID under both the dict (downstream artifact
    # provenance uses paper_id) and external_ids for symmetry with S2.
    external_ids["PubMed"] = pmid

    paper: Dict = {
        "paper_id": pmid,
        "title": title,
        "authors": authors,
        "publication_date": pub_date,
        "doi": doi,
        "abstract": abstract,
        "journal": journal,
        # PubMed does not provide an OA PDF URL field. Downstream OA waterfall
        # skips step 1 (S2 direct link) and resolves PDFs via Unpaywall / ArXiv
        # / Europe PMC using DOI + PMCID.
        "open_access_pdf": None,
        "external_ids": external_ids,
        # PubMed has no citation count; leave at 0 so existing ranking code
        # treats it as the floor. Accepted regression per design Section 9.
        "citation_count": 0,
        "fields_of_study": [],
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }
    paper.update(scan_data_availability(paper))
    return paper


def _xml_text(node: Optional[ET.Element]) -> str:
    """Flatten an XML node's full text content (including children like <i> tags)."""
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _parse_pub_date(article: ET.Element) -> str:
    """Return a YYYY/MM/DD string (best-effort; falls back to YYYY)."""
    pd = article.find(".//MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if pd is None:
        return ""

    year = (pd.findtext("Year") or "").strip()
    month = (pd.findtext("Month") or "").strip()
    day = (pd.findtext("Day") or "").strip()

    if not year:
        # MedlineDate fallback like "2024 Jan-Mar".
        medline = (pd.findtext("MedlineDate") or "").strip()
        m = re.search(r"(\d{4})", medline)
        if not m:
            return ""
        year = m.group(1)
        # Try to also pull a month abbreviation from the same string.
        m2 = re.search(r"([A-Za-z]{3})", medline)
        if m2:
            month = m2.group(1)

    if month:
        month_lower = month[:3].lower()
        month = _MONTH_MAP.get(month_lower, month.zfill(2) if month.isdigit() else "01")
    else:
        month = "01"

    if day and day.isdigit():
        day = day.zfill(2)
    else:
        day = "01"

    return f"{year}/{month}/{day}"


def search_pubmed_papers(
    query: str,
    max_results: int = 100,
    year_start: Optional[str] = None,
    year_end: Optional[str] = None,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
    project_id: Optional[str] = None,
    retstart: int = 0,
    mesh_expansion: bool = False,
    mesh_status_callback: Optional[callable] = None,
) -> List[Dict]:
    """PubMed search entry point. Signature mirrors ``search_papers`` in S2.

    Returns a list of paper dicts in the pipeline's normalised format
    (matching ``find.semantic_scholar_search._normalize_papers``). On HTTP /
    XML failures returns an empty list — the caller handles "no results".

    Args:
        query: User input. PubMed advanced syntax (field tags, boolean,
            phrases) is passed through verbatim.
        max_results: ESearch ``retmax``. Service caller passes
            ``candidate_pool_size`` (max_papers × 3).
        year_start: ``YYYY/MM/DD`` start of publication date filter.
        year_end: ``YYYY/MM/DD`` end of publication date filter.
        api_key: Optional NCBI API key. Raises rate limit 3→10 req/s.
        email: NCBI contact email. Required by ToS — falls back to
            ``UNPAYWALL_EMAIL`` (with CRITICAL log) if not set.
        project_id: Forwarded to pipeline_log for structured tracing.
    """
    resolved_email = _resolve_email(email, project_id)

    with _create_pubmed_session() as session:
        # conditional MeSH expansion before ESearch.
        effective_query = query
        if mesh_expansion:
            effective_query, mesh_terms, mesh_status = _expand_with_mesh(
                session=session, query=query,
                api_key=api_key, email=resolved_email, project_id=project_id,
            )
            pipeline_log(
                f"mesh_expanded={mesh_status == 'expanded'} status={mesh_status} "
                f"terms_count={len(mesh_terms)} original={query!r}",
                stage="find", component="pubmed_search", project_id=project_id,
            )
            if mesh_status_callback is not None:
                try:
                    mesh_status_callback(effective_query, mesh_status, mesh_terms)
                except Exception:
                    pass
        pmids = _esearch(
            session=session,
            query=effective_query,
            max_results=max_results,
            year_start=year_start,
            year_end=year_end,
            api_key=api_key,
            email=resolved_email,
            project_id=project_id,
            retstart=retstart,
        )
        if not pmids:
            return []

        papers_by_pmid = _efetch(
            session=session,
            pmids=pmids,
            api_key=api_key,
            email=resolved_email,
            project_id=project_id,
        )
        if not papers_by_pmid:
            return []

        # Enrich with PMCID where possible. Skips silently if ELink errors
        # (the OA waterfall just misses this one; not fatal).
        pmid_to_pmcid = _elink_pmid_to_pmcid(
            session=session,
            pmids=list(papers_by_pmid.keys()),
            api_key=api_key,
            email=resolved_email,
            project_id=project_id,
        )

    for pmid, pmcid in pmid_to_pmcid.items():
        if pmid in papers_by_pmid:
            papers_by_pmid[pmid]["external_ids"]["PMCID"] = pmcid

    # Preserve ESearch's ranking order (sort=relevance) — EFetch returns
    # articles in arbitrary order, so use the PMID list as the canonical order.
    return [papers_by_pmid[pmid] for pmid in pmids if pmid in papers_by_pmid]
