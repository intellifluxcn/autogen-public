"""title/abstract LLM pre-screen scorer.

Sits between the heuristic ranker (rank_papers_by_data_availability) and the
download loop in find/team.py. For EACH candidate in the FULL pool (no
front-N cap) it scores the paper's title + abstract with a lightweight
dedicated prompt via OpenRouter, then filters out papers below the threshold
and re-sorts the survivors by score descending.

Scoring scale: 0.0 - 10.0 (PRESCREEN_SCORE_SCALE_MAX). Higher = more likely
to contain the target data (gene expression / sequencing features paired with
drug-response measurements in tabular form).

Failure policy ("rather over-analyze than wrongly kill"):
  - A single transient LLM failure RETAINS the paper with prescreen_score=None
    and prescreen_error=True — it is NOT dropped, so it still reaches download.
  - But the failure RATE is tracked. If it exceeds PRESCREEN_FAILRATE_ABORT
    (default 0.5), PrescreenAbort is raised so Find fails loudly rather than
    silently retaining the whole (possibly 20k) pool on a systemic outage.

Persistence (G5 recall audit): every scored paper — passed, filtered-out, or
error — is written to the prescreen_scores table at score time via the DAO,
keyed (project_id, paper_id) with ON CONFLICT upsert (re-run safe).

Opt-in: the caller disables prescreen entirely by passing threshold=None
(driven by an unset PRESCREEN_SCORE_THRESHOLD env var). In that case this
module short-circuits and returns the candidate list unchanged — no LLM
calls, no cost, current behaviour preserved.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from utils.llm_config import (
    LLMCallFailed,
    build_openrouter_async_client,
    chat_completion_with_retry,
    get_openrouter_model,
)
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

PRESCREEN_SCORE_SCALE_MAX: float = 10.0
DEFAULT_PRESCREEN_CONCURRENCY: int = 8
DEFAULT_PRESCREEN_FAILRATE_ABORT: float = 0.5

# Bump on ANY change to the prompt/scale below — seeds the prescreen_cache
# prompt_hash so old-prompt cache rows become unreachable on the new prompt
# (mirrors ANALYSIS_PROMPT_TEMPLATE_VERSION). A bump also means the recall
# threshold must be re-calibrated.
PRESCREEN_PROMPT_TEMPLATE_VERSION = "2026-06-21-v2-rgate"

_PRESCREEN_SYSTEM_PROMPT = (
    "You are a fast relevance screener for a cancer drug-response dataset "
    "discovery pipeline. The pipeline seeks papers that provide a downloadable "
    "dataset linking, FOR THE SAME in-vitro tumor models, transcriptome / "
    "gene-expression data WITH drug-screening / drug-response measurements, so "
    "that each model can be ranked by drug sensitivity. The SCARCE, MANDATORY "
    "ingredient is the DRUG-RESPONSE data: most candidate papers publish "
    "expression / sequencing but NO drug-response measurements, and those are "
    "useless to this pipeline. Treat drug response as a hard GATE — a paper "
    "with no drug-response signal is irrelevant no matter how rich its omics. "
    "You see ONLY a paper's title and abstract, so judge POTENTIAL, not "
    "certainty: among papers that DO show a drug-response signal, when another "
    "required signal is plausible but unconfirmed, lean toward the higher band "
    "— full-text analysis verifies later, and missing a good paper is worse "
    "than passing a borderline one."
)

_PRESCREEN_USER_TEMPLATE = (
    "Score this paper from 0 to 10. DRUG-RESPONSE DATA IS THE GATE: this "
    "pipeline already has abundant expression/omics; what is scarce is "
    "drug-response measurements on in-vitro tumor models. A paper WITHOUT any "
    "drug-treatment / drug-response signal is useless here no matter how rich "
    "its sequencing.\n\n"
    "Three ingredients:\n"
    "1. IN-VITRO TUMOR MODEL (patient-derived or established; NOT in-vivo-only "
    "or clinical-trial-only). Any of: organoid / tumor organoid / PDO; cancer "
    "or tumor cell line, cell strain, patient-derived cells, primary tumor "
    "culture; cancer/tumor/glioma stem(-like) cells (CSC / GSC / TIC); "
    "tumorsphere / neurosphere / spheroid; or similar in-vitro models. Do NOT "
    "reward only organoids — all model types above count equally.\n"
    "2. [GATE] DRUG RESPONSE on those models — a quantitative readout (IC50, "
    "AUC, GR metrics, viability, dose-response, drug-sensitivity / DSS score, "
    "inhibition rate). Drug names may be abbreviations, brand names or compound "
    "codes (e.g. TMZ, Velcade, ZD6474) — do not penalize that. Multiple drugs "
    "(a panel) are better than one, but a single drug across many models still "
    "counts.\n"
    "3. TRANSCRIPTOME / SEQUENCING on the SAME models: RNA-seq, transcriptomic "
    "profiling, gene-expression matrix, microarray.\n\n"
    "Scoring (the drug-response gate is FIRM — never score >=3 for a paper with "
    "zero drug-response signal):\n"
    "  0-2  = NO drug-response signal of any kind — score HERE EVEN IF rich "
    "sequencing/expression is described (expression-only, omics-only, review, "
    "methods-only, or clinical-outcome-only with no in-vitro drug assay)\n"
    "  3-4  = drug response present but weak/unusable: NOT on an in-vitro model "
    "(e.g. clinical response only), OR purely qualitative with no quantitative "
    "readout\n"
    "  5-7  = quantitative drug response on in-vitro models (>=1 drug); "
    "expression / sequencing plausibly present\n"
    "  8-10 = paired quantitative drug response + expression on the SAME "
    "in-vitro models (matched / same-sample), enabling within-sample "
    "drug-sensitivity ranking\n\n"
    "Respond ONLY with compact JSON: "
    '{{"score": <number 0-10>, "reason": "<short phrase>"}}.\n\n'
    "TITLE: {title}\n\nABSTRACT: {abstract}"
)


class PrescreenAbort(RuntimeError):
    """Raised when the prescreen LLM failure rate exceeds the abort threshold.

    Signals a SYSTEMIC outage (service down / auth broken) rather than a
    one-off transient failure. Find should fail loudly instead of retaining
    the entire candidate pool.
    """


def _resolve_concurrency(concurrency: Optional[int]) -> int:
    if concurrency is not None:
        return max(1, concurrency)
    try:
        return max(1, int(os.getenv("PRESCREEN_CONCURRENCY", str(DEFAULT_PRESCREEN_CONCURRENCY))))
    except ValueError:
        return DEFAULT_PRESCREEN_CONCURRENCY


def _resolve_failrate_abort(failrate_abort: Optional[float]) -> float:
    if failrate_abort is not None:
        return failrate_abort
    try:
        return float(os.getenv("PRESCREEN_FAILRATE_ABORT", str(DEFAULT_PRESCREEN_FAILRATE_ABORT)))
    except ValueError:
        return DEFAULT_PRESCREEN_FAILRATE_ABORT


def _parse_score(raw: str) -> Optional[float]:
    """Extract a numeric 0-10 score from the LLM response.

    Tries strict JSON first, then a loose regex fallback. Returns None when
    no parseable number is found (caller treats that as a per-paper error).
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip ```json fences if present.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "score" in obj:
            return _clamp(float(obj["score"]))
    except (ValueError, TypeError):
        pass
    m = re.search(r'"?score"?\s*[:=]?\s*(-?\d+(?:\.\d+)?)', text)
    if not m:
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if m:
        try:
            return _clamp(float(m.group(1)))
        except ValueError:
            return None
    return None


def _parse_reason(raw: str) -> Optional[str]:
    if not raw:
        return None
    try:
        obj = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip())
        if isinstance(obj, dict):
            reason = obj.get("reason")
            return str(reason)[:280] if reason else None
    except (ValueError, TypeError):
        return None
    return None


def _clamp(score: float) -> float:
    return max(0.0, min(PRESCREEN_SCORE_SCALE_MAX, score))


def _prompt_hash() -> str:
    return hashlib.sha256(PRESCREEN_PROMPT_TEMPLATE_VERSION.encode("utf-8")).hexdigest()


def _content_hash(title: str, abstract: str) -> str:
    """SHA256 of whitespace-normalised title + abstract — the prescreen input.
    Invalidates the cache when a paper's title/abstract changes."""
    norm = " ".join((f"{title}\n{abstract}").split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _record(dao, project_id, paper_id, score, model, threshold, decision) -> None:
    if dao is None or not project_id or not paper_id:
        return
    try:
        dao.record_prescreen_score(
            project_id=project_id,
            paper_id=paper_id,
            prescreen_score=score,
            prescreen_model=model,
            threshold=threshold,
            decision=decision,
        )
    except Exception as e:  # persistence must never sink the pipeline
        pipeline_log(
            f"prescreen: failed to persist score for {paper_id}: {e}",
            stage="prescreen", team=None, project_id=project_id,
            level=logging.WARNING,
        )


async def prescreen_papers(
    candidates: List[Dict[str, Any]],
    *,
    project_id: Optional[str] = None,
    team_name: Optional[str] = None,
    threshold: Optional[float],
    concurrency: Optional[int] = None,
    failrate_abort: Optional[float] = None,
    client: Any = None,
    model: Optional[str] = None,
    dao: Any = None,
) -> List[Dict[str, Any]]:
    """Score the FULL candidate pool, filter < threshold, re-sort desc.

    When ``threshold`` is None the prescreen is DISABLED: the input list is
    returned unchanged with no LLM calls (opt-in / backward compat).
    """
    if threshold is None:
        return candidates
    if not candidates:
        return candidates

    if client is None:
        client = build_openrouter_async_client()
    if model is None:
        model = get_openrouter_model("OPENROUTER_PRESCREEN_MODEL")

    cap = _resolve_concurrency(concurrency)
    abort_rate = _resolve_failrate_abort(failrate_abort)
    sem = asyncio.Semaphore(cap)
    failures = 0

    pipeline_log(
        f"prescreen: start pool={len(candidates)} threshold={threshold} "
        f"model={model} concurrency={cap} failrate_abort={abort_rate}",
        stage="prescreen", team=team_name, project_id=project_id,
    )

    # Cross-project cache: reuse scores for papers already scored
    # by the same model+prompt+content in ANY project, so re-running the same
    # query doesn't re-call the LLM. content_hash is stashed on each paper for
    # the write-back below. dao=None (e.g. tests) → cache disabled, score all.
    prompt_hash = _prompt_hash()
    for paper in candidates:
        paper["_ps_chash"] = _content_hash(
            (paper.get("title") or "").strip(), (paper.get("abstract") or "").strip()
        )
    cache_hits = 0
    if dao is not None:
        try:
            wanted = {
                p["paper_id"]: p["_ps_chash"]
                for p in candidates if p.get("paper_id")
            }
            cached = dao.get_cached_prescreen_scores(model, prompt_hash, wanted)
            for paper in candidates:
                hit = cached.get(paper.get("paper_id"))
                if hit and hit.get("score") is not None:
                    paper["prescreen_score"] = hit["score"]
                    if hit.get("reason"):
                        paper["prescreen_reason"] = hit["reason"]
                    paper["_ps_cached"] = True
                    cache_hits += 1
        except Exception as e:  # cache must never sink the pipeline
            pipeline_log(
                f"prescreen: cache lookup failed ({type(e).__name__}: {e}); scoring all",
                stage="prescreen", team=team_name, project_id=project_id,
                level=logging.WARNING,
            )

    to_score = [p for p in candidates if not p.get("_ps_cached")]
    pipeline_log(
        f"prescreen: cache hits={cache_hits}/{len(candidates)} to_score={len(to_score)}",
        stage="prescreen", team=team_name, project_id=project_id,
    )

    # Shared cost sink across the concurrent scoring calls. asyncio is
    # single-threaded and the helper accumulates synchronously after its await,
    # so concurrent _score_one coroutines never race on this dict.
    prescreen_cost: Dict[str, Any] = {
        "model": None, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0
    }
    prescreen_started_at = time.monotonic()

    async def _score_one(paper: Dict[str, Any]) -> None:
        nonlocal failures
        title = (paper.get("title") or "").strip()
        abstract = (paper.get("abstract") or "").strip()
        messages = [
            {"role": "system", "content": _PRESCREEN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _PRESCREEN_USER_TEMPLATE.format(
                    title=title or "(no title)",
                    abstract=abstract or "(no abstract)",
                ),
            },
        ]
        async with sem:
            try:
                raw = await chat_completion_with_retry(
                    client,
                    model=model,
                    messages=messages,
                    stage="prescreen",
                    team=team_name,
                    project_id=project_id,
                    usage_sink=prescreen_cost,
                )
                score = _parse_score(raw)
                if score is None:
                    raise LLMCallFailed("unparseable prescreen response", is_retryable=False)
                paper["prescreen_score"] = score
                reason = _parse_reason(raw)
                if reason:
                    paper["prescreen_reason"] = reason
            except Exception as e:
                # Single transient/parse failure → RETAIN with score=None.
                failures += 1
                paper["prescreen_score"] = None
                paper["prescreen_error"] = True
                pipeline_log(
                    f"prescreen: paper {paper.get('paper_id')} score failed "
                    f"({type(e).__name__}: {e}); retained as error",
                    stage="prescreen", team=team_name, project_id=project_id,
                    level=logging.WARNING,
                )

    await asyncio.gather(*(_score_one(p) for p in to_score))

    # Project-level prescreen cost (only freshly-scored papers cost anything;
    # cache hits are free). Recorded under a synthetic paper name so it rolls up
    # into the per-project total without polluting per-paper rows.
    if dao is not None and project_id and prescreen_cost.get("cost_usd"):
        try:
            prescreen_cost["duration_ms"] = int((time.monotonic() - prescreen_started_at) * 1000)
            dao.record_stage_cost(project_id, "__prescreen__", "prescreen", prescreen_cost)
        except Exception as e:
            pipeline_log(
                f"prescreen: cost telemetry record failed ({type(e).__name__}: {e})",
                stage="prescreen", team=team_name, project_id=project_id,
                level=logging.WARNING,
            )

    # Write freshly-scored (non-error) papers back to the cross-project cache.
    if dao is not None and to_score:
        try:
            new_rows = [
                {
                    "paper_id": p.get("paper_id"),
                    "model_name": model,
                    "prompt_hash": prompt_hash,
                    "content_hash": p.get("_ps_chash"),
                    "prescreen_score": p.get("prescreen_score"),
                    "prescreen_reason": p.get("prescreen_reason"),
                }
                for p in to_score
                if p.get("paper_id") and not p.get("prescreen_error")
                and p.get("prescreen_score") is not None
            ]
            dao.insert_prescreen_cache(new_rows)
        except Exception as e:  # cache write must never sink the pipeline
            pipeline_log(
                f"prescreen: cache write failed ({type(e).__name__}: {e})",
                stage="prescreen", team=team_name, project_id=project_id,
                level=logging.WARNING,
            )

    # Abort guard uses the FRESHLY-SCORED denominator (cached papers can't fail).
    fail_rate = failures / len(to_score) if to_score else 0.0
    if fail_rate > abort_rate:
        pipeline_log(
            f"prescreen: ABORT — failure rate {fail_rate:.2f} > {abort_rate:.2f} "
            f"({failures}/{len(candidates)}); systemic LLM failure suspected",
            stage="prescreen", team=team_name, project_id=project_id,
            level=logging.ERROR,
        )
        raise PrescreenAbort(
            f"prescreen failure rate {fail_rate:.2f} exceeds abort threshold "
            f"{abort_rate:.2f} ({failures}/{len(candidates)} papers failed)"
        )

    # Filter + decision + persist. Every scored paper is persisted, including
    # filtered-out and error rows, for recall audit.
    survivors: List[Dict[str, Any]] = []
    for paper in candidates:
        paper_id = paper.get("paper_id")
        score = paper.get("prescreen_score")
        if paper.get("prescreen_error"):
            decision = "error"
            survivors.append(paper)  # error → retained (never dropped)
        elif score is not None and score >= threshold:
            decision = "passed"
            survivors.append(paper)
        else:
            decision = "filtered"
        _record(dao, project_id, paper_id, score, model, threshold, decision)

    # Re-sort survivors by score descending; error rows (None) sink to the end
    # but keep their relative (heuristic) order as a stable tie-break.
    survivors.sort(
        key=lambda p: p.get("prescreen_score") if p.get("prescreen_score") is not None else -1.0,
        reverse=True,
    )

    passed = sum(1 for p in candidates if p.get("prescreen_score") is not None
                 and not p.get("prescreen_error") and p.get("prescreen_score") >= threshold)
    # Drop internal cache bookkeeping keys before returning to the pipeline.
    for paper in candidates:
        paper.pop("_ps_chash", None)
        paper.pop("_ps_cached", None)
    pipeline_log(
        f"prescreen: done scored={len(candidates)} (llm={len(to_score)} cached={cache_hits}) "
        f"passed={passed} errors={failures} filtered={len(candidates) - len(survivors)} "
        f"survivors={len(survivors)}",
        stage="prescreen", team=team_name, project_id=project_id,
    )
    return survivors
