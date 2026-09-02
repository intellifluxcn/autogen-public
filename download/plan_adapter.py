"""Acquisition-plan helpers for repository website downloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from analyze.plan_model import AcquisitionTask, DownloadPlan
from download.acquisition_result import ProducedArtifact


def load_download_plan(plan_path: str) -> DownloadPlan:
    with open(plan_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return DownloadPlan.model_validate(data)


def resolve_repository_task(
    plan: DownloadPlan,
    task_override: Optional[AcquisitionTask] = None,
) -> AcquisitionTask:
    if task_override is not None:
        return task_override
    for task in plan.get_tasks_by_type("repository_download"):
        return task
    return AcquisitionTask(
        task_type="repository_download",
        priority=1,
        title="Primary repository download",
        success_criteria="Acquire the most relevant downloadable dataset files",
        steps=list(plan.steps),
        notes=plan.notes,
        desired_outputs=list(plan.desired_outputs),
        blocking_gaps=list(plan.blocking_gaps),
    )


def artifact_source_for_path(dataset_path: Path, repository_name: Optional[str]) -> str:
    name = dataset_path.name.lower()
    if "supp" in name or "table" in name:
        return "supplementary_download"
    repo = (repository_name or "").lower()
    if "supplement" in repo:
        return "supplementary_download"
    return "repository_download"


def build_downloaded_artifact_descriptor(
    *,
    dataset_path: Path,
    paper_name: str,
    plan: DownloadPlan,
    task_title: Optional[str],
    produced_by: str,
    confidence: Optional[float] = 1.0,
) -> ProducedArtifact:
    acquisition_source = artifact_source_for_path(dataset_path, plan.repository)
    return ProducedArtifact(
        file_path=str(dataset_path),
        artifact_type="dataset",
        acquisition_source=acquisition_source,
        acquisition_status="completed",
        trust_level="high",
        confidence=confidence,
        produced_by=produced_by,
        provenance={
            "paper": paper_name,
            "repository": plan.repository,
            "task_title": task_title,
            "primary_strategy": plan.primary_strategy,
        },
    )


def generate_prompt_from_plan(
    *,
    plan: DownloadPlan,
    paper_name: Optional[str],
    downloads_dir: Path,
    task_override: Optional[AcquisitionTask] = None,
) -> str:
    repository_task = resolve_repository_task(plan, task_override)

    if paper_name:
        download_path = downloads_dir / paper_name
    else:
        download_path = downloads_dir

    prompt = f"Your mission is to download the datasets for the paper: '{plan.source_paper_title}'.\n\n"
    prompt += f"Repository: {plan.repository or 'Unknown'}\n"
    prompt += f"Access Level: {plan.data_accessibility}\n"
    prompt += f"Requires Authentication: {'Yes' if plan.requires_authentication else 'No'}\n"
    if plan.estimated_download_size:
        prompt += f"Estimated Download Size: {plan.estimated_download_size}\n"
    prompt += "\n"

    prompt += "## DATA ACQUISITION STEPS\n"
    browser_actions = {"navigate", "download", "login"}
    skipped_steps: list[str] = []
    for step in repository_task.steps:
        if step.action not in browser_actions:
            skipped_steps.append(f"{step.step}. [{step.action}] {step.description}")
            continue
        line = f"{step.step}. [{step.action}] {step.description}"
        if step.url:
            line += f" | URL: {step.url}"
        if step.target_files:
            line += f" | Target files: {', '.join(step.target_files)}"
        if step.condition:
            line += f" | Condition: {step.condition}"
        if step.method:
            line += f" | Method: {step.method}"
        if step.prompt:
            line += f" | Input prompt: {step.prompt}"
        prompt += line + "\n"
    if skipped_steps:
        prompt += "\n## NON-BROWSER STEPS (DO NOT EXECUTE)\n"
        prompt += (
            "These steps are context only. Do not send emails, fill request forms, "
            "or manually extract figure/table values during repository download.\n"
        )
        for step in skipped_steps:
            prompt += step + "\n"
    task_notes = repository_task.notes or plan.notes
    if task_notes:
        prompt += f"\nPlan Notes: {task_notes}\n"
    prompt += "\n"
    prompt += "## INSTRUCTIONS\n"
    prompt += "Follow the structured steps above in order to download the required datasets.\n"
    prompt += "Treat each step action and URL as authoritative.\n\n"
    prompt += "## IMPORTANT GUIDELINES\n"
    prompt += "- Do NOT create todo.md or any tracking files — go straight to downloading\n"
    prompt += "- Do NOT send emails, submit access requests, or complete application/contact forms\n"
    prompt += "- Do NOT navigate to chrome://downloads or save the browser downloads page as a file\n"
    prompt += "- File names might not be exact matches - download datasets that are close enough\n"
    prompt += "- If a page doesn't load or returns errors, skip and move to the next step\n"
    prompt += "- If you've hit 3 minutes of runtime, call done with what you have\n"
    prompt += "- Background downloads may not show confirmation - if you clicked download, assume it's working\n"
    prompt += "- If exact URLs return 404 errors, use your best judgment to find alternatives\n"
    prompt += f"- Place all downloaded files in: '{download_path}'\n\n"

    if plan.requires_authentication:
        prompt += "## AUTOMATED AUTHENTICATION (IMPORTANT)\n"
        prompt += "- When you encounter a login/authentication page, first try to auto-fill credentials using the tool `get_linked_account_credentials(site)`.\n"
        prompt += "- Pass the login site's domain/host as the `site` argument (prefer the `step.url` host when available, otherwise use the current page URL).\n"
        prompt += "- If the tool returns no credentials, then call `request_human_help(...)` with what action/fields are needed.\n\n"

    scientific_guidelines = """## SCIENTIFIC DATA GUIDELINES (CRITICAL)

**FOCUS ON PROCESSED DATA — this is the primary goal:**
- ALWAYS download small processed files FIRST (Excel, CSV, Tab-delimited, .txt)
- These are usually labeled "Supplementary Files", "Processed Data", or "Table S1/S2/etc."
- Look on the main landing page - these files are typically <10MB and contain analyzed results
- These files are immediately usable for machine learning and are the PRIMARY TARGET
- Examples: Gene expression matrices, drug response tables, normalized counts

**If processed data is found and downloaded, call done — do not continue to raw data unless it is trivially easy (one click).**

**Raw data (GEO/SRA/PRIDE) is only worth pursuing if:**
- No processed data exists, AND
- The raw data is clearly linked and reasonably sized (<2GB)
- For massive datasets (>5GB), downloading an SRA Accession List (.txt) is sufficient

**GitHub repositories:**
- Dataset CSV/TSV/Excel files hosted on GitHub ARE worth downloading
- Do NOT download code repositories (.zip of code) — only actual dataset files
- Use the "Raw" button on GitHub to download individual data files

"""
    prompt += scientific_guidelines
    prompt += "Better to get some datasets quickly than spend all your time navigating complex sites!\n\n"

    prompt += """**LARGE FILE HANDLING:**
For files larger than 50MB that might timeout during browser download:
- Use the 'queue_background_download' tool to queue the URL for background download
- This will download the file AFTER the browser session ends, avoiding timeout issues
- Especially useful for: raw FASTQ files, proteomics archives, large GEO datasets
- If you discover a direct file URL or stable download href, prefer 'queue_background_download' over repeated browser clicks
- After queuing a direct download URL, keep navigating instead of waiting for the browser download to finish

**FILE SIZE LIMIT (CRITICAL):**
- DO NOT download files larger than 5GB - they will be rejected
- Skip raw sequencing files (FASTQ, BAM, SRA) if they exceed this limit
- Accession lists (.txt) and metadata files are preferred over massive raw data
- For huge datasets, just get the accession list - that's sufficient for our purposes
"""

    return prompt
