"""
Find Team using Semantic Scholar search and open access PDF download.

Replaces PubMed keyword search (poor relevance, low hit rate) with Semantic
Scholar's bulk search API and a multi-source open access waterfall:
S2 openAccessPdf → Unpaywall → ArXiv → PubMed Central.
"""

import asyncio
import logging
import os
import dotenv
import sys
from typing import Optional, List, Dict
from pathlib import Path
import time

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from find.semantic_scholar_search import rank_papers_by_data_availability, search_papers
from find.prescreen import prescreen_papers
from find.pubmed_search import search_pubmed_papers
from find.paper_downloader_oa import OpenAccessDownloader
from utils.path_utils import get_papers_dir, ensure_directories_exist
from utils.pipeline_log import pipeline_log
from utils.pipeline_messages import pm, pm_with_policy
from orchestrator_ui import push_message, update_stage, update_status

logger = logging.getLogger(__name__)

try:
    from models import PipelineStage
except ImportError:
    PipelineStage = None

dotenv.load_dotenv()


def _get_project_dao():
    """Lazy DAO import for easier testing and optional DB dependencies."""
    from database.dao import ProjectDAO
    return ProjectDAO()


def _unique_filename(papers_dir: str, base_filename: str) -> str:
    """Return a collision-free filename under papers_dir.

    If base_filename already exists, appends _2, _3, … until a free slot is
    found.  Keeps the .pdf extension in place.
    """
    candidate = os.path.join(papers_dir, base_filename)
    if not os.path.exists(candidate):
        return base_filename
    stem, ext = os.path.splitext(base_filename)
    counter = 2
    while True:
        new_name = f"{stem}_{counter}{ext}"
        if not os.path.exists(os.path.join(papers_dir, new_name)):
            return new_name
        counter += 1


def _is_valid_pdf(path: str) -> bool:
    """Return True if path starts with the PDF magic bytes (read-only, never deletes)."""
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'%PDF'
    except Exception:
        return False


def create_paper_filename(title: str, authors: List[str] = None, year: str = None) -> str:
    """Create a standardized filename: {author_lastname}{year}.pdf"""
    if year and '/' in year:
        year = year.split('/')[0]
    elif year and '-' in year:
        year = year.split('-')[0]

    author_part = "Unknown"
    if authors and len(authors) > 0:
        first_author = authors[0]
        if ',' in first_author:
            author_part = first_author.split(',')[0].strip()
        else:
            parts = first_author.split()
            author_part = parts[-1] if parts else first_author

        author_part = "".join(c for c in author_part if c.isalnum()).strip()
        author_part = author_part[:20]

    if author_part == "Unknown" and title and title != "Unknown":
        title_word = "".join(c for c in title.split()[0] if c.isalnum()) if title.split() else "Paper"
        author_part = title_word[:20]

    if year:
        filename = f"{author_part}{year}.pdf"
    else:
        filename = f"{author_part}_{int(time.time())}.pdf"

    filename = filename.replace(' ', '_')
    return filename


def _getenv_int(name: str, default: int) -> int:
    """Parse a positive int env var; UNSET / 0 / garbage → default."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def resolve_candidate_pool_size(max_papers: int) -> int:
    """decouple the wide-net candidate pool from the download
    target. ``PRESCREEN_POOL_SIZE`` sets how many candidates to fetch and rank
    independently of ``max_papers`` (the download cap). UNSET / 0 / garbage
    falls back to the historical ``max_papers * 3``; a configured value never
    shrinks the pool below that floor (``max()``)."""
    return max(_getenv_int("PRESCREEN_POOL_SIZE", 0), max_papers * 3)


def resolve_pubmed_max_pages() -> int:
    """ESearch pagination depth for the fill-to-target loop.
    ``PUBMED_MAX_PAGES`` (default 3). Deep pagination raises NCBI API spend —
    set ``NCBI_API_KEY`` to lift the rate limit (3→10 req/s)."""
    return _getenv_int("PUBMED_MAX_PAGES", 3)


def resolve_prescreen_threshold() -> Optional[float]:
    """LLM pre-screen score gate. ``PRESCREEN_SCORE_THRESHOLD``
    UNSET (or unparseable) → None → prescreen DISABLED (no scoring, no LLM
    cost, current behavior preserved). Only a configured numeric value
    activates the pre-screen scorer."""
    raw = os.getenv("PRESCREEN_SCORE_THRESHOLD")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class FindTeam:

    def __init__(self, headless: bool = False, team_name: str = "FindTeam",
                 websocket_handler=None, project_id: str = None,
                 user_email: Optional[str] = None):
        self.headless = headless
        self.team_name = team_name
        self.websocket_handler = websocket_handler
        self.project_id = project_id
        # per-user fill-to-target dedup needs user_email so
        # the find loop can skip papers the user has in other projects.
        # PipelineRunner threads this from project_meta["user_email"]; CLI
        # invocations leave it None and the fill-to-target path falls
        # back to no cross-project dedup.
        self.user_email = user_email
        self.api_key = os.getenv('S2_API_KEY')
        # NCBI E-utilities credentials. api_key is optional
        # (raises rate limit 3→10 req/s); email is required by NCBI ToS but
        # the search module falls back to UNPAYWALL_EMAIL if unset.
        self.ncbi_api_key = os.getenv('NCBI_API_KEY')
        self.ncbi_email = os.getenv('NCBI_EMAIL')
        #  feature flag: SEARCH_BACKEND env can force a global
        # fallback to S2 for emergency reversion ('s2' overrides per-project
        # setting). Default 'pubmed' → honour the project's stored backend.
        self._search_backend_override = (os.getenv('SEARCH_BACKEND') or '').strip().lower() or None
        # Per-project effective backend resolved at run() time from DAO.
        # 's2' or 'pubmed'. Drives BOTH search call AND dedup method choice
        # (must stay in sync to avoid querying wrong provenance field).
        self._effective_backend: Optional[str] = None
        # MeSH expansion flag (per-project, from DAO).
        # Resolved lazily in run() from project_meta.
        self._mesh_expansion: Optional[bool] = None

        self.downloader = OpenAccessDownloader(
            unpaywall_email=os.getenv('UNPAYWALL_EMAIL')
        )

        from storage import get_storage
        self.storage = get_storage()

    def download_paper(self, paper: Dict) -> Optional[str]:
        """Download a single paper using the open access waterfall strategy."""
        papers_dir = get_papers_dir()

        title = paper.get('title', 'Unknown')
        authors = paper.get('authors', [])
        pub_date = paper.get('publication_date', '')
        doi = paper.get('doi')
        open_access_pdf = paper.get('open_access_pdf')
        external_ids = paper.get('external_ids', {})

        filename = _unique_filename(papers_dir, create_paper_filename(title, authors, pub_date))

        # Log available download sources in actual waterfall order so the
        # user-facing message matches what download_paper will actually try.
        # See paper_downloader_oa.py: Europe PMC (PMCID) → ArXiv (ArXiv ID)
        # → Unpaywall (DOI).
        sources = []
        if external_ids.get('PMCID') or external_ids.get('PubMedCentral'):
            sources.append("Europe PMC")
        if external_ids.get('ArXiv'):
            sources.append("ArXiv")
        if doi:
            sources.append("Unpaywall")

        push_message(
            pm(
                "find.downloading",
                title=title[:80],
                sources=", ".join(sources) if sources else "none available",
            ),
            team_name=self.team_name
        )

        result = self.downloader.download_paper(
            doi=doi,
            open_access_pdf=open_access_pdf,
            external_ids=external_ids,
            output_dir=papers_dir,
            filename=filename,
            project_id=self.project_id,
            unpaywall_pdf_url=paper.get("unpaywall_pdf_url"),
            unpaywall_prefetched=paper.get("unpaywall_prefetched", False),
        )

        if result:
            push_message(
                pm("find.downloaded", file=os.path.basename(result)),
                team_name=self.team_name,
            )

            if self.project_id:
                storage_key = f"papers/{os.path.basename(result)}"
                storage_meta = {
                    'storage_backend': 'local',
                    's3_bucket': None,
                    's3_key': None,
                    'oss_bucket': None,
                    'oss_key': None,
                    'data_availability_score': paper.get('data_availability_score'),
                    'data_availability_signals': paper.get('data_availability_signals', []),
                }
                try:
                    storage_result = self.storage.upload(local_path=result, key=storage_key)
                    storage_meta.update({
                        'storage_backend': storage_result.storage_backend,
                        's3_bucket': storage_result.s3_bucket,
                        's3_key': storage_result.s3_key,
                        'oss_bucket': storage_result.oss_bucket,
                        'oss_key': storage_result.oss_key,
                    })
                    push_message(
                        pm("find.uploaded_storage", storage_key=storage_key),
                        team_name=self.team_name,
                    )
                except Exception as upload_err:
                    pipeline_log(
                        f"paper storage upload failed (will record as local): {upload_err}",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )
                    push_message(
                        pm("find.upload_failed_local"),
                        team_name=self.team_name,
                        message_type="warning",
                    )

                try:
                    dao = _get_project_dao()
                    # branch provenance key on effective backend.
                    # New PubMed projects MUST NOT write an empty s2_paper_id
                    # (it would pollute the idx_artifacts_provenance_s2id
                    # partial index which uses IS NOT NULL predicate).
                    backend = self._effective_backend or self._resolve_effective_backend()
                    if backend == "pubmed":
                        provenance = {'pmid': paper.get('paper_id', '')}
                    else:
                        provenance = {'s2_paper_id': paper.get('paper_id', '')}
                    dao.add_artifact(
                        project_id=self.project_id,
                        artifact_type="paper",
                        file_path=result,
                        stage_name="find",
                        storage_metadata=storage_meta,
                        provenance=provenance,
                    )
                    # populate global papers table so the
                    # analysis_cache FK (pmid → papers.pmid) is always
                    # satisfied when analyze stage later writes a cache row.
                    # Only meaningful for PubMed-backend papers; legacy S2
                    # rows have no PMID and we deliberately skip them.
                    if backend == "pubmed":
                        pmid = paper.get("paper_id")
                        if pmid:
                            ext_ids = paper.get("external_ids") or {}
                            dao.upsert_paper(
                                pmid=pmid,
                                doi=paper.get("doi") or ext_ids.get("DOI"),
                                pmcid=ext_ids.get("PMCID"),
                                title=paper.get("title"),
                                authors=paper.get("authors"),
                                publication_date=paper.get("publication_date"),
                                journal=paper.get("journal"),
                            )
                    pipeline_log(
                        f"paper artifact recorded: {storage_key}",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                    )
                except Exception as db_err:
                    pipeline_log(
                        f"paper artifact DB record failed: {db_err}",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )
        else:
            # Persist this one (the other two find-loop progress messages
            # are non-persistent). Unlike candidate_try / failed_remaining
            # — which are pure progress noise — no_oa_source is the only
            # record of which specific papers couldn't be downloaded. The
            # Find Summary card shows the aggregate shortfall, but only
            # these messages let the user trace back individual misses.
            push_message(
                pm("find.no_oa_source", title=title[:60]),
                team_name=self.team_name,
                message_type="warning",
            )

        return result

    def _resolve_effective_backend(self) -> str:
        """Decide which search backend this run will use.

        Precedence: env override (emergency rollback) > project metadata >
        'pubmed' default. Cached to ``self._effective_backend`` after first
        resolution so dedup branches and provenance writes stay consistent.
        """
        if self._effective_backend is not None:
            return self._effective_backend

        if self._search_backend_override in {"s2", "pubmed"}:
            self._effective_backend = self._search_backend_override
            # MeSH expansion only meaningful for PubMed.
            self._mesh_expansion = False if self._search_backend_override == "s2" else True
            return self._effective_backend

        if self.project_id:
            try:
                dao = _get_project_dao()
                meta = dao.get_project(self.project_id) or {}
                backend = (meta.get("search_backend") or "").strip().lower()
                if backend in {"s2", "pubmed"}:
                    self._effective_backend = backend
                    # cache MeSH flag from project meta.
                    self._mesh_expansion = bool(meta.get("mesh_expansion", True))
                    return backend
            except Exception:
                pass

        self._effective_backend = "pubmed"
        self._mesh_expansion = True  # default
        return self._effective_backend

    def _filter_user_already_seen(self, papers: List[Dict]) -> List[Dict]:
        """Drop papers already collected in any of the same user's projects.

        replaces what would otherwise be N per-PMID DB
        round-trips with a single batched ``WHERE pmid = ANY(%s)`` query.
        ``user_email`` empty → no cross-project dedup, return papers unchanged.
        """
        if not self.user_email:
            return papers
        pmids = [p["paper_id"] for p in papers if p.get("paper_id")]
        already_seen = self._find_user_already_seen_pmids(pmids)
        if not already_seen:
            return papers
        return [p for p in papers if p.get("paper_id") not in already_seen]

    def _find_user_already_seen_pmids(self, pmids: List[str]) -> set:
        """Return the subset of ``pmids`` already present in any of the same
        user's projects. Single DB round-trip via batched WHERE pmid = ANY(%s).
        Empty user_email / pmids → empty set (no cross-project dedup applies).
        """
        if not self.user_email or not pmids:
            return set()
        try:
            dao = _get_project_dao()
            return dao.find_user_already_seen_pmids(self.user_email, pmids)
        except Exception:
            return set()

    def _fill_to_target_pubmed(
        self,
        search_query: str,
        candidate_pool_size: int,
        target_count: int,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
    ) -> List[Dict]:
        """Iterate PubMed ESearch with `retstart` until we have enough fresh
        candidates to reach ``target_count`` after per-user dedup, or run out.

         design (§4.1): MAX_PAGES early-exit when (a) the
        user-dedup buffer reaches the fill target, or (b) ESearch returns
        fewer than retmax (query result set exhausted).

        MAX_PAGES is configurable via ``PUBMED_MAX_PAGES``
        (default 3). When wide-net is active (``candidate_pool_size`` exceeds
        the historical ``target_count * 3`` — i.e. ``PRESCREEN_POOL_SIZE``
        widened the pool), the early-exit keys off the pool target so the loop
        keeps paging to fill the whole pool instead of bailing at
        ``target_count * 2``. Narrow-net behavior is unchanged.
        """
        MAX_PAGES = resolve_pubmed_max_pages()
        wide_net = candidate_pool_size > target_count * 3
        # In wide-net mode we want to fill the whole pool; otherwise keep the
        # historical "max_papers * 2" OA-failure-tolerance buffer.
        fill_target = candidate_pool_size if wide_net else target_count * 2
        pipeline_log(
            f"find: fill_to_target max_pages={MAX_PAGES} "
            f"candidate_pool_size={candidate_pool_size} target_count={target_count} "
            f"wide_net={wide_net} fill_target={fill_target}",
            stage="find", team=self.team_name, project_id=self.project_id,
        )
        collected: List[Dict] = []
        # already_seen_in_user is populated inside the loop body and reused
        # by the end-of-function bias-sort. Init to empty set so the bias
        # sort still works if the loop never iterates (e.g., empty first batch).
        already_seen_in_user: set = set()
        retstart = 0
        for page in range(MAX_PAGES):
            # Write back MeSH status to projects table
            # on first page (subsequent pages reuse same query/expansion).
            # Review-fix H5: capture `page` by value (default-arg trick) so the
            # closure never reads a post-increment loop variable. Today the
            # callback runs synchronously inside search_pubmed_papers() so the
            # bug is latent — guarding here makes the code robust against
            # future async refactors.
            def _mesh_callback(
                expanded_query: str, status: str, terms: List[str],
                _page: int = page,
            ) -> None:
                if _page == 0:
                    try:
                        dao_inner = _get_project_dao()
                        dao_inner.set_mesh_expansion_result(
                            project_id=self.project_id,
                            expanded_query=expanded_query,
                            status=status,
                        )
                    except Exception:
                        pass  # observability best-effort

            batch = search_pubmed_papers(
                query=search_query,
                max_results=candidate_pool_size,
                year_start=date_start,
                year_end=date_end,
                api_key=self.ncbi_api_key,
                email=self.ncbi_email,
                project_id=self.project_id,
                retstart=retstart,
                mesh_expansion=bool(self._mesh_expansion),
                mesh_status_callback=_mesh_callback if page == 0 else None,
            )
            if not batch:
                break
            seen_pmids = {p["paper_id"] for p in collected}
            new_papers = [p for p in batch if p["paper_id"] not in seen_pmids]
            collected.extend(new_papers)

            # Review-fix H1: cache the per-iteration user-dedup result so the
            # end-of-function bias-sort reuses it (was a 2nd identical DB
            # round-trip + risk of inconsistency if DB state changed between
            # the two calls).
            already_seen_in_user = self._find_user_already_seen_pmids(
                [p["paper_id"] for p in collected],
            )
            after_user_dedup_count = sum(
                1 for p in collected if p["paper_id"] not in already_seen_in_user
            )
            if after_user_dedup_count >= fill_target:
                pipeline_log(
                    f"find: fill_to_target satisfied page={page+1} "
                    f"collected={len(collected)} after_dedup={after_user_dedup_count}",
                    stage="find", team=self.team_name, project_id=self.project_id,
                )
                break

            if len(batch) < candidate_pool_size:
                # NCBI exhausted the result set; next ESearch guaranteed empty.
                pipeline_log(
                    f"find: fill_to_target query exhausted at page={page+1} "
                    f"batch_size={len(batch)} < retmax={candidate_pool_size}",
                    stage="find", team=self.team_name, project_id=self.project_id,
                )
                break
            retstart += candidate_pool_size

        if len(collected) < target_count:
            pipeline_log(
                f"find: fill_to_target partial — collected {len(collected)} after "
                f"{MAX_PAGES} pages, requested {target_count}",
                stage="find", team=self.team_name, project_id=self.project_id,
                level=logging.WARNING,
            )
        # Bias the returned slice toward fresh-after-user-dedup papers so the
        # downstream download loop has enough fresh candidates within
        # candidate_pool_size. Reuses `already_seen_in_user` cached from the
        # last loop iteration — avoids a redundant DB call.
        fresh = [p for p in collected if p["paper_id"] not in already_seen_in_user]
        seen = [p for p in collected if p["paper_id"] in already_seen_in_user]
        return (fresh + seen)[:candidate_pool_size]

    async def _prefetch_unpaywall_urls(self, papers: List[Dict]) -> None:
        """Concurrently resolve Unpaywall PDF URLs for each candidate with a DOI.

        Writes the resolved URL onto ``paper["unpaywall_pdf_url"]`` when
        Unpaywall returns a verified PDF link. Papers without a DOI, or whose
        Unpaywall lookup fails or returns no ``url_for_pdf``, are left alone
        — the per-paper waterfall will then either succeed via Europe PMC /
        ArXiv (the fast paths) or fall through to a live Unpaywall lookup.

        Skip-with-PMCID optimisation: papers that carry a PMCID will almost
        always succeed at waterfall step 1 (Europe PMC direct PDF), so the
        Unpaywall lookup is wasted work. Excluding them cuts the prefetch
        load by ~50-70% for typical PubMed projects (organoid query: 489
        of 710 candidates have PMCID), freeing both wall clock and the
        shared 100k/day Unpaywall quota for cross-team use.

        Concurrency is capped by ``UNPAYWALL_PREFETCH_CONCURRENCY`` (default
        5) to stay inside Unpaywall's polite-use guideline (~10 req/s).
        """
        def _has_pmcid(p: Dict) -> bool:
            ext = p.get("external_ids") or {}
            return bool(ext.get("PMCID") or ext.get("PubMedCentral"))

        candidates_with_doi = [
            p for p in papers
            if p.get("doi") and not _has_pmcid(p)
        ]
        if not candidates_with_doi:
            return

        try:
            cap = max(1, int(os.getenv("UNPAYWALL_PREFETCH_CONCURRENCY", "5") or "5"))
        except ValueError:
            cap = 5

        sem = asyncio.Semaphore(cap)
        pipeline_log(
            f"find: unpaywall prefetch start candidates={len(candidates_with_doi)} concurrency={cap}",
            stage="find",
            team=self.team_name,
            project_id=self.project_id,
        )

        async def _resolve_one(paper: Dict) -> None:
            async with sem:
                # _get_unpaywall_url already swallows its exceptions and
                # returns None on any failure, so no try/except needed here.
                url = await asyncio.to_thread(
                    self.downloader._get_unpaywall_url,
                    paper["doi"],
                    self.project_id,
                )
                # Always record the outcome — None included — so the
                # downloader's waterfall step 3 knows "we already asked,
                # don't ask again". Without this, every no-OA paper costs
                # an extra Unpaywall round-trip in the download loop.
                paper["unpaywall_pdf_url"] = url
                paper["unpaywall_prefetched"] = True

        await asyncio.gather(
            *(_resolve_one(p) for p in candidates_with_doi),
            return_exceptions=True,
        )

        resolved = sum(1 for p in candidates_with_doi if p.get("unpaywall_pdf_url"))
        pipeline_log(
            f"find: unpaywall prefetch done resolved={resolved}/{len(candidates_with_doi)}",
            stage="find",
            team=self.team_name,
            project_id=self.project_id,
        )

    async def run(self, search_query: str, custom_request: Optional[str] = None,
                  max_papers: int = 10,
                  date_start: Optional[str] = None,
                  date_end: Optional[str] = None) -> Optional[List[str]]:
        # When either bound is None, no PubMed `mindate`/`maxdate` param is
        # sent — the search covers all of PubMed history up to the current
        # publication index. Previously the defaults were hardcoded
        # "1950/01/01" / "2024/12/31", and the latter went stale: any
        # paper published after 2024/12/31 was silently excluded even
        # when the user left the date picker empty. ESearch's
        # `if year_start:` / `if year_end:` guards already short-circuit
        # cleanly on None.
        ensure_directories_exist()

        effective_backend = self._resolve_effective_backend()

        update_stage(f"{self.team_name} - Searching")
        message, persist = pm_with_policy("find.search_start", query=search_query)
        push_message(message, team_name=self.team_name, persist=persist)
        pipeline_log(
            f"find: starting search backend={effective_backend} query={search_query!r} "
            f"max_papers={max_papers} date_range={date_start!r}..{date_end!r} "
            f"s2_api_key={'set' if self.api_key else 'unset'} "
            f"ncbi_api_key={'set' if self.ncbi_api_key else 'unset'} "
            f"ncbi_email={'set' if self.ncbi_email else 'unset'}",
            stage="find",
            team=self.team_name,
            project_id=self.project_id,
        )

        try:
            # Fetch a larger candidate pool so we can skip failed downloads
            # and keep trying until we reach the requested max_papers count.
            # PRESCREEN_POOL_SIZE decouples the wide-net pool
            # from the download target; UNSET/0 → historical max_papers*3.
            candidate_pool_size = resolve_candidate_pool_size(max_papers)
            pubmed_max_pages = resolve_pubmed_max_pages()

            if effective_backend == "pubmed":
                # fill-to-target loop. ESearch a batch, dedup
                # against user's existing artifacts, and if still short of the
                # pool target, advance retstart and fetch the next page. Caps
                # at PUBMED_MAX_PAGES (default 3) to bound NCBI API spend.
                pipeline_log(
                    f"find: candidate_pool_size={candidate_pool_size} "
                    f"(max_papers={max_papers}, PRESCREEN_POOL_SIZE="
                    f"{os.getenv('PRESCREEN_POOL_SIZE', 'unset')}); pubmed esearch "
                    f"fill-to-target loop (PUBMED_MAX_PAGES={pubmed_max_pages})",
                    stage="find",
                    team=self.team_name,
                    project_id=self.project_id,
                )
                # _fill_to_target_pubmed walks NCBI ESearch/EFetch/ELink via
                # sync requests with REQUEST_TIMEOUT=30s, up to MAX_PAGES=3
                # plus internal _efetch chunks. Running it directly here
                # would pin the event loop for tens of seconds on slow days.
                candidates = await asyncio.to_thread(
                    self._fill_to_target_pubmed,
                    search_query=search_query,
                    candidate_pool_size=candidate_pool_size,
                    target_count=max_papers,
                    date_start=date_start,
                    date_end=date_end,
                )
            else:
                # Legacy S2 path — kept for projects created before the
                # deploy timestamp and as the SEARCH_BACKEND='s2'
                # emergency-rollback target.
                pipeline_log(
                    f"find: candidate_pool_size={candidate_pool_size} (max_papers×3); "
                    f"first pass open_access_only=True min_citation_count=3",
                    stage="find",
                    team=self.team_name,
                    project_id=self.project_id,
                )
                # Sync HTTP to Semantic Scholar (30s per call). Offload so
                # the event loop stays responsive while the search runs.
                candidates = await asyncio.to_thread(
                    search_papers,
                    query=search_query,
                    max_results=candidate_pool_size,
                    year_start=date_start,
                    year_end=date_end,
                    api_key=self.api_key,
                    open_access_only=True,
                    min_citation_count=3,
                    project_id=self.project_id,
                )

                # Fallback: if OA-only search returns too few, supplement with non-OA results
                if len(candidates) < candidate_pool_size:
                    push_message(
                        pm("find.oa_supplement", count=len(candidates)),
                        team_name=self.team_name,
                        message_type="info",
                    )
                    pipeline_log(
                        f"find: OA-only pool thin ({len(candidates)} < {candidate_pool_size}) "
                        f"→ supplement pass open_access_only=False same citation filter",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.INFO,
                    )
                    fallback = await asyncio.to_thread(
                        search_papers,
                        query=search_query,
                        max_results=candidate_pool_size - len(candidates),
                        year_start=date_start,
                        year_end=date_end,
                        api_key=self.api_key,
                        min_citation_count=3,
                        project_id=self.project_id,
                    )
                    seen = {p['paper_id'] for p in candidates}
                    before = len(candidates)
                    candidates.extend(p for p in fallback if p['paper_id'] not in seen)
                    pipeline_log(
                        f"find: after supplement unique candidates extended by {len(candidates) - before} "
                        f"(total {len(candidates)})",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                    )

            if not candidates:
                push_message(
                    pm("find.no_candidates_hint"),
                    team_name=self.team_name,
                    message_type="error",
                )
                pipeline_log(
                    "find: no candidates after OA + optional non-OA supplement",
                    stage="find",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
                return None

            candidates = rank_papers_by_data_availability(candidates)
            top_signals = [
                {
                    "paper_id": paper.get("paper_id"),
                    "score": paper.get("data_availability_score", 0),
                    "signals": paper.get("data_availability_signals", [])[:5],
                }
                for paper in candidates[:5]
            ]
            pipeline_log(
                f"find: candidate order=data_availability_then_citations top={top_signals}",
                stage="find",
                team=self.team_name,
                project_id=self.project_id,
            )

            # LLM title/abstract pre-screen. Opt-in — when
            # PRESCREEN_SCORE_THRESHOLD is unset, resolve_prescreen_threshold()
            # returns None and prescreen_papers() is a no-op (returns the list
            # unchanged, no LLM cost). When configured it scores the FULL pool,
            # filters out papers below the threshold and re-sorts the survivors
            # by score desc. The heuristic rank above is retained as the input
            # ordering / stable tie-break. Reassigning `candidates` here means
            # the Unpaywall prefetch and download loop below only ever see the
            # filtered list — no Unpaywall quota spent on filtered-out papers.
            prescreen_threshold = resolve_prescreen_threshold()
            if prescreen_threshold is not None:
                dao_for_prescreen = None
                if self.project_id:
                    try:
                        dao_for_prescreen = _get_project_dao()
                    except Exception:
                        dao_for_prescreen = None
                before = len(candidates)
                candidates = await prescreen_papers(
                    candidates,
                    project_id=self.project_id,
                    team_name=self.team_name,
                    threshold=prescreen_threshold,
                    dao=dao_for_prescreen,
                )
                pipeline_log(
                    f"find: prescreen filtered {before} → {len(candidates)} "
                    f"candidates (threshold={prescreen_threshold})",
                    stage="find",
                    team=self.team_name,
                    project_id=self.project_id,
                )
                if not candidates:
                    push_message(
                        pm("find.no_candidates_hint"),
                        team_name=self.team_name,
                        message_type="error",
                    )
                    return None

            # Concurrent Unpaywall prefetch. The download waterfall otherwise
            # issues Unpaywall lookups serially per paper inside the download
            # loop (0.4-15s each). Prefetching in parallel — with a small
            # cap so we stay polite — lets the 30-odd lookups overlap with
            # ranking work and with each other, so by the time the download
            # loop starts each paper already has its PDF URL cached on the
            # candidate dict.
            await self._prefetch_unpaywall_urls(candidates)

            message, persist = pm_with_policy(
                "find.candidate_summary",
                candidates=len(candidates),
                target=max_papers,
            )
            push_message(message, team_name=self.team_name, persist=persist)
            pipeline_log(
                f"find: download phase order=data_availability_then_citations among {len(candidates)} candidate(s); "
                f"per-paper OA waterfall=Europe PMC → ArXiv → Unpaywall (prefetched)",
                stage="find",
                team=self.team_name,
                project_id=self.project_id,
            )

            # Resume check: identify papers already downloaded for this project.
            # Prefer paper_id (stable S2 key) over filename (unstable, author-format-dependent).
            papers_to_try = []
            already_downloaded = []
            skipped_in_other_projects = []

            if self.project_id:
                try:
                    dao = _get_project_dao()
                except Exception as dao_init_err:
                    pipeline_log(
                        f"find: DAO unavailable, resume/artifact recording disabled: {dao_init_err}",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.CRITICAL,
                    )
                    dao = None

            if self.project_id and dao:
                # Pre-fetch dedup sets in TWO queries (one project-local +
                # one cross-project) instead of 2*N per-candidate SQL round
                # trips. For a 710-candidate find this collapses ~1400
                # SELECTs into 2, saving multiple seconds of DB time and
                # corresponding event-loop pressure.
                candidate_ids = [p.get('paper_id') for p in candidates if p.get('paper_id')]
                if effective_backend == "pubmed":
                    project_dedup_hits = dao.has_paper_artifacts_by_pmids(
                        self.project_id, candidate_ids
                    )
                    cross_project_hits = dao.find_papers_in_other_user_projects_by_pmids(
                        candidate_ids, self.project_id
                    )
                else:
                    project_dedup_hits = dao.has_paper_artifacts_by_s2ids(
                        self.project_id, candidate_ids
                    )
                    cross_project_hits = dao.find_papers_in_other_user_projects_by_s2ids(
                        candidate_ids, self.project_id
                    )

                for paper in candidates:
                    paper_id = paper.get('paper_id', '')
                    title = paper.get('title', 'Unknown')
                    authors = paper.get('authors', [])
                    pub_date = paper.get('publication_date', '')
                    expected_filename = create_paper_filename(title, authors, pub_date)
                    paper_identifier = expected_filename.replace('.pdf', '')

                    # 1. Project-level check (resume within same project).
                    # paper_id is the right provenance key —
                    # legacy S2 projects store ``provenance.s2_paper_id``,
                    # PubMed projects store ``provenance.pmid``. Filename
                    # fallback still per-call (rare path, only triggers when
                    # paper_id is missing or the artifact predates the
                    # provenance migration).
                    found = (
                        (paper_id and paper_id in project_dedup_hits)
                        or dao.has_paper_artifact(self.project_id, paper_identifier)
                    )
                    if found:
                        already_downloaded.append(paper_identifier)
                        continue

                    # 2. Same-user cross-project dedup — skip without inserting.
                    # Honors privacy (other users' papers are never reused) and
                    # keeps this project's "papers" count honest: only fresh hits
                    # land in the DB. Cross-project skips no longer consume the
                    # max_papers quota (37fcde3), so the new project still
                    # targets max_papers fresh downloads.
                    if paper_id and paper_id in cross_project_hits:
                        skipped_in_other_projects.append(paper_identifier)
                        pipeline_log(
                            f"find: candidate already in user's other project, "
                            f"skipping without DB insert: {paper_identifier}",
                            stage="find",
                            team=self.team_name,
                            project_id=self.project_id,
                        )
                        continue

                    papers_to_try.append(paper)

                if skipped_in_other_projects:
                    push_message(
                        pm(
                            "find.skipped_in_other_projects",
                            count=len(skipped_in_other_projects),
                            target=max_papers,
                        ),
                        team_name=self.team_name,
                        message_type="info",
                    )
                if already_downloaded:
                    push_message(
                        pm("find.already_downloaded", count=len(already_downloaded)),
                        team_name=self.team_name,
                        message_type="info",
                    )
                    pipeline_log(
                        f"find: resume/skip already in DB: {len(already_downloaded)} paper(s) toward max_papers",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                    )
            else:
                papers_to_try = candidates

            # How many more successful downloads do we need?
            #
            # Cross-project skips do NOT consume the quota — the user expects
            # `max_papers` to mean "this project should end up with N fresh
            # PDFs", not "N total unique-to-the-user PMIDs across all
            # projects". The cross-project dedup still happens (those papers
            # are stripped from papers_to_try above so we don't re-download
            # them), but the freed-up slots get filled by fresh candidates
            # from the candidate pool (`max_papers × 3`). Only same-project
            # resume (already_downloaded) reduces still_needed, because that
            # genuinely already lives in this project.
            still_needed = max_papers - len(already_downloaded)

            if still_needed <= 0:
                push_message(
                    pm(
                        "find.nothing_to_do",
                        count=len(already_downloaded),
                        target=max_papers,
                    ),
                    team_name=self.team_name,
                    message_type="info",
                )
                papers_dir = get_papers_dir()
                return [os.path.join(papers_dir, f"{name}.pdf") for name in already_downloaded]

            push_message(
                pm(
                    "find.need_more",
                    needed=still_needed,
                    existing=len(already_downloaded),
                    target=max_papers,
                    candidates=len(papers_to_try),
                ),
                team_name=self.team_name
            )

            downloaded_paths: List[str] = []
            candidates_tried = 0

            update_stage(f"{self.team_name} - Downloading")

            # Concurrency control. Default cap=1 preserves the serial
            # behaviour the loop has always had; larger values process
            # multiple candidates in flight via a Semaphore. Each task
            # still runs the sync downloader on a worker thread
            # (asyncio.to_thread), so the event loop stays responsive
            # regardless of cap. Safe upper bound is roughly 5-10 for
            # politeness toward Europe PMC / Unpaywall;
            # 3-5 is the practical sweet spot for big projects.
            import random
            try:
                cap = max(1, int(os.getenv("FIND_DOWNLOAD_CONCURRENCY", "1") or "1"))
            except ValueError:
                cap = 1
            sem = asyncio.Semaphore(cap)
            target_reached = asyncio.Event()
            pipeline_log(
                f"find: download phase concurrency={cap} "
                f"(env FIND_DOWNLOAD_CONCURRENCY={os.getenv('FIND_DOWNLOAD_CONCURRENCY', 'unset')})",
                stage="find", team=self.team_name, project_id=self.project_id,
            )

            async def _attempt_paper(paper: Dict) -> None:
                """Attempt one OA waterfall, updating outer counters/lists.

                Mutates ``downloaded_paths`` / ``candidates_tried`` via
                ``nonlocal``. Sets ``target_reached`` once enough successes
                have accumulated so the remaining queued tasks short-circuit
                without wasting another download attempt.
                """
                nonlocal candidates_tried

                # Fast-exit if target already reached before we acquired the
                # semaphore — the next ~cap-1 in-flight tasks may still have
                # been started; this prevents the rest from doing the work.
                if target_reached.is_set():
                    return
                async with sem:
                    if target_reached.is_set():
                        return

                    candidates_tried += 1
                    title = paper.get('title', 'N/A')
                    # persist=False: per-candidate try lines are real-time
                    # progress noise (N=5000 → 15000 rows). Live WS fine;
                    # DB persistence pollutes the messages tab.
                    push_message(
                        pm(
                            "find.candidate_try",
                            candidate=candidates_tried,
                            index=len(downloaded_paths) + len(already_downloaded) + 1,
                            total=max_papers,
                            title=title[:70],
                        ),
                        team_name=self.team_name,
                        persist=False,
                    )

                    # Single attempt — OA waterfall already tries 3+ sources
                    # internally with urllib3 retry, outer retry doubled wall
                    # clock for guaranteed-fail papers without recovering any.
                    result = None
                    try:
                        # Offload to a thread: download_paper uses sync
                        # requests with up to 60s read timeout. Running it
                        # on the event loop would freeze every API request.
                        result = await asyncio.to_thread(self.download_paper, paper)
                    except Exception as download_exc:
                        pipeline_log(
                            f"find: download_paper raised for "
                            f"{paper.get('title', '')!r}: {download_exc}",
                            stage="find",
                            team=self.team_name,
                            project_id=self.project_id,
                            level=logging.WARNING,
                        )
                        result = None

                    if result:
                        downloaded_paths.append(result)
                        done = len(downloaded_paths) + len(already_downloaded)
                        push_message(
                            pm(
                                "find.download_complete_progress",
                                done=done,
                                total=max_papers,
                            ),
                            team_name=self.team_name,
                        )
                        if self.websocket_handler and self.project_id and PipelineStage:
                            await self.websocket_handler.update_project_progress(
                                self.project_id,
                                PipelineStage.FIND,
                                min(done / max_papers, 1.0),
                            )
                        if len(downloaded_paths) >= still_needed:
                            target_reached.set()
                    else:
                        # In concurrent mode the "remaining" estimate is
                        # approximate (tasks may finish out of order); for
                        # serial cap=1 this matches the old wording exactly.
                        remaining = max(0, len(papers_to_try) - candidates_tried)
                        # persist=False: ~half-N "moving to next" rows that
                        # drown out real signals; find_summary captures
                        # the aggregate shortfall at end-of-stage.
                        push_message(
                            pm(
                                "find.download_failed_remaining",
                                remaining=remaining,
                            ),
                            team_name=self.team_name,
                            message_type="warning",
                            persist=False,
                        )

                    # Rate limit ONLY in serial mode (cap=1). For cap>1 the
                    # download time itself + the semaphore implicitly spread
                    # requests; an extra sleep here would just queue tasks.
                    if cap == 1 and not target_reached.is_set():
                        await asyncio.sleep(random.uniform(1, 3))

            # Dispatch all candidates as concurrent tasks. The semaphore
            # caps actual in-flight downloads to `cap`; remaining tasks
            # park on Semaphore.acquire and pick up as slots free.
            attempt_tasks = [
                asyncio.create_task(_attempt_paper(p)) for p in papers_to_try
            ]
            await asyncio.gather(*attempt_tasks, return_exceptions=True)

            # Summary
            total_obtained = len(downloaded_paths) + len(already_downloaded)
            message, persist = pm_with_policy(
                "find.obtained_summary",
                obtained=total_obtained,
                requested=max_papers,
                newly=len(downloaded_paths),
                existing=len(already_downloaded),
                tried=candidates_tried,
            )
            push_message(message, team_name=self.team_name, persist=persist)

            # Structured summary into execution_logs so the project detail
            # Overview tab can render exact counts without parsing the
            # free-text message above. UI reads this via getProjectExecutionLogs
            # and picks the most recent event_type='find_summary' row.
            if self.project_id:
                try:
                    _dao = _get_project_dao()
                    _dao.add_execution_log(
                        project_id=self.project_id,
                        event_type="find_summary",
                        stage_name="find",
                        team_name=self.team_name,
                        message=(
                            f"Find summary: candidates={len(candidates)}, "
                            f"cross_project_skipped={len(skipped_in_other_projects)}, "
                            f"resumed={len(already_downloaded)}, "
                            f"newly_downloaded={len(downloaded_paths)}, "
                            f"total_obtained={total_obtained}, "
                            f"requested={max_papers}"
                        ),
                        payload={
                            "total_candidates": len(candidates),
                            "cross_project_skipped": len(skipped_in_other_projects),
                            "already_downloaded_resume": len(already_downloaded),
                            "newly_downloaded": len(downloaded_paths),
                            "total_obtained": total_obtained,
                            "requested_max_papers": max_papers,
                            "candidates_tried": candidates_tried,
                            "shortfall": max(0, max_papers - total_obtained),
                        },
                    )
                except Exception as e:
                    pipeline_log(
                        f"find: failed to write find_summary execution_log: {e}",
                        stage="find",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )

            if total_obtained < max_papers:
                push_message(
                    pm(
                        "find.shortfall_warning",
                        obtained=total_obtained,
                        requested=max_papers,
                        shortfall=max_papers - total_obtained,
                    ),
                    team_name=self.team_name,
                    message_type="warning",
                )

            if downloaded_paths:
                for path in downloaded_paths:
                    push_message(
                        pm("find.new_paper", file=os.path.basename(path)),
                        team_name=self.team_name,
                    )

            papers_dir = get_papers_dir()
            all_paths = downloaded_paths + [
                os.path.join(papers_dir, f"{name}.pdf") for name in already_downloaded
            ]
            return all_paths if all_paths else None

        except Exception as e:
            error_msg, persist = pm_with_policy("find.failed", error=e)
            logger.exception("%s", error_msg)
            push_message(
                error_msg,
                team_name=self.team_name,
                message_type="error",
                persist=persist,
            )
            update_status(error_msg, "error")
            pipeline_log(
                f"find: fatal error {e}",
                stage="find",
                team=self.team_name,
                project_id=self.project_id,
                level=logging.ERROR,
            )
            return None

    async def cleanup(self):
        if self.downloader:
            self.downloader.close()


async def main() -> None:
    from orchestrator_ui import OrchestratorUI, set_ui

    ui = OrchestratorUI()
    set_ui(ui)
    ui.start()

    team = FindTeam()
    try:
        search_query = input("Enter search query (or press Enter for default): ").strip()
        if not search_query:
            search_query = "cancer drug response prediction genomics IC50"

        result = await team.run(search_query, max_papers=10)
        if result:
            pipeline_log(
                f"Downloaded {len(result)} papers: {result}",
                stage="find",
                team="FindTeam",
            )
        else:
            pipeline_log(
                "Paper search/download failed",
                stage="find",
                team="FindTeam",
                level=logging.ERROR,
            )
    finally:
        await team.cleanup()
        ui.stop()


if __name__ == "__main__":
    asyncio.run(main())
