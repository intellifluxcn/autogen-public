"""Deterministic routing guard for acquisition plans.

The gate is intentionally conservative: it prevents plans with no executable
download path, controlled access, or author-contact-only instructions from
being sent to the browser agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal, Optional

from analyze.plan_model import AcquisitionTask, DownloadPlan, PlanStep


GateAction = Literal[
    "allow_repository_download",
    "route_author_contact",
    "route_embedded_extraction",
    "blocked_by_auth",
    "blocked_by_approval",
    "blocked_by_no_url",
    "manual_review",
]

ACTIONABLE_REPOSITORY_ACTIONS = {"navigate", "download", "login"}

DIRECT_DATA_URL_SUFFIXES = (
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
    ".gz",
    ".zip",
    ".tar",
    ".tgz",
    ".bz2",
    ".xz",
)

PUBLISHER_LANDING_TOKENS = (
    "doi.org/",
    "articlelanding",
    "articlepdf",
    "pubs.rsc.org",
    "onlinelibrary.wiley.com",
    "cell.com",
    "sciencedirect.com",
    "linkinghub.elsevier.com",
    "pmc.ncbi.nlm.nih.gov/articles/",
    "mdpi.com",
    "ashpublications.org",
    "jpet.aspetjournals.org",
)

CONTACT_TOKENS = (
    "contact author",
    "contact the author",
    "corresponding author",
    "email author",
    "email the author",
    "upon request",
    "available on request",
    "available upon request",
    "reasonable request",
    "request access",
    "request data",
)

APPROVAL_TOKENS = (
    "approval",
    "approved",
    "application form",
    "data access application",
    "data access committee",
    "dac",
    "institutional agreement",
    "controlled access",
    "dbgap",
    "ega",
    "egas",
    "phs",
)

CONTROLLED_ACCESS_URL_TOKENS = (
    "ega-archive.org",
    "dbgap",
    "ncbi.nlm.nih.gov/projects/gap",
    "study.cgi?study_id=phs",
)

CONTROLLED_ACCESS_REPOSITORY_TOKENS = (
    "european genome-phenome archive",
    "ega",
    "dbgap",
    "controlled access",
)

EMBEDDED_TOKENS = (
    "supplementary table",
    "supplementary data",
    "supplementary file",
    "supplementary material",
    "table s",
    "source data",
    "extract from table",
    "extract quantitative",
    "figure",
    "embedded",
    "table 1",
    "table 2",
    "table 3",
    "ic50",
    "viability",
    "drug sensitivity",
)


@dataclass(frozen=True)
class PlanGateDecision:
    """Route decision for one acquisition task."""

    action: GateAction
    reason: str
    has_actionable_url: bool
    matched_signals: list[str] = field(default_factory=list)
    replacement_task: Optional[AcquisitionTask] = None

    @property
    def allows_repository_download(self) -> bool:
        return self.action == "allow_repository_download"


def _step_text(task: AcquisitionTask) -> str:
    parts: list[str] = []
    for step in task.steps:
        parts.extend(
            [
                step.action or "",
                step.description or "",
                step.url or "",
                step.condition or "",
                step.method or "",
                step.prompt or "",
                " ".join(step.target_files or []),
            ]
        )
    return " ".join(parts)


def _combined_text(plan: DownloadPlan, task: AcquisitionTask) -> str:
    parts = [
        plan.source_paper_title or "",
        plan.data_accessibility or "",
        plan.repository or "",
        plan.notes or "",
        " ".join(plan.desired_outputs or []),
        " ".join(plan.blocking_gaps or []),
        task.title or "",
        task.success_criteria or "",
        task.notes or "",
        " ".join(task.desired_outputs or []),
        " ".join(task.blocking_gaps or []),
        _step_text(task),
    ]
    return " ".join(parts).lower()


def _matches(text: str, tokens: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for token in tokens:
        if " " in token or "-" in token:
            if token in text:
                matches.append(token)
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text):
            matches.append(token)
    return matches


def has_actionable_repository_url(task: AcquisitionTask) -> bool:
    """Return whether a repository task has a browser-executable URL."""

    for step in task.steps:
        if step.action in ACTIONABLE_REPOSITORY_ACTIONS and (step.url or "").strip():
            return True
    return False


def _actionable_urls(task: AcquisitionTask) -> list[str]:
    return [
        step.url.strip()
        for step in task.steps
        if step.action in ACTIONABLE_REPOSITORY_ACTIONS and (step.url or "").strip()
    ]


def _is_controlled_access_url(url: str) -> bool:
    url_lower = url.lower()
    return any(token in url_lower for token in CONTROLLED_ACCESS_URL_TOKENS)


def _has_public_actionable_url(task: AcquisitionTask) -> bool:
    return any(not _is_controlled_access_url(url) for url in _actionable_urls(task))


def _is_public_actionable_step(step: PlanStep) -> bool:
    if step.action not in {"navigate", "download"}:
        return False
    url = (step.url or "").strip()
    return bool(url) and not _is_controlled_access_url(url)


def public_repository_task_for_mixed_access(task: AcquisitionTask) -> AcquisitionTask:
    """Return a repository task containing only executable public URL steps.

    Generated plans often mix public GEO/GDC/GitHub/supplementary steps with
    EGA/dbGaP approval steps. The gate should not block the public portion, and
    downstream browser/direct executors should not spend time on login/request
    steps that cannot complete autonomously.
    """

    public_steps = [
        step.model_copy(update={"step": index})
        for index, step in enumerate(
            [step for step in task.steps if _is_public_actionable_step(step)],
            start=1,
        )
    ]
    if not public_steps:
        return task
    if len(public_steps) == len(task.steps):
        return task
    return task.model_copy(
        update={
            "steps": public_steps,
            "notes": "; ".join(
                part
                for part in [
                    task.notes,
                    "Restricted login/request steps were omitted; public repository URLs remain executable.",
                ]
                if part
            ),
        }
    )


def _is_controlled_access_context(plan: DownloadPlan, task: AcquisitionTask) -> bool:
    context_parts = [
        getattr(plan, "data_accessibility", None) or "",
        getattr(plan, "repository", None) or "",
    ]
    context = " ".join(context_parts).lower()
    if any(token in context for token in CONTROLLED_ACCESS_REPOSITORY_TOKENS):
        return True
    urls = _actionable_urls(task)
    return bool(urls) and all(_is_controlled_access_url(url) for url in urls)


def _has_direct_data_url(task: AcquisitionTask) -> bool:
    return any(url.lower().split("?", 1)[0].endswith(DIRECT_DATA_URL_SUFFIXES) for url in _actionable_urls(task))


def _is_publisher_or_doi_landing_only(task: AcquisitionTask) -> bool:
    urls = _actionable_urls(task)
    if not urls or _has_direct_data_url(task):
        return False
    return all(any(token in url.lower() for token in PUBLISHER_LANDING_TOKENS) for url in urls)


def _is_embedded_or_pdf_extraction_intent(text: str) -> bool:
    return any(
        token in text
        for token in (
            "main paper pdf",
            "supplementary information pdf",
            "supplementary materials",
            "supplementary material",
            "supplementary tables",
            "supplementary table",
            "extract quantitative",
            "extract or parse",
            "manually extract",
            "parse tabular",
            "table 1",
            "table 2",
            "table 3",
            "table s",
        )
    )


def _author_replacement_task(plan: DownloadPlan, task: AcquisitionTask) -> AcquisitionTask:
    return AcquisitionTask(
        task_type="author_contact",
        priority=task.priority,
        title=task.title or "Author contact required",
        success_criteria=task.success_criteria or "Record author-contact requirement",
        contact_targets=list(task.contact_targets),
        desired_outputs=list(task.desired_outputs or plan.desired_outputs),
        blocking_gaps=list(task.blocking_gaps or plan.blocking_gaps),
        notes=task.notes or plan.notes,
    )


def _embedded_replacement_task(plan: DownloadPlan, task: AcquisitionTask) -> AcquisitionTask:
    return AcquisitionTask(
        task_type="embedded_extraction",
        priority=task.priority,
        title=task.title or "Embedded extraction required",
        success_criteria=task.success_criteria or "Extract embedded table or supplementary data",
        embedded_targets=list(task.embedded_targets),
        desired_outputs=list(task.desired_outputs or plan.desired_outputs),
        blocking_gaps=list(task.blocking_gaps or plan.blocking_gaps),
        notes=task.notes or plan.notes,
    )


def embedded_replacement_task_for_repository(
    plan: DownloadPlan,
    task: AcquisitionTask,
) -> AcquisitionTask:
    """Build an embedded extraction task from a repository task."""

    return _embedded_replacement_task(plan, task)


def repository_task_has_embedded_fallback_intent(
    plan: DownloadPlan,
    task: AcquisitionTask,
) -> bool:
    """Return whether a failed repository task should fall back to embedded extraction."""

    if task.task_type != "repository_download":
        return False
    text = _combined_text(plan, task)
    embedded_signals = _matches(text, EMBEDDED_TOKENS)
    if not embedded_signals:
        return False
    if _has_direct_data_url(task):
        return False
    return _is_embedded_or_pdf_extraction_intent(text)


def _manual_replacement_task(
    plan: DownloadPlan,
    task: AcquisitionTask,
    decision: PlanGateDecision,
) -> AcquisitionTask:
    gaps = list(task.blocking_gaps or plan.blocking_gaps)
    if decision.action not in gaps:
        gaps.insert(0, decision.action)
    if not decision.has_actionable_url and "download_url_missing" not in gaps:
        gaps.insert(0, "download_url_missing")
    return AcquisitionTask(
        task_type="author_contact",
        priority=task.priority,
        title=task.title or "Manual acquisition review required",
        success_criteria=task.success_criteria or "Record blocked acquisition state",
        contact_targets=list(task.contact_targets),
        desired_outputs=list(task.desired_outputs or plan.desired_outputs),
        blocking_gaps=gaps,
        notes="; ".join(part for part in [task.notes or plan.notes, decision.reason] if part),
    )


def classify_repository_task(plan: DownloadPlan, task: AcquisitionTask) -> PlanGateDecision:
    """Classify whether a repository task should reach the browser agent."""

    if task.task_type != "repository_download":
        return PlanGateDecision(
            action="manual_review",
            reason=f"Plan gate only classifies repository_download tasks, got {task.task_type!r}",
            has_actionable_url=False,
        )

    text = _combined_text(plan, task)
    contact_signals = _matches(text, CONTACT_TOKENS)
    approval_signals = _matches(text, APPROVAL_TOKENS)
    embedded_signals = _matches(text, EMBEDDED_TOKENS)
    has_url = has_actionable_repository_url(task)

    if embedded_signals and (not has_url or (
        _is_publisher_or_doi_landing_only(task) and _is_embedded_or_pdf_extraction_intent(text)
    )):
        return PlanGateDecision(
            action="route_embedded_extraction",
            reason=(
                "Plan references embedded tables, figures, PDFs, or supplementary extraction "
                "without a direct data-file URL"
            ),
            has_actionable_url=has_url,
            matched_signals=embedded_signals,
            replacement_task=_embedded_replacement_task(plan, task),
        )

    if approval_signals and not _has_public_actionable_url(task):
        return PlanGateDecision(
            action="blocked_by_approval" if contact_signals else "blocked_by_auth",
            reason="Plan requires controlled access, approval, or institutional authorization",
            has_actionable_url=has_url,
            matched_signals=approval_signals + contact_signals,
        )

    if contact_signals and not has_url:
        return PlanGateDecision(
            action="route_author_contact",
            reason="Plan is author-contact or request-only and has no executable repository URL",
            has_actionable_url=has_url,
            matched_signals=contact_signals,
            replacement_task=_author_replacement_task(plan, task),
        )

    if not has_url:
        return PlanGateDecision(
            action="blocked_by_no_url",
            reason="Repository download task has no actionable URL",
            has_actionable_url=has_url,
        )

    return PlanGateDecision(
        action="allow_repository_download",
        reason="Repository task has at least one actionable URL and no blocking access signal",
        has_actionable_url=has_url,
    )


def normalize_download_plan_for_acquisition(
    plan: DownloadPlan,
) -> tuple[DownloadPlan, list[PlanGateDecision]]:
    """Apply deterministic strategy correction to a generated DownloadPlan.

    This is used immediately after LLM plan generation so obviously non-browser
    tasks do not remain encoded as repository downloads.
    """

    decisions: list[PlanGateDecision] = []
    normalized_tasks: list[AcquisitionTask] = []

    for task in plan.tasks:
        if task.task_type != "repository_download":
            normalized_tasks.append(task)
            continue

        decision = classify_repository_task(plan, task)
        decisions.append(decision)
        if decision.action == "allow_repository_download":
            normalized_tasks.append(task)
        elif decision.replacement_task is not None:
            normalized_tasks.append(decision.replacement_task)
        else:
            normalized_tasks.append(_manual_replacement_task(plan, task, decision))

    if not normalized_tasks:
        normalized_tasks = list(plan.tasks)

    data = plan.model_dump()
    data["tasks"] = [task.model_dump() for task in normalized_tasks]
    repo_task = next(
        (task for task in normalized_tasks if task.task_type == "repository_download"),
        None,
    )
    data["steps"] = [step.model_dump() for step in repo_task.steps] if repo_task else []

    changed_decisions = [
        decision for decision in decisions if decision.action != "allow_repository_download"
    ]
    if changed_decisions:
        note = (
            "Deterministic acquisition post-processing applied: "
            + "; ".join(
                f"{decision.action} ({decision.reason})" for decision in changed_decisions
            )
        )
        data["notes"] = "; ".join(part for part in [data.get("notes"), note] if part)

    return DownloadPlan.model_validate(data), decisions
