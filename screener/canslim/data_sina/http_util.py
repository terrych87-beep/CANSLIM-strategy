"""HTTP 与缓存小工具。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

import requests

from screener.canslim import config

_RETRYABLE = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)


def ensure_cache_dir() -> Path:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR


def cache_path(namespace: str, key: str, suffix: str = ".json") -> Path:
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:80]
    return ensure_cache_dir() / f"{namespace}_{safe}_{digest}{suffix}"


def http_get(
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = config.HTTP_TIMEOUT,
    sleep: float = 0.0,
    retries: int = 3,
) -> requests.Response:
    if sleep > 0:
        time.sleep(sleep)
    last_err: Optional[Exception] = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            resp = requests.get(
                url, params=params, timeout=timeout, headers=config.HTTP_UA
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp
        except _RETRYABLE as e:
            last_err = e
            if attempt >= retries:
                break
            time.sleep(1.5 * attempt)
        except requests.exceptions.HTTPError as e:
            # 5xx 可重试
            status = getattr(e.response, "status_code", 0) or 0
            last_err = e
            if status < 500 or attempt >= retries:
                raise
            time.sleep(1.5 * attempt)
    raise last_err  # type: ignore[misc]


def read_json_cache(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json_cache(path: Path, payload: Any) -> None:
    ensure_cache_dir()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def to_sina_symbol(code: str) -> str:
    """六位代码 → sh/sz 前缀。"""
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz")):
        return c
    digits = "".join(ch for ch in c if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"非法股票代码: {code}")
    if digits.startswith(("5", "6", "9")) or digits.startswith(("688", "689")):
        return f"sh{digits}"
    return f"sz{digits}"


def to_six_code(symbol_or_code: str) -> str:
    s = str(symbol_or_code).strip().lower()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 6:
        raise ValueError(f"非法代码: {symbol_or_code}")
    return digits[-6:]
