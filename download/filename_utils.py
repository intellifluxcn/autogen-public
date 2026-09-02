"""Filename normalization helpers for downloaded artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


MANIFEST_FILENAME_TOKENS = (
    "filereport",
    "manifest",
    "runtable",
    "sraruntable",
    "sra_run_table",
    "metadata",
)


def _looks_like_tsv(path: Path) -> bool:
    try:
        head = path.read_bytes()[:4096].decode("utf-8", errors="ignore")
    except OSError:
        return False
    first_line = head.splitlines()[0] if head.splitlines() else ""
    return first_line.count("\t") >= 2


def _looks_like_xlsx(path: Path) -> bool:
    try:
        header = path.read_bytes()[:4096]
    except OSError:
        return False
    if not header.startswith(b"PK\x03\x04"):
        return False
    return b"[Content_Types].xml" in header or b"xl/" in header


def _looks_like_gzip(path: Path) -> bool:
    try:
        header = path.read_bytes()[:2]
    except OSError:
        return False
    return header == b"\x1f\x8b"


def _looks_like_hdf5(path: Path) -> bool:
    try:
        header = path.read_bytes()[:8]
    except OSError:
        return False
    return header == b"\x89HDF\r\n\x1a\n"


def _looks_like_json(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lstrip()
    except OSError:
        return False
    return text.startswith("{") or text.startswith("[")


def _path_for_inferred_suffix(path: Path, suffix: str) -> Path:
    base_path = path.with_suffix("") if path.suffix.lower() == ".download" else path
    return base_path.with_suffix(suffix)


def _replace_with_unique_suffix(path: Path, suffix: str) -> Path:
    target_path = _path_for_inferred_suffix(path, suffix)
    if not target_path.exists():
        path.replace(target_path)
        return target_path

    stem = target_path.stem
    target_suffix = target_path.suffix
    counter = 1
    while True:
        candidate = target_path.with_name(f"{stem}_{counter}{target_suffix}")
        if not candidate.exists():
            path.replace(candidate)
            return candidate
        counter += 1


def _gzip_original_filename(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as handle:
            header = handle.read(512)
    except OSError:
        return None

    if len(header) < 11 or header[:2] != b"\x1f\x8b":
        return None
    flags = header[3]
    if not flags & 0x08:
        return None

    index = 10
    if flags & 0x04:
        if len(header) < index + 2:
            return None
        extra_len = int.from_bytes(header[index:index + 2], "little")
        index += 2 + extra_len
    if flags & 0x08:
        end = header.find(b"\x00", index)
        if end == -1:
            return None
        raw_name = header[index:end]
        try:
            original_name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            original_name = raw_name.decode("latin-1", errors="ignore")
        original_name = Path(original_name).name.strip()
        if original_name:
            return original_name
    return None


def normalize_download_filename(
    filename: str,
    *,
    url: Optional[str] = None,
    content_type: Optional[str] = None,
    source_path: Optional[Path] = None,
) -> str:
    """Return a clearer local filename for known extensionless data downloads."""

    clean_name = Path(filename).name or "downloaded_dataset"
    if Path(clean_name).suffix:
        return clean_name

    lower_name = clean_name.lower()
    lower_url = (url or "").lower()
    lower_content_type = (content_type or "").lower()
    context = f"{lower_name} {lower_url} {lower_content_type}"

    if "format=tsv" in context or any(token in context for token in MANIFEST_FILENAME_TOKENS):
        return f"{clean_name}.tsv"

    if source_path is not None:
        gzip_name = _gzip_original_filename(source_path)
        if gzip_name:
            return gzip_name if gzip_name.endswith(".gz") else f"{gzip_name}.gz"

    if source_path is not None and _looks_like_tsv(source_path):
        return f"{clean_name}.tsv"

    if source_path is not None and _looks_like_xlsx(source_path):
        return f"{clean_name}.xlsx"

    if source_path is not None and _looks_like_hdf5(source_path):
        return f"{clean_name}.h5"

    if source_path is not None and _looks_like_gzip(source_path):
        return f"{clean_name}.gz"

    if source_path is not None and _looks_like_json(source_path):
        return f"{clean_name}.json"

    return clean_name


def normalize_downloaded_file_path(path: Path) -> Path:
    """Rename an already downloaded file when content reveals a better name."""

    if not path.exists() or not path.is_file():
        return path
    if path.suffix and path.suffix.lower() != ".download":
        return path

    gzip_name = _gzip_original_filename(path)
    if gzip_name:
        target_name = gzip_name if gzip_name.endswith(".gz") else f"{gzip_name}.gz"
        target_path = path.with_name(target_name)
        if target_path == path:
            return path
        if not target_path.exists():
            path.replace(target_path)
            return target_path

        stem = target_path.stem
        suffix = target_path.suffix
        counter = 1
        while True:
            candidate = target_path.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                path.replace(candidate)
                return candidate
            counter += 1

    if _looks_like_xlsx(path):
        return _replace_with_unique_suffix(path, ".xlsx")

    if _looks_like_hdf5(path):
        return _replace_with_unique_suffix(path, ".h5")

    if _looks_like_gzip(path):
        return _replace_with_unique_suffix(path, ".gz")

    if _looks_like_json(path):
        return _replace_with_unique_suffix(path, ".json")

    return path
