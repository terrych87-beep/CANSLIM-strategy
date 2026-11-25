#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：无（使用当前时刻与本地交易日历）
# 输出参数：stdout 打印各源 30m/1m/1d 与 IF 运行点探测结果
# 中文映射：
#   - closed_30m_limit -> bar_filters 计算的已收盘 30m 边界
#   - _drop_future_and_unclosed -> 与生产同步相同的过滤
# 用途：开发期探测东财/新浪行情与 IF 取价是否可用
# 性质：【开发探测】非 BASELINE_v3.1 回归必跑；产物勿提交（原 data/_probe_em_sina.txt 已清理）
# 使用说明：python scripts/probe_em_sina.py

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
IF_SCRIPT = DATA / "期货合约数据获取.py"
LIVE_SCRIPT = DATA / "实时数据运行.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _last_two_trade_days(now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    cal = pd.read_csv(DATA / "上证指数日线加入实时数据.csv", encoding="utf-8-sig", usecols=["交易时间"])
    dates = sorted(pd.to_datetime(cal["交易时间"]).dt.normalize().unique())
    trade_days = [d for d in dates if d <= now.normalize()]
    last_td = trade_days[-1]
    prev_td = trade_days[-2] if len(trade_days) >= 2 else last_td
    return prev_td, last_td


def probe_if(prev_td: pd.Timestamp, last_td: pd.Timestamp, now: pd.Timestamp) -> None:
    if_mod = _load(IF_SCRIPT, "if_probe")
    if_mod.FAIL_CACHE.clear()
    tdates = if_mod._load_trade_dates()
    existing = if_mod._read_existing(DATA / "IF_front_next_runpoints.csv")
    seed = existing.iloc[-1]
    front, nxt = str(seed["front_contract"]), str(seed["next_contract"])
    front, nxt = if_mod._resolve_front_next(last_td, tdates, front, nxt)
    print(f"IF 合约（{last_td.date()}）front={front} next={nxt}")

    runpoints = ("10:00", "14:30", "14:57")
    for label, day in (("prev", prev_td), ("last", last_td)):
        print(f"\n--- IF {label} {day.date()} ---")
        for contract in (front, nxt):
            for src, klt in (("eastmoney_30m", "30"), ("eastmoney_1m", "1"), ("sina_1m", "1")):
                try:
                    if src.startswith("eastmoney"):
                        df = if_mod._fetch_em_kline(contract, klt, day, lmt=80)
                        df = df[df["datetime"].dt.normalize() == day]
                    else:
                        if day < now.normalize() - pd.Timedelta(days=6):
                            print(f"  {contract} sina_1m: skip (>6d ago)")
                            continue
                        df = if_mod._fetch_sina_minute(contract)
                        df = df[df["datetime"].dt.normalize() == day]
                    status = "OK" if len(df) else "EMPTY"
                    print(f"  {contract} {src}: {status} rows={len(df)}", end="")
                    if len(df):
                        print(
                            f" | {df['datetime'].min()} ~ {df['datetime'].max()} "
                            f"| last_close={df['close'].iloc[-1]}"
                        )
                        for hm in runpoints:
                            h, m = map(int, hm.split(":"))
                            run_dt = day + pd.Timedelta(hours=h, minutes=m)
                            if run_dt > now:
                                continue
                            px = if_mod._price_at_minute(df, run_dt)
                            print(f"      {hm} close={px}")
                    else:
                        print()
                except Exception as e:
                    print(f"  {contract} {src}: FAIL {type(e).__name__}: {e}")


def probe_index(prev_td: pd.Timestamp, last_td: pd.Timestamp, now: pd.Timestamp) -> None:
    live = _load(LIVE_SCRIPT, "live_probe")
    bf = _load(DATA / "bar_filters.py", "bf_probe")
    print("\n=== 上证指数 sh000001 ===")
    print(f"closed_30m_limit={bf.latest_closed_bar_time(now, '30m')}")
    print(f"closed_1d_limit={bf.latest_closed_daily_date(now)}")
    for freq in ("30m", "1m", "1d"):
        print(f"\n--- index {freq} ---")
        for src_name, fetcher in (
            ("sina", lambda f=freq: live._fetch_sina_raw(f, 120)),
            ("eastmoney", lambda f=freq: live._fetch_eastmoney_raw(f, 120, end_day=last_td)),
        ):
            try:
                df = fetcher()
                df = live._normalize_df(df)
                df = live._drop_future_and_unclosed(df, now, freq)
                if df.empty:
                    print(f"  {src_name}: EMPTY")
                    continue
                d = df[df["交易时间"].dt.normalize().isin([prev_td, last_td])]
                print(f"  {src_name}: total_rows={len(df)} hit_prev_last={len(d)}")
                if not d.empty:
                    for _, row in d.iterrows():
                        print(
                            f"    {row['交易时间']} O={row['开盘价']} H={row['最高价']} "
                            f"L={row['最低价']} C={row['收盘价']} src={row.get('data_source')}"
                        )
                    if freq == "30m":
                        for hm in ("10:00", "14:30"):
                            t = last_td + pd.Timedelta(hours=int(hm[:2]), minutes=int(hm[3:5]))
                            hit = d[d["交易时间"].dt.floor("min") == t]
                            if not hit.empty:
                                print(f"    last_day {hm} C={hit.iloc[-1]['收盘价']}")
                    if freq == "1m":
                        t = last_td + pd.Timedelta(hours=14, minutes=57)
                        hit = d[d["交易时间"].dt.floor("min") == t]
                        if not hit.empty:
                            print(f"    last_day 14:57 C={hit.iloc[-1]['收盘价']}")
            except Exception as e:
                print(f"  {src_name}: FAIL {type(e).__name__}: {e}")


def main() -> None:
    now = pd.Timestamp.now()
    prev_td, last_td = _last_two_trade_days(now)
    print("=== 探测时间 ===")
    print("now:", now)
    print("prev_trade_day:", prev_td.date())
    print("last_trade_day:", last_td.date())
    print("\n=== IF 期货（东财 + 新浪）===")
    probe_if(prev_td, last_td, now)
    probe_index(prev_td, last_td, now)


if __name__ == "__main__":
    main()
