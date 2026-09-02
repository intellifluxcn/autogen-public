#!/usr/bin/env python3
"""
Main orchestrator for the AutoGen Cancer Drug Response Research Pipeline.
Coordinates the three-stage pipeline: Find, Analysis, and Download teams.
"""

import asyncio
import argparse
import sys
import os
from pathlib import Path
from typing import Optional, List
import logging
from dotenv import load_dotenv
from database.dao import ProjectDAO
from storage import get_storage

from utils.logging_config import configure_logging

load_dotenv()

project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from utils.demo_user import DEMO_ADMIN_EMAIL
from utils.path_utils import get_papers_dir, get_project_notes_dir
from utils.pipeline_log import pipeline_log
from orchestrator_ui import OrchestratorUI, set_ui, update_stage, update_progress, update_status, set_orchestrator

configure_logging()


def qualify_enabled() -> bool:
    """Whether the qualify stage should run. Default OFF.

    The qualify stage shells out to the Claude CLI, which is not installed on
    the auto-deploy host. Gate it behind QUALIFY_ENABLED so production behaves
    exactly as before this stage existed (find→analyze→download→complete) until
    Claude Code is installed and authenticated on the host.
    """
    return os.getenv("QUALIFY_ENABLED", "").strip().lower() in ("1", "true", "yes")


class ResearchOrchestrator:

    def _import_team(self, dotted_path: str, attr: str, *, stage: str):
        """Import ``attr`` from ``dotted_path``; on ImportError emit a uniform
        diagnostic block (path, cwd) keyed by stage and re-raise.

        Replaces the three near-identical try/except ImportError blocks
        (FindTeam, AnalysisTeam, AcquisitionRouter) that used to copy 25
        lines of pipeline_log scaffolding.
        """
        try:
            module = __import__(dotted_path, fromlist=[attr])
            return getattr(module, attr)
        except ImportError as e:
            pipeline_log(
                f"Failed to import {attr} from {dotted_path}: {e}",
                stage=stage,
                team="Orchestrator",
                project_id=self.current_project_id,
                level=logging.ERROR,
            )
            pipeline_log(
                f"Python path: {sys.path}",
                stage=stage,
                team="Orchestrator",
                project_id=self.current_project_id,
                level=logging.ERROR,
            )
            pipeline_log(
                f"Current working directory: {os.getcwd()}",
                stage=stage,
                team="Orchestrator",
                project_id=self.current_project_id,
                level=logging.ERROR,
            )
            raise

    def __init__(self, use_ui: bool = True, prefer_web_ui: bool = False):
        self.use_ui = use_ui
        self.prefer_web_ui = prefer_web_ui
        self.ui = None
        self.find_team = None
        self.analysis_team = None
        self.download_team = None
        self.storage = None

        try:
            self.dao = ProjectDAO()
            pipeline_log("Database initialized successfully", stage="pipeline", component="orchestrator")
        except Exception as e:
            pipeline_log(
                f"Failed to initialize database: {e}",
                stage="pipeline",
                component="orchestrator",
                level=logging.ERROR,
            )
            raise

        self.current_project_id = None
        self.current_stage_id = None

    def _get_storage(self):
        """Lazily initialize storage backend."""
        if self.storage is None:
            self.storage = get_storage()
        return self.storage

    def _store_analysis_artifact(self, analysis: dict, project_id: Optional[str]) -> None:
        """Upload analysis markdown and optionally record artifact metadata."""
        notes_path = analysis.get("notes_path") if analysis else None
        if not notes_path or not os.path.exists(notes_path):
            return

        storage_key = f"analyses/{os.path.basename(notes_path)}"
        storage_result = self._get_storage().upload(local_path=notes_path, key=storage_key)

        if project_id:
            self.dao.add_artifact(
                project_id=project_id,
                artifact_type="analysis",
                file_path=notes_path,
                stage_name="analyze",
                storage_metadata={
                    "storage_backend": storage_result.storage_backend,
                    "s3_bucket": storage_result.s3_bucket,
                    "s3_key": storage_result.s3_key,
                    "oss_bucket": storage_result.oss_bucket,
                    "oss_key": storage_result.oss_key,
                },
            )
            pipeline_log(
                f"Uploaded and recorded analysis artifact: {storage_key}",
                stage="analyze",
                team="Orchestrator",
                project_id=project_id,
            )
        else:
            pipeline_log(
                f"Uploaded analysis artifact (no project record): {storage_key}",
                stage="analyze",
                team="Orchestrator",
                project_id=project_id,
            )

    def setup_ui(self):
        if self.use_ui and not self.ui:
            self.ui = OrchestratorUI()
            set_ui(self.ui)

            if hasattr(self.ui, '_ui_mode'):
                pipeline_log(
                    f"Using UI mode: {self.ui._ui_mode}",
                    stage="pipeline",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                )
                if self.ui._ui_mode == "web":
                    pipeline_log(
                        "Web UI detected - projects will appear in browser at http://localhost:3000",
                        stage="pipeline",
                        team="Orchestrator",
                        project_id=self.current_project_id,
                    )
                else:
                    pipeline_log(
                        "Using console mode",
                        stage="pipeline",
                        team="Orchestrator",
                        project_id=self.current_project_id,
                    )

            self.ui.start()
            update_status("AutoGen Research Pipeline initialized", "system")

    async def run_full_pipeline(self, initial_query: str, project_name: str = "") -> None:
        self.setup_ui()

        if not project_name:
            project_name = f"Research: {initial_query[:50]}..."

        if len(project_name) > 255:
            project_name = project_name[:252] + "..."

        self.current_project_id = self.dao.create_project(project_name, initial_query, user_email=DEMO_ADMIN_EMAIL)
        pipeline_log(
            f"Created project: {self.current_project_id}",
            stage="pipeline",
            team="Orchestrator",
            project_id=self.current_project_id,
        )

        self.dao.add_message(self.current_project_id, "Pipeline started",
                             team_name="Orchestrator", message_type="system")

        pipeline_log(
            "Starting full research pipeline",
            stage="pipeline",
            team="Orchestrator",
            project_id=self.current_project_id,
        )
        update_stage("Pipeline - Starting")
        update_progress(0.0)

        try:
            self.current_stage_id = self.dao.start_stage(self.current_project_id, "find")
            pipeline_log(
                "Stage 1: Finding and downloading papers",
                stage="find",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            self.dao.add_message(self.current_project_id, "Starting paper search", stage_name="find", message_type="system")

            update_stage("Stage 1 - Finding Papers")
            update_progress(0.1)
            update_status("Searching for and downloading research papers", "system")

            FindTeam = self._import_team("find.team", "FindTeam", stage="find")
            self.find_team = FindTeam(team_name="FindTeam", project_id=self.current_project_id)
            papers = await self.find_team.run(initial_query)

            if not papers:
                self.dao.complete_stage(self.current_stage_id, status="failed", error_message="No papers found")
                self.dao.update_project_status(self.current_project_id, "failed")
                return

            pipeline_log(
                f"Found and recorded {len(papers)} papers as artifacts during download",
                stage="find",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            self.dao.complete_stage(self.current_stage_id, status="completed")

            pipeline_log(
                f"Found {len(papers)} papers",
                stage="find",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            update_progress(0.3)
            update_status(f"Found {len(papers)} papers", "system")

            self.current_stage_id = self.dao.start_stage(self.current_project_id, "analyze")
            pipeline_log(
                "Stage 2: Analyzing papers for data suitability",
                stage="analyze",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            update_stage("Stage 2 - Analyzing Papers")
            update_progress(0.4)
            update_status("Analyzing papers for data suitability", "system")

            AnalysisTeam = self._import_team("analyze.team", "AnalysisTeam", stage="analyze")
            self.analysis_team = AnalysisTeam(team_name="AnalysisTeam", project_id=self.current_project_id)
            analyses = []

            for i, paper_path in enumerate(papers):
                pipeline_log(
                    f"Analyzing {paper_path}",
                    stage="analyze",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                )
                analysis = await self.analysis_team.run(paper_path)
                if analysis:
                    analyses.append(analysis)
                    try:
                        self._store_analysis_artifact(analysis, self.current_project_id)
                    except Exception as e:
                        pipeline_log(
                            f"Failed to upload analysis artifact: {e}",
                            stage="analyze",
                            team="Orchestrator",
                            project_id=self.current_project_id,
                            level=logging.WARNING,
                        )

                progress = 0.4 + (0.3 * (i + 1) / len(papers))
                update_progress(progress)

            if not analyses:
                pipeline_log(
                    "No suitable papers found. Aborting pipeline.",
                    stage="analyze",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                    level=logging.ERROR,
                )
                update_status("No suitable papers found. Pipeline aborted.", "error")
                return

            pipeline_log(
                f"Completed analysis of {len(analyses)} papers",
                stage="analyze",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            update_progress(0.7)
            update_status(f"Completed analysis of {len(analyses)} papers", "system")

            self.dao.complete_stage(self.current_stage_id)

            self.current_stage_id = self.dao.start_stage(self.current_project_id, "download")
            pipeline_log(
                "Stage 3: Downloading datasets",
                stage="download",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            update_stage("Stage 3 - Downloading Datasets")
            update_progress(0.8)
            update_status("Downloading datasets based on analysis", "system")

            AcquisitionRouter = self._import_team(
                "download.router", "AcquisitionRouter", stage="download"
            )
            self.download_team = AcquisitionRouter(project_id=self.current_project_id)
            downloads = []

            suitable_analyses = [a for a in analyses if a.get('has_suitable_data') in ['Yes', 'Partial']]
            pipeline_log(
                f"Found {len(suitable_analyses)} papers with suitable data (filtered out No)",
                stage="download",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            for i, analysis in enumerate(suitable_analyses):
                pipeline_log(
                    f"Downloading data for {analysis['title']}",
                    stage="download",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                )
                plan_path = analysis.get('plan_path')
                download_result = await self.download_team.run(plan_path=plan_path)
                if download_result:
                    downloads.append(download_result)

                progress = 0.8 + (0.15 * (i + 1) / len(suitable_analyses))
                update_progress(progress)

            self.dao.complete_stage(self.current_stage_id)

            # Stage 4: Qualify downloaded datasets. Gated OFF by
            # default — skipped cleanly unless QUALIFY_ENABLED is set, so
            # the pipeline behaves exactly as before this stage existed.
            qualify_counts = None
            if qualify_enabled():
                self.current_stage_id = self.dao.start_stage(self.current_project_id, "qualify")
                pipeline_log(
                    "Stage 4: Qualifying datasets",
                    stage="qualify",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                )
                update_stage("Stage 4 - Qualifying Datasets")
                update_progress(0.95)
                update_status("Qualifying downloaded datasets", "system")

                QualifyTeam = self._import_team("qualify.team", "QualifyTeam", stage="qualify")
                qualify_team = QualifyTeam(project_id=self.current_project_id)
                try:
                    qualify_counts = await qualify_team.run()
                finally:
                    await qualify_team.cleanup()

                # Fail-safe: a health-check failure (run() → None) records
                # the qualify STAGE as error but does NOT fail the project —
                # find/analyze/download already succeeded; qualify is a bolt-on.
                self._finalize_qualify_stage(qualify_counts)
            else:
                pipeline_log(
                    "Qualify stage disabled (QUALIFY_ENABLED not set) — skipping.",
                    stage="qualify",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                )

            pipeline_log(
                f"Pipeline complete. Downloaded {len(downloads)} datasets; qualify={qualify_counts}",
                stage="pipeline",
                team="Orchestrator",
                project_id=self.current_project_id,
            )
            update_progress(1.0)
            update_stage("Pipeline - Complete")
            update_status(f"Pipeline complete! Downloaded {len(downloads)} datasets", "system")

            self.dao.update_project_status(self.current_project_id, "completed")

        except Exception as e:
            pipeline_log(
                f"Pipeline failed: {e}",
                stage="pipeline",
                team="Orchestrator",
                project_id=self.current_project_id,
                level=logging.ERROR,
            )
            update_status(f"Pipeline failed: {e}", "error")
            if self.current_stage_id:
                self.dao.complete_stage(self.current_stage_id, status="failed", error_message=str(e))
            self.dao.update_project_status(self.current_project_id, "failed")
            raise
        finally:
            await self.cleanup()

    def _finalize_qualify_stage(self, qualify_counts) -> bool:
        """Record the qualify stage outcome and return whether it ran.

        Mark the stage as error only when it COULDN'T RUN (health-check
        failure → run() returns None). A returned counts dict completes the
        stage even if it carries per-dataset "error" verdicts. This matches
        the Web path (web_ui/backend/orchestrator_integration.py) so CLI and
        Web stay consistent.

        Fail-safe: a health-check failure records the qualify STAGE as
        error but does NOT fail the project — find/analyze/download already
        succeeded and qualify is a bolt-on. Returns True if qualify ran (stage
        completed), False if it could not run (stage marked error, project
        still allowed to complete).
        """
        if qualify_counts is None:
            self.dao.complete_stage(
                self.current_stage_id,
                status="failed",
                error_message="qualify health-check failed: claude unavailable",
            )
            pipeline_log(
                "Qualify stage could not run: claude unavailable. Install the "
                "Claude CLI and authenticate (`claude login`) so health checks "
                "pass, then re-run the qualify stage. The project still "
                "completes — find/analyze/download succeeded.",
                stage="qualify",
                team="Orchestrator",
                project_id=self.current_project_id,
                level=logging.ERROR,
            )
            update_status("Qualify stage unavailable: claude not installed", "error")
            return False

        self.dao.complete_stage(self.current_stage_id)
        return True

    def load_analysis_data_from_file(self, analysis_file_path: str) -> dict:
        """
        Load analysis data from markdown file and derive JSON plan path.
        If markdown file exists, paper has suitable data (implicit).
        """
        from pathlib import Path

        analysis_path = Path(analysis_file_path)
        if not analysis_path.exists():
            raise FileNotFoundError(f"Analysis file not found: {analysis_file_path}")

        paper_name = analysis_path.stem

        analysis_data = {
            "paper_path": str(Path(get_papers_dir()) / f"{paper_name}.pdf"),
            "notes_path": str(analysis_path),
            "plan_path": str(analysis_path.with_suffix(".plan.json")),
            "title": paper_name,
            "has_suitable_data": "Yes",
            "analysis_complete": True
        }

        return analysis_data

    async def run_stage(self, stage: str, input_data: str) -> Optional[any]:
        self.setup_ui()
        pipeline_log(f"Running stage: {stage}", stage=stage, team="Orchestrator", project_id=self.current_project_id)
        update_stage(f"Running {stage.title()} Stage")
        update_progress(0.0)

        try:
            if stage == "find":
                update_status("Searching for and downloading papers", "system")
                FindTeam = self._import_team("find.team", "FindTeam", stage="find")
                self.find_team = FindTeam(team_name="FindTeam")
                result = await self.find_team.run(input_data)
                update_progress(1.0)
                return result
            elif stage == "analyze":
                update_status("Analyzing paper for data suitability", "system")
                AnalysisTeam = self._import_team(
                    "analyze.team", "AnalysisTeam", stage="analyze"
                )
                self.analysis_team = AnalysisTeam(team_name="AnalysisTeam")
                result = await self.analysis_team.run(input_data)
                if result:
                    try:
                        self._store_analysis_artifact(result, self.current_project_id)
                    except Exception as e:
                        pipeline_log(
                            f"Failed to upload analysis artifact: {e}",
                            stage="analyze",
                            team="Orchestrator",
                            project_id=self.current_project_id,
                            level=logging.WARNING,
                        )
                update_progress(1.0)
                return result
            elif stage == "download":
                update_status("Downloading dataset", "system")
                AcquisitionRouter = self._import_team(
                    "download.router", "AcquisitionRouter", stage="download"
                )

                pipeline_log(
                    f"Download input_data type: {type(input_data)}, value: {input_data}",
                    stage="download",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                )

                if isinstance(input_data, str) and not os.path.exists(input_data):
                    notes_project_path = os.path.join(get_project_notes_dir(), input_data)
                    if os.path.exists(notes_project_path):
                        pipeline_log(
                            f"Found file in notes/project/: {notes_project_path}",
                            stage="download",
                            team="Orchestrator",
                            project_id=self.current_project_id,
                        )
                        input_data = notes_project_path

                if isinstance(input_data, str) and os.path.exists(input_data):
                    if input_data.endswith('.plan.json'):
                        pipeline_log(
                            f"Using JSON plan file for download: {input_data}",
                            stage="download",
                            team="Orchestrator",
                            project_id=self.current_project_id,
                        )
                        plan_path = input_data
                    else:
                        pipeline_log(
                            f"Unsupported file type: {input_data}",
                            stage="download",
                            team="Orchestrator",
                            project_id=self.current_project_id,
                            level=logging.ERROR,
                        )
                        raise ValueError(f"Unsupported file type. Expected .plan.json file, got: {input_data}")
                elif isinstance(input_data, dict):
                    pipeline_log(
                        "Using dict with plan_path",
                        stage="download",
                        team="Orchestrator",
                        project_id=self.current_project_id,
                    )
                    plan_path = input_data.get('plan_path')
                    if not plan_path:
                        raise ValueError(f"Dict must contain 'plan_path' key with JSON plan file path")
                else:
                    pipeline_log(
                        f"Invalid input_data: {input_data}",
                        stage="download",
                        team="Orchestrator",
                        project_id=self.current_project_id,
                        level=logging.ERROR,
                    )
                    raise ValueError(f"Invalid input_data. Expected JSON plan path or dict, got: {type(input_data)}")

                self.download_team = AcquisitionRouter()
                result = await self.download_team.run(plan_path=plan_path)
                update_progress(1.0)
                return result
            else:
                raise ValueError(f"Unknown stage: {stage}")

        except Exception as e:
            pipeline_log(
                f"Stage {stage} failed: {e}",
                stage=stage,
                team="Orchestrator",
                project_id=self.current_project_id,
                level=logging.ERROR,
            )
            update_status(f"Stage {stage} failed: {e}", "error")
            raise
        finally:
            await self.cleanup()

    async def run_parallel_analysis(self, paper_paths: List[str]) -> List[dict]:
        self.setup_ui()
        pipeline_log(
            f"Running parallel analysis on {len(paper_paths)} papers",
            stage="analyze",
            team="Orchestrator",
            project_id=self.current_project_id,
        )
        update_stage("Parallel Analysis")
        update_status(f"Running parallel analysis on {len(paper_paths)} papers", "system")

        AnalysisTeam = self._import_team("analyze.team", "AnalysisTeam", stage="analyze")
        analysis_teams = [AnalysisTeam(team_name=f"AnalysisTeam-{i+1}") for i, _ in enumerate(paper_paths)]

        try:
            tasks = [
                team.run(paper_path)
                for team, paper_path in zip(analysis_teams, paper_paths)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            analyses = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    pipeline_log(
                        f"Analysis of {paper_paths[i]} failed: {result}",
                        stage="analyze",
                        team="Orchestrator",
                        project_id=self.current_project_id,
                        level=logging.ERROR,
                    )
                elif result:
                    analyses.append(result)

            return analyses

        finally:
            for team in analysis_teams:
                await team.cleanup()

    async def run_parallel_downloads(self, analyses: List[dict]) -> List[dict]:
        suitable_analyses = [a for a in analyses if a.get('has_suitable_data') in ['Yes', 'Partial']]
        pipeline_log(
            f"Running parallel downloads for {len(suitable_analyses)} papers with suitable data (filtered out No)",
            stage="download",
            team="Orchestrator",
            project_id=self.current_project_id,
        )
        update_stage("Parallel Downloads")
        update_status(f"Running parallel downloads for {len(suitable_analyses)} papers", "system")

        AcquisitionRouter = self._import_team(
            "download.router", "AcquisitionRouter", stage="download"
        )
        download_teams = [
            AcquisitionRouter(team_name=f"AcquisitionRouter-{i+1}")
            for i, _ in enumerate(suitable_analyses)
        ]

        try:
            tasks = [
                team.run(plan_path=analysis.get('plan_path'))
                for team, analysis in zip(download_teams, suitable_analyses)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            downloads = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    pipeline_log(
                        f"Download for {suitable_analyses[i]['title']} failed: {result}",
                        stage="download",
                        team="Orchestrator",
                        project_id=self.current_project_id,
                        level=logging.ERROR,
                    )
                elif result:
                    downloads.append(result)

            return downloads

        finally:
            await self._cleanup_teams_concurrently(download_teams, label="parallel download")

    async def _cleanup_teams_concurrently(self, teams, *, label: str = "team") -> None:
        """Run cleanup() on a list of teams concurrently with a per-team timeout.

        A hung Playwright/browser-use teardown on one team must not block
        cleanup of the others — gather() with return_exceptions=True
        guarantees every cleanup is awaited even if peers raise or time out.
        """
        per_team_timeout = float(os.getenv("TEAM_CLEANUP_TIMEOUT_SECONDS", "20"))

        async def _safe(team):
            if team is None:
                return None
            try:
                await asyncio.wait_for(team.cleanup(), timeout=per_team_timeout)
            except asyncio.TimeoutError:
                pipeline_log(
                    f"{label} cleanup: timeout after {per_team_timeout:.0f}s for "
                    f"{getattr(team, 'team_name', type(team).__name__)}; abandoning",
                    stage="pipeline",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                    level=logging.WARNING,
                )
            except Exception as e:
                pipeline_log(
                    f"{label} cleanup error for "
                    f"{getattr(team, 'team_name', type(team).__name__)}: {e}",
                    stage="pipeline",
                    team="Orchestrator",
                    project_id=self.current_project_id,
                    level=logging.WARNING,
                )

        await asyncio.gather(*[_safe(t) for t in teams], return_exceptions=False)

    async def cleanup(self):
        pipeline_log(
            "Cleaning up resources",
            stage="pipeline",
            team="Orchestrator",
            project_id=self.current_project_id,
        )
        update_status("Cleaning up resources...", "system")

        await self._cleanup_teams_concurrently(
            [self.find_team, self.analysis_team, self.download_team],
            label="orchestrator",
        )

        if self.ui:
            self.ui.stop()
            self.ui = None


async def main():
    parser = argparse.ArgumentParser(description='AutoGen Research Pipeline Orchestrator')
    parser.add_argument('--stage', choices=['find', 'analyze', 'download', 'full'],
                        default='full', help='Stage to run')
    parser.add_argument('--input', required=False,
                        help='Input data (query for find/full, paper path for analyze, .plan.json file for download). If not provided, will be requested via UI.')
    parser.add_argument('--parallel', action='store_true',
                        help='Run analysis and downloads in parallel when possible')
    parser.add_argument('--papers', nargs='*',
                        help='List of paper paths for parallel analysis')
    parser.add_argument('--no-ui', action='store_true',
                        help='Run without UI (console only)')
    parser.add_argument('--web-ui', action='store_true',
                        help='Prefer web UI if available (requires web backend to be running)')

    args = parser.parse_args()

    if args.no_ui and not args.input:
        print("Error: --input is required when running with --no-ui")
        sys.exit(1)

    orchestrator = ResearchOrchestrator(
        use_ui=not args.no_ui,
        prefer_web_ui=args.web_ui
    )
    set_orchestrator(orchestrator)

    try:
        input_data = args.input
        if not args.no_ui and not input_data:
            orchestrator.setup_ui()
            ui = orchestrator.ui

            ui.push_message("Welcome to AutoGen Research Pipeline!")
            ui.push_message("This system coordinates three research teams:")
            ui.push_message("  Find Team - Searches and downloads research papers")
            ui.push_message("  Analysis Team - Analyzes papers for data suitability")
            ui.push_message("  Download Team - Downloads datasets based on analysis")
            ui.push_message("")

            if args.stage == 'full':
                input_data = await ui.request_input("Enter your research query to start the full pipeline:")
            elif args.stage == 'find':
                input_data = await ui.request_input("Enter your search query to find papers:")
            elif args.stage == 'analyze':
                input_data = await ui.request_input("Enter the path to the paper you want to analyze:")
            elif args.stage == 'download':
                input_data = await ui.request_input("Enter the path to the JSON download plan file (.plan.json):")

        if args.stage == 'full':
            if args.parallel:
                pipeline_log("Running full pipeline with parallel processing", stage="pipeline", team="Orchestrator")
                orchestrator.setup_ui()
                update_stage("Parallel Pipeline - Starting")

                FindTeam = orchestrator._import_team(
                    "find.team", "FindTeam", stage="find"
                )
                find_team = FindTeam(team_name="FindTeam")
                set_ui(orchestrator.ui) if orchestrator.ui else None
                papers = await find_team.run(input_data)
                await find_team.cleanup()

                if papers:
                    analyses = await orchestrator.run_parallel_analysis(papers)

                    if analyses:
                        downloads = await orchestrator.run_parallel_downloads(analyses)
                        update_stage("Parallel Pipeline - Complete")
                        update_status(f"Pipeline complete! Downloaded {len(downloads)} datasets", "system")
                        pipeline_log(
                            f"Pipeline complete. Downloaded {len(downloads)} datasets",
                            stage="pipeline",
                            team="Orchestrator",
                        )
            else:
                await orchestrator.run_full_pipeline(input_data)

        elif args.stage in ['find', 'analyze', 'download']:
            result = await orchestrator.run_stage(args.stage, input_data)
            if result:
                pipeline_log(f"Stage {args.stage} completed successfully", stage=args.stage, team="Orchestrator")
            else:
                pipeline_log(
                    f"Stage {args.stage} failed or returned no results",
                    stage=args.stage,
                    team="Orchestrator",
                    level=logging.ERROR,
                )

        elif args.papers and args.stage == 'analyze':
            analyses = await orchestrator.run_parallel_analysis(args.papers)
            pipeline_log(f"Analyzed {len(analyses)} papers", stage="analyze", team="Orchestrator")

        if orchestrator.ui and not args.no_ui:
            orchestrator.ui.push_message("Pipeline completed. You can close this window or start a new task.")
            try:
                while orchestrator.ui.ui_thread.is_alive():
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                pass

    except KeyboardInterrupt:
        pipeline_log("Pipeline interrupted by user", stage="pipeline", team="Orchestrator")
    except Exception as e:
        pipeline_log(f"Pipeline failed: {e}", stage="pipeline", team="Orchestrator", level=logging.ERROR)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
