"""
CANSLIM 选股 CLI（与中枢策略隔离）。

示例:
  python -m screener.canslim.run_screen --codes 600519,000858,600000
  python -m screener.canslim.run_screen --pool --out outputs/canslim_screen.csv
  python -m screener.canslim.run_screen --pool --resume --out outputs/canslim_screen.csv
  python -m screener.canslim.run_screen --pool --limit 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from screener.canslim import config
from screener.canslim.data_sina import fetch_daily_kline, fetch_pool_hs300_csi1000
from screener.canslim.score import score_one


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A股 CANSLIM 选股（新浪 + 自建前复权）")
    p.add_argument("--codes", type=str, default="", help="逗号分隔六位代码")
    p.add_argument("--pool", action="store_true", help="使用沪深300∪中证1000")
    p.add_argument("--limit", type=int, default=0, help="最多处理 N 只（0=不限）")
    p.add_argument("--out", type=str, default="", help="输出 CSV 路径")
    p.add_argument("--no-cache", action="store_true", help="禁用本地缓存")
    p.add_argument("--min-x", type=int, default=0, help="结束时仅保留 X/4 >= 该值（仍写出全量中间文件）")
    p.add_argument(
        "--resume",
        action="store_true",
        help="跳过 --out CSV 中已有且无 error 的代码（断点续跑）",
    )
    p.add_argument(
        "--flush-every",
        type=int,
        default=10,
        help="每处理 N 只追加写盘一次（默认 10）",
    )
    args = p.parse_args(argv)

    use_cache = not args.no_cache
    rows_meta: list[dict] = []

    out = args.out.strip()
    if not out:
        out_dir = _ROOT / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = str(out_dir / "canslim_screen.csv")
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.pool:
        print("[pool] 拉取沪深300 ∪ 中证1000 ...", flush=True)
        pool_df = fetch_pool_hs300_csi1000(use_cache=use_cache)
        print(f"[pool] size={len(pool_df)}", flush=True)
        for r in pool_df.itertuples(index=False):
            rows_meta.append({"code": str(r.code).zfill(6), "name": r.name, "pool": r.pool})
    elif args.codes.strip():
        for c in args.codes.replace("，", ",").split(","):
            c = c.strip()
            if not c:
                continue
            digits = "".join(ch for ch in c if ch.isdigit())
            if digits:
                c = digits.zfill(6)
            rows_meta.append({"code": c, "name": "", "pool": "manual"})
    else:
        p.error("请指定 --codes 或 --pool")

    if args.limit and args.limit > 0:
        rows_meta = rows_meta[: args.limit]

    done_codes: set[str] = set()
    prior_rows: list[dict] = []
    if args.resume and out_path.exists():
        try:
            prev = pd.read_csv(out_path, dtype={"code": str})
            if "code" in prev.columns:
                prev["code"] = prev["code"].astype(str).str.zfill(6)
                if "error" in prev.columns:
                    ok = prev[prev["error"].isna() | (prev["error"].astype(str).str.len() == 0)]
                else:
                    ok = prev
                done_codes = set(ok["code"].tolist())
                prior_rows = prev.to_dict(orient="records")
                print(f"[resume] skip {len(done_codes)} codes from {out_path}", flush=True)
        except Exception as e:
            print(f"[resume] ignore existing file: {e}", flush=True)

    if done_codes:
        before = len(rows_meta)
        rows_meta = [m for m in rows_meta if str(m["code"]).zfill(6) not in done_codes]
        print(f"[resume] remaining {len(rows_meta)}/{before}", flush=True)

    print(f"[bench] {config.BENCHMARK_SYMBOL}", flush=True)
    bench = fetch_daily_kline(config.BENCHMARK_SYMBOL, use_cache=use_cache)
    m_preview = __import__("screener.canslim.factors.m_gate", fromlist=["score_m"]).score_m(bench)
    print(f"[M] {m_preview.get('gate')} | {m_preview.get('detail')}", flush=True)

    results: list[dict] = list(prior_rows)
    n = len(rows_meta)
    flush_every = max(1, int(args.flush_every))
    new_since_flush = 0

    for i, meta in enumerate(rows_meta, 1):
        code = str(meta["code"]).zfill(6)
        print(f"[{i}/{n}] {code} {meta.get('name','')} ...", flush=True)
        one = score_one(
            code,
            name=meta.get("name") or "",
            pool=meta.get("pool") or "",
            bench_kline=bench,
            use_cache=use_cache,
        )
        results.append(one)
        new_since_flush += 1
        if one.get("error"):
            print(f"  ERR {one['error']}", flush=True)
        else:
            print(
                f"  X/4={one.get('X_of_4')}/{one.get('known_of_4')} "
                f"C={one.get('C_pass')} A={one.get('A_pass')} "
                f"S={one.get('S_pass')} L={one.get('L_pass')} M={one.get('M_gate')}",
                flush=True,
            )

        if new_since_flush >= flush_every or i == n:
            _write_csv(results, out_path)
            print(f"  [flush] rows={len(results)} -> {out_path}", flush=True)
            new_since_flush = 0

    df = pd.DataFrame(results)
    if args.min_x and "X_of_4" in df.columns:
        filtered = df[df["X_of_4"].fillna(-1) >= args.min_x].reset_index(drop=True)
        filt_path = out_path.with_name(out_path.stem + f"_min{args.min_x}" + out_path.suffix)
        filtered.to_csv(filt_path, index=False, encoding="utf-8-sig")
        print(f"[filter] X/4>={args.min_x} rows={len(filtered)} -> {filt_path}", flush=True)

    _write_csv(results, out_path)
    print(f"[done] rows={len(results)} -> {out_path}", flush=True)
    return 0


def _write_csv(rows: list[dict], path: Path) -> None:
    """先写临时文件再替换，降低 OneDrive/占用导致的 PermissionError。"""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df = pd.DataFrame(rows)
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    try:
        tmp.replace(path)
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{pd.Timestamp.now().strftime('%H%M%S')}{path.suffix}")
        tmp.replace(alt)
        print(f"  [warn] {path.name} 被占用，改写到 {alt.name}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
