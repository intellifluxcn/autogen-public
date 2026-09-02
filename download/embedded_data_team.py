"""Embedded paper data extraction executor."""

import csv
import json
import logging
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

from analyze.plan_model import AcquisitionTask, DownloadPlan
from download.acquisition_result import AcquisitionTaskResult, CoverageSummary, ProducedArtifact
from utils.path_utils import get_analyses_dir, get_browseruse_datasets_dir
from utils.pipeline_log import pipeline_log

# Bound the prose window sent to the extraction LLM (chars). The drug-response
# numbers live in results/methods + figure/table captions, well within this.
_LLM_EXTRACT_TEXT_LIMIT = int(os.getenv("EMBEDDED_LLM_EXTRACT_TEXT_LIMIT", str(120_000)))


def _embedded_llm_extraction_enabled() -> bool:
    return os.getenv("EMBEDDED_LLM_EXTRACTION_ENABLED", "true").strip().lower() not in (
        "false", "0", "no", "off", "",
    )


_LLM_EXTRACT_SYSTEM = (
    "You are a precise biomedical data extractor. You transcribe drug-response "
    "measurements exactly as reported; you never invent values."
)

_LLM_EXTRACT_PROMPT = (
    "Extract the DRUG-RESPONSE (drug-sensitivity) data from this paper into a "
    "single CSV matrix. Drug response means per-sample/per-cell-line drug "
    "sensitivity metrics: IC50, EC50, GI50, AUC, viability, or %inhibition.\n\n"
    "The data may appear ONLY in prose (e.g. 'IC50 was 2.5 µM for drug X in "
    "cell line A') or in FIGURES (dose-response curves, IC50 bar charts, "
    "heatmaps) — extract from the text AND the attached figure images.\n\n"
    "Output rules (obey EXACTLY):\n"
    "- First row = header: the first column is the sample/cell-line identifier, "
    "the remaining columns are drug names.\n"
    "- One row per sample/cell line; cells are the numeric metric value.\n"
    "- Include a final commented line `# metric: <IC50|AUC|...> unit: <unit>` "
    "describing what the numbers are.\n"
    "- Output ONLY raw CSV (no prose, no code fences).\n"
    "- If the paper contains NO per-sample drug-response values in text or "
    "figures, output exactly: NONE\n"
)

logger = logging.getLogger(__name__)

# Drug-response (R) relevance keywords — used to (1) rank figures so the
# dose-response/IC50 chart reaches the multimodal model and (2) focus the text
# window on R-bearing regions in long papers.
DRUG_RESPONSE_KEYWORDS = (
    "ic50", "ic 50", "ec50", "gi50", "gr50", "ld50", "auc",
    "dose-response", "dose response", "dose–response",
    "drug response", "drug-response", "drug sensitivity", "drug-sensitivity",
    "drug screen", "chemosensitivity", "cytotoxicity",
    "viability", "% inhibition", "inhibition rate", "sensitivity",
)

# Number of figures fed to the multimodal R extractor (relevance-ranked).
_LLM_EXTRACT_MAX_IMAGES = int(os.getenv("EMBEDDED_LLM_EXTRACT_MAX_IMAGES", "8"))
# Image detail for R figures: "high" lets the model read values off charts.
_LLM_EXTRACT_IMAGE_DETAIL = os.getenv("EMBEDDED_LLM_EXTRACT_IMAGE_DETAIL", "high").strip() or "high"


EMBEDDED_DATA_SIGNAL_TOKENS = (
    "auc",
    "cell line",
    "compound",
    "counts",
    "dose",
    "drug",
    "ec50",
    "expression",
    "fpkm",
    "gene",
    "gi50",
    "ic50",
    "inhibition",
    "matrix",
    "mutation",
    "response",
    "rna",
    "sample",
    "sensitivity",
    "tpm",
    "tumor",
    "viability",
)

EMBEDDED_BOILERPLATE_TOKENS = (
    "accepted manuscripts are published online",
    "before technical editing",
    "formatting and proof reading",
    "formatting and proofreading",
    "using this free service",
)

DRUG_TABLE_TOKENS = (
    "agent",
    "compound",
    "drug",
    "inhibitor",
    "mimetic",
    "therapy",
    "chemotherapeutic",
)

RESPONSE_TABLE_TOKENS = (
    "auc",
    "dss",
    "dose",
    "ec50",
    "gi50",
    "ic50",
    "inhibition",
    "response",
    "sensitivity",
    "viability",
)

CANCER_CONTEXT_TOKENS = (
    "cancer",
    "car t",
    "cell line",
    "leukemia",
    "lymphoma",
    "metastatic",
    "patient",
    "prostate",
    "sample",
    "tumor",
    "tumour",
)

NON_CANCER_CONTEXT_TOKENS = (
    "antimalarial",
    "bacterial",
    "cannabinoid composition",
    "dopamine",
    "food insecurity",
    "malaria",
    "metabolites",
    "p. vivax",
)

NON_RESPONSE_CHEMISTRY_TABLE_TOKENS = (
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

CLINICAL_BASELINE_TABLE_TOKENS = (
    "characteristic",
    "patients (n =",
    "mean ± sd",
    "median (range",
    "histologic diagnosis",
    "clinical disease stage",
)


class _HTMLTableParser(HTMLParser):
    """Minimal HTML table parser for MinerU markdown containing raw table blocks."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: List[Dict[str, Any]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._table_start_line = 0
        self._rows: List[List[str]] = []
        self._current_row: List[str] = []
        self._cell_parts: List[str] = []
        self._cell_colspan = 1

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "table":
            self._in_table = True
            self._table_start_line = max(0, self.getpos()[0] - 1)
            self._rows = []
            return
        if not self._in_table:
            return
        if tag_lower == "tr":
            self._in_row = True
            self._current_row = []
        elif tag_lower in {"td", "th"}:
            self._in_cell = True
            self._cell_parts = []
            self._cell_colspan = 1
            for name, value in attrs:
                if name.lower() == "colspan" and value:
                    try:
                        self._cell_colspan = max(1, min(20, int(value)))
                    except ValueError:
                        self._cell_colspan = 1
        elif tag_lower == "br" and self._in_cell:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if not self._in_table:
            return
        if tag_lower in {"td", "th"} and self._in_cell:
            cell = re.sub(r"\s+", " ", " ".join(self._cell_parts)).strip()
            self._current_row.extend([cell] * self._cell_colspan)
            self._cell_parts = []
            self._cell_colspan = 1
            self._in_cell = False
        elif tag_lower == "tr" and self._in_row:
            if any(cell for cell in self._current_row):
                self._rows.append(self._current_row)
            self._current_row = []
            self._in_row = False
        elif tag_lower == "table":
            parsed = EmbeddedDataTeam._normalize_delimited_rows(self._rows)
            if parsed is not None:
                self.tables.append(
                    {
                        "start_line": self._table_start_line,
                        "end_line": self.getpos()[0],
                        "header": parsed["header"],
                        "rows": parsed["rows"],
                        "source_format": "html_table",
                    }
                )
            self._rows = []
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell and data:
            self._cell_parts.append(data)


class EmbeddedDataTeam:
    """Extract or at least structure embedded paper data targets."""

    def __init__(self, team_name: str = "EmbeddedDataTeam", project_id: str = None, ui_callback=None):
        self.team_name = team_name
        self.project_id = project_id
        self.ui_callback = ui_callback
        self.output_dir = Path(get_browseruse_datasets_dir())
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _update_status(self, message: str) -> None:
        pipeline_log(message, stage="download", team=self.team_name, project_id=self.project_id)
        if self.ui_callback:
            self.ui_callback("status", message)

    @staticmethod
    def _notes_path_for_plan(plan_path: str) -> Path:
        return Path(plan_path).with_suffix("").with_suffix(".md")

    @staticmethod
    def _mineru_assets_dir_for_plan(plan_path: str) -> Path:
        paper_name = Path(plan_path).stem.replace(".plan", "")
        return Path(plan_path).parent / "mineru_assets" / paper_name

    @staticmethod
    def _global_mineru_assets_dir_for_plan(plan_path: str) -> Path:
        paper_name = Path(plan_path).stem.replace(".plan", "")
        return Path(get_analyses_dir()) / "mineru_assets" / paper_name

    @classmethod
    def _mineru_markdown_path_for_plan(cls, plan_path: str) -> Optional[Path]:
        for assets_dir in (
            cls._mineru_assets_dir_for_plan(plan_path),
            cls._global_mineru_assets_dir_for_plan(plan_path),
        ):
            full_md_path = assets_dir / "full.md"
            if full_md_path.exists():
                return full_md_path
        return None

    @classmethod
    def _load_embedded_source_text(cls, plan_path: str) -> str:
        mineru_md_path = cls._mineru_markdown_path_for_plan(plan_path)
        if mineru_md_path is not None:
            return mineru_md_path.read_text(encoding="utf-8", errors="replace")

        notes_path = cls._notes_path_for_plan(plan_path)
        if notes_path.exists():
            return notes_path.read_text(encoding="utf-8")
        return ""

    @staticmethod
    def _slugify(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.lower())
        return safe.strip("._-") or "embedded"

    @staticmethod
    def _split_markdown_row(line: str) -> List[str]:
        trimmed = line.strip()
        if trimmed.startswith("|"):
            trimmed = trimmed[1:]
        if trimmed.endswith("|"):
            trimmed = trimmed[:-1]
        return [cell.strip() for cell in trimmed.split("|")]

    @classmethod
    def _is_markdown_table_row(cls, line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2

    @classmethod
    def _is_separator_row(cls, cells: List[str]) -> bool:
        if not cells:
            return False
        for cell in cells:
            normalized = cell.replace(" ", "")
            if not normalized or not re.fullmatch(r":?-{3,}:?", normalized):
                return False
        return True

    @classmethod
    def _extract_markdown_tables(cls, notes_lines: List[str]) -> List[Dict[str, Any]]:
        tables: List[Dict[str, Any]] = []
        idx = 0
        while idx < len(notes_lines):
            if not cls._is_markdown_table_row(notes_lines[idx]):
                idx += 1
                continue

            start = idx
            block: List[str] = []
            while idx < len(notes_lines) and cls._is_markdown_table_row(notes_lines[idx]):
                block.append(notes_lines[idx])
                idx += 1

            if len(block) < 2:
                continue

            header = cls._split_markdown_row(block[0])
            separator = cls._split_markdown_row(block[1])
            if len(header) < 2 or not cls._is_separator_row(separator):
                continue

            rows = [cls._split_markdown_row(line) for line in block[2:]]
            rows = [row for row in rows if len(row) == len(header)]
            if not rows:
                continue

            tables.append(
                {
                    "start_line": start,
                    "end_line": idx - 1,
                    "header": header,
                    "rows": rows,
                    "source_format": "markdown_table",
                }
            )
        return tables

    @staticmethod
    def _normalize_delimited_rows(raw_rows: List[List[str]]) -> Optional[Dict[str, Any]]:
        if len(raw_rows) < 2:
            return None
        width = len(raw_rows[0])
        if width < 2:
            return None
        normalized_rows = [row for row in raw_rows if len(row) == width]
        if len(normalized_rows) < 2:
            return None
        return {
            "header": normalized_rows[0],
            "rows": normalized_rows[1:],
        }

    @classmethod
    def _parse_delimited_block(
        cls,
        block_lines: List[str],
        *,
        delimiter: str,
    ) -> Optional[Dict[str, Any]]:
        parsed_rows: List[List[str]] = []
        for line in block_lines:
            stripped = line.strip()
            if not stripped:
                continue
            row = [cell.strip() for cell in stripped.split(delimiter)]
            parsed_rows.append(row)
        return cls._normalize_delimited_rows(parsed_rows)

    @classmethod
    def _extract_fenced_delimited_tables(cls, notes_lines: List[str]) -> List[Dict[str, Any]]:
        tables: List[Dict[str, Any]] = []
        idx = 0
        while idx < len(notes_lines):
            start_line = notes_lines[idx].strip()
            if not start_line.startswith("```"):
                idx += 1
                continue

            fence_lang = start_line[3:].strip().lower()
            block_start = idx
            idx += 1
            block_lines: List[str] = []
            while idx < len(notes_lines) and not notes_lines[idx].strip().startswith("```"):
                block_lines.append(notes_lines[idx])
                idx += 1
            if idx >= len(notes_lines):
                break

            delimiter = "," if fence_lang == "csv" else "\t" if fence_lang == "tsv" else None
            if delimiter is None:
                idx += 1
                continue

            parsed = cls._parse_delimited_block(block_lines, delimiter=delimiter)
            if parsed is not None:
                tables.append(
                    {
                        "start_line": block_start,
                        "end_line": idx,
                        "header": parsed["header"],
                        "rows": parsed["rows"],
                        "source_format": fence_lang,
                    }
                )
            idx += 1
        return tables

    @classmethod
    def _extract_html_tables(cls, notes_text: str) -> List[Dict[str, Any]]:
        parser = _HTMLTableParser()
        try:
            parser.feed(notes_text)
        except Exception as e:
            pipeline_log(
                f"HTML table parsing failed: {e}",
                stage="download",
                team="EmbeddedDataTeam",
                level=logging.WARNING,
            )
            return []
        return parser.tables

    @staticmethod
    def _tokenize_text(value: str) -> List[str]:
        return [token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 3]

    @classmethod
    def _table_context_text(cls, table: Dict[str, Any], notes_lines: List[str], window: int = 3) -> str:
        start = max(0, int(table["start_line"]) - window)
        end = min(len(notes_lines), int(table["end_line"]) + window + 1)
        context_lines = notes_lines[start:end]
        header = table.get("header") or []
        rows = table.get("rows") or []
        flattened_rows = [" ".join(row) for row in rows[:3]]
        return "\n".join(context_lines + [" ".join(header)] + flattened_rows)

    @staticmethod
    def _numeric_cell_fraction(table: Dict[str, Any]) -> float:
        cells = [
            cell
            for row in table.get("rows") or []
            for cell in row
            if str(cell).strip()
        ]
        if not cells:
            return 0.0
        numeric = 0
        for cell in cells:
            text = str(cell).strip().replace(",", "")
            if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?%?", text, flags=re.IGNORECASE):
                numeric += 1
        return numeric / len(cells)

    @classmethod
    def _table_signal_score(cls, table: Dict[str, Any], context_text: str) -> float:
        context_lower = context_text.lower()
        header_lower = " ".join(table.get("header") or []).lower()
        row_count = len(table.get("rows") or [])
        score = 0.0
        for token in EMBEDDED_DATA_SIGNAL_TOKENS:
            if token in context_lower:
                score += 1.0
            if token in header_lower:
                score += 1.0
        if row_count >= 2:
            score += 0.5
        if cls._numeric_cell_fraction(table) >= 0.2:
            score += 1.0
        for token in EMBEDDED_BOILERPLATE_TOKENS:
            if token in context_lower:
                score -= 5.0
        return score

    @staticmethod
    def _collect_targets(task: AcquisitionTask, notes_text: str) -> List[Dict[str, Any]]:
        collected = []
        for target in task.embedded_targets:
            if EmbeddedDataTeam._is_boilerplate_label(target.label):
                continue
            pattern = re.escape(target.label)
            evidence_found = bool(re.search(pattern, notes_text, flags=re.IGNORECASE))
            collected.append(
                {
                    "target_type": target.target_type,
                    "label": target.label,
                    "page_hint": target.page_hint,
                    "description": target.description,
                    "extraction_hint": target.extraction_hint,
                    "evidence_found_in_analysis": evidence_found,
                }
            )
        if collected:
            return collected

        labels = []
        for line in notes_text.splitlines():
            if EmbeddedDataTeam._is_boilerplate_label(line):
                continue
            if any(token in line.lower() for token in ("table", "figure", "supplement")):
                labels.append(line.strip())
        return [
            {
                "target_type": "table" if "table" in label.lower() else "figure",
                "label": label[:160],
                "page_hint": None,
                "description": "Recovered from analysis notes",
                "extraction_hint": None,
                "evidence_found_in_analysis": True,
            }
            for label in labels[:10]
        ]

    @staticmethod
    def _is_boilerplate_label(label: str) -> bool:
        label_lower = (label or "").lower()
        return any(token in label_lower for token in EMBEDDED_BOILERPLATE_TOKENS) or any(
            token in label_lower
            for token in (
                "figure and table count",
                "reference count",
                "a list of figures that have associated raw data",
            )
        )

    @staticmethod
    def _find_label_line_index(label: str, notes_lines: List[str]) -> Optional[int]:
        pattern = re.compile(re.escape(label), flags=re.IGNORECASE)
        for idx, line in enumerate(notes_lines):
            if pattern.search(line):
                return idx
        return None

    @classmethod
    def _score_table_candidate(
        cls,
        *,
        target_row: Dict[str, Any],
        table: Dict[str, Any],
        notes_lines: List[str],
        label_index: Optional[int],
    ) -> float:
        score = 0.0
        context_text = cls._table_context_text(table, notes_lines)
        context_lower = context_text.lower()
        label = str(target_row.get("label") or "").strip()
        label_lower = label.lower()

        if label_lower and label_lower in context_lower:
            score += 12.0

        target_type = str(target_row.get("target_type") or "").lower()
        if target_type == "supplement" and "supplement" in context_lower:
            score += 5.0
        if target_type == "table" and "table" in context_lower:
            score += 3.0

        for field_name, weight in (("description", 2.0), ("extraction_hint", 1.5)):
            field_value = str(target_row.get(field_name) or "")
            for token in cls._tokenize_text(field_value):
                if token in context_lower:
                    score += weight

        for token in cls._tokenize_text(label):
            if token in context_lower:
                score += 1.0

        header_tokens = cls._tokenize_text(" ".join(table.get("header") or []))
        for token in header_tokens:
            if token in context_lower:
                score += 0.2

        if label_index is not None:
            distance = abs(int(table["start_line"]) - label_index)
            score += max(0.0, 6.0 - min(distance, 12) * 0.5)
            if int(table["start_line"]) > label_index:
                score += 1.0

        score += cls._table_signal_score(table, context_text)
        score += min(len(table.get("rows") or []), 5) * 0.1
        return score

    @classmethod
    def _is_extractable_table_candidate(
        cls,
        *,
        target_row: Dict[str, Any],
        table: Dict[str, Any],
        notes_lines: List[str],
        score: float,
    ) -> bool:
        context_text = cls._table_context_text(table, notes_lines)
        context_lower = context_text.lower()
        if any(token in context_lower for token in EMBEDDED_BOILERPLATE_TOKENS):
            return False
        table_text = " ".join(
            [" ".join(table.get("header") or [])]
            + [" ".join(row) for row in table.get("rows") or []]
        ).lower()
        joined_signal_text = f"{context_lower} {table_text}"
        if any(token in joined_signal_text for token in NON_CANCER_CONTEXT_TOKENS):
            return False
        has_response_signal = any(token in joined_signal_text for token in RESPONSE_TABLE_TOKENS)
        if not has_response_signal and any(
            token in joined_signal_text for token in NON_RESPONSE_CHEMISTRY_TABLE_TOKENS
        ):
            return False
        if not has_response_signal:
            clinical_hits = sum(
                1 for token in CLINICAL_BASELINE_TABLE_TOKENS if token in table_text
            )
            if clinical_hits >= 2:
                return False
        has_drug_signal = any(token in joined_signal_text for token in DRUG_TABLE_TOKENS)
        has_cancer_signal = any(token in joined_signal_text for token in CANCER_CONTEXT_TOKENS)
        target_text = " ".join(
            str(target_row.get(field) or "")
            for field in ("label", "description", "extraction_hint")
        ).lower()
        target_requests_drug_response = any(
            token in target_text for token in DRUG_TABLE_TOKENS + RESPONSE_TABLE_TOKENS
        )
        target_has_cancer_context = any(token in target_text for token in CANCER_CONTEXT_TOKENS)
        if target_requests_drug_response and not (
            has_drug_signal
            and has_response_signal
        ):
            return False
        signal_score = cls._table_signal_score(table, context_text)
        target_has_data_signal = any(token in target_text for token in EMBEDDED_DATA_SIGNAL_TOKENS)
        if signal_score < 1.0 and not target_has_data_signal:
            return False
        if signal_score < 0.0:
            return False
        return score > 1.0

    @classmethod
    def _select_table_for_target(
        cls,
        target_row: Dict[str, Any],
        tables: List[Dict[str, Any]],
        notes_lines: List[str],
        used_table_starts: set[int],
    ) -> Optional[Dict[str, Any]]:
        if target_row.get("target_type") not in {"table", "supplement"}:
            return None

        available_tables = [table for table in tables if table["start_line"] not in used_table_starts]
        if not available_tables:
            return None

        label_index = cls._find_label_line_index(str(target_row.get("label") or ""), notes_lines)
        if label_index is None:
            scored_without_label = [
                (
                    cls._score_table_candidate(
                        target_row=target_row,
                        table=table,
                        notes_lines=notes_lines,
                        label_index=None,
                    ),
                    table,
                )
                for table in available_tables
            ]
            for score, table in sorted(scored_without_label, key=lambda item: item[0], reverse=True):
                if cls._is_extractable_table_candidate(
                    target_row=target_row,
                    table=table,
                    notes_lines=notes_lines,
                    score=score,
                ):
                    return table
            return None

        scored = [
            (
                cls._score_table_candidate(
                    target_row=target_row,
                    table=table,
                    notes_lines=notes_lines,
                    label_index=label_index,
                ),
                table,
            )
            for table in available_tables
        ]
        for score, table in sorted(scored, key=lambda item: item[0], reverse=True):
            if cls._is_extractable_table_candidate(
                target_row=target_row,
                table=table,
                notes_lines=notes_lines,
                score=score,
            ):
                return table
        return None

    @staticmethod
    def _clean_llm_csv(raw: str) -> str:
        """Strip code fences / leading prose; return CSV body or '' if NONE/empty."""
        text = (raw or "").strip()
        if not text:
            return ""
        # Drop ```csv ... ``` fences if present.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        if text.upper().startswith("NONE") or text.upper() == "NONE":
            return ""
        lines = [ln for ln in text.splitlines() if ln.strip()]
        data_lines = [ln for ln in lines if not ln.lstrip().startswith("#")]
        # Need a header + at least one data row, and a delimiter to be a matrix.
        if len(data_lines) < 2 or "," not in data_lines[0]:
            return ""
        return "\n".join(lines) + "\n"

    @staticmethod
    def _focus_drug_response_text(notes_text: str, limit: int) -> str:
        """Return up to ``limit`` chars of text biased toward drug-response (R)
        regions. Short papers fit whole; for long ones, keep the head plus
        windows around drug-response keyword hits (where R numbers live) instead
        of blindly truncating to the head and losing late Results/supplement
        sections."""
        if len(notes_text) <= limit:
            return notes_text
        low = notes_text.lower()
        head = limit // 3                      # always keep the head (abstract/intro)
        chunks: List[tuple[int, int]] = [(0, head)]
        win = 1500
        for kw in DRUG_RESPONSE_KEYWORDS:
            start = 0
            while True:
                i = low.find(kw, start)
                if i < 0:
                    break
                chunks.append((max(0, i - win // 3), i + win))
                start = i + len(kw)
        chunks.sort()
        merged: List[tuple[int, int]] = []
        for s, e in chunks:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        out: List[str] = []
        total = 0
        for s, e in merged:
            seg = notes_text[s:e]
            if total + len(seg) > limit:
                seg = seg[: limit - total]
            out.append(seg)
            total += len(seg)
            if total >= limit:
                break
        return "\n…\n".join(out)

    async def _llm_extract_drug_response(
        self,
        *,
        paper_name: str,
        paper_dir: Path,
        notes_text: str,
        mineru_md_path: Optional[Path],
        sequence_no: int,
    ) -> Optional[ProducedArtifact]:
        """Multimodal LLM extraction of drug-response (R) from prose + figures
        when it isn't available as a table. Returns a LOW-TRUST embedded_dataset
        artifact (`.llm_extracted.csv`) or None. Best-effort: never raises."""
        from analyze.image_blocks import build_openai_multimodal_parts, collect_filtered_image_paths
        from utils.llm_config import (
            build_openrouter_async_client,
            chat_completion_with_retry,
            get_openrouter_model,
        )

        try:
            images: List[Path] = []
            if mineru_md_path is not None:
                images = collect_filtered_image_paths(
                    notes_text, mineru_md_path.parent,
                    max_images=_LLM_EXTRACT_MAX_IMAGES,
                    prioritize_keywords=DRUG_RESPONSE_KEYWORDS,
                )
            text_window = self._focus_drug_response_text(notes_text, _LLM_EXTRACT_TEXT_LIMIT)
            user_text = f"{_LLM_EXTRACT_PROMPT}\n\n=== PAPER TEXT ===\n{text_window}"
            parts = build_openai_multimodal_parts(
                user_text, images, detail=_LLM_EXTRACT_IMAGE_DETAIL
            )
            model = (
                os.getenv("EMBEDDED_EXTRACTION_MODEL", "").strip()
                or get_openrouter_model("OPENROUTER_ANALYSIS_MODEL")
            )
            client = build_openrouter_async_client()
            usage: Dict[str, Any] = {
                "model": None, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            }
            started = time.monotonic()
            try:
                raw = await chat_completion_with_retry(
                    client, model=model,
                    messages=[
                        {"role": "system", "content": _LLM_EXTRACT_SYSTEM},
                        {"role": "user", "content": parts},
                    ],
                    stage="download", team=self.team_name, project_id=self.project_id,
                    usage_sink=usage,
                )
            finally:
                with __import__("contextlib").suppress(Exception):
                    await client.close()

            csv_body = self._clean_llm_csv(raw)
            self._record_extraction_cost(paper_name, usage, started)
            if not csv_body:
                pipeline_log(
                    f"llm extraction: no drug-response found in text/figures for {paper_name}",
                    stage="download", team=self.team_name, project_id=self.project_id,
                )
                return None

            out_path = paper_dir / f"{sequence_no:02d}_drug_response.llm_extracted.csv"
            out_path.write_text(csv_body, encoding="utf-8")
            pipeline_log(
                f"llm extraction: drug-response matrix extracted from text/figures "
                f"for {paper_name} (imgs={len(images)}) → {out_path.name}",
                stage="download", team=self.team_name, project_id=self.project_id,
            )
            return ProducedArtifact(
                file_path=str(out_path),
                artifact_type="embedded_dataset",
                acquisition_source="paper_figure_extraction",
                acquisition_status="completed",
                trust_level="low",
                confidence=0.4,
                produced_by=self.__class__.__name__,
                provenance={
                    "paper": paper_name,
                    "extraction_method": "multimodal_llm",
                    "model": usage.get("model") or model,
                    "image_count": len(images),
                    "note": "LLM-extracted from prose/figures; values may be approximate",
                },
            )
        except Exception as e:
            pipeline_log(
                f"llm extraction failed for {paper_name}: {type(e).__name__}: {e}",
                stage="download", team=self.team_name, project_id=self.project_id,
                level=logging.WARNING,
            )
            return None

    def _record_extraction_cost(self, paper_name: str, usage: Dict[str, Any], started: float) -> None:
        if not self.project_id or not usage.get("cost_usd"):
            return
        try:
            from database.dao import ProjectDAO
            usage["duration_ms"] = int((time.monotonic() - started) * 1000)
            ProjectDAO().record_stage_cost(self.project_id, paper_name, "download", usage)
        except Exception as e:
            pipeline_log(
                f"llm extraction cost record failed for {paper_name}: {e}",
                stage="download", team=self.team_name, project_id=self.project_id,
                level=logging.WARNING,
            )

    @classmethod
    def _write_embedded_dataset_csv(
        cls,
        *,
        paper_dir: Path,
        target_row: Dict[str, Any],
        table: Dict[str, Any],
        sequence_no: int,
    ) -> Path:
        file_stem = cls._slugify(str(target_row.get("label") or f"embedded_table_{sequence_no}"))
        output_path = paper_dir / f"{sequence_no:02d}_{file_stem}.embedded.csv"
        with output_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(table["header"])
            writer.writerows(table["rows"])
        return output_path

    @staticmethod
    def _infer_coverage(
        *,
        extracted_targets: List[Dict[str, Any]],
        task: AcquisitionTask,
        plan: DownloadPlan,
    ) -> CoverageSummary:
        text_parts: List[str] = []
        text_parts.extend(task.desired_outputs or [])
        text_parts.extend(plan.desired_outputs or [])
        for target in extracted_targets:
            text_parts.extend(
                [
                    str(target.get("label") or ""),
                    str(target.get("description") or ""),
                    str(target.get("extraction_hint") or ""),
                ]
            )
        joined = " ".join(text_parts).lower()

        sequencing_keywords = (
            "rna",
            "sequencing",
            "expression",
            "transcript",
            "count matrix",
            "gene expression",
        )
        drug_keywords = (
            "drug",
            "response",
            "ic50",
            "auc",
            "viability",
            "sensitivity",
        )
        return CoverageSummary(
            sequencing_data=any(keyword in joined for keyword in sequencing_keywords),
            drug_response_data=any(keyword in joined for keyword in drug_keywords),
        )

    async def run(
        self,
        *,
        plan: DownloadPlan,
        task: AcquisitionTask,
        plan_path: str,
    ) -> Dict[str, Any]:
        paper_name = Path(plan_path).stem.replace(".plan", "")
        notes_path = self._notes_path_for_plan(plan_path)
        notes_text = self._load_embedded_source_text(plan_path)
        notes_lines = notes_text.splitlines()
        mineru_md_path = self._mineru_markdown_path_for_plan(plan_path)

        self._update_status("Preparing embedded extraction task")
        target_rows = self._collect_targets(task, notes_text)
        markdown_tables = self._extract_markdown_tables(notes_lines)
        fenced_tables = self._extract_fenced_delimited_tables(notes_lines)
        html_tables = self._extract_html_tables(notes_text)
        table_candidates = sorted(
            markdown_tables + fenced_tables + html_tables,
            key=lambda item: item["start_line"],
        )
        paper_dir = self.output_dir / paper_name
        paper_dir.mkdir(parents=True, exist_ok=True)

        report_path = paper_dir / "embedded_extraction_report.json"
        manifest_csv_path = paper_dir / "embedded_targets_manifest.csv"
        used_table_starts: set[int] = set()
        extracted_targets: List[Dict[str, Any]] = []
        produced_artifacts: List[ProducedArtifact] = []

        for index, target_row in enumerate(target_rows, start=1):
            table = self._select_table_for_target(
                target_row=target_row,
                tables=table_candidates,
                notes_lines=notes_lines,
                used_table_starts=used_table_starts,
            )
            if table is None:
                target_row["dataset_extracted"] = False
                continue

            used_table_starts.add(table["start_line"])
            dataset_path = self._write_embedded_dataset_csv(
                paper_dir=paper_dir,
                target_row=target_row,
                table=table,
                sequence_no=index,
            )
            target_row["dataset_extracted"] = True
            target_row["dataset_path"] = str(dataset_path)
            target_row["row_count"] = len(table["rows"])
            target_row["column_count"] = len(table["header"])
            target_row["table_start_line"] = table["start_line"]
            extracted_targets.append(target_row)
            produced_artifacts.append(
                ProducedArtifact(
                    file_path=str(dataset_path),
                    artifact_type="embedded_dataset",
                    acquisition_source="paper_table_extraction",
                    acquisition_status="completed",
                    trust_level="medium",
                    confidence=0.75,
                    produced_by=self.__class__.__name__,
                    provenance={
                        "paper": paper_name,
                        "target_label": target_row.get("label"),
                        "target_type": target_row.get("target_type"),
                        "source_notes_path": str(notes_path),
                        "source_markdown_path": str(mineru_md_path) if mineru_md_path else str(notes_path),
                        "table_start_line": table["start_line"],
                        "source_format": table.get("source_format", "markdown_table"),
                        "row_count": len(table["rows"]),
                        "column_count": len(table["header"]),
                    },
                )
            )

        # External publisher supplements: drug-response tables almost always live
        # in supplementary files (BMC MOESM*, Springer/Nature ESM, ...), NOT in
        # the PDF body — so in-PDF extraction above can't reach them. When a
        # supplement target was requested, fetch the real files from the DOI
        # landing page into THIS unit dir (alongside the repository expression
        # data) so qualify can link E + R. Best-effort; paywalled/JS publishers
        # (MDPI/Elsevier) yield nothing.
        supplement_count = 0
        if any(r.get("target_type") == "supplement" for r in target_rows):
            from download.supplement_downloader import download_supplements, extract_doi
            doi = extract_doi(notes_text) or extract_doi(getattr(plan, "notes", "") or "")
            if doi:
                supp_files = download_supplements(
                    doi, str(paper_dir), project_id=self.project_id, team=self.team_name
                )
                supplement_count = len(supp_files)
                for sp in supp_files:
                    produced_artifacts.append(
                        ProducedArtifact(
                            file_path=sp,
                            artifact_type="dataset",
                            acquisition_source="supplementary_download",
                            acquisition_status="completed",
                            trust_level="medium",
                            confidence=0.7,
                            produced_by=self.__class__.__name__,
                            provenance={"paper": paper_name, "doi": doi, "source": "publisher_supplement_doi"},
                        )
                    )

        # Clinical requirement: drug-response (R) is not always a table — it can live in
        # FIGURES (dose-response curves, IC50 bar charts, heatmaps) or PROSE
        # ("IC50 was 2.5 µM for ..."). Deterministic table parsing above can't
        # reach those. When a figure/page target was requested, or nothing
        # tabular was extracted, run a multimodal LLM pass that reads the full
        # text + figures and extracts a samples×drugs drug-response matrix. The
        # result is LOW TRUST (figure values are often estimates) — named with a
        # `.llm_extracted.` marker so qualify caps the verdict at `loose`.
        llm_extracted_count = 0
        wants_nontabular = any(
            r.get("target_type") in {"figure", "page"} for r in target_rows
        )
        if (wants_nontabular or (len(extracted_targets) == 0 and supplement_count == 0)) \
                and _embedded_llm_extraction_enabled():
            llm_artifact = await self._llm_extract_drug_response(
                paper_name=paper_name,
                paper_dir=paper_dir,
                notes_text=notes_text,
                mineru_md_path=mineru_md_path,
                sequence_no=len(extracted_targets) + 1,
            )
            if llm_artifact is not None:
                produced_artifacts.append(llm_artifact)
                llm_extracted_count = 1

        report = {
            "paper": paper_name,
            "primary_strategy": plan.primary_strategy,
            "supplement_files_downloaded": supplement_count,
            "llm_extracted_drug_response": llm_extracted_count,
            "task_title": task.title,
            "success_criteria": task.success_criteria,
            "targets": target_rows,
            "desired_outputs": task.desired_outputs or plan.desired_outputs,
            "blocking_gaps": task.blocking_gaps or plan.blocking_gaps,
            "markdown_table_count": len(markdown_tables),
            "fenced_table_count": len(fenced_tables),
            "html_table_count": len(html_tables),
            "extracted_dataset_count": len(extracted_targets),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        with manifest_csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "target_type",
                    "label",
                    "page_hint",
                    "description",
                    "extraction_hint",
                    "evidence_found_in_analysis",
                    "dataset_extracted",
                    "dataset_path",
                    "row_count",
                    "column_count",
                    "table_start_line",
                ],
            )
            writer.writeheader()
            for row in target_rows:
                writer.writerow(row)

        evidence_hits = sum(1 for row in target_rows if row.get("evidence_found_in_analysis"))
        extracted_count = len(extracted_targets)
        # Downloaded external supplements + LLM-extracted R count as produced data.
        produced_count = extracted_count + supplement_count + llm_extracted_count
        extractable_targets = [
            row for row in target_rows if row.get("target_type") in {"table", "supplement"}
        ]
        confidence = round(min(1.0, 0.45 + 0.1 * evidence_hits + 0.15 * produced_count), 2) if target_rows else 0.2
        if not target_rows and produced_count == 0:
            status = "partial"
            remaining_gaps = list(
                dict.fromkeys(["embedded_targets_missing"] + list(task.blocking_gaps or plan.blocking_gaps))
            )
        elif produced_count == 0:
            status = "partial"
            remaining_gaps = list(
                dict.fromkeys(["embedded_dataset_unextracted"] + list(task.blocking_gaps or plan.blocking_gaps))
            )
        elif extracted_count < len(extractable_targets) and supplement_count == 0:
            status = "partial"
            remaining_gaps = list(task.blocking_gaps or plan.blocking_gaps)
        else:
            status = "completed"
            remaining_gaps = []

        evidence_status = "partial" if target_rows else "failed"
        produced_artifacts.extend(
            [
                ProducedArtifact(
                    file_path=str(report_path),
                    artifact_type="acquisition_evidence",
                    acquisition_source="paper_table_extraction",
                    acquisition_status=evidence_status,
                    trust_level="low",
                    confidence=confidence,
                    produced_by=self.__class__.__name__,
                    provenance={
                        "paper": paper_name,
                        "report_type": "embedded_extraction_report",
                        "target_count": len(target_rows),
                        "extracted_dataset_count": extracted_count,
                    },
                ),
                ProducedArtifact(
                    file_path=str(manifest_csv_path),
                    artifact_type="acquisition_evidence",
                    acquisition_source="paper_table_extraction",
                    acquisition_status=evidence_status,
                    trust_level="low",
                    confidence=confidence,
                    produced_by=self.__class__.__name__,
                    provenance={
                        "paper": paper_name,
                        "report_type": "embedded_targets_manifest",
                        "target_count": len(target_rows),
                        "extracted_dataset_count": extracted_count,
                    },
                ),
            ]
        )
        coverage = self._infer_coverage(extracted_targets=extracted_targets, task=task, plan=plan)

        self._update_status(
            f"Embedded extraction task finished with {extracted_count} extracted dataset(s) "
            f"from {len(target_rows)} structured target(s)"
        )
        return AcquisitionTaskResult(
            task_type="embedded_extraction",
            status=status,
            produced_artifacts=produced_artifacts,
            coverage=coverage,
            confidence=confidence,
            remaining_gaps=remaining_gaps,
            message=(
                f"Extracted {extracted_count} embedded dataset(s)"
                if extracted_count
                else f"Structured {len(target_rows)} embedded extraction target(s)"
            ),
            metadata={
                "target_count": len(target_rows),
                "evidence_hits": evidence_hits,
                "markdown_table_count": len(markdown_tables),
                "fenced_table_count": len(fenced_tables),
                "html_table_count": len(html_tables),
                "extracted_dataset_count": extracted_count,
                "source_markdown": str(mineru_md_path) if mineru_md_path else str(notes_path),
            },
        ).model_dump()

    async def cleanup(self) -> None:
        pipeline_log(
            f"{self.__class__.__name__} cleaned up",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )
