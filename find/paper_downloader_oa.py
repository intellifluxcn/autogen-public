"""
Open Access PDF downloader.

Waterfall (legal OA first):
  1. Unpaywall API (UNPAYWALL_EMAIL) — by DOI
  2. ArXiv (if ArXiv ID present)
  3. Europe PMC (if PMCID present)

Only legitimate open-access sources are queried. Papers that are not open
access are not retrieved.

Removed the legacy step "Semantic Scholar openAccessPdf URL"
because the PubMed backend doesn't provide that field. Legacy S2-backed
projects keep ``open_access_pdf`` populated on their candidate dicts but the
step has been retired from the waterfall — callers will fall straight through
to Unpaywall, which already covers the same papers via DOI.
"""

import logging
import os
from typing import Optional, Dict

import requests

from utils.pipeline_log import pipeline_log
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
ARXIV_PDF_BASE = "https://arxiv.org/pdf"
EUROPEPMC_PDF = "https://europepmc.org/articles"

PDF_MAGIC = b'%PDF'


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _create_download_session() -> Session:
    session = Session()
    # General adapter: retry transient 5xx for hosts where 5xx genuinely
    # means "try again later" (ArXiv mirrors, Unpaywall, etc.).
    session.mount('https://', HTTPAdapter(max_retries=Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods={'GET'},
    )))
    # Host-specific override for EuropePMC. Its /api/getPdf endpoint
    # (where /articles/PMC.../?pdf=render redirects to) returns 500 — not
    # 404 — for PMC articles that don't have a PDF on file. So a 500 here
    # is a deterministic "I don't have it" signal, not a transient
    # overload. Without this override, urllib3 would burn ~10-15s on
    # exponential backoff retries per guaranteed-fail paper. Keep 429 in
    # the forcelist so rate-limit retries still work.
    #
    # session.mount uses longest-prefix match, so this overrides the
    # `https://` adapter above for europepmc.org URLs only.
    session.mount('https://europepmc.org/', HTTPAdapter(max_retries=Retry(
        total=2,
        backoff_factor=1.0,
        status_forcelist=[429],
        allowed_methods={'GET'},
    )))
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/pdf,application/octet-stream,*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    return session


class OpenAccessDownloader:
    """
    Downloads papers using an open-access-only waterfall.
    """

    DEFAULT_EMAIL = "your-email@example.com"  # set UNPAYWALL_EMAIL; Unpaywall requires a real contact address

    def __init__(
        self,
        unpaywall_email: str = None,
    ):
        self.unpaywall_email = (
            unpaywall_email or os.getenv("UNPAYWALL_EMAIL") or self.DEFAULT_EMAIL
        )
        self.session = _create_download_session()

    def download_paper(
        self,
        doi: str = None,
        open_access_pdf: dict = None,
        external_ids: dict = None,
        output_dir: str = '.',
        filename: str = None,
        project_id: Optional[str] = None,
        unpaywall_pdf_url: Optional[str] = None,
        unpaywall_prefetched: bool = False,
    ) -> Optional[str]:
        """
        Try to download a PDF using the waterfall strategy.

        Args:
            doi: Paper DOI (used for Unpaywall lookup).
            open_access_pdf: S2 openAccessPdf dict {"url": ..., "status": ...} or None.
            external_ids: S2 externalIds dict (ArXiv, PMCID, etc.).
            output_dir: Directory to save the downloaded PDF.
            filename: Target filename (without directory). Auto-generated if None.
            project_id: Optional project id for structured pipeline logs.
            unpaywall_pdf_url: Pre-resolved PDF URL from the batch Unpaywall
                prefetch (may be None).
            unpaywall_prefetched: True iff prefetch already queried Unpaywall
                for this DOI. When True, step 3 trusts the cached URL and
                skips the live lookup — including the None case (meaning
                "Unpaywall has no PDF for this paper, don't ask again").
                When False, step 3 does a live lookup as before.

        Returns:
            Absolute path to the downloaded PDF, or None if all sources fail.
        """
        os.makedirs(output_dir, exist_ok=True)
        external_ids = external_ids or {}

        if not filename:
            safe_doi = doi.replace('/', '_').replace(':', '_') if doi else 'unknown'
            filename = f"{safe_doi}.pdf"
        if not filename.endswith('.pdf'):
            filename += '.pdf'

        output_path = os.path.join(output_dir, filename)

        # Legacy step "Semantic Scholar openAccessPdf" removed
        # (PubMed projects don't carry an S2 OA URL).
        #
        # Waterfall order rationale: most PubMed candidates carry a PMCID
        # (from ELink), and Europe PMC serves the matching PDFs directly.
        # Putting it first lets the common case skip Unpaywall entirely,
        # which historically returned NCBI PMC landing pages (HTML, not PDF)
        # and burned ~5s per paper on the failed download attempt. Unpaywall
        # is kept as step 3 with stricter URL acceptance (only url_for_pdf,
        # see _get_unpaywall_url).

        # 1. Europe PMC (serves PDFs directly, unlike NCBI PMC which uses JS interstitial)
        pmcid = external_ids.get("PMCID") or external_ids.get("PubMedCentral")
        if pmcid:
            pmcid = str(pmcid)
            if not pmcid.startswith("PMC"):
                pmcid = f"PMC{pmcid}"
            epmc_url = f"{EUROPEPMC_PDF}/{pmcid}?pdf=render"
            pipeline_log(
                f"OA waterfall step=1 europe_pmc pmcid={pmcid}",
                stage="find",
                component="oa_download",
                project_id=project_id,
            )
            if self._download_pdf_to_file(epmc_url, output_path, project_id=project_id):
                pipeline_log(
                    f"OA waterfall success source=europe_pmc file={filename}",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                )
                return os.path.abspath(output_path)

        # 2. ArXiv direct
        arxiv_id = external_ids.get("ArXiv")
        if arxiv_id:
            arxiv_url = f"{ARXIV_PDF_BASE}/{arxiv_id}.pdf"
            pipeline_log(
                f"OA waterfall step=2 arxiv id={arxiv_id}",
                stage="find",
                component="oa_download",
                project_id=project_id,
            )
            if self._download_pdf_to_file(arxiv_url, output_path, project_id=project_id):
                pipeline_log(
                    f"OA waterfall success source=arxiv file={filename}",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                )
                return os.path.abspath(output_path)

        # 3. Unpaywall (prefetched URL preferred; falls back to live lookup
        # only when prefetch didn't run)
        if doi:
            if unpaywall_prefetched:
                # Trust the prefetched result, None included — prefetch
                # already asked Unpaywall, asking again would just waste a
                # round-trip per no-OA paper (×30 candidates = ~30-60s).
                pdf_url = unpaywall_pdf_url
                if pdf_url:
                    pipeline_log(
                        f"OA waterfall step=3 unpaywall (prefetched) doi={doi}",
                        stage="find",
                        component="oa_download",
                        project_id=project_id,
                    )
                else:
                    pipeline_log(
                        f"OA waterfall step=3 unpaywall skipped doi={doi} "
                        f"(prefetch returned no PDF URL)",
                        stage="find",
                        component="oa_download",
                        project_id=project_id,
                    )
            else:
                using_default = self.unpaywall_email == self.DEFAULT_EMAIL
                pipeline_log(
                    f"OA waterfall step=3 unpaywall doi={doi}"
                    + (" (using default email — set UNPAYWALL_EMAIL for better rate limits)" if using_default else ""),
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                )
                pdf_url = self._get_unpaywall_url(doi, project_id=project_id)
            if pdf_url:
                pipeline_log(
                    f"OA unpaywall resolved url={pdf_url[:120]}…",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                )
                if self._download_pdf_to_file(pdf_url, output_path, project_id=project_id):
                    pipeline_log(
                        f"OA waterfall success source=unpaywall file={filename}",
                        stage="find",
                        component="oa_download",
                        project_id=project_id,
                    )
                    return os.path.abspath(output_path)


        pipeline_log(
            f"OA waterfall exhausted all sources file={filename}",
            stage="find",
            component="oa_download",
            project_id=project_id,
            level=logging.WARNING,
        )
        return None

    def _get_unpaywall_url(self, doi: str, project_id: Optional[str] = None) -> Optional[str]:
        """Query Unpaywall for the best open access PDF URL.

        Returns only ``url_for_pdf`` — the field Unpaywall sets when it has
        verified the link points at an actual PDF. The fallback to ``url``
        (landing-page URL, often NCBI PMC HTML with a JS interstitial) was
        removed because the downloader spent ~5s downloading the HTML and
        then rejecting it. Falling through to step 2/3 immediately is
        strictly faster.
        """
        try:
            url = f"{UNPAYWALL_BASE}/{doi}"
            resp = self.session.get(url, params={"email": self.unpaywall_email}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("is_oa") and data.get("best_oa_location"):
                    loc = data["best_oa_location"]
                    return loc.get("url_for_pdf")
                pipeline_log(
                    "unpaywall: response OK but paper not OA or no best_oa_location",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                )
            elif resp.status_code == 404:
                pipeline_log(
                    f"unpaywall: DOI not found (404) doi={doi}",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                )
            else:
                pipeline_log(
                    f"unpaywall: HTTP {resp.status_code}",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                    level=logging.WARNING,
                )
        except Exception as e:
            pipeline_log(
                f"unpaywall error: {e}",
                stage="find",
                component="oa_download",
                project_id=project_id,
                level=logging.WARNING,
            )
        return None

    def _download_pdf_to_file(
        self, url: str, output_path: str, project_id: Optional[str] = None
    ) -> bool:
        """
        Stream-download a URL to file. Validates Content-Type and PDF magic bytes.
        Deletes incomplete/invalid files on failure.
        """
        try:
            resp = self.session.get(url, timeout=60, stream=True, allow_redirects=True)
            if resp.status_code != 200:
                pipeline_log(
                    f"download HTTP {resp.status_code} url={url[:80]}…",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                    level=logging.WARNING,
                )
                return False

            content_type = resp.headers.get("Content-Type", "").lower()
            if content_type and "html" in content_type:
                pipeline_log(
                    f"download rejected: got HTML content-type={content_type} url={url[:80]}…",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                    level=logging.WARNING,
                )
                return False

            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            if not self._validate_pdf(output_path, project_id=project_id):
                return False

            return True

        except requests.exceptions.Timeout:
            pipeline_log(
                f"download timeout url={url[:80]}…",
                stage="find",
                component="oa_download",
                project_id=project_id,
                level=logging.WARNING,
            )
        except requests.exceptions.RequestException as e:
            pipeline_log(
                f"download request error: {e}",
                stage="find",
                component="oa_download",
                project_id=project_id,
                level=logging.WARNING,
            )
        except Exception as e:
            pipeline_log(
                f"download unexpected error: {e}",
                stage="find",
                component="oa_download",
                project_id=project_id,
                level=logging.WARNING,
            )

        # Clean up partial file
        if os.path.exists(output_path):
            os.remove(output_path)
        return False

    def _validate_pdf(self, path: str, project_id: Optional[str] = None) -> bool:
        """Check that the file starts with the %PDF magic bytes."""
        try:
            with open(path, 'rb') as f:
                header = f.read(4)
            if header != PDF_MAGIC:
                pipeline_log(
                    f"PDF validation failed magic={header!r}",
                    stage="find",
                    component="oa_download",
                    project_id=project_id,
                    level=logging.WARNING,
                )
                os.remove(path)
                return False
            return True
        except Exception as e:
            pipeline_log(
                f"PDF validation error: {e}",
                stage="find",
                component="oa_download",
                project_id=project_id,
                level=logging.WARNING,
            )
            if os.path.exists(path):
                os.remove(path)
            return False

    def close(self):
        """Close the underlying requests session."""
        self.session.close()
