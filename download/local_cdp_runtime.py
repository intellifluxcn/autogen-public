"""Launch an on-demand local Chrome/Chromium process with a CDP endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils.pipeline_log import pipeline_log


_DEFAULT_CDP_HOST = "127.0.0.1"
_DEFAULT_CDP_STARTUP_TIMEOUT_SECONDS = 20.0
_DEFAULT_CDP_POLL_INTERVAL_SECONDS = 0.5
_DEFAULT_PROC_ROOT = Path("/proc")

logger = logging.getLogger(__name__)


def parse_local_cdp_startup_timeout_seconds() -> float:
    raw = os.getenv("DOWNLOAD_AGENT_LOCAL_CDP_STARTUP_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_CDP_STARTUP_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_CDP_STARTUP_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_CDP_STARTUP_TIMEOUT_SECONDS


def _iter_browser_binary_candidates() -> list[Path]:
    env_path = os.getenv("DOWNLOAD_AGENT_BROWSER_BINARY", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())

    candidates.extend(
        [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path.home() / "Library/Caches/ms-playwright/chromium-1187/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            Path.home() / ".cache/ms-playwright/chromium-1187/chrome-linux/chrome",
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
        ]
    )

    playwright_cache_roots = [
        Path.home() / "Library/Caches/ms-playwright",
        Path.home() / ".cache/ms-playwright",
    ]
    for root in playwright_cache_roots:
        if not root.exists():
            continue
        for child in sorted(root.glob("chromium-*")):
            candidates.extend(
                [
                    child / "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                    child / "chrome-linux/chrome",
                ]
            )
    return candidates


def resolve_browser_binary_path() -> Optional[Path]:
    for candidate in _iter_browser_binary_candidates():
        if candidate.exists():
            return candidate
    return None


# Substrings that identify a process whose executable is OUR managed browser
# (Playwright chromium / chrome-linux). Matched against /proc/<pid>/exe so it
# covers the main process, renderers, GPU, and crashpad helpers alike.
_MANAGED_BROWSER_EXE_TOKENS = ("ms-playwright/chromium", "/chrome-linux/", "chromium-headless-shell")


def reap_orphaned_browser_processes() -> int:
    """Kill leftover managed-browser processes (Playwright chromium + helpers).

    Browser subprocesses are normally torn down per-run via process-group kill,
    but an abnormal backend exit (SIGKILL / deploy mid-run) orphans the chrome
    tree — observed accumulating to dozens of procs / GBs of RSS across restarts.
    Call this ONLY at startup, before any download runs, where every such process
    is by definition orphaned. Linux-only (/proc); a no-op elsewhere. Returns the
    number of processes signalled."""
    if not os.path.isdir("/proc"):
        return 0
    killed = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            exe = os.readlink(f"/proc/{entry}/exe")
        except OSError:
            continue  # process gone / not ours / no permission
        exe = exe.split(" (deleted)")[0]
        if any(tok in exe for tok in _MANAGED_BROWSER_EXE_TOKENS):
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(entry), signal.SIGKILL)
                killed += 1
    return killed


def allocate_cdp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_DEFAULT_CDP_HOST, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


async def wait_for_cdp_ready(cdp_url: str, timeout_seconds: float) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: Optional[Exception] = None
    version_url = cdp_url.rstrip("/") + "/json/version"

    while asyncio.get_running_loop().time() < deadline:
        try:
            payload = await asyncio.to_thread(_fetch_json, version_url)
            if isinstance(payload, dict) and payload.get("webSocketDebuggerUrl"):
                return payload
        except Exception as e:
            last_error = e
        await asyncio.sleep(_DEFAULT_CDP_POLL_INTERVAL_SECONDS)

    raise RuntimeError(f"Timed out waiting for local CDP endpoint {version_url}: {last_error}")


def _fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(str(e)) from e


def iter_matching_pids_by_cmdline(
    *,
    needle: str,
    proc_root: Path = _DEFAULT_PROC_ROOT,
) -> list[int]:
    if not needle or not proc_root.is_dir():
        return []

    matches: list[int] = []
    for child in proc_root.iterdir():
        if not child.name.isdigit():
            continue
        cmdline_path = child / "cmdline"
        try:
            raw = cmdline_path.read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        try:
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        except Exception:
            continue
        if needle in cmdline:
            matches.append(int(child.name))
    return matches


def cleanup_processes_for_user_data_dir(
    *,
    user_data_dir: Path,
    proc_root: Path = _DEFAULT_PROC_ROOT,
) -> list[int]:
    matches = iter_matching_pids_by_cmdline(needle=str(user_data_dir), proc_root=proc_root)
    killed: list[int] = []
    for pid in matches:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
    return killed


@dataclass
class LocalCDPHandle:
    cdp_url: str
    process: asyncio.subprocess.Process
    user_data_dir: Path
    owns_user_data_dir: bool
    proc_root: Path = _DEFAULT_PROC_ROOT
    team_name: str = "WebsiteDownloadTeam"
    project_id: Optional[str] = None
    paper_name: Optional[str] = None
    browser_binary: Optional[str] = None

    async def stop(self) -> None:
        pgid: Optional[int] = None
        if self.process.returncode is None:
            with contextlib.suppress(Exception):
                pgid = os.getpgid(self.process.pid)

            if pgid is not None:
                pipeline_log(
                    "local CDP stop: sending SIGTERM to process group "
                    f"pid={self.process.pid} pgid={pgid} cdp_url={self.cdp_url} "
                    f"user_data_dir={self.user_data_dir}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                )
                with contextlib.suppress(Exception):
                    os.killpg(pgid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pipeline_log(
                        "local CDP stop: escalating to SIGKILL for process group "
                        f"pid={self.process.pid} pgid={pgid}",
                        stage="download",
                        team=self.team_name,
                        project_id=self.project_id,
                        level=logging.WARNING,
                    )
                    with contextlib.suppress(Exception):
                        os.killpg(pgid, signal.SIGKILL)
                    with contextlib.suppress(Exception):
                        await self.process.wait()
            else:
                pipeline_log(
                    "local CDP stop: no process group available; terminating direct process "
                    f"pid={self.process.pid} cdp_url={self.cdp_url}",
                    stage="download",
                    team=self.team_name,
                    project_id=self.project_id,
                    level=logging.WARNING,
                )
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    with contextlib.suppress(Exception):
                        await self.process.wait()
        killed = cleanup_processes_for_user_data_dir(
            user_data_dir=self.user_data_dir,
            proc_root=self.proc_root,
        )
        pipeline_log(
            "local CDP stop: cleanup by user_data_dir "
            f"user_data_dir={self.user_data_dir} killed_pids={killed}",
            stage="download",
            team=self.team_name,
            project_id=self.project_id,
        )
        if self.owns_user_data_dir:
            import shutil

            shutil.rmtree(self.user_data_dir, ignore_errors=True)
            pipeline_log(
                f"local CDP stop: removed temporary user_data_dir={self.user_data_dir}",
                stage="download",
                team=self.team_name,
                project_id=self.project_id,
            )


async def start_local_cdp_browser(
    *,
    chromium_args: list[str],
    profile_path_value: Optional[str],
    team_name: str = "WebsiteDownloadTeam",
    project_id: Optional[str] = None,
    paper_name: Optional[str] = None,
) -> LocalCDPHandle:
    browser_binary = resolve_browser_binary_path()
    if browser_binary is None:
        raise RuntimeError(
            "Could not find a Chrome/Chromium binary. "
            "Set DOWNLOAD_AGENT_BROWSER_BINARY or install Chrome/Chromium/Playwright browsers."
        )

    port = allocate_cdp_port()
    cdp_url = f"http://{_DEFAULT_CDP_HOST}:{port}"

    if profile_path_value:
        user_data_dir = Path(profile_path_value)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        owns_user_data_dir = False
    else:
        user_data_dir = Path(tempfile.mkdtemp(prefix="autogen-browser-profile-"))
        owns_user_data_dir = True

    cmd = [
        str(browser_binary),
        "--headless=new",
        f"--remote-debugging-address={_DEFAULT_CDP_HOST}",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
    ] + list(chromium_args)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    pgid: Optional[int] = None
    with contextlib.suppress(Exception):
        pgid = os.getpgid(process.pid)
    pipeline_log(
        "local CDP start: "
        f"pid={process.pid} pgid={pgid} cdp_url={cdp_url} "
        f"user_data_dir={user_data_dir} browser_binary={browser_binary}",
        stage="download",
        team=team_name,
        project_id=project_id,
    )

    try:
        await wait_for_cdp_ready(
            cdp_url=cdp_url,
            timeout_seconds=parse_local_cdp_startup_timeout_seconds(),
        )
    except Exception:
        if process.returncode is None:
            process.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=5.0)
        if owns_user_data_dir:
            import shutil

            shutil.rmtree(user_data_dir, ignore_errors=True)
        raise

    return LocalCDPHandle(
        cdp_url=cdp_url,
        process=process,
        user_data_dir=user_data_dir,
        owns_user_data_dir=owns_user_data_dir,
        proc_root=_DEFAULT_PROC_ROOT,
        team_name=team_name,
        project_id=project_id,
        paper_name=paper_name,
        browser_binary=str(browser_binary),
    )
