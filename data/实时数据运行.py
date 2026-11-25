#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --backfill-1m-sparse / --skip-if 等；或经 scripts/live_data_sync.py 动态加载
# 输出参数：增量写入 data/ 下 1m/5m/30m/1d/1w CSV；末尾可选同步 IF_front_next_runpoints.csv
# 中文映射：
#   - tail / 交易时间 -> CSV 末行时间，增量起点
#   - merged_path / output_path -> 合并源与写入目标
#   - bar_filters / _drop_future_and_unclosed -> 未收盘与未来 bar 过滤（v3.1）
#   - datalen -> 按 tail→当前 动态估算抓取条数
# 用途：新浪→腾讯→东财 fallback 抓取上证多周期增量；15:00 前不写「当日」日线，仍补已收盘历史日；无新增时跳过写入
# 使用说明：
#   python data/实时数据运行.py
#   启动实时数据同步.bat
#   python data/实时数据运行.py --backfill-1m-sparse --start-date 2016-01-04
# 增量规则：重叠 tail 仅留 1 条核对；其余为 >tail 的新增行

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
_DATA_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_DATA_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_DATA_SCRIPT_DIR))
import bar_filters as _bf

DATA_DIR = BASE_DIR / "data"
MERGED_DIR = DATA_DIR
LIVE_DIR = DATA_DIR
FORMAL_ENV_PATH = DATA_DIR / ".env"
IF_RUNPOINTS_PATH = DATA_DIR / "IF_front_next_runpoints.csv"
IF_RUNPOINTS_SCRIPT = DATA_DIR / "期货合约数据获取.py"
LEGACY_ENV_PATH = BASE_DIR.parent / "基于中枢的macd交易逻辑交易策略" / "正式策略" / "akshare_wecom_signal" / ".env"
TZ_NAME = "Asia/Shanghai"
SINA_SUFFIX_COLS = [
    "新浪开盘价",
    "新浪最高价",
    "新浪最低价",
    "新浪收盘价",
    "新浪涨跌",
    "新浪涨跌幅%",
    "新浪成交量",
    "新浪成交额",
    "收盘价差异相对比率",
]

STD_COLS = [
    "证券代码",
    "证券名称",
    "交易时间",
    "开盘价",
    "最高价",
    "最低价",
    "收盘价",
    "涨跌",
    "涨跌幅%",
    "成交量",
    "成交额",
    "data_source",
]

MERGED_NAME_MAP = {
    "1m": "上证指数1分钟加入实时数据.csv",
    "5m": "上证指数5分钟加入实时数据.csv",
    "30m": "上证指数30分钟加入实时数据.csv",
    "1d": "上证指数日线加入实时数据.csv",
    "1w": "上证指数周线加入实时数据.csv",
}

LIVE_NAME_MAP = {
    "1m": "上证指数1分钟加入实时数据.csv",
    "5m": "上证指数5分钟加入实时数据.csv",
    "30m": "上证指数30分钟加入实时数据.csv",
    "1d": "上证指数日线加入实时数据.csv",
    "1w": "上证指数周线加入实时数据.csv",
}


@dataclass
class Plan:
    freq: str
    merged_path: Path
    output_path: Path
    jump_limit: float


def _load_env_if_exists(path: Path) -> None:
    if not path.exists():
        return
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v




def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.replace(",", "").strip()
            if not s:
                return None
            return float(s)
        return float(v)
    except Exception:
        return None




def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=STD_COLS)




def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_df()
    d = df.copy()
    for c in STD_COLS:
        if c not in d.columns:
            d[c] = None
    d = d[STD_COLS].copy()
    d["交易时间"] = pd.to_datetime(d["交易时间"], errors="coerce")
    for c in ["开盘价", "最高价", "最低价", "收盘价", "涨跌", "涨跌幅%", "成交量", "成交额"]:
        d[c] = d[c].map(_safe_float)
    d = d.dropna(subset=["交易时间", "开盘价", "最高价", "最低价", "收盘价"])
    d = d.sort_values("交易时间").reset_index(drop=True)
    return d




def _load_existing_file(path: Path, *, verbose: bool = True) -> pd.DataFrame:
    if not path.exists():
        return _empty_df()
    if verbose:
        print(f"[读取旧文件] {path.name}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    if verbose:
        print(f"[旧文件列] {path.name}: {df.columns.tolist()}")
    if "data_source" not in df.columns:
        df["data_source"] = "existing"
    out = _normalize_df(df)
    if verbose:
        print(f"[旧文件完成] {path.name}: rows={len(out)} head_time={out['交易时间'].min() if not out.empty else None} tail_time={out['交易时间'].max() if not out.empty else None}")
    return out


def _read_csv_header(path: Path) -> list[str]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f), [])


def _read_file_tail_lines(path: Path, n: int = 5) -> list[list[str]]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        buf = bytearray()
        while pos > 0 and buf.count(b"\n") < n + 1:
            step = min(8192, pos)
            pos -= step
            f.seek(pos)
            buf[:0] = f.read(step)
        text = buf.decode("utf-8-sig", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    return list(csv.reader(StringIO("\n".join(lines[-n:]))))


def _read_file_tail_time(path: Path, freq: str) -> Optional[pd.Timestamp]:
    if not path.exists():
        return None
    header = _read_csv_header(path)
    if "交易时间" not in header:
        return None
    tcol = header.index("交易时间")
    for row in reversed(_read_file_tail_lines(path, 5)):
        if len(row) <= tcol:
            continue
        ts = pd.to_datetime(row[tcol], errors="coerce")
        if pd.isna(ts):
            continue
        ts = pd.Timestamp(ts)
        if freq == "1d":
            ts = ts.normalize()
        return ts
    return None


def _load_recent_rows_from_csv(path: Path, tail_lines: int = 40) -> pd.DataFrame:
    if not path.exists():
        return _empty_df()
    header = _read_csv_header(path)
    if not header:
        return _empty_df()
    raw_rows = _read_file_tail_lines(path, tail_lines)
    if not raw_rows:
        return _empty_df()
    width = len(header)
    records = [r[:width] for r in raw_rows if len(r) >= width]
    if not records:
        return _empty_df()
    df = pd.DataFrame(records, columns=header)
    if "data_source" not in df.columns:
        df["data_source"] = "existing"
    return _normalize_df(df)


def _live_has_new_rows(
    live_df: pd.DataFrame,
    tail: Optional[pd.Timestamp],
    freq: str,
) -> bool:
    if live_df is None or live_df.empty:
        return False
    d = _normalize_df(live_df)
    if tail is None or pd.isna(tail):
        return True
    tail = pd.Timestamp(tail)
    if freq == "1d":
        tail = tail.normalize()
        times = d["交易时间"].dt.normalize()
    else:
        times = d["交易时间"]
    return bool((times > tail).any())




def _recalc_change_fields(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("交易时间").copy()
    prev = d["收盘价"].shift(1)
    d["涨跌"] = (d["收盘价"] - prev).fillna(0.0)
    d["涨跌幅%"] = ((d["涨跌"] / prev) * 100.0).replace([pd.NA, pd.NaT, float("inf"), float("-inf")], 0.0).fillna(0.0)
    return d




def _is_valid_minute_time(dt: pd.Timestamp, freq: str) -> bool:
    hm = dt.strftime("%H:%M")
    if freq == "1m":
        return ("09:31" <= hm <= "11:30") or ("13:01" <= hm <= "15:00")
    if freq == "5m":
        return (("09:35" <= hm <= "11:30") or ("13:05" <= hm <= "15:00")) and dt.minute % 5 == 0
    if freq == "30m":
        return hm in {"10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"}
    return True




def _static_row_valid(row: dict, freq: str) -> bool:
    dt = pd.to_datetime(row["交易时间"], errors="coerce")
    if pd.isna(dt):
        return False
    vals = {k: _safe_float(row.get(k)) for k in ["开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"]}
    if any(vals[k] is None for k in ["开盘价", "最高价", "最低价", "收盘价"]):
        return False
    o, h, l, c = vals["开盘价"], vals["最高价"], vals["最低价"], vals["收盘价"]
    if min(o, h, l, c) <= 0:
        return False
    if h < max(o, l, c):
        return False
    if l > min(o, h, c):
        return False
    # 调试阶段：仅 30m 做时间规则约束，其它周期先放宽，避免误剔除
    if freq == "30m" and not _is_valid_minute_time(dt, freq):
        return False
    return True




def _sequence_row_valid(row: dict, prev_row: Optional[dict], freq: str, jump_limit: float) -> bool:
    dt = pd.to_datetime(row["交易时间"], errors="coerce")
    if pd.isna(dt):
        return False
    if prev_row is None:
        return True
    prev_dt = pd.to_datetime(prev_row["交易时间"], errors="coerce")
    if pd.isna(prev_dt) or dt <= prev_dt:
        return False
    gap = dt - prev_dt
    reset_gap_map = {
        "1m": pd.Timedelta(days=3),
        "5m": pd.Timedelta(days=5),
        "30m": pd.Timedelta(days=7),
        "1d": pd.Timedelta(days=31),
        "1w": pd.Timedelta(days=90),
    }
    if gap > reset_gap_map.get(freq, pd.Timedelta(days=7)):
        return True
    # 调试阶段：1m/5m/1d/1w 不做跳变剔除，只保留时间顺序校验
    if freq in {"1m", "5m", "1d", "1w"}:
        return True
    open_price = _safe_float(row.get("开盘价"))
    close_price = _safe_float(row.get("收盘价"))
    prev_close = _safe_float(prev_row.get("收盘价"))
    if open_price is None or close_price is None or prev_close is None or prev_close <= 0:
        return False
    if abs(open_price / prev_close - 1.0) > jump_limit:
        return False
    if abs(close_price / prev_close - 1.0) > jump_limit:
        return False
    return True




def _filter_valid_sequence(df: pd.DataFrame, freq: str, jump_limit: float) -> pd.DataFrame:
    if df is None or df.empty:
        print(f"[过滤] {freq} 输入为空")
        return _empty_df()
    rows = []
    kept = 0
    dropped_static = 0
    dropped_seq = 0
    prev_row = None
    for _, raw in df.sort_values("交易时间").iterrows():
        row = {c: raw.get(c) for c in STD_COLS}
        if not _static_row_valid(row, freq):
            dropped_static += 1
            continue
        if not _sequence_row_valid(row, prev_row, freq, jump_limit):
            dropped_seq += 1
            continue
        rows.append(row)
        prev_row = row
        kept += 1
    print(f"[过滤结果] {freq}: 输入={len(df)} 保留={kept} 静态剔除={dropped_static} 序列剔除={dropped_seq}")
    if not rows:
        return _empty_df()
    return _normalize_df(pd.DataFrame(rows))




SINA_SCALE_MAP = {"1m": 1, "5m": 5, "30m": 30, "1d": 240}
TENCENT_FREQ_MAP = {"1m": "m1", "5m": "m5", "30m": "m30"}
EASTMONEY_KLT_MAP = {"1m": "1", "5m": "5", "30m": "30", "1d": "101"}
KLINE_MAX_DATALEN = 1024
SINA_MAX_DATALEN = KLINE_MAX_DATALEN  # 兼容旧名
SINA_BARS_PER_TRADING_DAY = {"1m": 240, "5m": 48, "30m": 8, "1d": 1}
HTTP_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
LIVE_SOURCE_ORDER = ("sina", "tencent", "eastmoney")


def _latest_dt(df: pd.DataFrame, freq: str) -> Optional[pd.Timestamp]:
    if df is None or df.empty:
        return None
    try:
        dt = pd.to_datetime(df["交易时间"].max())
        if freq == "1d":
            dt = dt.normalize()
        return dt
    except Exception:
        return None


def _estimate_datalen(freq: str, start_dt: Optional[pd.Timestamp], end_dt: pd.Timestamp) -> int:
    """按已有 tail 到当前时间的间隔估算需抓取的 K 线条数（含 1 条重叠对比行）。"""
    if start_dt is None or pd.isna(start_dt):
        return KLINE_MAX_DATALEN
    start_dt = pd.Timestamp(start_dt)
    end_dt = pd.Timestamp(end_dt)
    if freq == "1d":
        days = (end_dt.normalize() - start_dt.normalize()).days + 2
        return max(2, min(days, KLINE_MAX_DATALEN))
    minutes = max(0.0, (end_dt - start_dt).total_seconds() / 60.0)
    bar_minutes = {"1m": 1, "5m": 5, "30m": 30}.get(freq, 30)
    est = int(minutes / bar_minutes) + 3
    if est <= KLINE_MAX_DATALEN:
        return max(2, est)
    calendar_days = max(1, (end_dt - start_dt).days + 1)
    trading_days = max(1, int(calendar_days * 5 / 7))
    est = trading_days * SINA_BARS_PER_TRADING_DAY.get(freq, 8) + 2
    return max(2, min(est, KLINE_MAX_DATALEN))


def _extended_trade_dates(
    calendar_path: Path,
    path_30m: Path,
    extra_30m: Optional[pd.DataFrame] = None,
    *,
    tail_lines: int = 500,
) -> list[pd.Timestamp]:
    """日线 CSV 滞后时，用 30m 已有交易日补全历，供 closed_limit / 历史补全使用。"""
    dates = set(_bf.load_trade_dates(calendar_path))
    chunks: list[pd.DataFrame] = []
    if path_30m.is_file():
        chunks.append(_load_recent_rows_from_csv(path_30m, tail_lines=tail_lines))
    if extra_30m is not None and not extra_30m.empty:
        chunks.append(_normalize_df(extra_30m))
    if chunks:
        df30 = pd.concat(chunks, ignore_index=True)
        if "交易时间" in df30.columns:
            dates.update(
                pd.to_datetime(df30["交易时间"], errors="coerce").dt.normalize().dropna()
            )
    return sorted(dates)


def _latest_closed_bar_time(
    now_dt: pd.Timestamp,
    freq: str,
    trade_dates: Optional[list[pd.Timestamp]] = None,
) -> pd.Timestamp:
    """当前时刻已收盘的最后一根 K 线标签时间（避免 10:03 误用 10:05 的 5 分钟 bar）。"""
    return _bf.latest_closed_bar_time(
        now_dt,
        freq,
        trade_dates=trade_dates,
        calendar_path=DATA_DIR / MERGED_NAME_MAP["1d"],
    )


def _drop_future_and_unclosed(
    df: pd.DataFrame,
    now_dt: pd.Timestamp,
    freq: str,
    trade_dates: Optional[list[pd.Timestamp]] = None,
) -> pd.DataFrame:
    # 方法说明
    # 输入参数：df，now_dt，freq（1m/5m/30m/1d/1w），trade_dates（可选，30m 补历）
    # 输出参数：pd.DataFrame（委托 bar_filters，日历取本地日线 CSV）
    if df is None or df.empty:
        return _empty_df()
    out = _bf.drop_future_and_unclosed(
        _normalize_df(df),
        now_dt,
        freq,
        trade_dates=trade_dates,
        calendar_path=DATA_DIR / MERGED_NAME_MAP["1d"],
    )
    if out.empty:
        return _empty_df()
    return _normalize_df(out)


def _trim_incremental(df: pd.DataFrame, existing_tail: Optional[pd.Timestamp], freq: str) -> pd.DataFrame:
    """仅保留 (已有 tail, 当前] 区间：新增全留，重叠 tail 只留 1 条用于对比。"""
    if df is None or df.empty:
        return _empty_df()
    d = _normalize_df(df)
    if existing_tail is None or pd.isna(existing_tail):
        return d
    existing_tail = pd.Timestamp(existing_tail)
    if freq == "1d":
        existing_tail = existing_tail.normalize()
        d = d.copy()
        d["交易时间"] = d["交易时间"].dt.normalize()
    newer = d[d["交易时间"] > existing_tail]
    overlap = d[d["交易时间"] == existing_tail].tail(1)
    if newer.empty and overlap.empty:
        print(f"[增量裁剪] {freq}: 无 tail={existing_tail} 之后数据")
        return _empty_df()
    parts = [x for x in (overlap, newer) if not x.empty]
    out = pd.concat(parts, ignore_index=True).sort_values("交易时间").reset_index(drop=True)
    print(
        f"[增量裁剪] {freq}: tail={existing_tail} 对比={len(overlap)} 新增={len(newer)} 合计={len(out)}"
    )
    return out


def _rows_to_kline_df(rows: list[dict], freq: str, source: str) -> pd.DataFrame:
    if not rows:
        return _empty_df()
    return _normalize_df(pd.DataFrame(rows))


def _parse_sina_kline_rows(rows: list, freq: str, now_dt: pd.Timestamp, source: str = "sina") -> pd.DataFrame:
    data = []
    for row in rows:
        trade_time = pd.to_datetime(row.get("day"), errors="coerce")
        if pd.isna(trade_time):
            continue
        if freq == "1d":
            trade_time = trade_time.normalize()
        data.append(
            {
                "证券代码": "000001",
                "证券名称": "上证指数",
                "交易时间": trade_time,
                "开盘价": _safe_float(row.get("open")),
                "最高价": _safe_float(row.get("high")),
                "最低价": _safe_float(row.get("low")),
                "收盘价": _safe_float(row.get("close")),
                "涨跌": None,
                "涨跌幅%": None,
                "成交量": _safe_float(row.get("volume")),
                "成交额": _safe_float(row.get("amount")),
                "data_source": f"{source}_{freq}",
            }
        )
    return _rows_to_kline_df(data, freq, source)


def _parse_tencent_kline_rows(rows: list, freq: str, source: str = "tencent") -> pd.DataFrame:
    data = []
    for row in rows:
        if not row or len(row) < 5:
            continue
        trade_time = pd.to_datetime(str(row[0]), format="%Y%m%d%H%M", errors="coerce")
        if pd.isna(trade_time):
            continue
        data.append(
            {
                "证券代码": "000001",
                "证券名称": "上证指数",
                "交易时间": trade_time,
                "开盘价": _safe_float(row[1]),
                "收盘价": _safe_float(row[2]),
                "最高价": _safe_float(row[3]),
                "最低价": _safe_float(row[4]),
                "涨跌": None,
                "涨跌幅%": None,
                "成交量": _safe_float(row[5]) if len(row) > 5 else None,
                "成交额": None,
                "data_source": f"{source}_{freq}",
            }
        )
    out = _rows_to_kline_df(data, freq, source)
    return out


def _parse_eastmoney_kline_rows(lines: list[str], freq: str, source: str = "eastmoney") -> pd.DataFrame:
    data = []
    for line in lines:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        trade_time = pd.to_datetime(parts[0], errors="coerce")
        if pd.isna(trade_time):
            continue
        if freq == "1d":
            trade_time = trade_time.normalize()
        data.append(
            {
                "证券代码": "000001",
                "证券名称": "上证指数",
                "交易时间": trade_time,
                "开盘价": _safe_float(parts[1]),
                "收盘价": _safe_float(parts[2]),
                "最高价": _safe_float(parts[3]),
                "最低价": _safe_float(parts[4]),
                "涨跌": None,
                "涨跌幅%": None,
                "成交量": _safe_float(parts[5]) if len(parts) > 5 else None,
                "成交额": _safe_float(parts[6]) if len(parts) > 6 else None,
                "data_source": f"{source}_{freq}",
            }
        )
    return _rows_to_kline_df(data, freq, source)


def _postprocess_live(
    raw: pd.DataFrame,
    *,
    existing_tail: Optional[pd.Timestamp],
    freq: str,
    now_dt: pd.Timestamp,
) -> pd.DataFrame:
    out = _drop_future_and_unclosed(raw, now_dt, freq)
    out = _trim_incremental(out, existing_tail, freq)
    if not out.empty:
        out = _recalc_change_fields(out)
    return out


def _fetch_sina_raw(freq: str, datalen: int) -> pd.DataFrame:
    if freq not in SINA_SCALE_MAP:
        return _empty_df()
    datalen = max(2, min(int(datalen), KLINE_MAX_DATALEN))
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20data=/CN_MarketData.getKLineData"
        f"?symbol=sh000001&scale={SINA_SCALE_MAP[freq]}&ma=no&datalen={datalen}"
    )
    resp = requests.get(url, timeout=20, headers=HTTP_UA)
    resp.raise_for_status()
    text = resp.text
    if "var data=(" not in text:
        raise RuntimeError(f"新浪{freq}返回格式异常: {text[:200]}")
    payload = text.split("var data=(", 1)[1].rsplit(");", 1)[0]
    rows = json.loads(payload)
    return _parse_sina_kline_rows(rows, freq, pd.Timestamp.now(), "sina")


def _fetch_tencent_raw(freq: str, datalen: int) -> pd.DataFrame:
    if freq not in TENCENT_FREQ_MAP:
        return _empty_df()
    datalen = max(2, min(int(datalen), KLINE_MAX_DATALEN))
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    params = {"param": f"sh000001,{TENCENT_FREQ_MAP[freq]},,{datalen}"}
    resp = requests.get(url, params=params, timeout=20, headers=HTTP_UA)
    resp.raise_for_status()
    j = json.loads(resp.text)
    node = j.get("data", {}).get("sh000001", {})
    rows = node.get(TENCENT_FREQ_MAP[freq]) or []
    if not rows:
        raise RuntimeError(f"腾讯{freq}无数据 keys={list(node.keys())}")
    return _parse_tencent_kline_rows(rows, freq, "tencent")


def _fetch_eastmoney_raw(freq: str, datalen: int, *, end_day: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    if freq not in EASTMONEY_KLT_MAP:
        return _empty_df()
    datalen = max(2, min(int(datalen), KLINE_MAX_DATALEN))
    end_str = pd.Timestamp(end_day).strftime("%Y%m%d") if end_day is not None else "20500101"
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": "1.000001",
        "klt": EASTMONEY_KLT_MAP[freq],
        "fqt": "0",
        "lmt": str(datalen),
        "end": end_str,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    resp = requests.get(url, params=params, timeout=25, headers=HTTP_UA)
    resp.raise_for_status()
    j = resp.json()
    if j.get("rc") != 0 or not j.get("data") or not j["data"].get("klines"):
        raise RuntimeError(f"东财{freq} rc={j.get('rc')} msg={j.get('message') or j}")
    return _parse_eastmoney_kline_rows(j["data"]["klines"], freq, "eastmoney")


def _fetch_kline_source(
    source: str,
    freq: str,
    existing_tail: Optional[pd.Timestamp],
    datalen: int,
    now_dt: pd.Timestamp,
) -> pd.DataFrame:
    fetchers = {
        "sina": _fetch_sina_raw,
        "tencent": _fetch_tencent_raw,
        "eastmoney": _fetch_eastmoney_raw,
    }
    fn = fetchers.get(source)
    if fn is None:
        return _empty_df()
    raw = fn(freq, datalen)
    return _postprocess_live(raw, existing_tail=existing_tail, freq=freq, now_dt=now_dt)


def _fetch_kline_with_fallback(
    freq: str,
    existing_tail: Optional[pd.Timestamp],
    datalen: int,
    now_dt: pd.Timestamp,
) -> pd.DataFrame:
    errors: list[str] = []
    for source in LIVE_SOURCE_ORDER:
        if source == "tencent" and freq in {"1d", "1w"}:
            continue
        try:
            print(f"[抓取开始] {source}{freq} tail={existing_tail} datalen={datalen} now={now_dt}")
            df = _fetch_kline_source(source, freq, existing_tail, datalen, now_dt)
            if df is not None and not df.empty:
                print(
                    f"[完成] {source}{freq} rows={len(df)} "
                    f"head={df['交易时间'].min()} tail={df['交易时间'].max()} "
                    f"source={df['data_source'].iloc[-1] if 'data_source' in df.columns else source}"
                )
                return df
            errors.append(f"{source}: 空结果")
            print(f"[跳过] {source}{freq} 无增量数据")
        except Exception as e:
            errors.append(f"{source}: {type(e).__name__}: {e}")
            print(f"[失败] {source}{freq} {type(e).__name__}: {e}")
    if errors:
        print(f"[失败] {freq} 全部数据源未返回可用增量: {'; '.join(errors)}")
    return _empty_df()


def _fetch_sina_kline(
    freq: str,
    existing_tail: Optional[pd.Timestamp],
    datalen: int,
    now_dt: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    now_dt = now_dt or pd.Timestamp.now()
    return _fetch_kline_with_fallback(freq, existing_tail, datalen, now_dt)




def _with_sina_columns(main_df: pd.DataFrame, sina_df: pd.DataFrame) -> pd.DataFrame:
    out = main_df.copy()
    for col in SINA_SUFFIX_COLS:
        if col not in out.columns:
            out[col] = None
    if sina_df is None or sina_df.empty or out.empty:
        return out

    out["交易时间"] = pd.to_datetime(out["交易时间"], errors="coerce")
    right = sina_df[["交易时间", "开盘价", "最高价", "最低价", "收盘价", "涨跌", "涨跌幅%", "成交量", "成交额"]].copy()
    right["交易时间"] = pd.to_datetime(right["交易时间"], errors="coerce")
    right = right.rename(
        columns={
            "开盘价": "新浪开盘价",
            "最高价": "新浪最高价",
            "最低价": "新浪最低价",
            "收盘价": "新浪收盘价",
            "涨跌": "新浪涨跌",
            "涨跌幅%": "新浪涨跌幅%",
            "成交量": "新浪成交量",
            "成交额": "新浪成交额",
        }
    )
    out = out.merge(right, on="交易时间", how="left", suffixes=("", "_dup"))
    for col in SINA_SUFFIX_COLS[:-1]:
        dup = f"{col}_dup"
        if dup in out.columns:
            out[col] = out[col].where(out[col].notna(), out[dup])
            out = out.drop(columns=[dup])

    def _calc_ratio(row):
        a = _safe_float(row.get("收盘价"))
        b = _safe_float(row.get("新浪收盘价"))
        if a is None or b is None or b == 0:
            return None
        return abs(a - b) / abs(b)

    out["收盘价差异相对比率"] = out.apply(_calc_ratio, axis=1)

    insert_after = ["成交额", "data_source"]
    cols = list(out.columns)
    anchor = max((cols.index(c) for c in insert_after if c in cols), default=len(cols) - 1)
    front = [c for c in cols[: anchor + 1] if c not in SINA_SUFFIX_COLS]
    middle = [c for c in SINA_SUFFIX_COLS if c in out.columns]
    tail = [c for c in cols[anchor + 1 :] if c not in middle]
    return out[front + middle + tail]




def _send_wecom_text_raw(content: str) -> None:
    webhook = (os.environ.get("WECOM_WEBHOOK") or "").strip()
    if not webhook:
        return
    payload = {"msgtype": "text", "text": {"content": content}}
    resp = requests.post(webhook, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"企业微信推送失败: {data}")




def _to_weekly_from_daily(df: pd.DataFrame, source_name: str = "itick_1d_to_1w_utc2sh") -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_df()
    d = df.copy()
    d["交易时间"] = pd.to_datetime(d["交易时间"], errors="coerce")
    d = d.dropna(subset=["交易时间"]).sort_values("交易时间").set_index("交易时间")
    agg = pd.DataFrame()
    agg["开盘价"] = d["开盘价"].resample("W-FRI").first()
    agg["最高价"] = d["最高价"].resample("W-FRI").max()
    agg["最低价"] = d["最低价"].resample("W-FRI").min()
    agg["收盘价"] = d["收盘价"].resample("W-FRI").last()
    agg["成交量"] = d["成交量"].resample("W-FRI").sum(min_count=1)
    agg["成交额"] = d["成交额"].resample("W-FRI").sum(min_count=1)
    agg = agg.dropna(subset=["收盘价"]).reset_index()
    if agg.empty:
        return _empty_df()
    agg.insert(0, "证券代码", "000001")
    agg.insert(1, "证券名称", "上证指数")
    agg["涨跌"] = None
    agg["涨跌幅%"] = None
    agg["data_source"] = source_name
    return _normalize_df(agg)


def _to_daily_from_30m(
    df_30m: pd.DataFrame,
    trade_date: pd.Timestamp,
    now_dt: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """将指定交易日的 30m K 线聚合为一根日线（仅作各源日 K 均无当日时的兜底）。"""
    if df_30m is None or df_30m.empty:
        return _empty_df()
    now_dt = now_dt or pd.Timestamp.now()
    day = pd.Timestamp(trade_date).normalize()
    if not _bf.is_after_daily_close(now_dt) and day >= now_dt.normalize():
        return _empty_df()
    d = _drop_future_and_unclosed(_normalize_df(df_30m), now_dt, "30m")
    day_bars = d[d["交易时间"].dt.normalize() == day].sort_values("交易时间")
    if day_bars.empty:
        return _empty_df()
    vol = day_bars["成交量"].sum(min_count=1)
    amt = day_bars["成交额"].sum(min_count=1)
    row = {
        "证券代码": "000001",
        "证券名称": "上证指数",
        "交易时间": day,
        "开盘价": float(day_bars.iloc[0]["开盘价"]),
        "最高价": float(day_bars["最高价"].max()),
        "最低价": float(day_bars["最低价"].min()),
        "收盘价": float(day_bars.iloc[-1]["收盘价"]),
        "涨跌": None,
        "涨跌幅%": None,
        "成交量": None if pd.isna(vol) else float(vol),
        "成交额": None if pd.isna(amt) else float(amt),
        "data_source": "fallback_30m_to_1d",
    }
    out = _normalize_df(pd.DataFrame([row]))
    return _recalc_change_fields(out)


def _intraday_daily_refresh_needed(existing_1d: pd.DataFrame, now_dt: pd.Timestamp) -> bool:
    """CSV 中已有「当日」日线时，是否仍应用本次抓取结果覆盖（仅收盘后、且为 30m 兜底行）。"""
    now_dt = pd.Timestamp(now_dt)
    if not _bf.is_after_daily_close(now_dt):
        return False
    today = now_dt.normalize()
    d = _normalize_df(existing_1d) if existing_1d is not None else _empty_df()
    if d.empty:
        return False
    row = d[d["交易时间"].dt.normalize() == today]
    if row.empty:
        return False
    src = str(row.iloc[-1].get("data_source") or "")
    return src.startswith("fallback_30m_to_1d")


def _supplement_daily_from_30m(
    daily_df: pd.DataFrame,
    existing_30m: pd.DataFrame,
    live_30m: pd.DataFrame,
    now_dt: Optional[pd.Timestamp] = None,
    *,
    refresh_today: bool = False,
    trade_dates: Optional[list[pd.Timestamp]] = None,
) -> tuple[pd.DataFrame, bool]:
    """各源日 K 均未含当日时，用已收盘 30m 聚合补一根（最后兜底，非首选）。"""
    now_dt = now_dt or pd.Timestamp.now()
    today = now_dt.normalize()
    d1 = _drop_future_and_unclosed(
        _normalize_df(daily_df) if daily_df is not None else _empty_df(),
        now_dt,
        "1d",
        trade_dates=trade_dates,
    )
    if not _bf.is_after_daily_close(now_dt):
        print(f"[日线补全] {today.date()} 未收盘，跳过 30m 聚合与当日日线")
        return d1, False
    has_today_live = not d1.empty and (d1["交易时间"].dt.normalize() >= today).any()
    if has_today_live and not refresh_today:
        print(f"[日线补全] 日线已含 {today.date()}（来自行情源），跳过 30m 聚合")
        return d1, False
    if has_today_live and refresh_today:
        print(f"[日线补全] refresh_today：使用行情源当日 {today.date()} 覆盖 CSV 中旧行")
        return d1, True
    base = _normalize_df(existing_30m) if existing_30m is not None else _empty_df()
    if not base.empty:
        base = base[base["交易时间"].dt.normalize() == today]
    live = _normalize_df(live_30m) if live_30m is not None else _empty_df()
    day_30m = pd.concat([base, live], ignore_index=True)
    day_30m = day_30m.drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间")
    patch = _to_daily_from_30m(day_30m, today, now_dt)
    if patch.empty:
        print(f"[日线补全] 30m 无 {today.date()} 已收盘数据，跳过")
        return d1, False
    out = pd.concat([d1, patch], ignore_index=True)
    out = out.drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间").reset_index(drop=True)
    out = _recalc_change_fields(out)
    print(
        f"[日线补全] 兜底：由30m合成 {today.date()} "
        f"O={patch.iloc[0]['开盘价']} H={patch.iloc[0]['最高价']} "
        f"L={patch.iloc[0]['最低价']} C={patch.iloc[0]['收盘价']}"
    )
    return out, True


def _supplement_history_daily_from_30m(
    daily_live: pd.DataFrame,
    existing_1d: pd.DataFrame,
    existing_30m: pd.DataFrame,
    live_30m: pd.DataFrame,
    now_dt: pd.Timestamp,
    trade_dates: Optional[list[pd.Timestamp]] = None,
) -> tuple[pd.DataFrame, bool]:
    """对 CSV tail 与「已收盘最后交易日」之间仍缺的日线，用 30m 聚合补全（盘前/盘中可用）。"""
    now_dt = pd.Timestamp(now_dt)
    calendar_path = DATA_DIR / MERGED_NAME_MAP["1d"]
    if trade_dates is None:
        trade_dates = _bf.load_trade_dates(calendar_path)
    closed_limit = _bf.latest_closed_daily_date(
        now_dt, trade_dates, calendar_path=calendar_path
    )

    d_existing = _normalize_df(existing_1d) if existing_1d is not None else _empty_df()
    d_live = _normalize_df(daily_live) if daily_live is not None else _empty_df()
    if d_existing.empty:
        return d_live, False

    tail_day = pd.Timestamp(d_existing["交易时间"].max()).normalize()
    known = set(pd.to_datetime(d_existing["交易时间"], errors="coerce").dt.normalize())
    if not d_live.empty:
        known.update(pd.to_datetime(d_live["交易时间"], errors="coerce").dt.normalize())

    missing = [
        td
        for td in trade_dates
        if tail_day < td <= closed_limit and td not in known
    ]
    if not missing:
        return d_live, False

    df30 = pd.concat(
        [_normalize_df(existing_30m), _normalize_df(live_30m)],
        ignore_index=True,
    )
    df30 = df30.drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间")

    patches: list[pd.DataFrame] = []
    for day in missing:
        patch = _to_daily_from_30m(df30, day, now_dt)
        if patch.empty:
            print(f"[日线历史补全] {day.date()} 30m 不足，跳过")
            continue
        patches.append(patch)
        print(
            f"[日线历史补全] {day.date()} 由30m合成 "
            f"C={patch.iloc[0]['收盘价']}"
        )

    if not patches:
        return d_live, False

    out = pd.concat([d_live, *patches], ignore_index=True)
    out = out.drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间").reset_index(drop=True)
    out = _recalc_change_fields(out)
    return out, True


def _merge_by_datetime(existing: pd.DataFrame, live: pd.DataFrame, freq: str) -> pd.DataFrame:
    if existing.empty and live.empty:
        return _empty_df()
    if live.empty:
        print(f"[更新] {freq}: 无新增数据")
        return existing
    if existing.empty:
        merged = live.drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间").reset_index(drop=True)
        print(f"[更新] {freq}: 旧文件为空，直接写入 rows={len(merged)}")
        return merged

    existing = existing.copy().set_index("交易时间")
    live = live.copy().set_index("交易时间")
    common_index = existing.index.intersection(live.index)
    if len(common_index) > 0:
        existing = existing.drop(index=common_index)
    merged = pd.concat([existing, live], axis=0)
    merged = merged.reset_index().drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间").reset_index(drop=True)
    print(f"[更新] {freq}: existing={len(existing)} live={len(live)} merged={len(merged)} tail={merged['交易时间'].max() if not merged.empty else None}")
    return merged




def _safe_fetch_live(
    freq: str,
    existing_tail: Optional[pd.Timestamp],
    datalen: int,
    now_dt: pd.Timestamp,
) -> pd.DataFrame:
    return _fetch_kline_with_fallback(freq, existing_tail, datalen, now_dt)


def _safe_fetch_sina(
    freq: str,
    existing_tail: Optional[pd.Timestamp],
    datalen: int,
    now_dt: pd.Timestamp,
) -> pd.DataFrame:
    """兼容旧名：实际走多源 fallback。"""
    return _safe_fetch_live(freq, existing_tail, datalen, now_dt)


SPARSE_1M_MORNING_START = "09:30"
SPARSE_1M_MORNING_END = "10:30"
SPARSE_1M_AFTERNOON = "14:57"
SPARSE_1M_DEFAULT_START = "2023-10-13"
SPARSE_1M_DEFAULT_END = "2025-09-09"


def _is_sparse_1m_target(ts: pd.Timestamp) -> bool:
    """稀疏 1m 目标时刻：09:30–10:30 上午 + 14:57。"""
    if not _is_valid_minute_time(ts, "1m"):
        return False
    hm = ts.strftime("%H:%M")
    if hm == SPARSE_1M_AFTERNOON:
        return True
    return SPARSE_1M_MORNING_START <= hm <= SPARSE_1M_MORNING_END


def _filter_sparse_1m_bars(df: pd.DataFrame, trade_day: pd.Timestamp) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_df()
    day = pd.Timestamp(trade_day).normalize()
    d = _normalize_df(df)
    d = d[d["交易时间"].dt.normalize() == day]
    if d.empty:
        return _empty_df()
    mask = d["交易时间"].map(_is_sparse_1m_target)
    out = d[mask].sort_values("交易时间").reset_index(drop=True)
    return out


def _load_trade_dates_between(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    daily_path = DATA_DIR / MERGED_NAME_MAP["1d"]
    df = _load_existing_file(daily_path, verbose=False)
    if df.empty:
        raise RuntimeError(f"日线为空，无法取交易日历: {daily_path}")
    days = df["交易时间"].dt.normalize().drop_duplicates().sort_values()
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    picked = days[(days >= start) & (days <= end)]
    return [pd.Timestamp(x) for x in picked.tolist()]


def _day_sparse_already_filled(existing: pd.DataFrame, trade_day: pd.Timestamp) -> bool:
    if existing is None or existing.empty:
        return False
    day = pd.Timestamp(trade_day).normalize()
    sub = existing[existing["交易时间"].dt.normalize() == day]
    if sub.empty:
        return False
    hms = set(sub["交易时间"].dt.strftime("%H:%M"))
    has_1457 = SPARSE_1M_AFTERNOON in hms
    has_morning = any(SPARSE_1M_MORNING_START <= hm <= SPARSE_1M_MORNING_END for hm in hms)
    has_1000 = "10:00" in hms
    return has_1457 and has_morning and has_1000


def _bar_dict_from_values(
    ts: pd.Timestamp,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    vol: Optional[float] = None,
    amt: Optional[float] = None,
    source: str,
) -> dict:
    return {
        "证券代码": "000001",
        "证券名称": "上证指数",
        "交易时间": ts,
        "开盘价": o,
        "最高价": h,
        "最低价": l,
        "收盘价": c,
        "涨跌": None,
        "涨跌幅%": None,
        "成交量": vol,
        "成交额": amt,
        "data_source": source,
    }


def _load_5m_day_bars(trade_day: pd.Timestamp) -> pd.DataFrame:
    path = DATA_DIR / MERGED_NAME_MAP["5m"]
    df = _load_existing_file(path, verbose=False)
    if df.empty:
        return _empty_df()
    day = pd.Timestamp(trade_day).normalize()
    return df[df["交易时间"].dt.normalize() == day].sort_values("交易时间").reset_index(drop=True)


def _synthesize_sparse_1m_from_5m(trade_day: pd.Timestamp) -> pd.DataFrame:
    """断档历史：东财/新浪 1m 接口无法按日拉上证，用本地连续 5m 合成稀疏 1m。"""
    day = pd.Timestamp(trade_day).normalize()
    bars_5m = _load_5m_day_bars(day)
    if bars_5m.empty:
        raise RuntimeError("local 5m empty for day")

    rows: list[dict] = []
    for _, bar in bars_5m.iterrows():
        bar_end = pd.Timestamp(bar["交易时间"])
        o = float(bar["开盘价"])
        h = float(bar["最高价"])
        l = float(bar["最低价"])
        c = float(bar["收盘价"])
        vol = _safe_float(bar.get("成交量"))
        amt = _safe_float(bar.get("成交额"))
        for i in range(5):
            ts = bar_end - pd.Timedelta(minutes=4 - i)
            if ts.normalize() != day:
                continue
            if not _is_sparse_1m_target(ts):
                continue
            rows.append(
                _bar_dict_from_values(ts, o, h, l, c, vol=vol, amt=amt, source="sparse_from_5m")
            )

    ts_1457 = day + pd.Timedelta(hours=14, minutes=57)
    before = bars_5m[bars_5m["交易时间"] <= ts_1457]
    after = bars_5m[bars_5m["交易时间"] > ts_1457]
    if not before.empty and not after.empty:
        t0 = pd.Timestamp(before.iloc[-1]["交易时间"])
        t1 = pd.Timestamp(after.iloc[0]["交易时间"])
        p0 = float(before.iloc[-1]["收盘价"])
        p1 = float(after.iloc[0]["收盘价"])
        if t1 > t0:
            ratio = (ts_1457 - t0) / (t1 - t0)
            px = p0 + (p1 - p0) * float(ratio)
            rows.append(
                _bar_dict_from_values(
                    ts_1457, px, px, px, px,
                    source="sparse_from_5m_interp_1457",
                )
            )

    if not rows:
        raise RuntimeError("sparse_from_5m produced no rows")
    out = _normalize_df(pd.DataFrame(rows))
    out = out.drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间").reset_index(drop=True)
    print(
        f"[K线] 000001 {day.date()} 1m <- local_5m_synth rows={len(out)} "
        f"range={out['交易时间'].min()}~{out['交易时间'].max()}"
    )
    return out


def _fetch_index_1m_for_trade_day(trade_day: pd.Timestamp, now_dt: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """按交易日抓取上证 1m（东财/腾讯/新浪近端；断档区间请走聚宽导出）。"""
    day = pd.Timestamp(trade_day).normalize()
    now_dt = now_dt or pd.Timestamp.now()
    gap_start = pd.Timestamp(SPARSE_1M_DEFAULT_START).normalize()
    gap_end = pd.Timestamp(SPARSE_1M_DEFAULT_END).normalize()
    if gap_start <= day <= gap_end:
        raise RuntimeError(
            f"{day.date()} 在 1m 断档区间；请用聚宽 scripts/jq_export_kline.py 导出后 "
            f"运行 scripts/merge_jq_export_1m.py 合并（东财/新浪无法按日拉历史上证 1m，勿用 5m 合成）"
        )

    errors: list[str] = []

    try:
        raw = _fetch_eastmoney_raw("1m", 280, end_day=day)
        raw = _normalize_df(raw)
        day_df = raw[raw["交易时间"].dt.normalize() == day]
        if not day_df.empty:
            print(f"[K线] 000001 {day.date()} 1m <- eastmoney rows={len(day_df)}")
            return _drop_future_and_unclosed(day_df.sort_values("交易时间").reset_index(drop=True), now_dt, "1m")
        if not raw.empty:
            errors.append(
                f"eastmoney: index 1m 仅近端({raw['交易时间'].min()}~{raw['交易时间'].max()})，非 {day.date()}"
            )
        else:
            errors.append("eastmoney: empty after day filter")
    except Exception as e:
        errors.append(f"eastmoney: {type(e).__name__}: {e}")
        print(f"[失败] 000001 {day.date()} 1m eastmoney {type(e).__name__}: {e}")

    try:
        raw = _fetch_tencent_raw("1m", 280)
        raw = _normalize_df(raw)
        day_df = raw[raw["交易时间"].dt.normalize() == day]
        if not day_df.empty:
            print(f"[K线] 000001 {day.date()} 1m <- tencent rows={len(day_df)}")
            return _drop_future_and_unclosed(day_df.sort_values("交易时间").reset_index(drop=True), now_dt, "1m")
        errors.append("tencent: empty after day filter")
    except Exception as e:
        errors.append(f"tencent: {type(e).__name__}: {e}")

    if day >= now_dt.normalize() - pd.Timedelta(days=6):
        try:
            raw = _fetch_sina_raw("1m", 280)
            raw = _normalize_df(raw)
            day_df = raw[raw["交易时间"].dt.normalize() == day]
            if not day_df.empty:
                print(f"[K线] 000001 {day.date()} 1m <- sina rows={len(day_df)}")
                return _drop_future_and_unclosed(day_df.sort_values("交易时间").reset_index(drop=True), now_dt, "1m")
            errors.append("sina: empty after day filter")
        except Exception as e:
            errors.append(f"sina: {type(e).__name__}: {e}")

    raise RuntimeError("; ".join(errors))


def _verify_sparse_1m_gap(path: Path, sample_days: list[str]) -> None:
    if not path.exists():
        print("[验收] 1m 文件不存在")
        return
    df = pd.read_csv(path, encoding="utf-8-sig", usecols=["交易时间", "收盘价"])
    df["交易时间"] = pd.to_datetime(df["交易时间"], errors="coerce")
    for ds in sample_days:
        ts = pd.Timestamp(f"{ds} 14:57:00")
        sub = df[df["交易时间"] <= ts]
        if sub.empty:
            print(f"[验收] {ds} 14:57 asof 无数据")
            continue
        row = sub.iloc[-1]
        print(f"[验收] {ds} 14:57 asof -> {row['交易时间']} close={row['收盘价']}")


def backfill_sparse_1m(
    start_date: str = SPARSE_1M_DEFAULT_START,
    end_date: str = SPARSE_1M_DEFAULT_END,
    *,
    dry_run: bool = False,
    skip_existing: bool = True,
    sleep_sec: float = 0.25,
) -> dict:
    """补 1m 断档稀疏数据：每个交易日 09:30–10:30 + 14:57。"""
    if FORMAL_ENV_PATH.exists():
        _load_env_if_exists(FORMAL_ENV_PATH)
    else:
        _load_env_if_exists(LEGACY_ENV_PATH)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    path = LIVE_DIR / LIVE_NAME_MAP["1m"]
    existing = _load_existing_file(path)
    trade_days = _load_trade_dates_between(start, end)
    print(
        f"[稀疏补数] 区间={start.date()}~{end.date()} 交易日={len(trade_days)} "
        f"目标=上午{SPARSE_1M_MORNING_START}-{SPARSE_1M_MORNING_END}+{SPARSE_1M_AFTERNOON} dry_run={dry_run}"
    )

    chunks: list[pd.DataFrame] = []
    failed: list[tuple[str, str]] = []
    skipped = 0
    now_dt = pd.Timestamp.now()

    for idx, day in enumerate(trade_days, start=1):
        if skip_existing and _day_sparse_already_filled(existing, day):
            skipped += 1
            continue
        try:
            day_full = _fetch_index_1m_for_trade_day(day, now_dt=now_dt)
            sparse = _filter_sparse_1m_bars(day_full, day)
            if sparse.empty:
                failed.append((day.date().isoformat(), "filter empty"))
                print(f"[跳过] {day.date()} 过滤后无稀疏 bar")
                continue
            sparse["data_source"] = sparse["data_source"].fillna("sparse_backfill_1m")
            chunks.append(sparse)
            print(
                f"[进度] {idx}/{len(trade_days)} {day.date()} sparse_rows={len(sparse)} "
                f"range={sparse['交易时间'].min()}~{sparse['交易时间'].max()}"
            )
        except Exception as e:
            failed.append((day.date().isoformat(), str(e)))
            print(f"[失败] {idx}/{len(trade_days)} {day.date()} {e}")
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    if not chunks:
        print(f"[完成] 无新增稀疏条 skipped={skipped} failed={len(failed)}")
        return {"ok": len(failed) == 0, "added_rows": 0, "skipped_days": skipped, "failed": failed}

    batch = pd.concat(chunks, ignore_index=True)
    batch = batch.drop_duplicates(subset=["交易时间"], keep="last").sort_values("交易时间").reset_index(drop=True)
    print(f"[汇总] 待写入 sparse rows={len(batch)} head={batch['交易时间'].min()} tail={batch['交易时间'].max()}")

    if dry_run:
        print("[dry-run] 未写入 CSV")
        return {"ok": True, "added_rows": len(batch), "skipped_days": skipped, "failed": failed, "dry_run": True}

    merged = _merge_by_datetime(existing, batch, "1m")
    merged = _with_sina_columns(merged, batch)
    merged = _drop_future_and_unclosed(merged, now_dt, "1m")
    plan = _build_plan("1m")
    written_path = path
    try:
        _write_outputs([plan], {"1m": merged})
    except RuntimeError as write_err:
        alt = path.with_name(f"{path.stem}_sparse_backfill{path.suffix}")
        alt.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(alt, index=False, encoding="utf-8-sig")
        written_path = alt
        print(f"[警告] 主 CSV 被占用，已写入备用文件: {alt} ({write_err})")
    _verify_sparse_1m_gap(written_path, ["2024-01-05", "2024-11-05", "2025-07-10", "2026-06-22"])
    print(f"[完成] 写入 {written_path.name} added={len(batch)} skipped={skipped} failed={len(failed)}")
    return {
        "ok": len(failed) == 0,
        "added_rows": len(batch),
        "skipped_days": skipped,
        "failed": failed,
        "written_path": str(written_path),
    }


def _load_all_existing(plans, freqs: Optional[set[str]] = None):
    result = {}
    for plan in plans:
        if freqs is not None and plan.freq not in freqs:
            continue
        result[plan.freq] = _load_existing_file(plan.output_path)
    return result


def _fetch_all_live(
    tails: dict[str, Optional[pd.Timestamp]],
    trade_dates: Optional[list[pd.Timestamp]] = None,
) -> dict[str, pd.DataFrame]:
    now_dt = pd.Timestamp.now()
    calendar_path = DATA_DIR / MERGED_NAME_MAP["1d"]
    result = {}
    for freq in ("1m", "5m", "30m", "1d"):
        if freq == "1d" and not _bf.is_after_daily_close(now_dt):
            closed = _bf.latest_closed_daily_date(
                now_dt, trade_dates, calendar_path=calendar_path
            )
            print(
                f"[抓取说明] 1d: 未收盘，仍拉取 tail 之后至 {closed.date()} 的历史日线，跳过当日标签"
            )
        tail = tails.get(freq)
        datalen = _estimate_datalen(freq, tail, now_dt)
        if tail is not None and freq != "1d":
            bars_per_day = SINA_BARS_PER_TRADING_DAY.get(freq, 8)
            calendar_days = max(1, (now_dt - tail).days + 1)
            uncapped = max(2, int(calendar_days * 5 / 7) * bars_per_day + 2)
            if uncapped > KLINE_MAX_DATALEN:
                print(
                    f"[警告] {freq} 估算需 {uncapped} 条，接口上限 {KLINE_MAX_DATALEN}，"
                    f"仅抓取最近 {KLINE_MAX_DATALEN} 条，长时间未更新可能缺段"
                )
        result[freq] = _safe_fetch_live(freq, tail, datalen, now_dt)
    return result


def _build_plan(freq: str) -> Plan:
    return Plan(
        freq=freq,
        merged_path=MERGED_DIR / MERGED_NAME_MAP[freq],
        output_path=LIVE_DIR / LIVE_NAME_MAP[freq],
        jump_limit=0.50 if freq == "1w" else 0.10,
    )



def _write_outputs(
    plans: list[Plan],
    outputs: dict[str, pd.DataFrame],
    now_dt: Optional[pd.Timestamp] = None,
    trade_dates: Optional[list[pd.Timestamp]] = None,
) -> None:
    now_dt = now_dt or pd.Timestamp.now()
    write_errors: list[str] = []
    for plan in plans:
        df = outputs.get(plan.freq)
        if df is None:
            df = _empty_df()
        else:
            df = _drop_future_and_unclosed(df, now_dt, plan.freq, trade_dates=trade_dates)
        plan.output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.to_csv(plan.output_path, index=False, encoding="utf-8-sig")
            print(f"[写入完成] {plan.freq} -> {plan.output_path.name} rows={len(df)} head={df['交易时间'].min() if not df.empty else None} tail={df['交易时间'].max() if not df.empty else None}")
        except PermissionError:
            msg = f"[跳过] {plan.freq} -> {plan.output_path.name} 文件被占用，未能写入"
            write_errors.append(msg)
            print(msg)
        except Exception as e:
            msg = f"[失败] {plan.freq} -> {plan.output_path.name} {type(e).__name__}: {e}"
            write_errors.append(msg)
            print(msg)
    if write_errors:
        raise RuntimeError("; ".join(write_errors))


def _load_if_sync_module():
    spec = importlib.util.spec_from_file_location("if_runpoints_sync_module", IF_RUNPOINTS_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载: {IF_RUNPOINTS_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["if_runpoints_sync_module"] = mod
    spec.loader.exec_module(mod)
    return mod


def sync_if_runpoints_data() -> dict:
    # 方法说明
    # 输入参数：无（读 IF_RUNPOINTS_PATH 与日线交易日历）
    # 输出参数：dict（ok、added_rows、message、tail 等，来自 期货合约数据获取.sync_if_runpoints）
    # 用途：main() 末尾默认调用；--skip-if 可跳过
    if not IF_RUNPOINTS_SCRIPT.is_file():
        return {"ok": False, "added_rows": 0, "message": f"未找到脚本: {IF_RUNPOINTS_SCRIPT}"}
    if not IF_RUNPOINTS_PATH.is_file():
        return {"ok": False, "added_rows": 0, "message": f"未找到基线 CSV: {IF_RUNPOINTS_PATH}"}
    print(f"[IF合约] 开始同步 -> {IF_RUNPOINTS_PATH.name}")
    mod = _load_if_sync_module()
    return mod.sync_if_runpoints(
        output_path=IF_RUNPOINTS_PATH,
        trade_calendar=DATA_DIR / MERGED_NAME_MAP["1d"],
    )


def main(argv: Optional[list[str]] = None) -> None:
    # 方法说明
    # 输入参数：argv（可选，子进程/回测 sync 调用时勿污染 sys.argv）
    # 输出参数：无（写 CSV 或打印统计）
    # 用途：默认路径为上证五周期增量 + IF 同步；稀疏补数模式仅写 1m 指定时刻
    import argparse

    parser = argparse.ArgumentParser(description="上证行情增量同步 / 1m 稀疏补数")
    parser.add_argument(
        "--backfill-1m-sparse",
        action="store_true",
        help="补 1m 断档稀疏数据（09:30-10:30 + 14:57）",
    )
    parser.add_argument("--start-date", default=SPARSE_1M_DEFAULT_START)
    parser.add_argument("--end-date", default=SPARSE_1M_DEFAULT_END)
    parser.add_argument("--dry-run", action="store_true", help="只抓取统计，不写 CSV")
    parser.add_argument("--no-skip-existing", action="store_true", help="不跳过已补齐的交易日")
    parser.add_argument("--sleep-sec", type=float, default=0.25, help="逐日抓取间隔秒数")
    parser.add_argument("--skip-if", action="store_true", help="不同步 IF_front_next_runpoints.csv")
    args = parser.parse_args(argv)

    if args.backfill_1m_sparse:
        backfill_sparse_1m(
            args.start_date,
            args.end_date,
            dry_run=args.dry_run,
            skip_existing=not args.no_skip_existing,
            sleep_sec=max(0.0, float(args.sleep_sec)),
        )
        return

    if FORMAL_ENV_PATH.exists():
        _load_env_if_exists(FORMAL_ENV_PATH)
    else:
        _load_env_if_exists(LEGACY_ENV_PATH)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    plans = [_build_plan(freq) for freq in ("1m", "5m", "30m", "1d", "1w")]
    tails = {plan.freq: _read_file_tail_time(plan.output_path, plan.freq) for plan in plans}
    for plan in plans:
        print(f"[tail] {plan.freq}: {tails.get(plan.freq)}")

    now_dt = pd.Timestamp.now()
    path_1d = next(p.output_path for p in plans if p.freq == "1d")
    path_30m = next(p.output_path for p in plans if p.freq == "30m")
    calendar_path = DATA_DIR / MERGED_NAME_MAP["1d"]
    trade_dates = _extended_trade_dates(calendar_path, path_30m)
    existing_1d = _load_existing_file(path_1d, verbose=False)
    strip_today_1d = _bf.needs_strip_today_daily(existing_1d, now_dt)
    refresh_today_1d = _intraday_daily_refresh_needed(existing_1d, now_dt)
    if refresh_today_1d:
        print(f"[日线] 检测到当日 {now_dt.date()} fallback 行需覆盖更新（仅收盘后）")
    elif strip_today_1d:
        print(f"[日线] 未收盘，将剔除 CSV 中当日 {now_dt.date()} 日线")

    live_map = _fetch_all_live(tails, trade_dates=trade_dates)
    trade_dates = _extended_trade_dates(calendar_path, path_30m, live_map.get("30m"))
    for freq in list(live_map.keys()):
        live_map[freq] = _drop_future_and_unclosed(
            live_map.get(freq), now_dt, freq, trade_dates=trade_dates
        )
    recent_30m = _load_recent_rows_from_csv(path_30m, tail_lines=500)
    live_map["1d"], history_supplemented = _supplement_history_daily_from_30m(
        live_map["1d"],
        existing_1d,
        recent_30m,
        live_map["30m"],
        now_dt,
        trade_dates=trade_dates,
    )
    live_map["1d"], daily_supplemented = _supplement_daily_from_30m(
        live_map["1d"],
        recent_30m,
        live_map["30m"],
        now_dt=now_dt,
        refresh_today=refresh_today_1d,
        trade_dates=trade_dates,
    )
    live_map["1w"] = _to_weekly_from_daily(live_map["1d"], source_name="sina_1d_to_1w")

    update_freqs: set[str] = set()
    for freq in ("1m", "5m", "30m", "1d"):
        if _live_has_new_rows(live_map.get(freq), tails.get(freq), freq):
            update_freqs.add(freq)
    if daily_supplemented:
        update_freqs.add("1d")
    if history_supplemented:
        update_freqs.add("1d")
    if refresh_today_1d:
        update_freqs.add("1d")
    if strip_today_1d:
        update_freqs.add("1d")
    if (
        _live_has_new_rows(live_map.get("1w"), tails.get("1w"), "1d")
        or daily_supplemented
        or history_supplemented
        or refresh_today_1d
        or strip_today_1d
    ):
        update_freqs.add("1w")

    if not update_freqs:
        print("[完成] 各周期均无新增，跳过上证 CSV 写入")
    else:
        print(f"[更新计划] 需写入: {sorted(update_freqs)}")
        existing_map = _load_all_existing(plans, freqs=update_freqs)

        merged_map = {}
        for freq in ("1m", "5m", "30m", "1d", "1w"):
            if freq not in update_freqs:
                continue
            merged_map[freq] = _merge_by_datetime(existing_map[freq], live_map[freq], freq)
            merged_map[freq] = _drop_future_and_unclosed(
                merged_map[freq], now_dt, freq, trade_dates=trade_dates
            )

        result_map = {}
        for freq in update_freqs:
            result_map[freq] = _with_sina_columns(merged_map[freq], live_map[freq])
            result_map[freq] = _drop_future_and_unclosed(
                result_map[freq], now_dt, freq, trade_dates=trade_dates
            )

        _write_outputs(
            [p for p in plans if p.freq in update_freqs],
            result_map,
            now_dt=now_dt,
            trade_dates=trade_dates,
        )

    if args.skip_if:
        print("[IF合约] 已跳过（--skip-if）")
        return

    if_result = sync_if_runpoints_data()
    if not if_result.get("ok"):
        print(f"[IF合约警告] {if_result.get('message')}")
    elif if_result.get("added_rows", 0):
        print(f"[IF合约完成] +{if_result['added_rows']} 行 tail={if_result.get('tail')}")
    else:
        print(f"[IF合约完成] {if_result.get('message', '无新增')}")


if __name__ == "__main__":
    main()
