"""Centralized pipeline message templates in English."""

from __future__ import annotations

_MESSAGES: Dict[str, str] = {
    "pipeline.start_requested": "Pipeline {mode} requested",
    "pipeline.find_started": "Find stage started",
    "pipeline.analyze_started": "Analyze stage started",
    "pipeline.download_started": "Download stage started",
    "pipeline.find_none": "No papers found for query: {query}",
    "pipeline.find_success": "Successfully found {count} papers",
    "pipeline.paused_after_find": (
        "Find stage complete ({count} papers). Project auto-paused per "
        "PAUSE_AFTER_FIND — review results and resume to start analysis."
    ),
    "pipeline.analyze_summary": (
        "Analyzed {papers} papers: {valid} valid analyses, {suitable} with suitable data"
    ),
    "pipeline.no_suitable": "No suitable papers found. Aborting pipeline.",
    "pipeline.completed": (
        "Pipeline completed successfully. Total artifacts: {papers} papers, "
        "{analyses} analyses, {datasets} datasets"
    ),
    "pipeline.failed": "Pipeline failed: {error}",
    "find.search_start": "Starting Semantic Scholar search for: {query}",
    "find.candidate_summary": (
        "Found {candidates} candidate papers, will download until {target} are obtained"
    ),
    "find.need_more": (
        "Need {needed} more papers (have {existing}, target {target}). "
        "Trying up to {candidates} candidates in relevance order..."
    ),
    "find.candidate_try": "Candidate {candidate} ({index}/{total}): {title}",
    "find.downloading": "Downloading paper: {title} | Sources: {sources}",
    "find.downloaded": "Successfully downloaded: {file}",
    "find.uploaded_storage": "Uploaded to storage: {storage_key}",
    "find.upload_failed_local": "Warning: Failed to upload to storage (paper downloaded locally)",
    "find.no_oa_source": "Could not download: {title} (no open access source found)",
    "find.oa_supplement": (
        "OA-only search returned {count} papers, supplementing with non-OA results..."
    ),
    "find.no_candidates_hint": "No papers found. Try broadening the search query.",
    "find.already_downloaded": (
        "Found {count} papers already downloaded, counting toward target"
    ),
    "find.skipped_in_other_projects": (
        "{count} candidate paper(s) skipped — already in your other projects "
        "(this project will still target {target} fresh downloads)"
    ),
    "find.nothing_to_do": "Already have {count} papers (target: {target}), nothing to do",
    "find.download_complete_progress": "Download complete ({done}/{total})",
    "find.download_failed_remaining": (
        "Download failed, moving to next candidate ({remaining} remaining)"
    ),
    "find.obtained_summary": (
        "Obtained {obtained}/{requested} requested papers "
        "({newly} newly downloaded, {existing} previously downloaded, {tried} candidates tried)"
    ),
    "find.shortfall_warning": (
        "Only {obtained}/{requested} papers retrieved — candidate pool exhausted "
        "({shortfall} short). Downstream stages will process fewer papers than requested."
    ),
    "find.new_paper": "New paper: {file}",
    "find.failed": "Paper search/download failed: {error}",
    "analyze.init": "Initializing Analysis Team...",
    "analyze.start": "Starting analysis of {paper}",
    "analyze.extract_mineru": "Trying MinerU PDF extraction...",
    "analyze.extract_pdf": "Extracting text from PDF (pdfplumber)...",
    "analyze.mineru_ok": "MinerU OK ({chars} chars markdown)",
    "analyze.mineru_too_short": (
        "MinerU output too short ({chars} chars); falling back to pdfplumber"
    ),
    "analyze.images_attached": "Including {count} image(s) in LLM request",
    "analyze.extracted_chars": "Extracted {chars} characters from PDF",
    "analyze.llm_start": "Analyzing paper with LLM...",
    "analyze.no_suitable": "{paper} has no suitable data - no file created",
    "analyze.llm_failed": "Analysis aborted for {paper}: LLM call failed permanently (marked no_analysis)",
    "analyze.llm_failed_retryable": "Analysis aborted for {paper}: LLM call failed due to transient error — re-running this paper may succeed",
    "analyze.parse_degraded": "Analysis output for {paper} could not be auto-parsed; saved raw markdown for manual review",
    "analyze.plan_invalid": "Download plan for {paper} failed validation; markdown saved, awaiting manual review",
    "analyze.save_markdown": "Saving analysis to markdown...",
    "analyze.saved_markdown": "Analysis saved to {path}",
    "analyze.validate_plan": "Validating structured download plan (JSON)...",
    "analyze.saved_plan": "Download plan saved to {path}",
    "analyze.done": "Analysis complete for {paper}",
    "download.start": "Starting download process",
    "download.generate_prompt": "Generating prompt from JSON download plan...",
    "download.objective_ready": "Objective generated. Starting local browser agent...",
    "download.dir_created": "Paper-specific download folder created: {paper}/",
    "download.error": "An error occurred: {error}",
    "download.skip_already_downloaded": "Skipping {paper} (datasets already downloaded)",
    "download.skip_no_actionable_steps": (
        "Skipping {paper} (plan has no actionable automated steps)"
    ),
    "download.timeout": "Timeout: Process stopped at 5 minutes. Partial downloads may be available.",
    "download.queue_processing": "Processing {count} queued background downloads...",
    "download.queue_completed": "Completed {count} background downloads",
    "download.queue_error": "Warning: Background download error: {error}",
    "download.wait_in_progress": "Waiting for in-progress downloads to complete...",
    "download.wait_active": "Waiting for {count} downloads... ({seconds}s)",
    "download.uploaded_artifacts": "Uploaded {count} dataset artifacts to storage",
    "download.record_artifacts_warning": "Warning: Failed to record artifacts (downloads may have completed)",
    "download.finished": "Download process finished.",
}

PERSIST_MESSAGE_KEYS = {
    "pipeline.start_requested",
    "pipeline.find_started",
    "pipeline.analyze_started",
    "pipeline.download_started",
    "pipeline.find_none",
    "pipeline.find_success",
    "pipeline.paused_after_find",
    "pipeline.analyze_summary",
    "pipeline.no_suitable",
    "pipeline.completed",
    "pipeline.failed",
    "find.search_start",
    "find.candidate_summary",
    "find.obtained_summary",
    "find.failed",
    "analyze.start",
    "analyze.no_suitable",
    "analyze.done",
    "download.start",
    "download.skip_already_downloaded",
    "download.skip_no_actionable_steps",
    "download.timeout",
    "download.error",
    "download.uploaded_artifacts",
    "download.record_artifacts_warning",
    "download.finished",
}


def pm(key: str, **params: object) -> str:
    """Render an English pipeline message by template key."""
    template = _MESSAGES.get(key) or key
    safe_params = {k: str(v) for k, v in params.items()}
    try:
        return template.format(**safe_params)
    except Exception:
        return template


def should_persist_message_key(key: str) -> bool:
    """Whether a template key should be persisted into messages table."""
    return key in PERSIST_MESSAGE_KEYS


def pm_with_policy(key: str, **params: object) -> tuple[str, bool]:
    """Render localized message and return persistence decision."""
    return pm(key, **params), should_persist_message_key(key)
