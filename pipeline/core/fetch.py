"""克制的 HTTP 客户端。

各社与 OCCTO 的页面是公开网页不是 API（见 CLAUDE.md 抓取纪律）：
串行、带间隔、可识别 User-Agent、失败退避重试。这个模块是唯一的出站入口，
所有 ingest 脚本都必须经它，避免某个脚本自己 requests.get 绕过纪律。
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx

USER_AGENT = "powerjp/0.0.1 (+https://github.com/ibarapascal/powerjp; open grid data pipeline)"

DEFAULT_DELAY = 2.0  # 每次请求之间的最小间隔（秒）
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 3

log = logging.getLogger("powerjp.fetch")

_last_request_at: dict[str, float] = {}


def _throttle(host: str, delay: float) -> None:
    """按 host 分别限速 —— 不同事业者之间不必互相等待。"""
    last = _last_request_at.get(host)
    if last is not None:
        elapsed = time.monotonic() - last
        if elapsed < delay:
            time.sleep(delay - elapsed)
    _last_request_at[host] = time.monotonic()


def get(url: str, *, delay: float = DEFAULT_DELAY, timeout: float = DEFAULT_TIMEOUT,
        headers: dict[str, str] | None = None) -> httpx.Response:
    """串行 GET，带限速与退避重试。返回 4xx 时不重试（重试没有意义）。"""
    host = httpx.URL(url).host or ""
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle(host, delay)
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                resp = client.get(url, headers=merged)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} from {url}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and 400 <= exc.response.status_code < 500:
                raise
            last_error = exc
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = exc
        backoff = delay * (2 ** attempt)
        log.warning("第 %d 次失败（%s），%.1fs 后重试：%s", attempt, last_error, backoff, url)
        time.sleep(backoff)

    raise RuntimeError(f"{MAX_RETRIES} 次尝试后仍失败：{url}") from last_error


def new_session(*, timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """带 cookie 的会话。用于需要先建 session 再取数的老式表单系统。

    调用方负责 close（或用 with）。限速仍由 post()/session_get() 统一施加。
    """
    return httpx.Client(
        follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    )


def post(client: httpx.Client, url: str, *, data: dict | None = None,
         headers: dict[str, str] | None = None, delay: float = DEFAULT_DELAY,
         retries: int = MAX_RETRIES) -> httpx.Response:
    """会话内的 POST，限速与退避跟 get() 同一套纪律。"""
    host = httpx.URL(url).host or ""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        _throttle(host, delay)
        try:
            resp = client.post(url, data=data, headers=headers)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} from {url}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and 400 <= exc.response.status_code < 500:
                raise
            last_error = exc
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = exc
        backoff = delay * (2 ** attempt)
        log.warning("POST 第 %d 次失败（%s），%.1fs 后重试：%s",
                    attempt, last_error, backoff, url)
        time.sleep(backoff)
    raise RuntimeError(f"{retries} 次尝试后仍失败（POST）：{url}") from last_error


def download(url: str, dest: Path, *, force: bool = False, delay: float = DEFAULT_DELAY) -> Path:
    """下载到 dest。已存在且非 force 时跳过（raw 层幂等，见 CLAUDE.md 抓取纪律）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        log.info("已存在，跳过：%s", dest.name)
        return dest
    resp = get(url, delay=delay)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(resp.content)
    tmp.replace(dest)
    log.info("下载完成：%s (%d bytes)", dest.name, dest.stat().st_size)
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def setup_logging(verbose: bool = True) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
