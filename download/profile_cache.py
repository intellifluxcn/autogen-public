"""Per-site browser auth-state cache helpers with run-level isolation."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse


class DownloadProfileCache:
    """Manage per-site auth-state cache with isolated per-run working profiles."""

    def __init__(self, *, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.runs_dir = base_dir / "runs"
        self.site_cache_dir = base_dir / "site_cache"
        self.locks_dir = base_dir / ".locks"

    @staticmethod
    def slugify(value: Optional[str]) -> str:
        if not value:
            return "unknown"
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip().lower())
        return safe.strip("._") or "unknown"

    @classmethod
    def normalize_site(cls, site_or_url: Optional[str]) -> Optional[str]:
        if not site_or_url:
            return None
        raw = site_or_url.strip()
        if not raw:
            return None
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = (parsed.hostname or raw).strip().lower()
        if host.startswith("www."):
            host = host[4:]
        return cls.slugify(host) if host else None

    def cache_scope_key(self, *, user_email: Optional[str]) -> str:
        return self.slugify(user_email) if user_email else "anonymous"

    def site_cache_path(self, *, site: str, user_email: Optional[str]) -> Path:
        return self.site_cache_dir / self.cache_scope_key(user_email=user_email) / site

    def cookie_snapshot_path(self, *, site: str, user_email: Optional[str]) -> Path:
        return self.site_cache_path(site=site, user_email=user_email) / "cookies.json"

    def lock_path(self, *, site: str, user_email: Optional[str]) -> Path:
        filename = f"{self.cache_scope_key(user_email=user_email)}__{site}.lock"
        return self.locks_dir / filename

    @contextmanager
    def site_lock(self, *, site: str, user_email: Optional[str]):
        lock_path = self.lock_path(site=site, user_email=user_email)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fp = open(lock_path, "a+", encoding="utf-8")
        try:
            try:
                import fcntl

                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            except ImportError:
                pass
            yield
        finally:
            try:
                try:
                    import fcntl

                    fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                except ImportError:
                    pass
            finally:
                fp.close()

    def load_cookies(self, *, site: Optional[str], user_email: Optional[str]) -> list[dict[str, Any]]:
        if not site:
            return []
        snapshot_path = self.cookie_snapshot_path(site=site, user_email=user_email)
        with self.site_lock(site=site, user_email=user_email):
            if not snapshot_path.exists():
                return []
            try:
                payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []
        if not isinstance(payload, list):
            return []
        import time
        now = time.time()
        valid: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            expires = item.get("expires") or item.get("expiry")
            if expires is not None:
                try:
                    if float(expires) < now:
                        continue
                except (TypeError, ValueError):
                    pass
            valid.append(item)
        return valid

    def save_cookies(
        self,
        *,
        site: Optional[str],
        user_email: Optional[str],
        cookies: list[dict[str, Any]],
    ) -> bool:
        if not site or not cookies:
            return False
        snapshot_path = self.cookie_snapshot_path(site=site, user_email=user_email)
        with self.site_lock(site=site, user_email=user_email):
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        return True


def candidate_sites_from_urls(urls: Iterable[Optional[str]]) -> list[str]:
    seen: list[str] = []
    for url in urls:
        site = DownloadProfileCache.normalize_site(url)
        if site and site not in seen:
            seen.append(site)
    return seen


async def _resolve_page_from_source(source_obj: Any) -> Any:
    candidates = [
        source_obj,
        getattr(source_obj, "browser", None),
        getattr(source_obj, "browser_session", None),
    ]
    browser_session = None
    for candidate in candidates:
        if candidate is None:
            continue
        if callable(getattr(candidate, "must_get_current_page", None)):
            browser_session = candidate
            break
        nested = getattr(candidate, "browser_session", None)
        if nested is not None and callable(getattr(nested, "must_get_current_page", None)):
            browser_session = nested
            break
    if browser_session is None:
        return None
    try:
        return await browser_session.must_get_current_page()
    except Exception:
        return None


def _context_from_page(page: Any) -> Any:
    if page is None:
        return None
    context_attr = getattr(page, "context", None)
    if callable(context_attr):
        try:
            return context_attr()
        except Exception:
            return None
    return context_attr


async def restore_cookies_to_browser(source_obj: Any, cookies: list[dict[str, Any]]) -> int:
    if not cookies:
        return 0
    page = await _resolve_page_from_source(source_obj)
    context = _context_from_page(page)
    add_cookies = getattr(context, "add_cookies", None)
    if not callable(add_cookies):
        return 0
    try:
        await add_cookies(cookies)
        return len(cookies)
    except Exception:
        return 0


async def extract_cookies_from_browser(source_obj: Any) -> list[dict[str, Any]]:
    page = await _resolve_page_from_source(source_obj)
    context = _context_from_page(page)
    cookies_fn = getattr(context, "cookies", None)
    if not callable(cookies_fn):
        return []
    try:
        cookies = await cookies_fn()
    except Exception:
        return []
    if not isinstance(cookies, list):
        return []
    return [item for item in cookies if isinstance(item, dict)]
