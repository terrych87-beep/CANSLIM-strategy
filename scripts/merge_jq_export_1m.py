#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：CLI --input（聚宽导出 CSV），--target（本地 1m CSV，默认 data/上证指数1分钟加入实时数据.csv）
# 输出参数：合并写入 target CSV（同交易时间去重，保留新数据）
# 中文映射：
#   - target -> 本地 1m 主文件
#   - 实时数据运行._merge_by_datetime -> 合并逻辑（动态加载）
# 用途：将聚宽 sparse 1m 补进本地主 CSV，供 14:57 精确取价
# 使用说明：
#   python scripts/merge_jq_export_1m.py --input data/jq_1m_sparse.csv

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _load_live_module(data_dir: Path):
    script = data_dir / "实时数据运行.py"
    spec = importlib.util.spec_from_file_location("live_data_run_module", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载: {script}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_data_run_module"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "data"
    parser = argparse.ArgumentParser(description="合并聚宽导出 1m 到本地 CSV")
    parser.add_argument("--input", required=True, help="聚宽导出的 CSV 路径")
    parser.add_argument(
        "--target",
        default=str(data_dir / "上证指数1分钟加入实时数据.csv"),
        help="本地 1m CSV",
    )
    args = parser.parse_args()

    mod = _load_live_module(data_dir)
    mod.BASE_DIR = root
    mod.DATA_DIR = data_dir
    mod.MERGED_DIR = data_dir
    mod.LIVE_DIR = data_dir

    inp = Path(args.input)
    tgt = Path(args.target)
    if not inp.is_absolute():
        inp = (root / inp).resolve()

    existing = mod._load_existing_file(tgt)
    jq_df = mod._load_existing_file(inp)
    if jq_df.empty:
        raise SystemExit(f"输入为空: {inp}")

    jq_df = jq_df.copy()
    if "data_source" not in jq_df.columns:
        jq_df["data_source"] = "jq_bt_1m"
    else:
        jq_df["data_source"] = jq_df["data_source"].fillna("jq_bt_1m")
    # 对齐本地口径：000001 -> 1
    if "证券代码" in jq_df.columns:
        jq_df["证券代码"] = jq_df["证券代码"].astype(str).str.replace(r"^000001$", "1", regex=True)
    if "证券名称" in jq_df.columns:
        jq_df["证券名称"] = jq_df["证券名称"].fillna("上证指数")

    merged = mod._merge_by_datetime(existing, jq_df, "1m")
    merged = mod._with_sina_columns(merged, jq_df)
    plan = mod._build_plan("1m")

    try:
        mod._write_outputs([plan], {"1m": merged})
        written = tgt
    except RuntimeError as err:
        alt = tgt.with_name(f"{tgt.stem}_jq_merged{tgt.suffix}")
        merged.to_csv(alt, index=False, encoding="utf-8-sig")
        written = alt
        print(f"[警告] 主文件被占用，已写入: {alt} ({err})")

    mod._verify_sparse_1m_gap(
        written,
        ["2024-01-05", "2024-11-05", "2025-07-10", "2026-06-22"],
    )
    print(f"[完成] merged rows={len(merged)} -> {written}")


if __name__ == "__main__":
    main()
