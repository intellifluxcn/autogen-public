"""Post-run download finalization helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from download.acquisition_result import ProducedArtifact
from download.artifact_validation import artifact_file_signature, validate_downloaded_file
from utils.pipeline_log import pipeline_log


def _parse_storage_upload_attempts() -> int:
    raw = os.getenv("DOWNLOAD_STORAGE_UPLOAD_ATTEMPTS", "").strip()
    if not raw:
        return 3
    try:
        value = int(raw)
    except ValueError:
        return 3
    return value if value > 0 else 3


def _parse_storage_upload_retry_backoff_seconds() -> float:
    raw = os.getenv("DOWNLOAD_STORAGE_UPLOAD_RETRY_BACKOFF_SECONDS", "").strip()
    if not raw:
        return 2.0
    try:
        value = float(raw)
    except ValueError:
        return 2.0
    return value if value >= 0 else 2.0


def _should_delete_local_after_upload() -> bool:
    """STORAGE_DELETE_LOCAL_AFTER_UPLOAD env switch.

    Defaults to ``true`` because a deployment can hit a disk-full
    cascade (PG WAL ENOSPC → cluster recovery wedge) after only a few
    multi-GB dataset downloads accumulated as redundant copies of the
    OSS-backed canonical files. Set to ``false`` to keep the legacy
    "leave a local cache too" behaviour.
    """
    raw = os.getenv("STORAGE_DELETE_LOCAL_AFTER_UPLOAD", "true").strip().lower()
    return raw not in ("false", "0", "no", "off", "")


def _qualify_will_handle_local_cleanup() -> bool:
    """Whether to DEFER local-file deletion to the qualify stage.

    Deferring lets qualify read local copies (no re-fetch) but holds the WHOLE
    download batch on disk until qualify runs — which spiked disk to 100% on a
    GDSC run. Default now: do NOT defer — delete each file right after its
    confirmed remote upload (bounding download-stage disk to ~one file in flight)
    and let qualify re-materialise each unit from remote storage on demand
    (``_materialize_unit`` already falls back to storage.download; datasets AND
    embedded files now carry an oss/s3 key). Set DOWNLOAD_DEFER_LOCAL_FOR_QUALIFY=
    true to restore the keep-local-for-qualify behaviour (faster, higher peak).
    Only meaningful when qualify is enabled; with qualify off, post-upload
    deletion happens regardless."""
    if os.getenv("QUALIFY_ENABLED", "false").strip().lower() not in ("true", "1", "yes", "on"):
        return False
    return os.getenv("DOWNLOAD_DEFER_LOCAL_FOR_QUALIFY", "false").strip().lower() in (
        "true", "1", "yes", "on",
    )


def _maybe_remove_local_after_upload(
    local_path: str,
    storage_result: Any,
    *,
    team_name: str,
    project_id: Optional[str],
) -> None:
    """Drop the local copy once it's safely in remote storage.

    No-ops for the ``local`` backend (where the local file IS the
    canonical artifact), when the env switch is off, or when qualify is
    enabled (qualify reads the local copy then deletes it per-unit).
    Failure to remove is logged but never raised — disk hygiene is
    best-effort and must not break the pipeline.
    """
    if not _should_delete_local_after_upload():
        return
    if _qualify_will_handle_local_cleanup():
        return  # keep local for qualify; it deletes per-unit after validating
    backend = getattr(storage_result, "storage_backend", None) or "local"
    if backend == "local":
        return
    try:
        os.remove(local_path)
        pipeline_log(
            f"Removed local copy after {backend} upload: {local_path}",
            stage="download",
            team=team_name,
            project_id=project_id,
        )
    except FileNotFoundError:
        pass  # already gone, fine
    except Exception as e:
        pipeline_log(
            f"Could not remove local copy {local_path}: {e}",
            stage="download",
            team=team_name,
            project_id=project_id,
            level=logging.WARNING,
        )


def _upload_with_retries(
    *,
    storage: Any,
    local_path: str,
    key: str,
    team_name: str,
    project_id: Optional[str],
) -> Any:
    attempts = _parse_storage_upload_attempts()
    backoff_seconds = _parse_storage_upload_retry_backoff_seconds()
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            return storage.upload(local_path=local_path, key=key)
        except Exception as e:
            last_error = e
            if attempt >= attempts:
                break
            sleep_seconds = backoff_seconds * attempt
            pipeline_log(
                f"Storage upload failed; retrying attempt={attempt + 1}/{attempts} "
                f"key={key}: {e}",
                stage="download",
                team=team_name,
                project_id=project_id,
                level=logging.WARNING,
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    assert last_error is not None
    raise last_error


def _cleanup_stale_no_valid_report(
    *,
    dao: Any,
    project_id: str,
    paper_downloads_dir: Path,
    team_name: str,
) -> None:
    report_path = paper_downloads_dir / "no_valid_dataset_report.json"
    if report_path.exists():
        try:
            report_path.unlink()
            pipeline_log(
                f"Removed stale no-valid-dataset report after valid dataset capture: {report_path}",
                stage="download",
                team=team_name,
                project_id=project_id,
            )
        except OSError as e:
            pipeline_log(
                f"Failed to remove stale no-valid-dataset report {report_path}: {e}",
                stage="download",
                team=team_name,
                project_id=project_id,
                level=logging.WARNING,
            )

    delete_method = getattr(dao, "delete_artifact_by_path", None)
    if not callable(delete_method):
        return

    try:
        deleted_count = delete_method(project_id=project_id, file_path=str(report_path))
        if deleted_count:
            pipeline_log(
                f"Removed {deleted_count} stale no-valid-dataset artifact record(s)",
                stage="download",
                team=team_name,
                project_id=project_id,
            )
    except Exception as e:
        pipeline_log(
            f"Failed to remove stale no-valid-dataset artifact record: {e}",
            stage="download",
            team=team_name,
            project_id=project_id,
            level=logging.WARNING,
        )


async def finalize_download_run(
    *,
    download_queue: Any,
    paper_name: str,
    plan: Any,
    task_override: Any,
    paper_downloads_dir: Path,
    downloads_dir: Path,
    project_id: Optional[str],
    team_name: str,
    status_helper: Any,
    wait_for_downloads_to_complete: Any,
    storage: Any,
    build_downloaded_artifact_descriptor: Any,
    artifact_source_for_path: Any,
    produced_by: str,
) -> tuple[int, list[ProducedArtifact]]:
    dataset_count = 0
    invalid_file_reasons: dict[str, int] = {}
    invalid_files: list[dict[str, str]] = []

    if download_queue.get_pending_count() > 0:
        pending_count = download_queue.get_pending_count()
        pipeline_log(
            f"background queue: draining {pending_count} queued URL(s) after agent",
            stage="download",
            team=team_name,
            project_id=project_id,
        )
        status_helper.update_status_key("download.queue_processing", count=pending_count)
        status_helper.record_download_log(
            paper_name=paper_name,
            event_type="background_queue_start",
            status="info",
            message=f"Processing {pending_count} queued background downloads",
            metadata={"pending_count": pending_count},
        )

        try:
            completed_files = await download_queue.process_all(
                destination_dir=paper_downloads_dir,
                timeout_per_file=120,
                max_concurrent=2,
            )
            pipeline_log(
                f"DownloadQueue completed_files={completed_files}",
                stage="download",
                team=team_name,
                project_id=project_id,
            )
            status_helper.update_status_key("download.queue_completed", count=len(completed_files))
            status_helper.record_download_log(
                paper_name=paper_name,
                event_type="background_queue_completed",
                status="info",
                message=f"Completed {len(completed_files)} background downloads",
                metadata={"completed_files": completed_files},
            )
        except Exception as e:
            pipeline_log(
                f"DownloadQueue error: {e}",
                stage="download",
                team=team_name,
                project_id=project_id,
                level=logging.ERROR,
            )
            status_helper.update_status_key("download.queue_error", error=e)
            status_helper.record_download_log(
                paper_name=paper_name,
                event_type="background_queue_error",
                status="error",
                message=f"Background queue error: {e}",
            )

    if paper_downloads_dir.exists():
        status_helper.update_status_key("download.wait_in_progress")
        completed_in_time = await wait_for_downloads_to_complete(
            paper_downloads_dir, timeout_seconds=120
        )
        if not completed_in_time:
            # Clean up partial .crdownload files left by a timed-out download
            for stale in paper_downloads_dir.glob("*.crdownload"):
                try:
                    stale.unlink()
                    pipeline_log(
                        f"Removed stale partial download: {stale.name}",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                        level=logging.WARNING,
                    )
                except OSError:
                    pass

    # Capture produced-artifact descriptors BEFORE the DAO-recording pass below,
    # which uploads each dataset to remote storage and may then delete the local
    # copy (_maybe_remove_local_after_upload). Re-scanning the directory after
    # that pass would find an emptied dir and return [], so the scan must run
    # while the files still exist. This pass is gated only on the directory
    # existing (not on project_id) because produced_artifacts is the function's
    # return value used for run status/coverage even when there is no DB.
    produced_artifacts: list[ProducedArtifact] = []
    if paper_downloads_dir.exists():
        seen_signatures: set[tuple[int, str]] = set()
        for path in sorted(paper_downloads_dir.iterdir()):
            validation = validate_downloaded_file(path, plan=plan, task_override=task_override)
            if not validation.is_valid:
                continue
            signature = artifact_file_signature(path)
            if signature and signature in seen_signatures:
                continue
            if signature:
                seen_signatures.add(signature)
            produced_artifacts.append(
                build_downloaded_artifact_descriptor(
                    dataset_path=path,
                    paper_name=paper_name,
                    plan=plan,
                    task_title=task_override.title if task_override else None,
                    confidence=validation.confidence,
                )
            )

    if project_id and paper_downloads_dir.exists():
        try:
            from database.dao import ProjectDAO

            dao = ProjectDAO()
            seen_signatures: set[tuple[int, str]] = set()
            for dataset_file in sorted(paper_downloads_dir.iterdir()):
                validation = validate_downloaded_file(
                    dataset_file,
                    plan=plan,
                    task_override=task_override,
                )
                if not validation.is_valid:
                    invalid_file_reasons[validation.reason] = (
                        invalid_file_reasons.get(validation.reason, 0) + 1
                    )
                    invalid_files.append(
                        {
                            "file_name": dataset_file.name,
                            "reason": validation.reason,
                        }
                    )
                    pipeline_log(
                        f"Skipping invalid downloaded file: {dataset_file.name} "
                        f"reason={validation.reason}",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                        level=logging.WARNING,
                    )
                    continue

                signature = artifact_file_signature(dataset_file)
                if signature and signature in seen_signatures:
                    pipeline_log(
                        f"Skipping duplicate downloaded file: {dataset_file.name}",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                        level=logging.WARNING,
                    )
                    continue
                if signature:
                    seen_signatures.add(signature)

                provenance = {
                    "paper": paper_name,
                    "repository": plan.repository,
                    "task_title": task_override.title if task_override else None,
                    "primary_strategy": plan.primary_strategy,
                    "validation_reason": validation.reason,
                }
                acquisition_source = artifact_source_for_path(dataset_file, plan.repository)

                # Upload and DB insert are separated so a DB failure after a
                # successful upload doesn't silently orphan the file in storage.
                storage_key = f"datasets/{paper_name}/{dataset_file.name}"
                storage_result = None
                try:
                    # Sync storage.upload (S3/OSS HTTP) + time.sleep retry
                    # backoff would block the event loop for up to ~15s on
                    # remote backends. Offload to a thread so the rest of
                    # the backend stays responsive while the upload runs.
                    storage_result = await asyncio.to_thread(
                        _upload_with_retries,
                        storage=storage,
                        local_path=str(dataset_file),
                        key=storage_key,
                        team_name=team_name,
                        project_id=project_id,
                    )
                    pipeline_log(
                        f"Uploaded dataset artifact: {storage_key}",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                    )
                except Exception as upload_err:
                    pipeline_log(
                        f"Upload failed, will record local path only: "
                        f"{dataset_file.name} ({upload_err})",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                        level=logging.WARNING,
                    )

                try:
                    dao.add_artifact(
                        project_id=project_id,
                        artifact_type="dataset",
                        file_path=str(dataset_file),
                        stage_name="download",
                        acquisition_source=acquisition_source,
                        acquisition_status="completed",
                        trust_level=validation.trust_level,
                        confidence=validation.confidence,
                        produced_by=produced_by,
                        provenance=provenance,
                        **({
                            "storage_metadata": {
                                "storage_backend": storage_result.storage_backend,
                                "s3_bucket": storage_result.s3_bucket,
                                "s3_key": storage_result.s3_key,
                                "oss_bucket": storage_result.oss_bucket,
                                "oss_key": storage_result.oss_key,
                            }
                        } if storage_result else {}),
                    )
                    dataset_count += 1
                    # Disk hygiene: now that the file is referenced from the
                    # artifacts table AND living in remote storage, drop the
                    # local copy. Gated by STORAGE_DELETE_LOCAL_AFTER_UPLOAD
                    # (default on for remote backends).
                    if storage_result is not None:
                        _maybe_remove_local_after_upload(
                            str(dataset_file), storage_result,
                            team_name=team_name, project_id=project_id,
                        )
                except Exception as db_err:
                    pipeline_log(
                        f"DB insert failed for {dataset_file.name}: {db_err}",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                        level=logging.ERROR,
                    )

            if dataset_count > 0:
                _cleanup_stale_no_valid_report(
                    dao=dao,
                    project_id=project_id,
                    paper_downloads_dir=paper_downloads_dir,
                    team_name=team_name,
                )
                pipeline_log(
                    f"artifacts: recorded {dataset_count} dataset file(s) for paper={paper_name!r}",
                    stage="download",
                    team=team_name,
                    project_id=project_id,
                )
                status_helper.update_status_key("download.uploaded_artifacts", count=dataset_count)
            else:
                pipeline_log(
                    f"artifacts: no files to record under {paper_downloads_dir}",
                    stage="download",
                    team=team_name,
                    project_id=project_id,
                    level=logging.WARNING,
                )
                report_path = paper_downloads_dir / "no_valid_dataset_report.json"
                report = {
                    "paper": paper_name,
                    "artifact_status": "no_valid_dataset_files",
                    "reason": (
                        "download directory contained no valid dataset files"
                        if invalid_files
                        else "download directory was empty"
                    ),
                    "team_name": team_name,
                    "produced_by": produced_by,
                    "repository": getattr(plan, "repository", None),
                    "primary_strategy": getattr(plan, "primary_strategy", None),
                    "task_title": getattr(task_override, "title", None) if task_override else None,
                    "invalid_file_reasons": invalid_file_reasons,
                    "invalid_files": invalid_files,
                }
                try:
                    report_path.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError as write_err:
                    pipeline_log(
                        f"Failed to write no_valid_dataset_report: {write_err}",
                        stage="download",
                        team=team_name,
                        project_id=project_id,
                        level=logging.ERROR,
                    )
                    report_path = None  # skip DB insert below

                if report_path is not None:
                    try:
                        dao.add_artifact(
                            project_id=project_id,
                            artifact_type="acquisition_evidence",
                            file_path=str(report_path),
                            stage_name="download",
                            acquisition_source="repository_evidence",
                            acquisition_status="no_valid_dataset_files",
                            trust_level="low",
                            confidence=0.9,
                            produced_by=produced_by,
                            provenance={
                                "paper": paper_name,
                                "repository": getattr(plan, "repository", None),
                                "primary_strategy": getattr(plan, "primary_strategy", None),
                                "task_title": getattr(task_override, "title", None)
                                if task_override
                                else None,
                                "report_type": "no_valid_dataset_report",
                                "invalid_file_reasons": invalid_file_reasons,
                                "invalid_file_count": len(invalid_files),
                            },
                        )
                    except Exception as db_err:
                        pipeline_log(
                            f"DB insert failed for no_valid_dataset_report: {db_err}",
                            stage="download",
                            team=team_name,
                            project_id=project_id,
                            level=logging.ERROR,
                        )
                        # Keep the file on disk — it has useful debug info even without a DB record
        except Exception as e:
            pipeline_log(
                f"Failed to record dataset artifacts: {e}",
                stage="download",
                team=team_name,
                project_id=project_id,
                level=logging.WARNING,
            )
            status_helper.update_status_key("download.record_artifacts_warning")

    status_helper.update_status_key("download.finished")
    pipeline_log(
        f"Download run finished; base dir={downloads_dir}",
        stage="download",
        team=team_name,
        project_id=project_id,
    )

    return dataset_count, produced_artifacts
