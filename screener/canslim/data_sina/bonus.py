"""新浪分红配送表解析。"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from screener.canslim import config
from screener.canslim.data_sina.http_util import (
    cache_path,
    http_get,
    read_json_cache,
    to_six_code,
    write_json_cache,
)

_BONUS_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{code}.phtml"
)


def fetch_bonus_events(code: str, *, use_cache: bool = True) -> pd.DataFrame:
    """
    返回已实施分红事件。
    列: announce_date, song_per_10, zhuan_per_10, cash_per_10, ex_date, register_date
    单位: 送/转/派息均为「每 10 股」。
    """
    six = to_six_code(code)
    cpath = cache_path("bonus", six)
    if use_cache:
        cached = read_json_cache(cpath)
        if cached is not None:
            df = pd.DataFrame(cached)
            if not df.empty:
                df["ex_date"] = pd.to_datetime(df["ex_date"])
                if "announce_date" in df.columns:
                    df["announce_date"] = pd.to_datetime(df["announce_date"], errors="coerce")
                if "register_date" in df.columns:
                    df["register_date"] = pd.to_datetime(df["register_date"], errors="coerce")
            return df

    url = _BONUS_URL.format(code=six)
    resp = http_get(url, sleep=config.REQUEST_SLEEP_SEC)
    html = resp.text
    rows = _parse_bonus_html(html)
    df = pd.DataFrame(rows)
    if use_cache:
        payload: list[dict[str, Any]] = []
        for r in rows:
            payload.append(
                {
                    "announce_date": r["announce_date"].strftime("%Y-%m-%d")
                    if pd.notna(r["announce_date"])
                    else None,
                    "song_per_10": r["song_per_10"],
                    "zhuan_per_10": r["zhuan_per_10"],
                    "cash_per_10": r["cash_per_10"],
                    "ex_date": r["ex_date"].strftime("%Y-%m-%d"),
                    "register_date": r["register_date"].strftime("%Y-%m-%d")
                    if pd.notna(r.get("register_date"))
                    else None,
                }
            )
        write_json_cache(cpath, payload)
    return df


def _parse_bonus_html(html: str) -> list[dict]:
    # 行结构: 公告日, 送股, 转增, 派息, 状态, 除权除息日, 股权登记日, 红股上市日, 查看
    row_pat = re.compile(
        r"<tr>\s*"
        r"<td>(?P<announce>\d{4}-\d{2}-\d{2})</td>\s*"
        r"<td>(?P<song>[^<]*)</td>\s*"
        r"<td>(?P<zhuan>[^<]*)</td>\s*"
        r"<td>(?P<cash>[^<]*)</td>\s*"
        r"<td>(?P<status>[^<]*)</td>\s*"
        r"<td>(?P<ex>\d{4}-\d{2}-\d{2})</td>\s*"
        r"<td>(?P<reg>[^<]*)</td>",
        re.IGNORECASE,
    )
    out: list[dict] = []
    for m in row_pat.finditer(html):
        status = (m.group("status") or "").strip()
        if status != "实施":
            continue
        song = _num(m.group("song"))
        zhuan = _num(m.group("zhuan"))
        cash = _num(m.group("cash"))
        if song == 0 and zhuan == 0 and cash == 0:
            continue
        reg_raw = (m.group("reg") or "").strip()
        reg = pd.to_datetime(reg_raw, errors="coerce") if re.match(r"\d{4}-\d{2}-\d{2}", reg_raw) else pd.NaT
        out.append(
            {
                "announce_date": pd.to_datetime(m.group("announce")),
                "song_per_10": song,
                "zhuan_per_10": zhuan,
                "cash_per_10": cash,
                "ex_date": pd.to_datetime(m.group("ex")),
                "register_date": reg,
            }
        )
    return out


def _num(v: str) -> float:
    try:
        s = (v or "").strip().replace(",", "")
        if s in ("", "--", "None"):
            return 0.0
        return float(s)
    except Exception:
        return 0.0
