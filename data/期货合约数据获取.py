#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --since（可选）；读取 IF_front_next_runpoints.csv tail 与交易日历
# 输出参数：追加写入 IF_front_next_runpoints.csv；sync_if_runpoints 返回 {ok, added_rows, message, tail}
# 中文映射：
#   - front_contract / next_contract -> 主力 / 次主力 IF 合约
#   - front_close / next_close -> 对应运行点收盘价
#   - RUNPOINT_TIMES -> 8 档运行点（10:00…11:00、13:00…14:57，不含 11:30）
#   - datetime -> 运行点时刻
# 用途：增量更新 IF 前后月运行点；东财 30m 失败时 fallback 新浪 1m；由 data/实时数据运行.py 末尾调用
# 使用说明：
#   python data/期货合约数据获取.py
#   python data/期货合约数据获取.py --since 2026-06-02
# 换月规则：到期月第三个周五为最后交易日，次一交易日 front 切换为次月合约

from __future__ import annotations

import json
import re
import sys
import time
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
OUTPUT_PATH = DATA_DIR / "IF_front_next_runpoints.csv"
TRADE_CALENDAR = DATA_DIR / "上证指数日线加入实时数据.csv"

RUNPOINT_TIMES = ("10:00", "10:30", "11:00", "13:00", "13:30", "14:00", "14:30", "14:57")
HTTP_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SINA_MIN_URL = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_X=/InnerFuturesNewService.getFewMinLine"
MAX_KLINE = 1024
FAIL_CACHE: set[tuple[str, str, str, str]] = set()


def _parse_contract_ym(contract: str) -> tuple[int, int]:
    digits = "".join(c for c in str(contract) if c.isdigit())
    if len(digits) < 4:
        raise ValueError(f"无法解析合约: {contract}")
    return 2000 + int(digits[:2]), int(digits[2:4])


def _contract_code(year: int, month: int) -> str:
    return f"IF{year % 100:02d}{month:02d}"


def _advance_contract(contract: str, months: int = 1) -> str:
    y, m = _parse_contract_ym(contract)
    m += months
    while m > 12:
        m -= 12
        y += 1
    return _contract_code(y, m)


def _third_friday(year: int, month: int) -> pd.Timestamp:
    first = pd.Timestamp(year=year, month=month, day=1)
    offset = (4 - first.dayofweek) % 7
    return (first + pd.Timedelta(days=offset + 14)).normalize()


def _load_trade_dates() -> list[pd.Timestamp]:
    if not TRADE_CALENDAR.exists():
        raise FileNotFoundError(f"未找到交易日历: {TRADE_CALENDAR}")
    df = pd.read_csv(TRADE_CALENDAR, encoding="utf-8-sig", usecols=["交易时间"])
    dates = pd.to_datetime(df["交易时间"], errors="coerce").dt.normalize().dropna().unique()
    return sorted(pd.Timestamp(d) for d in dates)


def _first_trade_on_or_after(day: pd.Timestamp, trade_dates: list[pd.Timestamp]) -> pd.Timestamp:
    day = pd.Timestamp(day).normalize()
    for td in trade_dates:
        if td >= day:
            return td
    return day


def _front_roll_date(front_contract: str, trade_dates: list[pd.Timestamp]) -> Optional[pd.Timestamp]:
    """当月合约到期后切换为次月合约。

    规则：到期月最后交易日 = 该月第三个周五；若遇法定节假日则顺延至下一交易日。
    换月日 = 最后交易日之后的第一个交易日（即过了到期日后 front 变为 next month）。
    """
    y, m = _parse_contract_ym(front_contract)
    last_trading_day = _first_trade_on_or_after(_third_friday(y, m), trade_dates)
    for td in trade_dates:
        if td > last_trading_day:
            return td
    return None


def _resolve_front_next(
    trade_day: pd.Timestamp,
    trade_dates: list[pd.Timestamp],
    front: str,
    nxt: str,
) -> tuple[str, str]:
    roll_day = _front_roll_date(front, trade_dates)
    while roll_day is not None and trade_day >= roll_day:
        front = _advance_contract(front, 1)
        nxt = _advance_contract(front, 1)
        roll_day = _front_roll_date(front, trade_dates)
    return front, nxt


def _format_dt(dt: pd.Timestamp) -> str:
    dt = pd.Timestamp(dt)
    return f"{dt.year}/{dt.month}/{dt.day} {dt.strftime('%H:%M')}"


def _read_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=["datetime", "front_contract", "front_close", "next_contract", "next_close"]
        )
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    return df.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)


def _http_get(url: str, *, params: dict | None = None, timeout: int = 25, retries: int = 3) -> requests.Response:
    last_err: Exception | None = None
    for i in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers=HTTP_UA)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            if i + 1 < retries:
                time.sleep(1.5 * (i + 1))
    raise last_err  # type: ignore[misc]


def _parse_em_klines(lines: list[str], source: str) -> pd.DataFrame:
    rows = []
    for line in lines:
        parts = line.split(",")
        if len(parts) < 3:
            continue
        ts = pd.to_datetime(parts[0], errors="coerce")
        if pd.isna(ts):
            continue
        rows.append({"datetime": ts, "close": float(parts[2]), "source": source})
    if not rows:
        return pd.DataFrame(columns=["datetime", "close", "source"])
    return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)


def _fetch_em_kline(
    contract: str,
    klt: str,
    end_day: pd.Timestamp,
    lmt: int = 300,
) -> pd.DataFrame:
    params = {
        "secid": f"220.{contract}",
        "klt": str(klt),
        "fqt": "0",
        "lmt": str(max(2, min(int(lmt), MAX_KLINE))),
        "end": pd.Timestamp(end_day).strftime("%Y%m%d"),
        "fields1": "f1",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    resp = _http_get(EM_KLINE_URL, params=params, timeout=40)
    payload = resp.json()
    if payload.get("rc") != 0 or not payload.get("data") or not payload["data"].get("klines"):
        raise RuntimeError(f"东财 {contract} klt={klt} 无数据 rc={payload.get('rc')}")
    return _parse_em_klines(payload["data"]["klines"], "eastmoney")


def _fetch_sina_minute(contract: str) -> pd.DataFrame:
    resp = _http_get(SINA_MIN_URL, params={"symbol": contract, "type": "1"})
    text = resp.text
    m = re.search(r"\((\[.*\])\)", text, re.S)
    if not m:
        raise RuntimeError(f"新浪 {contract} 返回格式异常: {text[:120]}")
    raw = json.loads(m.group(1))
    if not raw:
        raise RuntimeError(f"新浪 {contract} 空结果")
    rows = []
    for item in raw:
        ts = pd.to_datetime(item.get("d"), errors="coerce")
        if pd.isna(ts):
            continue
        rows.append({"datetime": ts, "close": float(item.get("c")), "source": "sina"})
    if not rows:
        raise RuntimeError(f"新浪 {contract} 空结果")
    return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)


def _filter_closed_bars(df: pd.DataFrame, now: pd.Timestamp, klt: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    freq = {"30": "30m", "1": "1m"}.get(str(klt))
    if freq is None:
        return df
    return _bf.drop_future_and_unclosed(
        df,
        now,
        freq,
        time_col="datetime",
        calendar_path=TRADE_CALENDAR,
    )


def _bars_for_day(
    contract: str,
    trade_day: pd.Timestamp,
    klt: str,
    now: pd.Timestamp,
    cache: dict[tuple[str, str, str], pd.DataFrame],
) -> pd.DataFrame:
    day = pd.Timestamp(trade_day).normalize()
    key = (contract, day.strftime("%Y%m%d"), klt)
    fail_key = (*key, "fail")
    if fail_key in FAIL_CACHE:
        raise RuntimeError("cached fail")
    if key in cache:
        return cache[key]

    errors: list[str] = []
    lmt = 20 if klt == "30" else 280
    try:
        df = _fetch_em_kline(contract, klt, day, lmt=lmt)
        df = df[df["datetime"].dt.normalize() == day]
        df = _filter_closed_bars(df, now, klt)
        if not df.empty:
            print(f"[K线] {contract} {day.date()} klt={klt} <- eastmoney rows={len(df)}")
            cache[key] = df
            return df
        errors.append("eastmoney: empty after day filter")
    except Exception as e:
        errors.append(f"eastmoney: {e}")
        print(f"[失败] {contract} {day.date()} klt={klt} eastmoney {type(e).__name__}: {e}")

    # 30m 走 1m 精确匹配；不在此缓存 fail，避免阻断同合约 1m fallback
    if klt != "1":
        raise RuntimeError("; ".join(errors))

    if day >= now.normalize() - pd.Timedelta(days=6):
        try:
            df = _fetch_sina_minute(contract)
            df = df[df["datetime"].dt.normalize() == day]
            df = _filter_closed_bars(df, now, klt)
            if not df.empty:
                print(f"[K线] {contract} {day.date()} <- sina(1m) rows={len(df)}")
                cache[key] = df
                return df
            errors.append("sina: empty after day filter")
        except Exception as e:
            errors.append(f"sina: {e}")
            print(f"[失败] {contract} {day.date()} sina {type(e).__name__}: {e}")

    FAIL_CACHE.add(fail_key)
    raise RuntimeError("; ".join(errors))


def _quote_close(
    contract: str,
    run_dt: pd.Timestamp,
    hm: str,
    now: pd.Timestamp,
    cache: dict[tuple[str, str, str], pd.DataFrame],
) -> Optional[float]:
    trade_day = pd.Timestamp(run_dt).normalize()
    if hm != "14:57":
        try:
            bars = _bars_for_day(contract, trade_day, "30", now, cache)
            price = _price_at_minute(bars, run_dt)
            if price is not None:
                return price
        except Exception as e:
            print(f"[30m缺价] {contract} {_format_dt(run_dt)}: {e}")
    try:
        bars = _bars_for_day(contract, trade_day, "1", now, cache)
        return _price_at_minute(bars, run_dt)
    except Exception as e:
        print(f"[缺价] {contract} {_format_dt(run_dt)}: {e}")
        return None


def _price_at_minute(bars: pd.DataFrame, dt: pd.Timestamp) -> Optional[float]:
    if bars is None or bars.empty:
        return None
    target = pd.Timestamp(dt).floor("min")
    # 新浪 1m 午后首根常为 13:01，运行点标签仍为 13:00
    candidates = [target]
    if target.strftime("%H:%M") == "13:00":
        candidates.append(target + pd.Timedelta(minutes=1))
    for t in candidates:
        hit = bars[bars["datetime"].dt.floor("min") == t]
        if not hit.empty:
            return float(hit.iloc[-1]["close"])
    return None


def _runpoint_is_due(run_dt: pd.Timestamp, now: pd.Timestamp) -> bool:
    return pd.Timestamp(run_dt) <= pd.Timestamp(now)


def _due_runpoints_for_day(trade_day: pd.Timestamp, now: pd.Timestamp) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []
    day = pd.Timestamp(trade_day).normalize()
    for hm in RUNPOINT_TIMES:
        h, m = map(int, hm.split(":"))
        run_dt = day + pd.Timedelta(hours=h, minutes=m)
        if _runpoint_is_due(run_dt, now):
            out.append(run_dt)
    return out


def _missing_runpoints_on_day(
    trade_day: pd.Timestamp,
    existing: pd.DataFrame,
    now: pd.Timestamp,
) -> list[pd.Timestamp]:
    due = _due_runpoints_for_day(trade_day, now)
    if not due:
        return []
    existing_times = set(pd.to_datetime(existing["datetime"], errors="coerce"))
    return [dt for dt in due if dt not in existing_times]


def _build_day_rows(
    trade_day: pd.Timestamp,
    front: str,
    nxt: str,
    now: pd.Timestamp,
    bar_cache: dict[tuple[str, str, str], pd.DataFrame],
) -> list[dict]:
    rows: list[dict] = []
    for hm in RUNPOINT_TIMES:
        h, m = map(int, hm.split(":"))
        run_dt = trade_day.normalize() + pd.Timedelta(hours=h, minutes=m)
        if not _runpoint_is_due(run_dt, now):
            continue
        fp = _quote_close(front, run_dt, hm, now, bar_cache)
        np = _quote_close(nxt, run_dt, hm, now, bar_cache)
        if fp is None or np is None:
            print(f"[跳过] {_format_dt(run_dt)} front={fp} next={np}")
            continue
        rows.append(
            {
                "datetime": run_dt,
                "front_contract": front,
                "front_close": fp,
                "next_contract": nxt,
                "next_close": np,
            }
        )
    return rows


def _extend_new_days_for_intraday_today(
    new_days: list[pd.Timestamp],
    tail_day: pd.Timestamp,
    now: pd.Timestamp,
    trade_dates: list[pd.Timestamp],
) -> list[pd.Timestamp]:
    """15:00 前日线 CSV 无当日时，仍按工作日增量同步 IF 运行点。"""
    today = now.normalize()
    if today <= tail_day or today in new_days:
        return new_days
    if today in trade_dates:
        new_days.append(today)
        return sorted(set(new_days))
    if today.weekday() < 5:
        new_days.append(today)
        print(f"[IF日历] {today.date()} 不在日线 CSV，按工作日纳入同步")
    return sorted(set(new_days))


def sync_if_runpoints(
    since: str | None = None,
    *,
    output_path: Path | None = None,
    trade_calendar: Path | None = None,
) -> dict:
    # 方法说明
    # 输入参数：since（仅处理该日及之后），output_path，trade_calendar
    # 输出参数：dict（ok、added_rows、message、tail、front、next）
    # 用途：按 tail 增量补运行点；同日缺档补洞；未收盘时刻跳过；15:00 前无日线历时仍同步当日
    global TRADE_CALENDAR, OUTPUT_PATH
    out_path = Path(output_path) if output_path else OUTPUT_PATH
    cal_path = Path(trade_calendar) if trade_calendar else TRADE_CALENDAR
    prev_cal, prev_out = TRADE_CALENDAR, OUTPUT_PATH
    TRADE_CALENDAR, OUTPUT_PATH = cal_path, out_path
    try:
        now = pd.Timestamp.now()
        FAIL_CACHE.clear()
        trade_dates = _load_trade_dates()
        existing = _read_existing(out_path)

        if existing.empty:
            return {"ok": False, "added_rows": 0, "message": "现有 CSV 为空，需保留历史基线后再增量更新"}

        tail_dt = pd.Timestamp(existing["datetime"].max())
        tail_day = tail_dt.normalize()
        seed_row = existing.iloc[-1]
        seed_front = str(seed_row["front_contract"])
        seed_next = str(seed_row["next_contract"])

        new_days = [d for d in trade_dates if d > tail_day and d <= now.normalize()]
        new_days = _extend_new_days_for_intraday_today(new_days, tail_day, now, trade_dates)
        if since:
            since_ts = pd.Timestamp(since).normalize()
            new_days = [d for d in new_days if d >= since_ts]

        missing_on_tail = _missing_runpoints_on_day(tail_day, existing, now)
        if missing_on_tail:
            if tail_day not in new_days:
                new_days.insert(0, tail_day)
            print(
                f"[IF补洞] {tail_day.date()} 缺 {len(missing_on_tail)} 个运行点: "
                f"{', '.join(pd.Timestamp(x).strftime('%H:%M') for x in missing_on_tail)}"
            )
        elif not new_days and tail_dt.date() == now.normalize().date():
            new_days = [tail_day]
        elif not new_days:
            return {
                "ok": True,
                "added_rows": 0,
                "message": f"无新交易日（tail={tail_dt}）",
                "tail": str(tail_dt),
            }

        print(f"[IF增量] tail={tail_dt} 待补交易日={len(new_days)} front={seed_front} next={seed_next}")

        minute_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
        new_rows: list[dict] = []
        rolling_front, rolling_next = seed_front, seed_next
        existing_times = set(pd.to_datetime(existing["datetime"], errors="coerce"))

        for day in new_days:
            rolling_front, rolling_next = _resolve_front_next(day, trade_dates, rolling_front, rolling_next)
            front, nxt = rolling_front, rolling_next
            print(f"[IF日] {day.date()} front={front} next={nxt}")

            day_rows = _build_day_rows(day, front, nxt, now, minute_cache)
            for row in day_rows:
                run_ts = pd.Timestamp(row["datetime"])
                if run_ts in existing_times:
                    continue
                new_rows.append(row)

        if not new_rows:
            print("[IF完成] 无新增运行点")
            return {"ok": True, "added_rows": 0, "message": "无新增运行点", "tail": str(tail_dt)}

        append_df = pd.DataFrame(new_rows).sort_values("datetime").reset_index(drop=True)
        merged = pd.concat([existing, append_df], ignore_index=True)
        merged = merged.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
        merged["datetime"] = merged["datetime"].map(_format_dt)

        out_buf = StringIO()
        merged.to_csv(out_buf, index=False, encoding="utf-8-sig")
        out_path.write_text(out_buf.getvalue(), encoding="utf-8-sig")
        tail_str = append_df["datetime"].iloc[-1]
        print(f"[IF写入] {out_path.name} +{len(append_df)} 行 tail={tail_str}")
        return {
            "ok": True,
            "added_rows": len(append_df),
            "message": "ok",
            "tail": str(tail_str),
            "front": rolling_front,
            "next": rolling_next,
        }
    finally:
        TRADE_CALENDAR, OUTPUT_PATH = prev_cal, prev_out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="增量更新 IF_front_next_runpoints.csv")
    parser.add_argument(
        "--since",
        help="仅处理该日期及之后的交易日（YYYY-MM-DD，默认 tail 下一日）",
    )
    args = parser.parse_args()

    result = sync_if_runpoints(args.since)
    if not result.get("ok"):
        print(f"[错误] {result.get('message')}")
        sys.exit(1)
    if result.get("added_rows", 0) == 0 and "无新" in str(result.get("message", "")):
        pass


if __name__ == "__main__":
    main()
