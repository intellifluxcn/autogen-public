"""Markdown image parsing and heuristics for analysis multimodal input."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_NAME_KEYWORD_RE = re.compile(r"(figure|fig|table|tbl)", re.IGNORECASE)

# Caption window (chars after an image ref) scanned for relevance keywords.
_CAPTION_WINDOW = 240


def extract_markdown_image_refs(markdown: str) -> List[str]:
    return [m.group(1).strip() for m in IMG_MD_RE.finditer(markdown or "")]


def image_passes_size_or_name_heuristic(path: Path, min_size_bytes: int = 51200) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size > min_size_bytes:
        return True
    return bool(_NAME_KEYWORD_RE.search(path.name))


def guess_image_mime_from_header(data: bytes) -> Optional[str]:
    if len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def resolve_markdown_image_path(ref: str, md_base: Path) -> Optional[Path]:
    ref = ref.strip()
    if ref.startswith(("http://", "https://", "data:")):
        return None
    p = Path(ref)
    if not p.is_absolute():
        p = (md_base / p).resolve()
    return p


def _iter_image_candidates(markdown: str, md_base: Path, max_bytes_per_image: int,
                           min_size_heuristic_bytes: int):
    """Yield (resolved_path, caption_context) for each valid image ref in
    document order. caption_context = alt text + the text following the ref
    (where mineru places figure captions), lowercased for keyword scoring."""
    seen = set()
    for m in IMG_MD_RE.finditer(markdown or ""):
        ref = m.group(1).strip()
        p = resolve_markdown_image_path(ref, md_base)
        if p is None or not p.is_file():
            continue
        try:
            rp = str(p.resolve())
        except OSError:
            continue
        if rp in seen:
            continue
        if p.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if sz <= 0 or sz > max_bytes_per_image:
            continue
        if not image_passes_size_or_name_heuristic(p, min_size_heuristic_bytes):
            continue
        try:
            header = p.read_bytes()[:32]
        except OSError:
            continue
        if guess_image_mime_from_header(header) is None:
            continue
        seen.add(rp)
        alt = (m.group(0)[2:].split("]", 1)[0]) if "]" in m.group(0) else ""
        tail = markdown[m.end():m.end() + _CAPTION_WINDOW]
        nxt = tail.find("![")          # don't absorb the next figure's caption
        if nxt != -1:
            tail = tail[:nxt]
        caption = f"{alt} {tail}".lower()
        yield p, caption


def collect_filtered_image_paths(
    markdown: str,
    md_base: Path,
    *,
    max_images: int = 5,
    max_bytes_per_image: int = 4 * 1024 * 1024,
    min_size_heuristic_bytes: int = 51200,
    prioritize_keywords: Optional[Sequence[str]] = None,
) -> List[Path]:
    """Collect figure images referenced in ``markdown``.

    By default returns the first ``max_images`` valid images in document order.
    When ``prioritize_keywords`` is given (e.g. drug-response terms), ALL valid
    candidates are scored by how many keywords appear in each figure's caption
    and the top ``max_images`` are returned (document order breaks ties) — so a
    dose-response/IC50 figure deep in the paper still reaches the model instead
    of being crowded out by earlier, irrelevant figures."""
    candidates = list(
        _iter_image_candidates(markdown, md_base, max_bytes_per_image, min_size_heuristic_bytes)
    )
    if not prioritize_keywords:
        return [p for p, _ in candidates[:max_images]]
    kws = [k.lower() for k in prioritize_keywords]
    scored = [
        (sum(1 for k in kws if k in caption), idx, p)
        for idx, (p, caption) in enumerate(candidates)
    ]
    # Highest keyword score first; document order (idx) as a stable tie-break.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [p for _, _, p in scored[:max_images]]


def image_file_to_data_url_block(path: Path, detail: str = "low") -> Optional[Tuple[dict, str]]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    mime = guess_image_mime_from_header(data[:32])
    if mime is None:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    block = {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}", "detail": detail},
    }
    return block, mime


def build_openai_multimodal_parts(
    user_text: str, image_paths: List[Path], *, detail: str = "low"
) -> List[dict[str, Any]]:
    parts: List[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for p in image_paths:
        tup = image_file_to_data_url_block(p, detail=detail)
        if tup:
            parts.append(tup[0])
    return parts
