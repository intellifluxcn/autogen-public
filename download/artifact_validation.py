"""Downloaded artifact validation and coverage inference."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from download.acquisition_result import CoverageSummary, ProducedArtifact


DATA_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".txt",
    ".gct",
    ".mtx",
    ".h5",
    ".h5ad",
    ".xls",
    ".xlsx",
    ".parquet",
    ".feather",
    ".rds",
    ".rda",
    ".json",
}

ARCHIVE_EXTENSIONS = {
    ".gz",
    ".zip",
    ".tar",
    ".tgz",
    ".bz2",
    ".xz",
}

STRUCTURE_EXTENSIONS = {
    ".bcif",
    ".cif",
    ".pdb",
    ".xml",
}

STRUCTURE_ARCHIVE_SUFFIXES = {
    ".bcif.gz",
    ".cif.gz",
    ".pdb.gz",
    ".pdb1.gz",
    ".xml.gz",
}

MANIFEST_NAMES = (
    "manifest",
    "run table",
    "runtable",
    "sraruntable",
    "sra_run_table",
    "filereport",
    "metadata",
    "biospecimen",
    "clinical",
)

INVALID_EXTENSIONS = {
    ".html",
    ".htm",
    ".pdf",
    ".doc",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
}

INVALID_NAME_TOKENS = (
    "login",
    "signin",
    "sign-in",
    "error",
    "forbidden",
    "denied",
    "application_form",
    "application-form",
    "research_application",
    "request_form",
    "author_contact_report",
    "acquisition_blocked_report",
    "embedded_extraction_report",
    "embedded_targets_manifest",
    "no_valid_dataset_report",
)

INVALID_EMBEDDED_NAME_TOKENS = (
    "accepted_manuscripts_are_published_online",
    "before_technical_editing",
    "figure_and_table_count",
    "reference_count",
    "using_this_free_service",
)

INVALID_JSON_NAME_TOKENS = (
    "tdmrep-policy",
)

INVALID_JSON_FILENAMES = {
    "configs.json",
    "xgb_params.json",
}

INVALID_JSON_PREFIXES = (
    "ligands",
    "proteins",
    "sw_sim_vectors",
)

CODE_OR_DOC_FILENAMES = {
    "citation.cff",
    "environment.yml",
    "environment.yaml",
    "license",
    "package-lock.json",
    "package.json",
    "poetry.lock",
    "pyproject.toml",
    "readme",
    "readme.md",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}

SEQUENCING_KEYWORDS = (
    "rna",
    "rnaseq",
    "rna-seq",
    "sequencing",
    "expression",
    "transcript",
    "count",
    "counts",
    "matrix",
    "gene",
    "gse",
    "sra",
    "prjna",
    "fastq",
    "bam",
    "h5ad",
    "mtx",
    "omics",
)

DRUG_RESPONSE_KEYWORDS = (
    "drug",
    "response",
    "ic50",
    "auc",
    "viability",
    "sensitivity",
    "dose",
    "screen",
    "screening",
    "gdsc",
    "ctrp",
    "prism",
    "depmap",
    "cellminer",
    "growth inhibition",
)

BIOMEDICAL_TABLE_KEYWORDS = SEQUENCING_KEYWORDS + DRUG_RESPONSE_KEYWORDS + (
    "cell line",
    "cell_line",
    "tumor",
    "tumour",
    "cancer",
    "patient",
    "sample",
    "mutation",
    "mutations",
    "copy number",
    "methylation",
    "protein",
    "metabol",
    "compound",
    "inhibitor",
    "mic",
    "ec50",
    "gi50",
)

BEHAVIORAL_TABLE_KEYWORDS = (
    "totalstars",
    "reward",
    "adversity",
    "abuse",
    "neglect",
    "food_insecurity",
    "external",
    "cdi_tot",
)

NON_RESPONSE_CHEMISTRY_TABLE_KEYWORDS = (
    "rel. deprotonation energy",
    "relative deprotonation energy",
    "rel. binding energy",
    "relative binding energy",
    "rel. complexation energy",
    "relative complexation energy",
    "energy of reduction",
    "dft calculations",
    "density functional theory",
)

CLINICAL_BASELINE_TABLE_KEYWORDS = (
    "characteristic,patients",
    "patients (n =",
    "mean ± sd",
    "median (range",
    "histologic diagnosis",
    "clinical disease stage",
)

EMBEDDED_RESPONSE_METRIC_KEYWORDS = (
    "auc",
    "dss",
    "ec50",
    "gi50",
    "ic50",
    "inhibition",
    "response",
    "sensitivity",
    "viability",
)

IMAGE_ARCHIVE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

GENERIC_EXTENSIONLESS_FILENAMES = re.compile(
    r"^(?:download|downloaded_dataset|file|data|dataset)(?:[_-]\d+)?$"
)


@dataclass(frozen=True)
class ArtifactValidation:
    """Validation decision for one local file."""

    is_valid: bool
    reason: str
    trust_level: str = "high"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


def _suffixes(path: Path) -> set[str]:
    return {suffix.lower() for suffix in path.suffixes}


def _combined_suffix(path: Path) -> str:
    suffixes = path.suffixes
    if len(suffixes) >= 2:
        return "".join(suffixes[-2:]).lower()
    return path.suffix.lower()


def _target_text(plan: Any, task_override: Any) -> str:
    parts: list[str] = []
    for obj in (plan, task_override):
        if obj is None:
            continue
        for attr in ("desired_outputs", "blocking_gaps"):
            value = getattr(obj, attr, None) or []
            parts.extend(str(item) for item in value)
        for attr in ("notes", "repository", "source_paper_title", "title", "success_criteria"):
            value = getattr(obj, attr, None)
            if value:
                parts.append(str(value))
        for step in getattr(obj, "steps", None) or []:
            parts.extend(
                [
                    str(getattr(step, "description", "") or ""),
                    str(getattr(step, "url", "") or ""),
                    " ".join(getattr(step, "target_files", None) or []),
                ]
            )
    return " ".join(parts).lower()


def _is_explicitly_targeted(path: Path, plan: Any, task_override: Any) -> bool:
    filename = path.name.lower()
    if not path.suffix and GENERIC_EXTENSIONLESS_FILENAMES.match(filename):
        return False
    target_text = _target_text(plan, task_override)
    return filename in target_text


def _looks_like_html(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(512).lstrip().lower()
    except OSError:
        return False
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<title>login" in head


def _looks_like_chrome_extension(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return False
    return head == b"Cr24"


def _looks_like_git_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            content = handle.read(256).decode("utf-8", errors="ignore")
    except OSError:
        return False
    lines = content.splitlines()
    return (
        len(lines) >= 3
        and lines[0] == "version https://git-lfs.github.com/spec/v1"
        and lines[1].startswith("oid sha256:")
        and lines[2].startswith("size ")
    )


def _looks_like_boilerplate_embedded_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv" or ".embedded" not in path.name.lower():
        return False
    name_lower = path.name.lower()
    if any(token in name_lower for token in INVALID_EMBEDDED_NAME_TOKENS):
        return True
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
    except OSError:
        return False
    return any(
        token in head
        for token in (
            "accepted manuscripts are published online",
            "before technical editing",
            "formatting and proof reading",
            "formatting and proofreading",
            "using this free service",
            "figure and table count",
            "reference count",
            "access the most recent version of this article",
            "access the most recent supplemental material",
            "author manuscripts have been peer reviewed",
            "author manuscript",
            "updated version",
            "supplementary material",
        )
    )


def _looks_like_non_response_embedded_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv" or ".embedded" not in path.name.lower():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")[:8192].lower()
    except OSError:
        return False

    has_response_metric = any(token in text for token in EMBEDDED_RESPONSE_METRIC_KEYWORDS)
    if has_response_metric:
        return False

    if any(token in text for token in NON_RESPONSE_CHEMISTRY_TABLE_KEYWORDS):
        return True

    clinical_hits = sum(1 for token in CLINICAL_BASELINE_TABLE_KEYWORDS if token in text)
    return clinical_hits >= 2


def _looks_like_web_app_manifest_json(path: Path) -> bool:
    if path.suffix.lower() != ".json" or path.name.lower() != "manifest.json":
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore")[:64 * 1024])
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    web_manifest_keys = {"short_name", "start_url", "display", "theme_color", "icons"}
    if len(web_manifest_keys.intersection(data)) >= 3:
        return True
    data_keys = set(data)
    return bool({"short_name", "name"}.issubset(data_keys) and len(data_keys) <= 3)


def _looks_like_tdm_policy_json(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return False
    name_lower = path.name.lower()
    if any(token in name_lower for token in INVALID_JSON_NAME_TOKENS):
        return True
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:128 * 1024].lower()
    except OSError:
        return False
    return "tdmrep" in text and ("elsevier" in text or "text and data mining" in text)


def _looks_like_repository_metadata_json(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore")[:128 * 1024])
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    payload = data.get("data")
    if not isinstance(payload, dict):
        return False
    project_metadata_keys = {
        "dbgap_accession_number",
        "disease_type",
        "primary_site",
        "project_id",
        "releasable",
        "released",
        "state",
    }
    if "project_id" in payload and len(project_metadata_keys.intersection(payload)) >= 4:
        return True
    return False


def _looks_like_image_only_zip(path: Path) -> bool:
    if path.suffix.lower() != ".zip":
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
    except (OSError, zipfile.BadZipFile):
        return False
    content_names = [
        name
        for name in names
        if not Path(name).name.startswith("._")
        and not name.startswith("__MACOSX/")
        and Path(name).name.lower() not in {"thumbs.db", ".ds_store"}
    ]
    if not content_names:
        return False
    suffixes = {Path(name).suffix.lower() for name in content_names}
    return bool(suffixes) and suffixes.issubset(IMAGE_ARCHIVE_EXTENSIONS)


def _looks_like_unrelated_behavioral_csv(path: Path) -> bool:
    if path.suffix.lower() not in {".csv", ".tsv"}:
        return False
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")[:8192].lower()
    except OSError:
        return False
    if not any(token in text for token in BEHAVIORAL_TABLE_KEYWORDS):
        return False
    strong_biomedical_tokens = [
        token for token in BIOMEDICAL_TABLE_KEYWORDS if len(token) > 3 and token != "count"
    ]
    short_biomedical_patterns = (
        r"\brna\b",
        r"\bdna\b",
        r"\bgse\d+\b",
        r"\bsra\b",
    )
    has_biomedical_signal = any(token in text for token in strong_biomedical_tokens) or any(
        re.search(pattern, text) for pattern in short_biomedical_patterns
    )
    return not has_biomedical_signal


def artifact_file_signature(path: Path) -> Optional[tuple[int, str]]:
    hasher = hashlib.sha256()
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return None
    return size, hasher.hexdigest()


def validate_downloaded_file(
    path: Path,
    *,
    plan: Any,
    task_override: Any = None,
) -> ArtifactValidation:
    """Return whether a file should count as a usable downloaded dataset."""

    if not path.is_file():
        return ArtifactValidation(False, "not_a_file", confidence=0.0)
    if path.suffix == ".crdownload":
        return ArtifactValidation(False, "incomplete_browser_download", confidence=0.0)

    try:
        size = path.stat().st_size
    except OSError:
        return ArtifactValidation(False, "stat_failed", confidence=0.0)
    if size == 0:
        return ArtifactValidation(False, "empty_file", confidence=0.0)

    name_lower = path.name.lower()
    suffixes = _suffixes(path)
    metadata = {"size": size, "suffixes": sorted(suffixes)}

    if any(token in name_lower for token in INVALID_NAME_TOKENS):
        return ArtifactValidation(False, "invalid_name_token", confidence=0.0, metadata=metadata)

    if name_lower in INVALID_JSON_FILENAMES or (
        name_lower.endswith(".json")
        and any(name_lower.startswith(prefix) for prefix in INVALID_JSON_PREFIXES)
    ):
        return ArtifactValidation(False, "auxiliary_json", confidence=0.0, metadata=metadata)

    if _looks_like_web_app_manifest_json(path):
        return ArtifactValidation(False, "web_app_manifest", confidence=0.0, metadata=metadata)

    if _looks_like_tdm_policy_json(path):
        return ArtifactValidation(False, "tdm_policy_json", confidence=0.0, metadata=metadata)

    if _looks_like_repository_metadata_json(path):
        return ArtifactValidation(False, "repository_metadata_json", confidence=0.0, metadata=metadata)

    if name_lower in CODE_OR_DOC_FILENAMES:
        return ArtifactValidation(False, "code_or_docs_file", confidence=0.0, metadata=metadata)

    if _looks_like_html(path):
        return ArtifactValidation(False, "html_or_login_page", confidence=0.0, metadata=metadata)

    if _looks_like_chrome_extension(path):
        return ArtifactValidation(False, "chrome_extension_download", confidence=0.0, metadata=metadata)

    if _looks_like_git_lfs_pointer(path):
        return ArtifactValidation(False, "git_lfs_pointer", confidence=0.0, metadata=metadata)

    if _looks_like_boilerplate_embedded_csv(path):
        return ArtifactValidation(False, "embedded_boilerplate_table", confidence=0.0, metadata=metadata)

    if _looks_like_non_response_embedded_csv(path):
        return ArtifactValidation(False, "non_response_embedded_table", confidence=0.0, metadata=metadata)

    if _looks_like_unrelated_behavioral_csv(path):
        return ArtifactValidation(False, "unrelated_behavioral_table", confidence=0.0, metadata=metadata)

    explicitly_targeted = _is_explicitly_targeted(path, plan, task_override)
    if (_combined_suffix(path) in STRUCTURE_ARCHIVE_SUFFIXES or suffixes & STRUCTURE_EXTENSIONS) and not explicitly_targeted:
        return ArtifactValidation(False, "structure_file", confidence=0.0, metadata=metadata)

    if suffixes & INVALID_EXTENSIONS and not explicitly_targeted:
        return ArtifactValidation(False, "invalid_extension", confidence=0.0, metadata=metadata)

    if suffixes & DATA_EXTENSIONS:
        return ArtifactValidation(True, "data_extension", metadata=metadata)

    if suffixes & ARCHIVE_EXTENSIONS:
        if size < 100:
            return ArtifactValidation(False, "archive_too_small", confidence=0.0, metadata=metadata)
        if _looks_like_image_only_zip(path):
            return ArtifactValidation(False, "image_only_archive", confidence=0.0, metadata=metadata)
        return ArtifactValidation(True, "archive_extension", confidence=0.85, metadata=metadata)

    if any(token in name_lower for token in MANIFEST_NAMES):
        return ArtifactValidation(True, "manifest_or_metadata", trust_level="medium", confidence=0.8, metadata=metadata)

    if explicitly_targeted:
        return ArtifactValidation(True, "explicit_target_file", trust_level="medium", confidence=0.7, metadata=metadata)

    return ArtifactValidation(False, "unsupported_file_type", confidence=0.0, metadata=metadata)


def infer_coverage_for_artifacts(
    *,
    artifacts: list[ProducedArtifact],
    plan: Any,
    task_override: Any = None,
) -> CoverageSummary:
    """Infer modality coverage from artifact names and plan/task intent."""

    if not artifacts:
        return CoverageSummary()

    text_parts = [_target_text(plan, task_override)]
    for artifact in artifacts:
        text_parts.append(Path(artifact.file_path).name.lower())
        text_parts.extend(str(value).lower() for value in artifact.provenance.values())
    joined = " ".join(text_parts)
    return CoverageSummary(
        sequencing_data=any(keyword in joined for keyword in SEQUENCING_KEYWORDS),
        drug_response_data=any(keyword in joined for keyword in DRUG_RESPONSE_KEYWORDS),
    )
