"""Download external publisher supplementary DATA files for a paper DOI.

Drug-response tables usually live in publisher supplements (BMC/Springer
``MOESM*``, Elsevier ``mmc*``, Nature/Springer ESM, figshare, GEO suppl),
NOT in the PDF body — so embedded (in-PDF) extraction never captures them.
This resolves the DOI landing page, finds supplement links, and downloads the
data-format files (.xlsx/.csv/...) into the unit dir so qualify can read them.

Heuristic + best-effort: paywalled / bot-protected publishers may yield
nothing; that's logged, never raised.
"""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse, unquote

import requests

from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
# Data formats qualify can read (PDF/DOCX supplements are excluded — not tabular).
_DATA_EXTS = (".xlsx", ".xls", ".csv", ".tsv", ".txt", ".tab", ".gct", ".zip", ".gz")
# URL/text tokens that mark a supplementary-material link.
_SUPP_HINTS = (
    "moesm", "mmc", "/media/", "/esm/", "supplement", "additional",
    "supporting", "/suppl/", "static-content.springer", "els-cdn",
    "figshare", "ndownloader",
)
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>)\]]+")

SUPPLEMENT_MAX_FILES = int(os.getenv("SUPPLEMENT_MAX_FILES", "15"))
SUPPLEMENT_MAX_FILE_BYTES = int(
    os.getenv("SUPPLEMENT_MAX_FILE_BYTES", str(100 * 1024 * 1024))
)
SUPPLEMENT_TIMEOUT = float(os.getenv("SUPPLEMENT_TIMEOUT_SECONDS", "60"))


def extract_doi(text: Optional[str]) -> Optional[str]:
    """Pull the first DOI out of free text (analysis notes / plan), normalized."""
    if not text:
        return None
    m = _DOI_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip(".,;)")


def _is_supplement_link(url: str, text: str = "") -> bool:
    low = url.lower()
    if not low.endswith(_DATA_EXTS):
        return False
    txt = (text or "").lower()
    return (
        any(h in low for h in _SUPP_HINTS)
        or any(h in txt for h in ("supplement", "additional file", "supporting", "table s", "data s"))
    )


def _filename_from(url: str, resp: requests.Response) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    name = unquote(m.group(1)) if m else os.path.basename(urlparse(url).path)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "supplement"
    return name[:180]


def _download_one(session: requests.Session, url: str, dest_dir: str, max_bytes: int) -> Optional[str]:
    try:
        with session.get(url, headers={"User-Agent": _UA}, timeout=SUPPLEMENT_TIMEOUT,
                         stream=True, allow_redirects=True) as r:
            if r.status_code != 200:
                return None
            name = _filename_from(url, r)
            dest = os.path.join(dest_dir, name)
            wrote = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1024 * 256):
                    if not chunk:
                        continue
                    wrote += len(chunk)
                    if wrote > max_bytes:
                        raise ValueError("supplement exceeds size cap")
                    f.write(chunk)
            return dest
    except Exception as e:
        logger.warning("supplement download failed %s: %s", url, e)
        try:
            if 'dest' in dir() and os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
        return None


def download_supplements(
    doi: str,
    dest_dir: str,
    *,
    session: Optional[requests.Session] = None,
    project_id: Optional[str] = None,
    team: Optional[str] = None,
) -> List[str]:
    """Resolve the DOI landing page, find supplement data-file links, download
    them into ``dest_dir``. Returns the written file paths (possibly empty)."""
    if not doi:
        return []
    doi = doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "").lstrip("/")
    sess = session or requests.Session()
    os.makedirs(dest_dir, exist_ok=True)
    try:
        r = sess.get("https://doi.org/" + doi, headers={"User-Agent": _UA},
                     timeout=SUPPLEMENT_TIMEOUT, allow_redirects=True)
    except Exception as e:
        pipeline_log(f"supplement: landing fetch failed for {doi}: {e}",
                     stage="download", team=team, project_id=project_id, level=logging.WARNING)
        return []
    if r.status_code != 200:
        pipeline_log(f"supplement: landing status {r.status_code} for {doi}",
                     stage="download", team=team, project_id=project_id, level=logging.WARNING)
        return []

    base = r.url
    html = r.text
    links: set[str] = set()
    # 1) anchor hrefs (with link text for context)
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = urljoin(base, m.group(1))
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        if _is_supplement_link(href, text):
            links.add(href)
    # 2) raw URLs anywhere (scripts/JSON) on known supplement hosts
    for m in re.finditer(r'https?://[^\s"\'<>]+', html):
        href = m.group(0)
        if _is_supplement_link(href):
            links.add(href)

    if not links:
        pipeline_log(f"supplement: no data supplements found on landing for {doi} ({base})",
                     stage="download", team=team, project_id=project_id)
        return []

    downloaded: List[str] = []
    for url in sorted(links)[:SUPPLEMENT_MAX_FILES]:
        path = _download_one(sess, url, dest_dir, SUPPLEMENT_MAX_FILE_BYTES)
        if path:
            downloaded.append(path)
    pipeline_log(
        f"supplement: downloaded {len(downloaded)}/{len(links)} supplement file(s) for {doi}",
        stage="download", team=team, project_id=project_id,
    )
    return downloaded
