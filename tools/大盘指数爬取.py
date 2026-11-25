#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --mode（sync/rebuild/daily/intraday/both），--date，--full，--excel，--verify，--reference
# 输出参数：写入 大盘资金流向.xlsx（散点数据/分时数据/日线数据）；更新 {stem}_lastfilemtime.txt
# 中文映射：
#   - BBD -> 股宝 DDX 大盘指标
#   - 散点数据 -> API 原始累计 BBD；分时数据 -> 5 分钟非累计；日线数据 -> 日线 BBD
# 用途：从股宝网拉取上证 BBD，与 MACD 主链独立；详见 tools/README.md
# 使用说明：
#   .\env\run_with_conda.bat tools\大盘指数爬取.py
#   .\env\run_with_conda.bat tools\大盘指数爬取.py --mode sync --verify
#   .\env\run_with_conda.bat tools\大盘指数爬取.py --mode rebuild --verify

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data.bar_filters import is_after_daily_close, load_trade_dates

DATA_DIR = ROOT_DIR / "data"
TEMPLATE_PATH = ROOT_DIR / "大盘资金流向.xlsx"
REFERENCE_PATH = DATA_DIR / "大盘指数模板.xlsx"
SCATTER_IMPORT_PATH = ROOT_DIR / "大盘指数模板.xlsx"

API_DAILY = "http://ddx.gubit.cn/ygetddedata.php"
API_INTRADAY = "http://ddx.gubit.cn/ygetddxdata.php"
CODE = "dp000001"
REQUEST_TIMEOUT = 60
REQUEST_RETRIES = 3

WS_SCATTER = "散点数据"
WS_INTRADAY = "分时数据"
WS_DAILY = "日线数据"
LEGACY_WS_INTRADAY = "Sheet1"
LEGACY_WS_DAILY = "daily数据"

# 5 分钟仅生成该日之后（不含 2025-12-05 当日）
INTRADAY_FLOOR = date(2025, 12, 5)
# 散点 15:00 与日线 BBD 允许相对误差
DAILY_SCATTER_TOLERANCE = 0.05
VERIFY_DAILY_START = date(2025, 9, 4)
VERIFY_DAILY_END = date(2026, 6, 5)
VERIFY_INTRADAY_SAMPLE = date(2026, 6, 5)

# 5 分钟网格：9:35~11:30、13:05~15:00；插值最多向前找 10 分钟
MAX_CUM_LOOKAHEAD_MIN = 10
# BBD 单位为万元；5 分钟段增量 |值| < 10 视为垃圾置空
MIN_VALID_SEGMENT_ABS = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "http://ddx.gubit.cn/drawdapanddx.html",
}

_STATE_PATH: Path | None = None


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}")


def state_path_for(excel_path: Path) -> Path:
    return excel_path.with_name(f"{excel_path.stem}_lastfilemtime.txt")


def set_state_path(excel_path: Path) -> None:
    global _STATE_PATH
    _STATE_PATH = state_path_for(excel_path)


def load_lastfilemtime() -> str:
    path = _STATE_PATH
    if path and path.exists():
        return path.read_text(encoding="utf-8").strip() or "0"
    return "0"


def save_lastfilemtime(value: str) -> None:
    if not _STATE_PATH:
        return
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(str(value), encoding="utf-8")


def ensure_template(path: Path = TEMPLATE_PATH) -> Path:
    """若模板不存在则自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        ensure_workbook_structure(path)
        return path

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = WS_SCATTER
    ws.append(["时间", "BBD"])
    wb.create_sheet(WS_INTRADAY).append(["时间", "BBD"])
    wb.create_sheet(WS_DAILY).append(["时间", "BBD"])
    _order_workbook_sheets(wb)

    wb.save(path)
    _log(f"已创建 Excel 模板: {path}")
    return path


def _order_workbook_sheets(wb: openpyxl.Workbook) -> None:
    for i, name in enumerate((WS_SCATTER, WS_INTRADAY, WS_DAILY)):
        if name not in wb.sheetnames:
            continue
        idx = wb.sheetnames.index(name)
        if idx != i:
            wb.move_sheet(name, offset=i - idx)


def _copy_sheet_rows(wb: openpyxl.Workbook, src_name: str, dst_name: str) -> int:
    if src_name not in wb.sheetnames:
        return 0
    src = wb[src_name]
    if dst_name in wb.sheetnames:
        dst = wb[dst_name]
    else:
        dst = wb.create_sheet(dst_name)
        dst.append(["时间", "BBD"])

    existing: set[str] = set()
    for row in dst.iter_rows(min_row=2, values_only=True):
        if row and row[0]:
            existing.add(_time_key(row[0]))

    copied = 0
    for row in src.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        key = _time_key(row[0])
        if key in existing:
            continue
        dst.append(list(row))
        copied += 1
    return copied


def _import_legacy_scatter_to_sheet(wb: openpyxl.Workbook, src_name: str) -> int:
    """从旧 Sheet1 复制原始散点到 散点数据。"""
    if src_name not in wb.sheetnames:
        return 0
    rows: list[list] = []
    for row in wb[src_name].iter_rows(min_row=2, values_only=True):
        if not row or not row[0] or row[1] is None:
            continue
        parsed = _parse_time_value(row[0])
        if parsed is None:
            continue
        rows.append([parsed.replace(microsecond=0), float(row[1])])
    if not rows:
        return 0
    return _append_rows_bulk(wb, WS_SCATTER, rows)


def _append_rows_bulk(wb: openpyxl.Workbook, ws_name: str, prepared_rows: list[list]) -> int:
    if ws_name not in wb.sheetnames:
        ws = wb.create_sheet(ws_name)
        ws.append(["时间", "BBD"])
    else:
        ws = wb[ws_name]
    headers = [cell.value for cell in ws[1]]
    if not headers or headers[0] is None:
        ws.append(["时间", "BBD"])
    existing: set[str] = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0]:
            existing.add(_time_key(row[0]))
    added = 0
    for row in prepared_rows:
        key = _time_key(row[0])
        if key in existing:
            continue
        ws.append(row)
        existing.add(key)
        added += 1
    return added


def ensure_workbook_structure(path: Path) -> None:
    """确保 散点数据/分时数据/日线数据 存在；旧 sheet 一次性迁移。"""
    wb = openpyxl.load_workbook(path)
    changed = False

    if WS_DAILY not in wb.sheetnames and LEGACY_WS_DAILY in wb.sheetnames:
        n = _copy_sheet_rows(wb, LEGACY_WS_DAILY, WS_DAILY)
        _log(f"迁移日线: {LEGACY_WS_DAILY} → {WS_DAILY}，复制 {n} 行")
        changed = True

    if WS_SCATTER not in wb.sheetnames and LEGACY_WS_INTRADAY in wb.sheetnames:
        n = _import_legacy_scatter_to_sheet(wb, LEGACY_WS_INTRADAY)
        _log(f"迁移散点: {LEGACY_WS_INTRADAY} → {WS_SCATTER}，复制 {n} 行")
        changed = True

    for legacy in (LEGACY_WS_INTRADAY, LEGACY_WS_DAILY):
        if legacy not in wb.sheetnames:
            continue
        if legacy == LEGACY_WS_INTRADAY and WS_SCATTER in wb.sheetnames:
            del wb[legacy]
            _log(f"已删除旧 sheet: {legacy}")
            changed = True
        elif legacy == LEGACY_WS_DAILY and WS_DAILY in wb.sheetnames:
            del wb[legacy]
            _log(f"已删除旧 sheet: {legacy}")
            changed = True

    for name in (WS_SCATTER, WS_INTRADAY, WS_DAILY):
        if name not in wb.sheetnames:
            wb.create_sheet(name).append(["时间", "BBD"])
            changed = True

    before = wb.sheetnames.copy()
    _order_workbook_sheets(wb)
    if wb.sheetnames != before:
        changed = True

    if changed:
        wb.save(path)
    wb.close()


def _daily_sheet_name(wb: openpyxl.Workbook) -> str:
    if WS_DAILY in wb.sheetnames:
        return WS_DAILY
    if LEGACY_WS_DAILY in wb.sheetnames:
        return LEGACY_WS_DAILY
    return WS_DAILY


def _intraday_sheet_name(wb: openpyxl.Workbook) -> str:
    if WS_INTRADAY in wb.sheetnames:
        return WS_INTRADAY
    if LEGACY_WS_INTRADAY in wb.sheetnames:
        return LEGACY_WS_INTRADAY
    return WS_INTRADAY


def _parse_time_value(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt, size in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _time_key(value) -> str:
    parsed = _parse_time_value(value)
    if parsed is None:
        return str(value)
    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        return parsed.strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _to_daily_cell(value: str) -> datetime:
    parsed = _parse_time_value(value)
    if parsed is None:
        raise ValueError(f"无法解析日线时间: {value}")
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def _to_intraday_cell(value: str) -> datetime:
    parsed = _parse_time_value(value)
    if parsed is None:
        raise ValueError(f"无法解析分时时间: {value}")
    return parsed.replace(microsecond=0)


def _to_bbd_value(value: str | float) -> float:
    return float(value)


def _minute_delta(t_end: datetime, t_start: datetime) -> int:
    return int((t_end - t_start).total_seconds() // 60)


def _five_minute_grid(trade_day: date) -> list[datetime]:
    """上午 9:35~11:30、下午 13:05~15:00，步长 5 分钟。"""
    slots: list[datetime] = []
    for start_min, end_min in ((9 * 60 + 35, 11 * 60 + 30), (13 * 60 + 5, 15 * 60 + 0)):
        m = start_min
        while m <= end_min:
            slots.append(
                datetime(trade_day.year, trade_day.month, trade_day.day, m // 60, m % 60)
            )
            m += 5
    return slots


def _raw_rows_to_cum_series(raw_rows: list[dict[str, str]]) -> list[tuple[datetime, float]]:
    dedup: dict[datetime, float] = {}
    for row in raw_rows:
        t = _parse_time_value(row.get("时间", ""))
        if t is None:
            continue
        dedup[t.replace(second=0, microsecond=0)] = _to_bbd_value(row["BBD"])
    return sorted(dedup.items())


def _find_cum_at_or_after(
    series: list[tuple[datetime, float]],
    target: datetime,
    *,
    max_ahead_min: int = MAX_CUM_LOOKAHEAD_MIN,
) -> tuple[datetime, float] | None:
    for t, v in series:
        if t < target:
            continue
        if _minute_delta(t, target) > max_ahead_min:
            return None
        return t, v
    return None


def _segment_quality(value: float | None, *, is_first: bool) -> float | None:
    if value is None:
        return None
    if is_first:
        return value
    if abs(value) < MIN_VALID_SEGMENT_ABS:
        return None
    return value


def transform_cumulative_to_5m(
    raw_rows: list[dict[str, str]],
    trade_day: date,
) -> list[list]:
    """散点累计 BBD → 5 分钟非累计（首根 9:35 用 cum(9:35)）。"""
    series = _raw_rows_to_cum_series(raw_rows)
    if not series:
        return []

    slots = _five_minute_grid(trade_day)
    out: list[list] = []

    for idx, slot in enumerate(slots):
        if idx == 0:
            hit = _find_cum_at_or_after(series, slot)
            val = _segment_quality(hit[1] if hit else None, is_first=True)
            out.append([slot, val])
            continue

        t0 = slots[idx - 1]
        cum_t0 = _find_cum_at_or_after(series, t0)
        hit = _find_cum_at_or_after(series, slot)
        if cum_t0 is None or hit is None:
            out.append([slot, None])
            continue

        t_star, cum_star = hit
        span = _minute_delta(t_star, t0)
        target_span = _minute_delta(slot, t0)
        if span <= 0 or target_span <= 0:
            out.append([slot, None])
            continue

        seg = (cum_star - cum_t0[1]) * target_span / span
        out.append([slot, _segment_quality(seg, is_first=False)])

    return out


def _prepare_intraday_5m_rows(raw_rows: list[dict[str, str]], trade_day: date) -> list[list]:
    return transform_cumulative_to_5m(raw_rows, trade_day)


def _parse_record_blob(blob: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in blob.split("|"):
        item = item.strip()
        if not item or "," not in item:
            continue
        if "<=>" in item:
            item = item.split("<=>")[-1]
        cols = [c.strip() for c in item.split(",")]
        if len(cols) < 3:
            continue
        rows.append({"时间": cols[-1], "BBD": cols[2]})
    return rows


def parse_daily_response(res: str) -> tuple[list[dict[str, str]], str | None]:
    """解析日线接口返回，返回 (数据列表, 新的 lastfilemtime)。"""
    mtime_match = re.search(r"lastfilemtime<=>(\d+)", res)
    new_mtime = mtime_match.group(1) if mtime_match else None

    rows: list[dict[str, str]] = []
    for marker in ("==new<=>", "==old<=>", "==ls<=>"):
        if marker not in res:
            continue
        parts = res.split(marker)
        for chunk in parts[1:]:
            stop_at = min(
                (chunk.find(m) for m in ("==new<=>", "==old<=>", "==ls<=>") if chunk.find(m) != -1),
                default=-1,
            )
            blob = chunk[:stop_at] if stop_at != -1 else chunk
            rows.extend(_parse_record_blob(blob))

    dedup: dict[str, dict[str, str]] = {}
    for row in rows:
        dedup[row["时间"]] = row
    result = list(dedup.values())
    result.sort(key=lambda x: x["时间"])
    return result, new_mtime


def parse_intraday_response(res: str, date_str: str) -> list[dict[str, str]]:
    """解析分时接口返回。"""
    blob = res.split("data<=>", 1)[1] if "data<=>" in res else res
    rows: list[dict[str, str]] = []
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    for item in blob.split("|"):
        item = item.strip()
        if not item or "," not in item:
            continue
        cols = [c.strip() for c in item.split(",")]
        if len(cols) < 3:
            continue
        time_part = cols[-2]
        bbd = cols[-3]
        if ":" in time_part and len(time_part) <= 5:
            dt = f"{date_fmt} {time_part}:00" if len(time_part) == 5 else f"{date_fmt} {time_part}"
        else:
            dt = time_part
        rows.append({"时间": dt, "BBD": bbd})
    return rows


def _prepare_daily_rows(data_list: list[dict[str, str]]) -> list[list]:
    rows: list[list] = []
    for data in data_list:
        t = data.get("时间", "")
        if not t:
            continue
        rows.append([_to_daily_cell(str(t)), _to_bbd_value(data["BBD"])])
    return rows


def _append_rows(file_path: Path, ws_name: str, prepared_rows: list[list]) -> int:
    if not prepared_rows:
        return 0

    wb = openpyxl.load_workbook(file_path)
    if ws_name not in wb.sheetnames:
        ws = wb.create_sheet(ws_name)
        ws.append(["时间", "BBD"])
    else:
        ws = wb[ws_name]

    headers = [cell.value for cell in ws[1]]
    if not headers or headers[0] is None:
        ws.delete_rows(1)
        ws.insert_rows(1)
        ws.append(["时间", "BBD"])

    existing_times: set[str] = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0]:
            existing_times.add(_time_key(row[0]))

    new_rows = []
    for row in prepared_rows:
        key = _time_key(row[0])
        if key in existing_times:
            continue
        new_rows.append(row)

    if not new_rows:
        wb.close()
        return 0

    for row in new_rows:
        ws.append(row)
    wb.save(file_path)
    wb.close()
    return len(new_rows)


def update_excel_daily(data_list: list[dict[str, str]], file_path: Path) -> int:
    return _append_rows(file_path, WS_DAILY, _prepare_daily_rows(data_list))


def _purge_scatter_after_floor(file_path: Path) -> int:
    wb = openpyxl.load_workbook(file_path)
    if WS_SCATTER not in wb.sheetnames:
        wb.close()
        return 0
    ws = wb[WS_SCATTER]
    removed = 0
    for row_idx in range(ws.max_row, 1, -1):
        val = ws.cell(row=row_idx, column=1).value
        parsed = _parse_time_value(val)
        if parsed and parsed.date() > INTRADAY_FLOOR:
            ws.delete_rows(row_idx)
            removed += 1
    if removed:
        wb.save(file_path)
    wb.close()
    return removed


def excel_max_daily_date(file_path: Path) -> date | None:
    wb = openpyxl.load_workbook(file_path, read_only=True)
    ws_name = _daily_sheet_name(wb)
    if ws_name not in wb.sheetnames:
        wb.close()
        return None
    max_day: date | None = None
    for row in wb[ws_name].iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        parsed = _parse_time_value(row[0])
        if parsed is None:
            continue
        day = parsed.date()
        if max_day is None or day > max_day:
            max_day = day
    wb.close()
    return max_day


def excel_intraday_dates(file_path: Path) -> set[date]:
    wb = openpyxl.load_workbook(file_path, read_only=True)
    ws_name = _intraday_sheet_name(wb)
    if ws_name not in wb.sheetnames:
        wb.close()
        return set()
    days: set[date] = set()
    for row in wb[ws_name].iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        parsed = _parse_time_value(row[0])
        if parsed is not None:
            days.add(parsed.date())
    wb.close()
    return days


def _trade_day_list(end_day: date) -> list[date]:
    trade_ts = load_trade_dates()
    out = [ts.date() for ts in trade_ts if INTRADAY_FLOOR < ts.date() <= end_day]
    if end_day not in out and end_day >= INTRADAY_FLOOR + timedelta(days=1):
        out.append(end_day)
        out.sort()
    return out


def missing_intraday_trade_days(file_path: Path, end_day: date) -> list[date]:
    existing = excel_intraday_dates(file_path)
    return [d for d in _trade_day_list(end_day) if d not in existing]


def _parse_date_arg(value: str) -> date:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def _normalize_api_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


def _prepare_scatter_rows(raw_rows: list[dict[str, str]]) -> list[list]:
    rows: list[list] = []
    for data in raw_rows:
        t = data.get("时间", "")
        if not t:
            continue
        rows.append([_to_intraday_cell(str(t)), _to_bbd_value(data["BBD"])])
    return rows


def _scatter_day_ok(day: date) -> bool:
    return day > INTRADAY_FLOOR


def excel_scatter_dates(file_path: Path) -> set[date]:
    wb = openpyxl.load_workbook(file_path, read_only=True)
    if WS_SCATTER not in wb.sheetnames:
        wb.close()
        return set()
    days: set[date] = set()
    for row in wb[WS_SCATTER].iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        parsed = _parse_time_value(row[0])
        if parsed is not None:
            days.add(parsed.date())
    wb.close()
    return days


def _load_scatter_rows_for_day(file_path: Path, day: date) -> list[dict[str, str]]:
    return _load_scatter_by_day(file_path).get(day, [])


def _load_scatter_by_day(file_path: Path) -> dict[date, list[dict[str, str]]]:
    wb = openpyxl.load_workbook(file_path, read_only=True)
    if WS_SCATTER not in wb.sheetnames:
        wb.close()
        return {}
    by_day: dict[date, list[dict[str, str]]] = {}
    for row in wb[WS_SCATTER].iter_rows(min_row=2, values_only=True):
        if not row or not row[0] or row[1] is None:
            continue
        parsed = _parse_time_value(row[0])
        if parsed is None:
            continue
        day = parsed.date()
        by_day.setdefault(day, []).append(
            {"时间": parsed.strftime("%Y-%m-%d %H:%M:%S"), "BBD": str(row[1])}
        )
    wb.close()
    for rows in by_day.values():
        rows.sort(key=lambda x: x["时间"])
    return by_day


def _daily_bbd_for_day(file_path: Path, day: date) -> float | None:
    key = day.strftime("%Y-%m-%d")
    daily = _load_sheet_series(file_path, WS_DAILY)
    if key in daily:
        return daily[key]
    for k, v in daily.items():
        parsed = _parse_time_value(k)
        if parsed and parsed.date() == day:
            return v
    return None


def _scatter_close_bbd(raw_rows: list[dict[str, str]]) -> float | None:
    if not raw_rows:
        return None
    return _to_bbd_value(raw_rows[-1]["BBD"])


def validate_scatter_vs_daily(
    raw_rows: list[dict[str, str]],
    file_path: Path,
    day: date,
) -> tuple[bool, str]:
    daily_bbd = _daily_bbd_for_day(file_path, day)
    close_bbd = _scatter_close_bbd(raw_rows)
    if close_bbd is None:
        return False, "散点无收盘数据"
    if daily_bbd is None:
        return True, "无日线参照，跳过校验"
    diff = abs(close_bbd - daily_bbd)
    if abs(daily_bbd) < 1e-6:
        ok = diff < 1e-6
    else:
        ok = diff / abs(daily_bbd) <= DAILY_SCATTER_TOLERANCE
    msg = f"散点15:00={close_bbd} 日线={daily_bbd} 偏差={diff / max(abs(daily_bbd), 1e-6):.2%}"
    return ok, msg


def _remove_sheet_date(file_path: Path, ws_name: str, day: date) -> int:
    wb = openpyxl.load_workbook(file_path)
    if ws_name not in wb.sheetnames:
        wb.close()
        return 0
    ws = wb[ws_name]
    removed = 0
    for row_idx in range(ws.max_row, 1, -1):
        val = ws.cell(row=row_idx, column=1).value
        parsed = _parse_time_value(val)
        if parsed and parsed.date() == day:
            ws.delete_rows(row_idx)
            removed += 1
    if removed:
        wb.save(file_path)
    wb.close()
    return removed


def _append_sheet_rows_force(file_path: Path, ws_name: str, prepared_rows: list[list]) -> int:
    if not prepared_rows:
        return 0
    wb = openpyxl.load_workbook(file_path)
    if ws_name not in wb.sheetnames:
        ws = wb.create_sheet(ws_name)
        ws.append(["时间", "BBD"])
    else:
        ws = wb[ws_name]
    for row in prepared_rows:
        ws.append(row)
    wb.save(file_path)
    wb.close()
    return len(prepared_rows)


def update_excel_scatter(raw_rows: list[dict[str, str]], file_path: Path) -> int:
    return _append_rows(file_path, WS_SCATTER, _prepare_scatter_rows(raw_rows))


def import_scatter_from_template(file_path: Path, template_path: Path = SCATTER_IMPORT_PATH) -> int:
    if not template_path.is_file():
        _log(f"散点导入模板不存在: {template_path}")
        return 0
    wb_src = openpyxl.load_workbook(template_path, read_only=True)
    if LEGACY_WS_INTRADAY not in wb_src.sheetnames:
        wb_src.close()
        return 0
    rows: list[list] = []
    for row in wb_src[LEGACY_WS_INTRADAY].iter_rows(min_row=2, values_only=True):
        if not row or not row[0] or row[1] is None:
            continue
        parsed = _parse_time_value(row[0])
        if parsed is None:
            continue
        rows.append([parsed.replace(microsecond=0), float(row[1])])
    wb_src.close()
    if not rows:
        return 0
    wb = openpyxl.load_workbook(file_path)
    added = _append_rows_bulk(wb, WS_SCATTER, rows)
    _order_workbook_sheets(wb)
    wb.save(file_path)
    wb.close()
    _log(f"从 {template_path.name} 导入散点 {added} 行")
    return added


def rebuild_5m_for_day(file_path: Path, day: date, raw_rows: list[dict[str, str]] | None = None) -> int:
    if not _scatter_day_ok(day):
        return 0
    if raw_rows is None:
        raw_rows = _load_scatter_rows_for_day(file_path, day)
    if not raw_rows:
        return 0
    prepared = _prepare_intraday_5m_rows(raw_rows, day)
    _remove_sheet_date(file_path, WS_INTRADAY, day)
    return _append_sheet_rows_force(file_path, WS_INTRADAY, prepared)


def rebuild_all_5m_from_scatter(file_path: Path) -> int:
    _log("加载散点数据…")
    by_day = _load_scatter_by_day(file_path)
    days = sorted(d for d in by_day if _scatter_day_ok(d))
    all_rows: list[list] = []
    for idx, day in enumerate(days, start=1):
        if idx == 1 or idx == len(days) or idx % 50 == 0:
            _log(f"重建5分钟 [{idx}/{len(days)}] {day}")
        all_rows.extend(_prepare_intraday_5m_rows(by_day[day], day))
    wb = openpyxl.load_workbook(file_path)
    if WS_INTRADAY in wb.sheetnames:
        del wb[WS_INTRADAY]
    ws = wb.create_sheet(WS_INTRADAY)
    ws.append(["时间", "BBD"])
    for row in all_rows:
        ws.append(row)
    _order_workbook_sheets(wb)
    wb.save(file_path)
    wb.close()
    _log(f"5分钟重建完成，共 {len(all_rows)} 条（{len(days)} 个交易日）")
    return len(all_rows)


def missing_scatter_trade_days(file_path: Path, end_day: date) -> list[date]:
    existing = excel_scatter_dates(file_path)
    trade_days = [d.date() for d in load_trade_dates() if INTRADAY_FLOOR < d.date() <= end_day]
    if end_day not in trade_days and end_day > INTRADAY_FLOOR:
        trade_days.append(end_day)
    return sorted(set(trade_days) - existing)


def bbd_daily_request(lastfilemtime: str) -> str:
    params = {
        "code": CODE,
        "m": str(random.random()),
        "lastfilemtime": lastfilemtime,
        "getlsold": "1",
    }
    resp = requests.get(API_DAILY, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def bbd_request(day: date | str) -> str:
    api_date = _normalize_api_date(day)
    params = {
        "code": CODE,
        "m": str(random.random()),
        "date": api_date,
    }
    last_exc: requests.RequestException | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            resp = requests.get(API_INTRADAY, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if attempt + 1 < REQUEST_RETRIES:
                wait = 2 * (attempt + 1)
                _log(f"分时 API 重试 {attempt + 1}/{REQUEST_RETRIES}（{api_date}）: {exc}")
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def fetch_scatter_raw(day: date) -> list[dict[str, str]]:
    api_date = _normalize_api_date(day)
    compact = api_date.replace("-", "")
    res = bbd_request(day)
    rows = parse_intraday_response(res, compact)
    return rows


def fetch_daily(file_path: Path, full: bool = False, *, two_round: bool = False) -> int:
    # sync / --full 双轮必须从 mtime=0 拉「新段」，否则仅有状态文件时会重复拉旧段、漏近期日线
    last_mtime = "0" if (full or two_round) else load_lastfilemtime()
    _log(f"请求日线数据，lastfilemtime={last_mtime}")
    res = bbd_daily_request(last_mtime)
    rows, new_mtime = parse_daily_response(res)
    if not rows:
        _log("未解析到日线数据，请检查接口返回或网络")
        _log(f"响应片段: {res[:200]}...")
        return 0

    added = update_excel_daily(rows, file_path)
    if new_mtime:
        save_lastfilemtime(new_mtime)

    if two_round and new_mtime:
        after_r1 = excel_max_daily_date(file_path)
        target_day = date.today()
        need_r2 = after_r1 is None or after_r1 < target_day
        if need_r2:
            _log(f"日线第二轮，lastfilemtime={new_mtime}（末日期 {after_r1} < {target_day}）")
            res2 = bbd_daily_request(new_mtime)
            rows2, _ = parse_daily_response(res2)
            if rows2:
                added += update_excel_daily(rows2, file_path)
        else:
            _log(f"日线跳过第二轮（末日期 {after_r1} 已至 {target_day}）")

    _log(f"日线: 解析 {len(rows)} 条，新增 {added} 条")
    if rows:
        _log(f"最新: {rows[-1]['时间']}  BBD={rows[-1]['BBD']}")
    return added


def fetch_scatter_day(
    day: date,
    file_path: Path,
    *,
    replace: bool = False,
) -> tuple[list[dict[str, str]], int]:
    _log(f"请求散点，date={day}{'（覆盖）' if replace else ''}")
    try:
        rows = fetch_scatter_raw(day)
    except requests.RequestException as exc:
        _log(f"散点 API 失败 {day}: {exc}")
        return [], 0
    if not rows:
        _log(f"散点无数据 {day}")
        return [], 0

    ok, msg = validate_scatter_vs_daily(rows, file_path, day)
    _log(f"散点校验 {day}: {msg}")
    if not ok:
        _log(f"散点校验未通过，跳过写入 {day}")
        return rows, 0

    if replace:
        removed = _remove_sheet_date(file_path, WS_SCATTER, day)
        if removed:
            _log(f"已移除 {day} 旧散点 {removed} 条")
        added = _append_sheet_rows_force(file_path, WS_SCATTER, _prepare_scatter_rows(rows))
    else:
        added = update_excel_scatter(rows, file_path)
    _log(f"散点: {len(rows)} 条，写入 {added} 条")
    return rows, added


def fetch_intraday(
    date_str: str,
    file_path: Path,
    *,
    replace: bool = False,
) -> int:
    day = _parse_date_arg(date_str)
    rows, _ = fetch_scatter_day(day, file_path, replace=replace)
    if not rows:
        return 0
    added = rebuild_5m_for_day(file_path, day, rows)
    valid = sum(1 for r in _prepare_intraday_5m_rows(rows, day) if r[1] is not None)
    _log(f"5分钟: 写入 {added} 条（有效 {valid}）")
    if day == date.today() and not is_after_daily_close(pd.Timestamp.now()):
        _log(f"今日散点 {len(rows)} 条，待收盘后重跑")
    return added


def sync_daily(file_path: Path, target_day: date) -> int:
    before = excel_max_daily_date(file_path)
    _log(f"日线 sync，Excel 末日期={before}，目标={target_day}")
    added = fetch_daily(file_path, full=False, two_round=True)
    after = excel_max_daily_date(file_path)
    if after and after < target_day:
        _log(f"警告: 日线仍缺至 {target_day}，当前末日期 {after}")
    return added


def sync_scatter_and_5m(file_path: Path, target_day: date) -> tuple[int, int]:
    missing = missing_scatter_trade_days(file_path, target_day)
    today = date.today()
    refresh: list[date] = []
    if today <= target_day:
        refresh.append(today)
    days = sorted(set(missing) | set(refresh))
    if not days:
        _log("散点 sync：无缺失交易日")
        return 0, 0

    _log(f"散点 sync：待拉 {len(days)} 个交易日（{days[0]} ~ {days[-1]}）")
    scatter_added = 0
    five_m_added = 0
    for idx, day in enumerate(days, start=1):
        replace = day == today
        _log(f"散点 [{idx}/{len(days)}] {day}")
        rows, s_added = fetch_scatter_day(day, file_path, replace=replace)
        scatter_added += s_added
        if not rows:
            continue
        ok, _ = validate_scatter_vs_daily(rows, file_path, day)
        if ok:
            five_m_added += rebuild_5m_for_day(file_path, day, rows)
    return scatter_added, five_m_added


def _reset_scatter_sheet(file_path: Path) -> None:
    wb = openpyxl.load_workbook(file_path)
    if WS_SCATTER in wb.sheetnames:
        del wb[WS_SCATTER]
    ws = wb.create_sheet(WS_SCATTER)
    ws.append(["时间", "BBD"])
    _order_workbook_sheets(wb)
    wb.save(file_path)
    wb.close()


def run_rebuild(file_path: Path, target_day: date | None = None) -> int:
    target = target_day or date.today()
    _log(f"全量重建开始，目标截止 {target}")
    fetch_daily(file_path, full=False, two_round=True)
    _reset_scatter_sheet(file_path)
    import_scatter_from_template(file_path)
    removed = _purge_scatter_after_floor(file_path)
    if removed:
        _log(f"已清除 {INTRADAY_FLOOR} 之后旧散点 {removed} 条")
    missing = missing_scatter_trade_days(file_path, target)
    _log(f"散点回填 {len(missing)} 个交易日")
    for idx, day in enumerate(missing, start=1):
        _log(f"回填 [{idx}/{len(missing)}] {day}")
        fetch_scatter_day(day, file_path, replace=True)
    today = date.today()
    if today <= target:
        fetch_scatter_day(today, file_path, replace=True)
    rebuild_all_5m_from_scatter(file_path)
    _log("全量重建完成")
    return 0


def sync_intraday(file_path: Path, target_day: date) -> int:
    _, five_m = sync_scatter_and_5m(file_path, target_day)
    return five_m


def _load_sheet_series(file_path: Path, ws_name: str) -> dict[str, float | None]:
    wb = openpyxl.load_workbook(file_path, read_only=True)
    if ws_name not in wb.sheetnames:
        wb.close()
        return {}
    out: dict[str, float | None] = {}
    for row in wb[ws_name].iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        key = _time_key(row[0])
        out[key] = None if row[1] is None else float(row[1])
    wb.close()
    return out


def _verify_intraday_5m_sample(
    target_intra: dict[str, float | None],
    sample_day: date,
) -> tuple[list[str], list[str]]:
    """样本日 5 分钟分时：目标 Excel 与 API 散点重算结果对照。"""
    errors: list[str] = []
    warnings: list[str] = []
    prefix = sample_day.strftime("%Y-%m-%d")
    target_sample = {k: v for k, v in target_intra.items() if k.startswith(prefix)}
    if not target_sample:
        errors.append(f"目标文件无 {sample_day} 5 分钟分时样本")
        return errors, warnings

    try:
        raw_rows = fetch_scatter_raw(sample_day)
    except requests.RequestException as exc:
        errors.append(f"分时样本日 API 请求失败 {sample_day}: {exc}")
        return errors, warnings

    expected_rows = transform_cumulative_to_5m(raw_rows, sample_day)
    expected = {_time_key(row[0]): row[1] for row in expected_rows}

    for key, exp_val in sorted(expected.items()):
        if key not in target_sample:
            errors.append(f"5分钟分时缺失(样本日): {key}")
            continue
        tgt_val = target_sample[key]
        if exp_val is None and tgt_val is None:
            continue
        if exp_val is None or tgt_val is None:
            errors.append(f"5分钟分时空值不一致 {key}: 目标={tgt_val} 期望={exp_val}")
        elif abs(tgt_val - exp_val) > 1e-6:
            errors.append(f"5分钟分时不一致 {key}: 目标={tgt_val} 期望={exp_val}")

    for key in sorted(set(target_sample) - set(expected)):
        errors.append(f"5分钟分时多余(样本日): {key}")

    null_cnt = sum(1 for v in expected.values() if v is None)
    if null_cnt:
        warnings.append(f"样本日 {sample_day} 期望空值档 {null_cnt} 条（|段增量|<{MIN_VALID_SEGMENT_ABS} 或缺散点）")
    return errors, warnings


def _report_reference_intraday_diff(
    target_intra: dict[str, float | None],
    ref_intra: dict[str, float | None],
    sample_day: date,
) -> list[str]:
    """与 data/ 历史散点参照对比：仅报告（格式不同，不作阻断）。"""
    notes: list[str] = []
    prefix = sample_day.strftime("%Y-%m-%d")
    ref_sample = {k: v for k, v in ref_intra.items() if k.startswith(prefix)}
    target_sample = {k: v for k, v in target_intra.items() if k.startswith(prefix)}
    if not ref_sample:
        notes.append(f"参照文件无 {sample_day} 分时（跳过参照对比）")
        return notes

    common = set(ref_sample) & set(target_sample)
    missing = sorted(set(ref_sample) - set(target_sample))
    extra = sorted(set(target_sample) - set(ref_sample))
    mism = [
        key
        for key in common
        if target_sample.get(key) is not None
        and ref_sample.get(key) is not None
        and abs(float(target_sample[key]) - float(ref_sample[key])) > 1e-6
    ]
    notes.append(
        f"参照对比 {sample_day}: 参照={len(ref_sample)}条 目标={len(target_sample)}条 "
        f"共有={len(common)} 缺失={len(missing)} 多余={len(extra)} 值不同={len(mism)}"
    )
    if missing:
        notes.append(f"  参照有/目标无 示例: {', '.join(missing[:5])}")
    if mism:
        key = mism[0]
        notes.append(
            f"  值不同示例 {key}: 目标={target_sample[key]} 参照={ref_sample[key]}"
        )
    return notes


def verify_against_reference(target_path: Path, reference_path: Path) -> tuple[list[str], list[str]]:
    if not reference_path.is_file():
        return [f"参照文件不存在: {reference_path}"], []

    target_daily = _load_sheet_series(target_path, WS_DAILY)
    ref_daily = _load_sheet_series(reference_path, LEGACY_WS_DAILY)
    if not ref_daily:
        ref_daily = _load_sheet_series(reference_path, WS_DAILY)
    target_intra = _load_sheet_series(target_path, WS_INTRADAY)
    ref_intra = _load_sheet_series(reference_path, LEGACY_WS_INTRADAY)

    errors: list[str] = []
    warnings: list[str] = []

    overlap_end = date(2025, 9, 3)
    for key, ref_val in ref_daily.items():
        if ref_val is None:
            continue
        ref_day = _parse_time_value(key)
        if ref_day is None or ref_day.date() > overlap_end:
            continue
        if key not in target_daily:
            errors.append(f"日线缺失: {key}")
            continue
        tgt = target_daily[key]
        if tgt is None:
            errors.append(f"日线空值: {key}")
            continue
        if abs(tgt - ref_val) > 1e-6:
            errors.append(f"日线不一致 {key}: 目标={tgt} 参照={ref_val}")

    d = VERIFY_DAILY_START
    while d <= VERIFY_DAILY_END:
        key = d.strftime("%Y-%m-%d")
        if key not in ref_daily:
            d += timedelta(days=1)
            continue
        if key not in target_daily:
            errors.append(f"日线缺失(验收段): {key}")
        else:
            tgt = target_daily[key]
            if tgt is None:
                errors.append(f"日线空值(验收段): {key}")
            elif abs(tgt - ref_daily[key]) > 1e-6:
                errors.append(
                    f"日线不一致(验收段) {key}: 目标={tgt} 参照={ref_daily[key]}"
                )
        d += timedelta(days=1)

    intra_errors, intra_warnings = _verify_intraday_5m_sample(
        target_intra, VERIFY_INTRADAY_SAMPLE
    )
    errors.extend(intra_errors)
    warnings.extend(intra_warnings)
    warnings.extend(
        _report_reference_intraday_diff(
            target_intra, ref_intra, VERIFY_INTRADAY_SAMPLE
        )
    )

    return errors, warnings


def run_sync(file_path: Path, *, verify: bool, reference_path: Path) -> int:
    target_day = date.today()
    _log(f"sync 开始，目标截止 {target_day}")

    daily_added = sync_daily(file_path, target_day)
    scatter_added, intra_added = sync_scatter_and_5m(file_path, target_day)
    _log(
        f"sync 完成，日线新增 {daily_added} 条，散点写入 {scatter_added} 条，"
        f"5分钟写入 {intra_added} 条"
    )

    if not verify:
        return 0

    _log(f"校验参照: {reference_path}")
    errors, warnings = verify_against_reference(file_path, reference_path)
    for msg in warnings:
        _log(f"校验提示: {msg}")
    if errors:
        _log(f"校验失败，共 {len(errors)} 项差异:")
        for msg in errors[:50]:
            _log(f"  {msg}")
        if len(errors) > 50:
            _log(f"  ... 另有 {len(errors) - 50} 项")
        return 1

    _log("校验通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="爬取大盘指数 BBD 数据到 Excel")
    parser.add_argument(
        "--mode",
        choices=["sync", "rebuild", "rebuild-5m", "daily", "intraday", "both"],
        default="sync",
        help="sync=增量同步(默认), rebuild=全量重建散点+5分钟, rebuild-5m=仅从散点重建5分钟, daily=日线, intraday=散点+5分钟",
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="分时模式日期，格式 YYYYMMDD，默认今天",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="日线全量拉取（忽略本地 lastfilemtime，并双轮补洞）",
    )
    parser.add_argument(
        "--excel",
        default=str(TEMPLATE_PATH),
        help="Excel 目标路径",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="sync 结束后校验：日线对 data/大盘指数模板.xlsx；样本日分时对 API 重拉；有阻断性差异则 exit≠0",
    )
    parser.add_argument(
        "--reference",
        default=str(REFERENCE_PATH),
        help="校验参照 Excel 路径",
    )
    args = parser.parse_args()

    file_path = ensure_template(Path(args.excel))
    set_state_path(file_path)
    _log(f"Excel 路径: {file_path}")
    _log(f"状态文件: {_STATE_PATH}")

    if args.mode == "sync":
        return run_sync(file_path, verify=args.verify, reference_path=Path(args.reference))
    if args.mode == "rebuild":
        code = run_rebuild(file_path)
        if args.verify:
            _log(f"校验参照: {args.reference}")
            errors, warnings = verify_against_reference(file_path, Path(args.reference))
            for msg in warnings:
                _log(f"校验提示: {msg}")
            if errors:
                _log(f"校验失败，共 {len(errors)} 项差异:")
                for msg in errors[:50]:
                    _log(f"  {msg}")
                return 1
            _log("校验通过")
        return code
    if args.mode == "rebuild-5m":
        rebuild_all_5m_from_scatter(file_path)
        if args.verify:
            _log(f"校验参照: {args.reference}")
            errors, warnings = verify_against_reference(file_path, Path(args.reference))
            for msg in warnings:
                _log(f"校验提示: {msg}")
            if errors:
                _log(f"校验失败，共 {len(errors)} 项差异:")
                for msg in errors[:50]:
                    _log(f"  {msg}")
                return 1
            _log("校验通过")
        return 0

    total = 0
    if args.mode in ("daily", "both"):
        total += fetch_daily(file_path, full=args.full, two_round=args.full)
    if args.mode in ("intraday", "both"):
        total += fetch_intraday(args.date, file_path)

    _log(f"完成，共新增 {total} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
