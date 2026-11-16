"""Polite HTTP layer with on-disk caching.

Politeness rules (per-host minimum interval, adaptive back-off on 429,
retries with jitter-free linear delay) are the same idea as the reference
``hallu-citation-check`` demo, but the cache lives in a user-level cache
directory so it is shared across projects.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

USER_AGENT = "hx-bib/0.1 (mailto:noreply@example.com)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

HOST_MIN_INTERVAL = {
    "export.arxiv.org": 3.1,
    "arxiv.org": 1.0,
    "dblp.org": 1.6,
    "api.crossref.org": 0.5,
    "api2.openreview.net": 0.5,
}


def default_cache_path() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "hx-bib", "http-cache.json")


class Net:
    """Throttled, cached HTTP GET helper (stdlib only).

    Thread-safe: concurrent workers share one :class:`Net`; the per-host
    politeness spacing and the on-disk cache are guarded by locks, and cache
    writes are atomic (temp file + rename) so several processes may share
    the same cache file safely.
    """

    def __init__(
        self,
        delay: float = 0.8,
        use_cache: bool = True,
        timeout: int = 30,
        cache_path: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        self.delay = delay
        self.timeout = timeout
        self.verbose = verbose
        self.use_cache = use_cache
        self.cache_path = cache_path or default_cache_path()
        self.cache: Dict[str, dict] = {}
        self._last = 0.0
        self._host_last: Dict[str, float] = {}
        self._host_penalty: Dict[str, float] = {}
        self._lock = threading.Lock()      # protects throttling state
        self._save_lock = threading.Lock()  # serializes cache writes
        self.arxiv_prefetch: Optional[Dict[str, Optional[dict]]] = None
        if use_cache and os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, encoding="utf-8") as fh:
                    self.cache = json.load(fh)
            except (OSError, ValueError):
                self.cache = {}

    # -- cache --------------------------------------------------------------
    def save(self) -> None:
        if not self.use_cache:
            return
        with self._save_lock:
            try:
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                fd, tmp = tempfile.mkstemp(
                    dir=os.path.dirname(self.cache_path), suffix=".tmp"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(self.cache, fh)
                    os.replace(tmp, self.cache_path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except OSError:
                pass

    # -- throttling ----------------------------------------------------------
    def _throttle(self, host: Optional[str]) -> None:
        with self._lock:
            now = time.time()
            wait = self.delay - (now - self._last)
            if host:
                need = HOST_MIN_INTERVAL.get(host, 0.0) + self._host_penalty.get(host, 0.0)
                wait = max(wait, need - (now - self._host_last.get(host, 0.0)))
            if wait > 0:
                time.sleep(wait)
            t = time.time()
            self._last = t
            if host:
                self._host_last[host] = t

    def get(
        self,
        url: str,
        accept: Optional[str] = None,
        retries: int = 3,
        ua: Optional[str] = None,
    ) -> Tuple[Optional[int], str]:
        cache_key = (accept or "") + "|" + url
        if self.use_cache and cache_key in self.cache:
            entry = self.cache[cache_key]
            return entry["status"], entry["body"]

        try:
            host = urllib.parse.urlparse(url).hostname or ""
        except ValueError:
            host = ""
        headers = {"User-Agent": ua or USER_AGENT}
        if accept:
            headers["Accept"] = accept

        result: Tuple[Optional[int], str] = (None, "")
        for attempt in range(retries):
            self._throttle(host)
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    result = (resp.getcode(), resp.read().decode("utf-8", "replace"))
                break
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")
                except OSError:
                    pass
                if exc.code == 429:
                    self._host_penalty[host] = min(
                        self._host_penalty.get(host, 0.0) + 2.0, 10.0
                    )
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                result = (exc.code, body)
                break
            except Exception:  # URLError, timeout, SSL reset, …
                if attempt < retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                result = (None, "")
                break

        if self.verbose:
            sys.stderr.write(f"    GET {result[0]} {url}\n")
        if self.use_cache and result[0] not in (None, 429, 500, 502, 503, 504):
            self.cache[cache_key] = {"status": result[0], "body": result[1]}
        return result
