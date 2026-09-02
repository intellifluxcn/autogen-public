"""
Background download queue for decoupled downloading.

This module captures download URLs during browser sessions and downloads them
independently using aiohttp. This allows downloads to continue even after
the browser session times out or closes.

Key Features:
- Captures download URLs from browser CDP events or manual addition
- Downloads files using aiohttp with streaming support
- 10-minute timeout per file
- Handles large files without loading into memory
- Tracks download status (pending, downloading, completed, failed)
"""
import asyncio
import aiohttp
import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import List, Optional, Set
from dataclasses import dataclass, field
from urllib.parse import urlparse, unquote

from download.filename_utils import normalize_download_filename, normalize_downloaded_file_path
from utils.pipeline_log import pipeline_log

logger = logging.getLogger(__name__)

# Maximum file size limit (5GB) - prevents accidental massive downloads
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024 * 1024  # 5GB


@dataclass
class PendingDownload:
    """Represents a download waiting to be processed."""
    url: str
    suggested_filename: Optional[str] = None
    destination_dir: Optional[Path] = None
    status: str = 'pending'  # pending, downloading, completed, failed
    error: Optional[str] = None
    file_path: Optional[Path] = None
    file_size: int = 0


@dataclass
class DownloadQueue:
    """
    Manages a queue of downloads that can continue after browser closes.

    Usage:
        queue = DownloadQueue()

        # Add downloads during browser session
        queue.add_download("https://example.com/file.csv", "data.csv")

        # Process all downloads after browser closes
        completed = await queue.process_all(Path("./downloads"))
    """
    downloads: List[PendingDownload] = field(default_factory=list)
    _completed_urls: Set[str] = field(default_factory=set)

    def add_download(
        self,
        url: str,
        suggested_filename: str = None,
        destination_dir: Path = None
    ) -> bool:
        """
        Add a download to the queue.

        Args:
            url: URL to download
            suggested_filename: Optional filename (will be inferred if not provided)
            destination_dir: Optional destination directory

        Returns:
            True if added, False if already in queue
        """
        # Skip duplicates
        if url in self._completed_urls:
            logger.debug(f"[DownloadQueue] Skipping duplicate URL: {url}")
            return False

        # Check if already in pending queue
        for d in self.downloads:
            if d.url == url:
                logger.debug(f"[DownloadQueue] URL already queued: {url}")
                return False

        download = PendingDownload(
            url=url,
            suggested_filename=suggested_filename,
            destination_dir=destination_dir
        )
        self.downloads.append(download)
        pipeline_log(
            f"[DownloadQueue] Added: {url[:80]}...",
            stage="download",
            component="download_queue",
        )
        return True

    async def process_all(
        self,
        destination_dir: Path,
        timeout_per_file: int = 600,
        max_concurrent: int = 3
    ) -> List[Path]:
        """
        Process all pending downloads using aiohttp.

        Args:
            destination_dir: Default directory for downloads
            timeout_per_file: Maximum seconds per file (default 10 minutes)
            max_concurrent: Maximum concurrent downloads

        Returns:
            List of successfully downloaded file paths
        """
        pending = [d for d in self.downloads if d.status == 'pending']

        if not pending:
            pipeline_log(
                "[DownloadQueue] No pending downloads to process.",
                stage="download",
                component="download_queue",
            )
            return []

        pipeline_log(
            f"[DownloadQueue] Processing {len(pending)} queued downloads...",
            stage="download",
            component="download_queue",
        )
        completed_files = []

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(max_concurrent)

        async def download_with_semaphore(download: PendingDownload) -> Optional[Path]:
            async with semaphore:
                return await self._process_single_download(
                    download,
                    destination_dir,
                    timeout_per_file
                )

        # Process downloads concurrently
        tasks = [download_with_semaphore(d) for d in pending]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Path):
                completed_files.append(result)
            elif isinstance(result, Exception):
                pipeline_log(
                    f"[DownloadQueue] Download task exception: {result}",
                    stage="download",
                    component="download_queue",
                    level=logging.ERROR,
                )

        pipeline_log(
            f"[DownloadQueue] Completed {len(completed_files)}/{len(pending)} downloads",
            stage="download",
            component="download_queue",
        )
        return completed_files

    async def _process_single_download(
        self,
        download: PendingDownload,
        default_dest_dir: Path,
        timeout: int
    ) -> Optional[Path]:
        """Process a single download."""
        dest_dir = download.destination_dir or default_dest_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        download.status = 'downloading'

        try:
            file_path = await self._download_file(
                url=download.url,
                dest_dir=dest_dir,
                suggested_filename=download.suggested_filename,
                timeout=timeout
            )

            if file_path and file_path.exists():
                download.status = 'completed'
                download.file_path = file_path
                download.file_size = file_path.stat().st_size
                self._completed_urls.add(download.url)
                pipeline_log(
                    f"[DownloadQueue] Completed: {file_path.name} ({download.file_size:,} bytes)",
                    stage="download",
                    component="download_queue",
                )
                return file_path
            else:
                download.status = 'failed'
                download.error = 'Download returned no content or file not created'
                return None

        except asyncio.TimeoutError:
            download.status = 'failed'
            download.error = f'Timeout after {timeout}s'
            pipeline_log(
                f"[DownloadQueue] Timeout downloading: {download.url[:60]}...",
                stage="download",
                component="download_queue",
                level=logging.ERROR,
            )
            return None

        except Exception as e:
            download.status = 'failed'
            download.error = str(e)
            pipeline_log(
                f"[DownloadQueue] Failed {download.url[:60]}...: {e}",
                stage="download",
                component="download_queue",
                level=logging.ERROR,
            )
            return None

    async def _download_file(
        self,
        url: str,
        dest_dir: Path,
        suggested_filename: str = None,
        timeout: int = 600
    ) -> Optional[Path]:
        """
        Download a single file with streaming support.

        Args:
            url: URL to download
            dest_dir: Directory to save file
            suggested_filename: Optional filename
            timeout: Timeout in seconds

        Returns:
            Path to downloaded file or None
        """
        timeout_config = aiohttp.ClientTimeout(total=timeout)

        # Configure headers to appear as a browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
        }

        async with aiohttp.ClientSession(timeout=timeout_config, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    raise Exception(f"HTTP {response.status}: {response.reason}")

                # Check file size before downloading (if Content-Length available)
                content_length = response.headers.get('Content-Length')
                if content_length:
                    size_bytes = int(content_length)
                    if size_bytes > MAX_FILE_SIZE_BYTES:
                        size_gb = size_bytes / (1024 ** 3)
                        raise Exception(f"File too large ({size_gb:.1f}GB). Max allowed: 5GB. Skipping download.")

                # Determine filename
                filename = self._determine_filename(
                    response,
                    url,
                    suggested_filename
                )

                file_path = dest_dir / filename

                # Handle duplicate filenames
                if file_path.exists():
                    base = file_path.stem
                    suffix = file_path.suffix
                    counter = 1
                    while file_path.exists():
                        file_path = dest_dir / f"{base}_{counter}{suffix}"
                        counter += 1

                # Stream to file (handles large files without loading into memory)
                total_size = 0
                chunk_size = 64 * 1024  # 64KB chunks

                with open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(chunk_size):
                        f.write(chunk)
                        total_size += len(chunk)

                        # Abort if file exceeds size limit during streaming
                        if total_size > MAX_FILE_SIZE_BYTES:
                            f.close()
                            file_path.unlink(missing_ok=True)
                            size_gb = total_size / (1024 ** 3)
                            raise Exception(f"Download aborted: file exceeded 5GB limit ({size_gb:.1f}GB downloaded)")

                        # Log progress for large files (every 100MB)
                        if total_size % (100 * 1024 * 1024) < chunk_size:
                            pipeline_log(
                                f"[DownloadQueue] Progress: {total_size / (1024*1024):.0f}MB downloaded",
                                stage="download",
                                component="download_queue",
                            )

                file_path = normalize_downloaded_file_path(file_path)
                duplicate_path = self._existing_duplicate_path(dest_dir, file_path)
                if duplicate_path is not None:
                    file_path.unlink(missing_ok=True)
                    pipeline_log(
                        f"[DownloadQueue] Duplicate skipped: {filename} -> {duplicate_path.name}",
                        stage="download",
                        component="download_queue",
                    )
                    return duplicate_path

                pipeline_log(
                    f"[DownloadQueue] Saved: {filename} ({total_size:,} bytes)",
                    stage="download",
                    component="download_queue",
                )
                return file_path

    @staticmethod
    def _file_digest(path: Path, chunk_size: int = 1024 * 1024) -> Optional[str]:
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
        except OSError:
            return None
        return hasher.hexdigest()

    @classmethod
    def _existing_duplicate_path(cls, directory: Path, candidate_path: Path) -> Optional[Path]:
        candidate_digest = cls._file_digest(candidate_path)
        if not candidate_digest:
            return None
        try:
            candidate_size = candidate_path.stat().st_size
            paths = sorted(directory.iterdir())
        except OSError:
            return None
        for path in paths:
            if path == candidate_path or not path.is_file():
                continue
            try:
                if path.stat().st_size != candidate_size:
                    continue
            except OSError:
                continue
            if cls._file_digest(path) == candidate_digest:
                return path
        return None

    def _determine_filename(
        self,
        response: aiohttp.ClientResponse,
        url: str,
        suggested_filename: str = None
    ) -> str:
        """Determine the filename from various sources."""
        # Priority 1: Suggested filename
        if suggested_filename:
            return normalize_download_filename(
                suggested_filename,
                url=url,
                content_type=response.headers.get('Content-Type', ''),
            )

        # Priority 2: Content-Disposition header
        cd = response.headers.get('Content-Disposition', '')
        if 'filename=' in cd:
            # Handle both filename= and filename*= formats
            if 'filename*=' in cd:
                # RFC 5987 encoded filename
                parts = cd.split("filename*=")
                if len(parts) > 1:
                    encoded = parts[1].split(';')[0].strip()
                    if "''" in encoded:
                        filename = unquote(encoded.split("''")[1])
                        return normalize_download_filename(
                            filename.strip('"\''),
                            url=url,
                            content_type=response.headers.get('Content-Type', ''),
                        )

            # Regular filename=
            parts = cd.split('filename=')
            if len(parts) > 1:
                filename = parts[1].split(';')[0].strip()
                return normalize_download_filename(
                    filename.strip('"\''),
                    url=url,
                    content_type=response.headers.get('Content-Type', ''),
                )

        # Priority 3: URL path
        parsed = urlparse(url)
        path_filename = unquote(parsed.path.split('/')[-1])
        if path_filename:
            normalized_path_filename = normalize_download_filename(
                path_filename,
                url=url,
                content_type=response.headers.get('Content-Type', ''),
            )
            if '.' in normalized_path_filename:
                return normalized_path_filename

        # Priority 4: Guess from content type
        content_type = response.headers.get('Content-Type', '')
        ext = mimetypes.guess_extension(content_type.split(';')[0]) or ''

        # Fallback: Generate a name
        return normalize_download_filename(
            f"download_{hash(url) % 10000}{ext}",
            url=url,
            content_type=content_type,
        )

    def get_pending_count(self) -> int:
        """Get number of pending downloads."""
        return len([d for d in self.downloads if d.status == 'pending'])

    def get_completed_count(self) -> int:
        """Get number of completed downloads."""
        return len([d for d in self.downloads if d.status == 'completed'])

    def get_failed_downloads(self) -> List[PendingDownload]:
        """Get list of failed downloads."""
        return [d for d in self.downloads if d.status == 'failed']

    def get_summary(self) -> dict:
        """Get a summary of download queue status."""
        return {
            'total': len(self.downloads),
            'pending': self.get_pending_count(),
            'completed': self.get_completed_count(),
            'failed': len(self.get_failed_downloads()),
            'completed_urls': list(self._completed_urls)
        }

    def clear(self):
        """Clear all downloads from the queue."""
        self.downloads.clear()
        self._completed_urls.clear()
