"""Source-specific direct download executors.

These executors intentionally cover conservative, high-confidence cases before
falling back to browser automation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import ssl
import urllib.parse
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from analyze.plan_model import AcquisitionTask, DownloadPlan
from download.acquisition_result import AcquisitionTaskResult
from download.artifact_finalize import finalize_download_run
from download.download_queue import DownloadQueue
from download.artifact_validation import (
    CODE_OR_DOC_FILENAMES,
    infer_coverage_for_artifacts,
    validate_downloaded_file,
)
from download.filename_utils import normalize_download_filename, normalize_downloaded_file_path
from utils.path_utils import get_browseruse_datasets_dir
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

# Oversize-file guard: a usable processed expression/drug matrix is rarely >1GB
# compressed; bigger files are almost always raw signal/single-cell/sequencing.
# Above the cap we HEAD-SAMPLE streamable formats (gzip/text — the head is enough
# for qualify to judge structure) and SKIP non-streamable ones (zip/xlsx/h5 whose
# head can't be read from a byte prefix). Both are configurable.
DOWNLOAD_MAX_FILE_BYTES = int(os.getenv("DOWNLOAD_MAX_FILE_BYTES", str(1024 ** 3)))
DOWNLOAD_HEAD_SAMPLE_BYTES = int(os.getenv("DOWNLOAD_HEAD_SAMPLE_BYTES", str(50 * 1024 * 1024)))
# Streamable: decompress/parse from the start, so a byte-prefix head sample works.
_STREAMABLE_SUFFIXES = (".gz", ".tgz", ".bz2", ".xz", ".txt", ".csv", ".tsv", ".tab", ".gct", ".tar")
# Raw sequencing / array binaries: never a tabular E/R matrix — skip at any size.
_RAW_SKIP_TOKENS = (".fastq", ".fq", ".bam", ".sam", ".sra", ".cram", ".cel")
# qualify recognises this prefix as a truncated HEAD sample (caps verdict at loose).
_HEAD_SAMPLE_PREFIX = "__SAMPLE__"


class _SkipDownload(Exception):
    """Raised to skip a file that is not worth downloading (raw / oversize binary)."""


def _is_raw_skip_name(name_lower: str) -> bool:
    return any(
        name_lower.endswith(tok) or name_lower.endswith(tok + ".gz") or (tok + ".") in name_lower
        for tok in _RAW_SKIP_TOKENS
    )


def _is_streamable_name(name_lower: str) -> bool:
    return name_lower.endswith(_STREAMABLE_SUFFIXES)


GEO_ACCESSION_RE = re.compile(r"\bGSE\d+\b", re.IGNORECASE)
SRA_ACCESSION_RE = re.compile(r"\b(PRJNA\d+|PRJEB\d+|SRP\d+|ERP\d+)\b", re.IGNORECASE)
GDC_PROJECT_RE = re.compile(r"\bTCGA-[A-Z0-9]+\b", re.IGNORECASE)
BIOSTUDIES_ACCESSION_RE = re.compile(r"\bE-[A-Z]+-\d+\b", re.IGNORECASE)
FIGSHARE_ARTICLE_ID_RE = re.compile(r"/articles/(?:.*/)?(\d+)(?:/|$)")
FIGSHARE_DOI_ID_RE = re.compile(r"(?:^|[./])figshare\.(\d+)(?:\.v\d+)?(?:$|[/?#])", re.IGNORECASE)
CBIOPORTAL_STUDY_ID_RE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+_\d{4}\b", re.IGNORECASE)
DIRECT_FILE_HOST_TOKENS = (
    "figshare.com",
    "zenodo.org",
    "osf.io",
    "osfstorage",
    "springernature",
    "nature.com",
    "sciencedirect.com",
    "cell.com",
    "mdpi.com",
    "wiley.com",
    "plos.org",
    "rsc.org",
    "acs.org",
    "ashpublications.org",
    "aspetjournals.org",
    "pmc.ncbi.nlm.nih.gov",
)

SUPPLEMENTARY_URL_TOKENS = (
    "downloadsupplement",
    "supplementary",
    "supplemental",
    "source-data",
    "sourcedata",
    "source_data",
    "suppdata",
    "article_deployments",
    "attachment",
    "downloadfile",
    "downloadattachment",
    "/suppl/",
    "/supplement/",
    "/cms/attachment/",
    "/mmc",
)

DATA_FILE_SUFFIXES = (
    ".csv",
    ".tsv",
    ".txt",
    ".json",
    ".xlsx",
    ".xls",
    ".parquet",
    ".feather",
    ".h5",
    ".h5ad",
    ".rds",
    ".rda",
    ".gz",
    ".zip",
    ".tar",
    ".tgz",
    ".bz2",
    ".xz",
)

GITHUB_RELEVANT_DIR_NAMES = (
    "data",
    "dataset",
    "datasets",
    "processed",
    "affps",
    "tables",
    "input",
    "inputs",
    "csv",
)


class _HrefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() not in {"a", "link", "meta", "script"}:
            return
        attrs_dict = {name.lower(): value for name, value in attrs if name and value}
        rel = str(attrs_dict.get("rel") or "").lower()
        if tag.lower() == "link" and "manifest" in rel.split():
            return
        for name, value in attrs:
            name_lower = name.lower()
            if name_lower in {"href", "src", "content"} and value:
                self.hrefs.append(value)
            elif name_lower.startswith("data-") and value:
                self.hrefs.append(value)
GITHUB_DATA_SUFFIXES = DATA_FILE_SUFFIXES

PROCESSED_FILE_TOKENS = (
    "processed",
    "matrix",
    "counts",
    "count",
    "fpkm",
    "tpm",
    "genes",
    "barcodes",
    "metadata",
    "sdrf",
    "idf",
)

RAW_SEQUENCE_FILE_TOKENS = (
    ".fastq",
    ".fq",
    ".bam",
    ".cram",
)

TCGA_PROJECT_ALIASES = {
    "ACC": "TCGA-ACC",
    "BLCA": "TCGA-BLCA",
    "BRCA": "TCGA-BRCA",
    "CESC": "TCGA-CESC",
    "CHOL": "TCGA-CHOL",
    "COAD": "TCGA-COAD",
    "DLBC": "TCGA-DLBC",
    "ESCA": "TCGA-ESCA",
    "GBM": "TCGA-GBM",
    "HNSC": "TCGA-HNSC",
    "HNSCC": "TCGA-HNSC",
    "KICH": "TCGA-KICH",
    "KIRC": "TCGA-KIRC",
    "KIRP": "TCGA-KIRP",
    "LAML": "TCGA-LAML",
    "LGG": "TCGA-LGG",
    "LIHC": "TCGA-LIHC",
    "LUAD": "TCGA-LUAD",
    "LUSC": "TCGA-LUSC",
    "MESO": "TCGA-MESO",
    "OV": "TCGA-OV",
    "PAAD": "TCGA-PAAD",
    "PCPG": "TCGA-PCPG",
    "PRAD": "TCGA-PRAD",
    "READ": "TCGA-READ",
    "SARC": "TCGA-SARC",
    "SKCM": "TCGA-SKCM",
    "STAD": "TCGA-STAD",
    "TGCT": "TCGA-TGCT",
    "THCA": "TCGA-THCA",
    "THYM": "TCGA-THYM",
    "UCEC": "TCGA-UCEC",
    "UCS": "TCGA-UCS",
    "UVM": "TCGA-UVM",
}


class DirectDownloadTeam:
    """Download high-confidence public data URLs without BrowserUse."""

    def __init__(
        self,
        team_name: str = "DirectDownloadTeam",
        project_id: str = None,
        ui_callback=None,
    ):
        self.team_name = team_name
        self.project_id = project_id
        self.ui_callback = ui_callback
        self.downloads_dir = Path(get_browseruse_datasets_dir())
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        from storage import get_storage

        self.storage = get_storage()

    @staticmethod
    def _paper_name(plan_path: str) -> str:
        return Path(plan_path).stem.replace(".plan", "")

    @staticmethod
    def _geo_series_dir(accession: str) -> str:
        upper = accession.upper()
        return f"{upper[:-3]}nnn"

    @classmethod
    def _geo_matrix_url(cls, accession: str) -> str:
        upper = accession.upper()
        return (
            "https://ftp.ncbi.nlm.nih.gov/geo/series/"
            f"{cls._geo_series_dir(upper)}/{upper}/matrix/{upper}_series_matrix.txt.gz"
        )

    @classmethod
    def _geo_base_url(cls, accession: str) -> str:
        upper = accession.upper()
        return f"https://ftp.ncbi.nlm.nih.gov/geo/series/{cls._geo_series_dir(upper)}/{upper}/"

    @classmethod
    def _geo_matrix_urls(cls, accession: str) -> list[str]:
        upper = accession.upper()
        matrix_base = f"{cls._geo_base_url(upper)}matrix/"
        try:
            hrefs = cls._fetch_page_hrefs(matrix_base)
        except Exception:
            return [cls._geo_matrix_url(upper)]

        urls = []
        for href in hrefs:
            filename = Path(urllib.parse.unquote(href)).name
            lower = filename.lower()
            if (
                filename.startswith(upper)
                and "series_matrix" in lower
                and lower.endswith(".txt.gz")
            ):
                urls.append(urllib.parse.urljoin(matrix_base, href))
        return list(dict.fromkeys(urls)) or [cls._geo_matrix_url(upper)]

    @staticmethod
    def _ena_run_table_url(accession: str) -> str:
        fields = ",".join(
            [
                "run_accession",
                "study_accession",
                "sample_accession",
                "experiment_accession",
                "library_strategy",
                "library_layout",
                "fastq_ftp",
                "submitted_ftp",
            ]
        )
        return (
            "https://www.ebi.ac.uk/ena/portal/api/filereport"
            f"?accession={accession.upper()}&result=read_run&fields={fields}&format=tsv&download=true"
        )

    @staticmethod
    def _gdc_project_metadata_url(project_id: str) -> str:
        return f"https://api.gdc.cancer.gov/projects/{project_id.upper()}"

    @staticmethod
    def _gdc_project_files_tsv_url(project_id: str) -> str:
        filters = {
            "op": "in",
            "content": {
                "field": "cases.project.project_id",
                "value": [project_id.upper()],
            },
        }
        params = {
            "filters": json.dumps(filters),
            "fields": (
                "file_id,file_name,data_type,data_format,experimental_strategy"
            ),
            "format": "TSV",
            "size": "2000",
        }
        return f"https://api.gdc.cancer.gov/files?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _step_urls(task: AcquisitionTask) -> list[str]:
        urls = []
        for step in task.steps:
            if step.url:
                urls.append(step.url)
        return urls

    @staticmethod
    def _task_text(plan: DownloadPlan, task: AcquisitionTask) -> str:
        parts = [
            plan.repository or "",
            plan.notes or "",
            " ".join(plan.desired_outputs or []),
            task.title or "",
            task.notes or "",
            " ".join(task.desired_outputs or []),
        ]
        for step in task.steps:
            parts.extend(
                [
                    step.description or "",
                    step.url or "",
                    " ".join(step.target_files or []),
                ]
            )
        return " ".join(parts)

    @classmethod
    def _extract_geo_accessions(cls, plan: DownloadPlan, task: AcquisitionTask) -> list[str]:
        text = cls._task_text(plan, task)
        return list(dict.fromkeys(match.group(0).upper() for match in GEO_ACCESSION_RE.finditer(text)))

    @classmethod
    def _extract_sra_accessions(cls, plan: DownloadPlan, task: AcquisitionTask) -> list[str]:
        text = cls._task_text(plan, task)
        return list(dict.fromkeys(match.group(0).upper() for match in SRA_ACCESSION_RE.finditer(text)))

    @classmethod
    def _extract_gdc_projects(cls, plan: DownloadPlan, task: AcquisitionTask) -> list[str]:
        text = cls._task_text(plan, task)
        projects = [match.group(0).upper() for match in GDC_PROJECT_RE.finditer(text)]
        lower_text = text.lower()
        has_tcga_context = any(
            token in lower_text
            for token in ("tcga", "gdc", "portal.gdc.cancer.gov", "cancer genome atlas")
        )
        if has_tcga_context:
            for alias, project_id in TCGA_PROJECT_ALIASES.items():
                if re.search(rf"\b{re.escape(alias)}\b", text, flags=re.IGNORECASE):
                    projects.append(project_id)
        return list(dict.fromkeys(projects))

    @classmethod
    def _extract_biostudies_accessions(cls, plan: DownloadPlan, task: AcquisitionTask) -> list[str]:
        text = cls._task_text(plan, task)
        return list(
            dict.fromkeys(match.group(0).upper() for match in BIOSTUDIES_ACCESSION_RE.finditer(text))
        )

    @staticmethod
    def _github_raw_url(url: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path
        name = Path(urllib.parse.unquote(path)).name.lower()
        if name in CODE_OR_DOC_FILENAMES:
            return None
        if host == "raw.githubusercontent.com" and path.lower().endswith(GITHUB_DATA_SUFFIXES):
            return url
        if host != "github.com":
            return None

        parts = [part for part in path.split("/") if part]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, _, branch = parts[:4]
            file_path = "/".join(parts[4:])
            if (
                Path(urllib.parse.unquote(file_path)).name.lower() not in CODE_OR_DOC_FILENAMES
                and file_path.lower().endswith(GITHUB_DATA_SUFFIXES)
            ):
                return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
        return None

    @staticmethod
    def _github_media_url(url: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        path_parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        if host == "media.githubusercontent.com":
            return url
        if host == "raw.githubusercontent.com" and len(path_parts) >= 4:
            owner, repo, branch = path_parts[:3]
            file_path = "/".join(urllib.parse.quote(part) for part in path_parts[3:])
            return f"https://media.githubusercontent.com/media/{owner}/{repo}/{branch}/{file_path}"
        if host == "github.com" and len(path_parts) >= 5 and path_parts[2] == "blob":
            owner, repo, _, branch = path_parts[:4]
            file_path = "/".join(urllib.parse.quote(part) for part in path_parts[4:])
            return f"https://media.githubusercontent.com/media/{owner}/{repo}/{branch}/{file_path}"
        return None

    @classmethod
    def _extract_github_raw_urls(cls, task: AcquisitionTask) -> list[str]:
        urls = []
        for url in cls._step_urls(task):
            raw_url = cls._github_raw_url(url)
            if raw_url:
                urls.append(raw_url)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _direct_file_url(url: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        path = urllib.parse.unquote(parsed.path).lower()
        if not any(token in host for token in DIRECT_FILE_HOST_TOKENS):
            return None
        if path.endswith(DATA_FILE_SUFFIXES):
            return url
        query = urllib.parse.parse_qs(parsed.query)
        for values in query.values():
            for value in values:
                if urllib.parse.unquote(value).lower().endswith(DATA_FILE_SUFFIXES):
                    return url
        return None

    @staticmethod
    def _generic_data_file_url(url: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url)
        path = urllib.parse.unquote(parsed.path).lower()
        filename = Path(path).name
        if filename in CODE_OR_DOC_FILENAMES:
            return None
        if filename in {"manifest.json", "tdmrep-policy.json"}:
            return None
        if path.endswith(DATA_FILE_SUFFIXES):
            return url
        query = urllib.parse.parse_qs(parsed.query)
        for values in query.values():
            for value in values:
                if urllib.parse.unquote(value).lower().endswith(DATA_FILE_SUFFIXES):
                    return url
        return None

    @staticmethod
    def _supplementary_candidate_url(url: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if not any(token in host for token in DIRECT_FILE_HOST_TOKENS):
            return None
        joined = urllib.parse.unquote(f"{parsed.path}?{parsed.query}").lower()
        filename = Path(urllib.parse.unquote(parsed.path).lower()).name
        if filename in CODE_OR_DOC_FILENAMES or filename in {"manifest.json", "tdmrep-policy.json"}:
            return None
        if filename.endswith((".html", ".htm", ".pdf")):
            return None
        if any(token in joined for token in SUPPLEMENTARY_URL_TOKENS):
            return url
        return None

    @staticmethod
    def _is_publisher_or_repository_page(url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        return bool(host)

    @staticmethod
    def _fetch_page_hrefs(url: str, timeout_seconds: int = 30) -> list[str]:
        request = urllib.request.Request(url, headers={"User-Agent": "AI4MED-AutoGen/1.0"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                return []
            body = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        parser = _HrefParser()
        parser.feed(body)
        return parser.hrefs

    @classmethod
    def _discover_data_file_urls(cls, page_url: str) -> list[str]:
        if not cls._is_publisher_or_repository_page(page_url):
            return []
        discovered: list[str] = []
        for href in cls._fetch_page_hrefs(page_url):
            absolute = urllib.parse.urljoin(page_url, href)
            raw_url = cls._github_raw_url(absolute)
            if raw_url:
                discovered.append(raw_url)
            elif cls._generic_data_file_url(absolute):
                discovered.append(absolute)
            elif cls._supplementary_candidate_url(absolute):
                discovered.append(absolute)
        return list(dict.fromkeys(discovered))

    @staticmethod
    def _fetch_json(
        url: str,
        timeout_seconds: int = 30,
        *,
        accept: str = "application/vnd.github+json",
        max_bytes: int = 8 * 1024 * 1024,
    ) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "User-Agent": "AI4MED-AutoGen/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read(max_bytes).decode("utf-8", errors="replace"))

    @classmethod
    def _fetch_json_with_retries(
        cls,
        url: str,
        *,
        timeout_seconds: int = 60,
        accept: str = "application/json",
        attempts: int = 3,
    ) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return cls._fetch_json(
                    url,
                    timeout_seconds=timeout_seconds,
                    accept=accept,
                )
            except Exception as e:
                last_error = e
                if attempt < attempts:
                    continue
        assert last_error is not None
        raise last_error

    @classmethod
    def _biostudies_file_urls(cls, accession: str) -> list[str]:
        data = cls._fetch_json_with_retries(
            f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{accession}",
            timeout_seconds=90,
            accept="application/json",
        )
        files: list[dict[str, Any]] = []

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            for file_group in value.get("files", []) or []:
                if isinstance(file_group, list):
                    for file_item in file_group:
                        if isinstance(file_item, dict):
                            files.append(file_item)
                elif isinstance(file_group, dict):
                    files.append(file_group)
            walk(value.get("subsections", []))

        walk(data.get("section", {}) if isinstance(data, dict) else {})
        selected: list[str] = []
        fallback: list[str] = []
        for item in files:
            path = str(item.get("path") or "")
            if not path:
                continue
            lower_path = path.lower()
            if any(token in lower_path for token in RAW_SEQUENCE_FILE_TOKENS):
                continue
            if not lower_path.endswith(DATA_FILE_SUFFIXES):
                continue
            url = f"https://www.ebi.ac.uk/biostudies/files/{accession}/{urllib.parse.quote(path)}"
            fallback.append(url)
            attributes = item.get("attributes") or []
            attr_text = " ".join(
                str(attr.get("value") or attr.get("name") or "")
                for attr in attributes
                if isinstance(attr, dict)
            ).lower()
            if any(token in f"{lower_path} {attr_text}" for token in PROCESSED_FILE_TOKENS):
                selected.append(url)
        return list(dict.fromkeys(selected or fallback))

    @staticmethod
    def _osf_node_id(url: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower() != "osf.io":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        return parts[0] if parts else None

    @staticmethod
    def _figshare_article_id(url: str) -> Optional[str]:
        parsed = urllib.parse.urlparse(url)
        if "figshare.com" in parsed.netloc.lower():
            match = FIGSHARE_ARTICLE_ID_RE.search(parsed.path)
            return match.group(1) if match else None
        if parsed.netloc.lower() == "doi.org":
            match = FIGSHARE_DOI_ID_RE.search(urllib.parse.unquote(parsed.path))
            return match.group(1) if match else None
        match = FIGSHARE_DOI_ID_RE.search(url)
        return match.group(1) if match else None

    @classmethod
    def _figshare_file_urls(cls, url: str) -> list[str]:
        article_id = cls._figshare_article_id(url)
        if not article_id:
            return []
        data = cls._fetch_json_with_retries(
            f"https://api.figshare.com/v2/articles/{article_id}",
            timeout_seconds=60,
            accept="application/json",
        )
        urls: list[str] = []
        for item in data.get("files", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            download_url = item.get("download_url")
            if not download_url or not name.lower().endswith(DATA_FILE_SUFFIXES):
                continue
            urls.append(str(download_url))
        return list(dict.fromkeys(urls))

    @classmethod
    def _osf_file_urls(cls, url: str, max_files: int = 100) -> list[str]:
        node_id = cls._osf_node_id(url)
        if not node_id:
            return []
        discovered: list[str] = []
        queue = [f"https://api.osf.io/v2/nodes/{node_id}/files/osfstorage/"]
        seen: set[str] = set()
        while queue and len(discovered) < max_files:
            api_url = queue.pop(0)
            if api_url in seen:
                continue
            seen.add(api_url)
            data = cls._fetch_json_with_retries(
                api_url,
                timeout_seconds=60,
                accept="application/vnd.api+json",
            )
            for item in data.get("data", []) if isinstance(data, dict) else []:
                if not isinstance(item, dict):
                    continue
                attrs = item.get("attributes") or {}
                links = item.get("links") or {}
                kind = attrs.get("kind")
                name = str(attrs.get("name") or "")
                if kind == "file":
                    download_url = links.get("download")
                    if download_url and name.lower().endswith(DATA_FILE_SUFFIXES):
                        discovered.append(str(download_url))
                elif kind == "folder":
                    rel = item.get("relationships") or {}
                    files_link = ((rel.get("files") or {}).get("links") or {}).get("related")
                    if isinstance(files_link, dict):
                        files_link = files_link.get("href")
                    if files_link:
                        queue.append(str(files_link))
            next_url = ((data.get("links") or {}).get("next") if isinstance(data, dict) else None)
            if next_url:
                queue.append(str(next_url))
        return list(dict.fromkeys(discovered))

    @classmethod
    def _extract_cbioportal_study_ids(cls, plan: DownloadPlan, task: AcquisitionTask) -> list[str]:
        text = cls._task_text(plan, task)
        if "cbioportal" not in text.lower():
            return []
        return list(
            dict.fromkeys(match.group(0).lower() for match in CBIOPORTAL_STUDY_ID_RE.finditer(text))
        )

    @staticmethod
    def _cbioportal_datahub_urls(study_id: str) -> list[str]:
        base = f"https://raw.githubusercontent.com/cBioPortal/datahub/master/public/{study_id}"
        return [
            f"{base}/data_clinical_sample.txt",
            f"{base}/data_clinical_patient.txt",
            f"{base}/data_mutations.txt",
            f"{base}/data_CNA.txt",
            f"{base}/data_mrna_seq_v2_rsem.txt",
            f"{base}/data_mrna_seq_fpkm.txt",
            f"{base}/data_log2_cna.txt",
        ]

    @staticmethod
    def _github_repo_parts(url: str) -> Optional[tuple[str, str, str, Optional[str]]]:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower() != "github.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return None
        owner, repo = parts[:2]
        branch = "master"
        subpath: Optional[str] = None
        if len(parts) >= 5 and parts[2] in {"tree", "blob"}:
            branch = parts[3]
            subpath = "/".join(parts[4:])
        return owner, repo, branch, subpath

    @classmethod
    def _github_search_repository_urls(cls, repo_name: str) -> list[str]:
        query = urllib.parse.quote(f"{repo_name} in:name")
        data = cls._fetch_json(f"https://api.github.com/search/repositories?q={query}&per_page=5")
        urls: list[str] = []
        for item in data.get("items", []) if isinstance(data, dict) else []:
            full_name = str(item.get("full_name") or "")
            if full_name.lower().endswith(f"/{repo_name.lower()}"):
                html_url = item.get("html_url")
                if html_url:
                    urls.append(str(html_url))
        return list(dict.fromkeys(urls))

    @classmethod
    def _github_default_branch(cls, owner: str, repo: str) -> Optional[str]:
        data = cls._fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
        if isinstance(data, dict) and data.get("default_branch"):
            return str(data["default_branch"])
        return None

    @classmethod
    def _github_contents_urls(
        cls,
        *,
        owner: str,
        repo: str,
        branch: str,
        subpath: Optional[str],
        max_depth: int = 2,
        max_files: int = 100,
    ) -> list[str]:
        discovered: list[str] = []

        def walk(path: Optional[str], depth: int, force_descend: bool = False) -> None:
            if len(discovered) >= max_files or depth > max_depth:
                return
            quoted_path = "/".join(urllib.parse.quote(part) for part in (path or "").split("/") if part)
            api_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
            if quoted_path:
                api_url += f"/{quoted_path}"
            api_url += f"?ref={urllib.parse.quote(branch)}"
            data = cls._fetch_json(api_url)
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                if not isinstance(entry, dict) or len(discovered) >= max_files:
                    continue
                entry_type = entry.get("type")
                name = str(entry.get("name") or "")
                entry_path = str(entry.get("path") or "")
                if entry_type == "file":
                    download_url = entry.get("download_url")
                    if (
                        download_url
                        and name.lower() not in CODE_OR_DOC_FILENAMES
                        and name.lower().endswith(DATA_FILE_SUFFIXES)
                    ):
                        discovered.append(str(download_url))
                elif entry_type == "dir":
                    lower_path = entry_path.lower()
                    relevant = force_descend or any(token in lower_path for token in GITHUB_RELEVANT_DIR_NAMES)
                    if relevant or (path is None and depth == 0):
                        walk(entry_path, depth + 1, force_descend=relevant)

        walk(subpath, 0, force_descend=bool(subpath))
        return list(dict.fromkeys(discovered))

    @classmethod
    def _discover_github_data_urls(cls, page_url: str) -> list[str]:
        raw_url = cls._github_raw_url(page_url)
        if raw_url:
            return [raw_url]

        parts = cls._github_repo_parts(page_url)
        if parts is None:
            return []
        owner, repo, branch, subpath = parts

        branches = [branch]
        if branch == "master":
            try:
                default_branch = cls._github_default_branch(owner, repo)
                if default_branch and default_branch not in branches:
                    branches.append(default_branch)
            except Exception:
                pass
        for candidate_branch in branches:
            try:
                urls = cls._github_contents_urls(
                    owner=owner,
                    repo=repo,
                    branch=candidate_branch,
                    subpath=subpath,
                )
                if urls:
                    return urls
            except Exception:
                continue

        discovered: list[str] = []
        for repo_url in cls._github_search_repository_urls(repo):
            searched_parts = cls._github_repo_parts(repo_url)
            if searched_parts is None:
                continue
            searched_owner, searched_repo, searched_branch, _ = searched_parts
            searched_branches = [searched_branch]
            if searched_branch == "master":
                try:
                    default_branch = cls._github_default_branch(searched_owner, searched_repo)
                    if default_branch and default_branch not in searched_branches:
                        searched_branches.append(default_branch)
                except Exception:
                    pass
            try:
                for candidate_branch in searched_branches:
                    discovered.extend(
                        cls._github_contents_urls(
                            owner=searched_owner,
                            repo=searched_repo,
                            branch=candidate_branch,
                            subpath=subpath,
                        )
                    )
            except Exception:
                continue
        return list(dict.fromkeys(discovered))

    async def _discover_github_data_urls_for_task(self, task: AcquisitionTask) -> list[str]:
        discovered: list[str] = []
        for url in self._step_urls(task):
            try:
                discovered.extend(await asyncio.to_thread(self._discover_github_data_urls, url))
            except Exception as e:
                pipeline_log(
                    f"github data discovery failed url={url!r}: {e}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
        return list(dict.fromkeys(discovered))

    async def _discover_data_file_urls_for_task(self, task: AcquisitionTask) -> list[str]:
        discovered: list[str] = []
        for url in self._step_urls(task):
            if urllib.parse.urlparse(url).netloc.lower() == "github.com":
                continue
            try:
                osf_urls = await asyncio.to_thread(self._osf_file_urls, url)
                discovered.extend(osf_urls)
                figshare_urls = await asyncio.to_thread(self._figshare_file_urls, url)
                discovered.extend(figshare_urls)
                page_urls = await asyncio.to_thread(self._discover_data_file_urls, url)
                discovered.extend(page_urls)
            except Exception as e:
                pipeline_log(
                    f"supplementary link discovery failed url={url!r}: {e}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
        return list(dict.fromkeys(discovered))

    @classmethod
    def _extract_generic_direct_data_urls(cls, task: AcquisitionTask) -> list[str]:
        urls: list[str] = []
        for step_url in cls._step_urls(task):
            host = urllib.parse.urlparse(step_url).netloc.lower()
            if host in {"github.com", "raw.githubusercontent.com", "media.githubusercontent.com"}:
                continue
            generic_url = cls._generic_data_file_url(step_url)
            if generic_url:
                urls.append(generic_url)
        return list(
            dict.fromkeys(urls)
        )

    @classmethod
    def _filter_urls_by_task_relevance(
        cls,
        urls: list[str],
        *,
        plan: DownloadPlan,
        task: AcquisitionTask,
    ) -> list[str]:
        if len(urls) <= 1:
            return urls

        parts = [
            plan.repository or "",
            plan.notes or "",
            " ".join(plan.desired_outputs or []),
            task.title or "",
            task.notes or "",
            " ".join(task.desired_outputs or []),
        ]
        for step in task.steps:
            parts.extend(
                [
                    step.description or "",
                    " ".join(step.target_files or []),
                ]
            )
        text = " ".join(parts).lower()
        tokens = []
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", text):
            if token in {
                "data",
                "dataset",
                "datasets",
                "download",
                "repository",
                "public",
                "from",
                "with",
                "files",
                "code",
                "study",
                "paper",
                "cancer",
                "omics",
                "mass",
                "spectrometry",
                "clinical",
                "program",
                "databank",
                "proteomics",
                "processed",
                "source",
            }:
                continue
            tokens.append(token)
        tokens = list(dict.fromkeys(tokens))
        if not tokens:
            return urls

        matched = []
        for url in urls:
            parsed = urllib.parse.urlparse(url)
            filename = urllib.parse.unquote(Path(parsed.path).name)
            filename_words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", filename)
            url_tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", filename_words.lower()))
            if any(token in url_tokens for token in tokens):
                matched.append(url)
        return matched or urls

    @classmethod
    def _extract_direct_file_urls(cls, task: AcquisitionTask) -> list[str]:
        return list(
            dict.fromkeys(
                url for url in (cls._direct_file_url(step_url) for step_url in cls._step_urls(task)) if url
            )
        )

    @staticmethod
    def _target_path(destination_dir: Path, url: str) -> Path:
        parsed = urllib.parse.urlparse(url)
        raw_filename = Path(urllib.parse.unquote(parsed.path)).name or "downloaded_dataset"
        filename = normalize_download_filename(raw_filename, url=url)
        target = destination_dir / filename
        if not target.exists():
            return target
        stem = target.stem
        suffix = target.suffix
        counter = 1
        while True:
            candidate = destination_dir / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _preferred_target_path(destination_dir: Path, url: str) -> Path:
        parsed = urllib.parse.urlparse(url)
        raw_filename = Path(urllib.parse.unquote(parsed.path)).name or "downloaded_dataset"
        filename = normalize_download_filename(raw_filename, url=url)
        return destination_dir / filename

    @staticmethod
    def _filename_from_content_disposition(content_disposition: str) -> Optional[str]:
        if not content_disposition:
            return None
        filename_star = re.search(
            r"filename\*\s*=\s*(?:UTF-8''|utf-8'')?([^;]+)",
            content_disposition,
            flags=re.IGNORECASE,
        )
        if filename_star:
            value = filename_star.group(1).strip().strip('"')
            name = Path(urllib.parse.unquote(value)).name
            if name:
                return name
        filename = re.search(r'filename\s*=\s*"([^"]+)"', content_disposition, flags=re.IGNORECASE)
        if filename:
            name = Path(filename.group(1)).name
            if name:
                return name
        filename = re.search(r"filename\s*=\s*([^;]+)", content_disposition, flags=re.IGNORECASE)
        if filename:
            name = Path(filename.group(1).strip().strip('"')).name
            if name:
                return name
        return None

    @classmethod
    def _preferred_target_path_from_headers(
        cls,
        destination_dir: Path,
        url: str,
        headers: Any,
    ) -> Path:
        content_disposition = ""
        content_type = ""
        if headers is not None:
            content_disposition = headers.get("content-disposition", "") or headers.get(
                "Content-Disposition", ""
            )
            content_type = headers.get("content-type", "") or headers.get("Content-Type", "")
        filename = cls._filename_from_content_disposition(str(content_disposition))
        if filename:
            normalized = normalize_download_filename(filename, url=url, content_type=str(content_type))
            return destination_dir / normalized
        return cls._preferred_target_path(destination_dir, url)

    @staticmethod
    def _file_digest(path: Path, chunk_size: int = 1024 * 1024) -> Optional[str]:
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
        except OSError:
            return None
        return hasher.hexdigest()

    @classmethod
    def _existing_duplicate_path(cls, destination_dir: Path, candidate_path: Path) -> Optional[Path]:
        candidate_digest = cls._file_digest(candidate_path)
        if not candidate_digest:
            return None
        try:
            candidate_size = candidate_path.stat().st_size
        except OSError:
            return None
        for existing_path in sorted(destination_dir.iterdir()):
            if existing_path == candidate_path or not existing_path.is_file():
                continue
            try:
                if existing_path.stat().st_size != candidate_size:
                    continue
            except OSError:
                continue
            if cls._file_digest(existing_path) == candidate_digest:
                return existing_path
        return None

    @staticmethod
    def _unique_path_for_existing_target(target: Path) -> Path:
        if not target.exists():
            return target
        stem = target.stem
        suffix = target.suffix
        counter = 1
        while True:
            candidate = target.parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _download_url_once(url: str, destination_dir: Path, timeout_seconds: int = 90) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "AI4MED-AutoGen/1.0"})
        try:
            response_ctx = urllib.request.urlopen(request, timeout=timeout_seconds)
        except ssl.SSLError:
            response_ctx = urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
                context=ssl._create_unverified_context(),
            )
        except urllib.error.URLError as e:
            if not isinstance(getattr(e, "reason", None), ssl.SSLError):
                raise
            response_ctx = urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
                context=ssl._create_unverified_context(),
            )
        with response_ctx as response:
            preferred_target = DirectDownloadTeam._preferred_target_path_from_headers(
                destination_dir,
                url,
                response.headers,
            )
            # Oversize / raw-file guard (see DOWNLOAD_MAX_FILE_BYTES above).
            name_lower = preferred_target.name.lower()
            if _is_raw_skip_name(name_lower):
                raise _SkipDownload(f"raw non-tabular file skipped: {preferred_target.name}")
            try:
                content_length = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                content_length = 0
            streamable = _is_streamable_name(name_lower)
            head_only = False
            byte_limit: Optional[int] = None
            if content_length > DOWNLOAD_MAX_FILE_BYTES:
                if not streamable:
                    raise _SkipDownload(
                        f"oversize non-streamable file skipped "
                        f"({content_length / 1e9:.1f}GB > "
                        f"{DOWNLOAD_MAX_FILE_BYTES / 1e9:.1f}GB): {preferred_target.name}"
                    )
                head_only = True
                byte_limit = DOWNLOAD_HEAD_SAMPLE_BYTES
            # Head samples carry the marker prefix so qualify treats them as
            # truncated (cap at loose) rather than a complete matrix.
            if head_only:
                preferred_target = preferred_target.with_name(
                    _HEAD_SAMPLE_PREFIX + preferred_target.name
                )

            target = preferred_target
            download_target = target
            used_temporary_target = preferred_target.exists()
            if used_temporary_target:
                download_target = DirectDownloadTeam._unique_path_for_existing_target(
                    preferred_target.with_suffix(f"{preferred_target.suffix}.download")
                )
            written = 0
            with download_target.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    # Running cap: covers servers that omit Content-Length
                    # (chunked / gzip transfer-encoding). Once we cross the max
                    # mid-stream, head-sample streamable files and abort others.
                    if byte_limit is None and not head_only and (written + len(chunk)) > DOWNLOAD_MAX_FILE_BYTES:
                        if not streamable:
                            handle.close()
                            download_target.unlink(missing_ok=True)
                            raise _SkipDownload(
                                f"oversize non-streamable file skipped (>"
                                f"{DOWNLOAD_MAX_FILE_BYTES / 1e9:.1f}GB, no Content-Length): "
                                f"{preferred_target.name}"
                            )
                        head_only = True
                        byte_limit = DOWNLOAD_HEAD_SAMPLE_BYTES
                    if byte_limit is not None and (written + len(chunk)) >= byte_limit:
                        handle.write(chunk[: max(0, byte_limit - written)])
                        written = byte_limit
                        break
                    handle.write(chunk)
                    written += len(chunk)
            if head_only:
                pipeline_log(
                    f"oversize streamable file head-sampled "
                    f"({written / 1e6:.0f}MB of >{DOWNLOAD_MAX_FILE_BYTES / 1e9:.1f}GB): "
                    f"{download_target.name}",
                    stage="download",
                    team="DirectDownloadTeam",
                )
                # A mid-stream rename for head-sampled files whose oversize was
                # only discovered after writing started (Content-Length absent).
                if not download_target.name.startswith(_HEAD_SAMPLE_PREFIX) and \
                        ".download" not in download_target.name:
                    renamed = download_target.with_name(_HEAD_SAMPLE_PREFIX + download_target.name)
                    download_target.replace(renamed)
                    download_target = renamed
        download_target = normalize_downloaded_file_path(download_target)
        duplicate_path = DirectDownloadTeam._existing_duplicate_path(destination_dir, download_target)
        if duplicate_path is not None:
            download_target.unlink(missing_ok=True)
            return duplicate_path
        if used_temporary_target and download_target.suffix == ".download":
            target = DirectDownloadTeam._unique_path_for_existing_target(preferred_target)
            download_target.replace(target)
        elif used_temporary_target:
            target = DirectDownloadTeam._unique_path_for_existing_target(download_target)
            if target != download_target:
                download_target.replace(target)
        else:
            target = download_target
        return target

    @staticmethod
    def _url_filename(url: str) -> str:
        """Best-effort target filename for a URL — honouring the GEO/NCBI
        ``?file=`` (and ``filename=``) query form whose path basename is just
        'download', not the real file."""
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ("file", "filename"):
            if qs.get(key):
                return Path(urllib.parse.unquote(qs[key][0])).name.lower()
        return Path(urllib.parse.unquote(parsed.path)).name.lower()

    @classmethod
    def _dedup_urls_by_filename(cls, urls: list[str]) -> list[str]:
        """Collapse URLs that resolve to the same filename (e.g. GEO returns both
        an ftp:// and an https:// URL for one file) so we don't download it twice.
        Prefer non-ftp (https supports Range, needed for head-sampling). URLs
        whose filename can't be determined are all kept."""
        chosen: dict[str, str] = {}
        order: list[str] = []
        no_name: list[str] = []
        for u in urls:
            name = cls._url_filename(u)
            if not name:
                no_name.append(u)
                continue
            if name not in chosen:
                chosen[name] = u
                order.append(name)
            elif chosen[name].lower().startswith("ftp") and not u.lower().startswith("ftp"):
                chosen[name] = u  # upgrade ftp → https for the same file
        return [chosen[n] for n in order] + no_name

    @staticmethod
    def _download_url(url: str, destination_dir: Path, timeout_seconds: int = 90) -> Path:
        target = DirectDownloadTeam._download_url_once(url, destination_dir, timeout_seconds)
        validation = validate_downloaded_file(target, plan=None)
        if validation.reason != "git_lfs_pointer":
            return target

        media_url = DirectDownloadTeam._github_media_url(url)
        if not media_url or media_url == url:
            return target

        pipeline_log(
            f"git lfs pointer detected; retrying GitHub media URL for {target.name}",
            stage="download",
            team="DirectDownloadTeam",
        )
        target.unlink(missing_ok=True)
        return DirectDownloadTeam._download_url_once(media_url, destination_dir, timeout_seconds)

    async def _download_urls(self, urls: list[str], destination_dir: Path) -> list[Path]:
        downloaded: list[Path] = []
        for url in urls:
            try:
                path = await asyncio.to_thread(self._download_url, url, destination_dir)
                downloaded.append(path)
                pipeline_log(
                    f"direct download completed url={url!r} path={path}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
            except _SkipDownload as e:
                pipeline_log(
                    f"direct download skipped url={url!r}: {e}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
            except Exception as e:
                pipeline_log(
                    f"direct download failed url={url!r}: {e}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
        return downloaded

    async def run(
        self,
        *,
        plan: DownloadPlan,
        task: AcquisitionTask,
        plan_path: str,
        status_helper: Any,
        build_downloaded_artifact_descriptor: Any,
        artifact_source_for_path: Any,
        wait_for_downloads_to_complete: Any,
    ) -> Optional[dict[str, Any]]:
        if task.task_type != "repository_download":
            return None

        urls: list[str] = []
        source = None
        geo_accessions = self._extract_geo_accessions(plan, task)
        if geo_accessions:
            for accession in geo_accessions:
                urls.extend(await asyncio.to_thread(self._geo_matrix_urls, accession))
            source = "geo"
        sra_accessions = self._extract_sra_accessions(plan, task)
        if sra_accessions:
            urls.extend(self._ena_run_table_url(accession) for accession in sra_accessions)
            source = "sra_ena" if source is None else f"{source}+sra_ena"
        gdc_projects = self._extract_gdc_projects(plan, task)
        if gdc_projects:
            urls.extend(self._gdc_project_files_tsv_url(project_id) for project_id in gdc_projects)
            source = "gdc" if source is None else f"{source}+gdc"
        biostudies_accessions = self._extract_biostudies_accessions(plan, task)
        if biostudies_accessions:
            for accession in biostudies_accessions:
                try:
                    urls.extend(await asyncio.to_thread(self._biostudies_file_urls, accession))
                except Exception as e:
                    pipeline_log(
                        f"BioStudies discovery failed accession={accession!r}: {e}",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )
            source = "biostudies" if source is None else f"{source}+biostudies"
        cbioportal_study_ids = self._extract_cbioportal_study_ids(plan, task)
        if cbioportal_study_ids:
            for study_id in cbioportal_study_ids:
                urls.extend(self._cbioportal_datahub_urls(study_id))
            source = "cbioportal_datahub" if source is None else f"{source}+cbioportal_datahub"
        github_urls = self._extract_github_raw_urls(task)
        if github_urls:
            urls.extend(github_urls)
            source = "github" if source is None else f"{source}+github"
        discovered_github_urls = [] if github_urls else await self._discover_github_data_urls_for_task(task)
        if discovered_github_urls:
            urls.extend(discovered_github_urls)
            source = "github_tree" if source is None else f"{source}+github_tree"
        direct_file_urls = self._extract_direct_file_urls(task)
        if direct_file_urls:
            urls.extend(direct_file_urls)
            source = "direct_file" if source is None else f"{source}+direct_file"
        generic_direct_urls = self._extract_generic_direct_data_urls(task)
        if generic_direct_urls:
            urls.extend(generic_direct_urls)
            source = "generic_direct_file" if source is None else f"{source}+generic_direct_file"
        discovered_urls = await self._discover_data_file_urls_for_task(task)
        discovered_urls = self._filter_urls_by_task_relevance(
            discovered_urls,
            plan=plan,
            task=task,
        )
        if discovered_urls:
            urls.extend(discovered_urls)
            source = "publisher_supplementary" if source is None else f"{source}+publisher_supplementary"

        urls = self._dedup_urls_by_filename(list(dict.fromkeys(urls)))
        if not urls:
            return None

        paper_name = self._paper_name(plan_path)
        paper_downloads_dir = self.downloads_dir / paper_name
        await self._download_urls(urls, paper_downloads_dir)

        dataset_count, produced_artifacts = await finalize_download_run(
            download_queue=DownloadQueue(),
            paper_name=paper_name,
            plan=plan,
            task_override=task,
            paper_downloads_dir=paper_downloads_dir,
            downloads_dir=self.downloads_dir,
            project_id=self.project_id,
            team_name=self.team_name,
            status_helper=status_helper,
            wait_for_downloads_to_complete=wait_for_downloads_to_complete,
            storage=self.storage,
            build_downloaded_artifact_descriptor=build_downloaded_artifact_descriptor,
            artifact_source_for_path=artifact_source_for_path,
            produced_by=self.__class__.__name__,
        )
        coverage = infer_coverage_for_artifacts(
            artifacts=produced_artifacts,
            plan=plan,
            task_override=task,
        )
        status = "completed" if produced_artifacts or dataset_count > 0 else "skipped"
        return AcquisitionTaskResult(
            task_type="repository_download",
            status=status,
            produced_artifacts=produced_artifacts,
            coverage=coverage,
            confidence=0.95 if produced_artifacts else None,
            remaining_gaps=[] if coverage.sequencing_data and coverage.drug_response_data else list(task.blocking_gaps or plan.blocking_gaps),
            message=(
                f"Direct downloader recorded {len(produced_artifacts)} artifact(s)"
                if produced_artifacts
                else "Direct downloader found source URLs but produced no valid files"
            ),
            metadata={
                "paper": paper_name,
                "direct_downloader": source,
                "direct_urls": urls,
            },
        ).model_dump()

    async def cleanup(self) -> None:
        pass
