#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# 模块说明
# 输入参数：project_root；可选 data_dir、env_file、start_date/end_date、force、fail_soft
# 输出参数：dict（ok、warnings、skipped、data_dir、error）
# 中文映射：
#   - fail_soft -> 失败时返回 warnings 不抛异常
#   - force -> 忽略 data_coverage 已覆盖检查
#   - 实时数据运行.main -> 实际抓取与合并入口
# 用途：回测前增量同步上证 CSV；由 run_backtest.py 在数据未覆盖或 --force-sync-data 时调用
# 使用说明：一般不直接运行；run_backtest 或 scripts 层 import sync_live_data

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path
from typing import Any


from engine.data_coverage import data_covers_backtest_range
from engine.data_portal import DataPortal


def _load_live_module(data_dir: Path):
    script = data_dir / '实时数据运行.py'
    if not script.exists():
        raise FileNotFoundError(f'未找到实时数据脚本: {script}')
    name = 'live_data_run_module'
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'无法加载: {script}')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sync_live_data(
    project_root: Path | str,
    *,
    data_dir: Path | str | None = None,
    env_file: Path | str | None = None,
    fail_soft: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    # 方法说明
    # 输入参数：project_root；data_dir；env_file（默认 data/.env）；区间与 force/fail_soft
    # 输出参数：dict（ok、warnings、skipped、data_dir）
    # 用途：已覆盖区间且 force=False 时跳过；否则动态加载 实时数据运行.py 并调用 main()
    root = Path(project_root).resolve()
    ddir = Path(data_dir).resolve() if data_dir else (root / 'data')
    env_path = Path(env_file).resolve() if env_file else (ddir / '.env')
    warnings: list[str] = []

    if not force and start_date and end_date:
        portal = DataPortal(str(ddir))
        covered, cover_msg = data_covers_backtest_range(portal, start_date, end_date)
        if covered:
            msg = f'[sync-data] 跳过：{cover_msg}'
            print(msg)
            return {'ok': True, 'warnings': [msg], 'skipped': True, 'data_dir': str(ddir)}

    if not env_path.exists():
        msg = f'[sync-data] 未找到 {env_path}，将尝试无 .env 运行（企业微信等可能不可用）'
        warnings.append(msg)
        print(msg)

    try:
        mod = _load_live_module(ddir)
        mod.BASE_DIR = root
        mod.DATA_DIR = ddir
        mod.MERGED_DIR = ddir
        mod.LIVE_DIR = ddir
        mod.FORMAL_ENV_PATH = env_path
        # 勿让 实时数据运行.main() 解析 run_backtest 的 sys.argv（传空列表走默认增量同步）
        mod.main([])
        return {'ok': True, 'warnings': warnings, 'data_dir': str(ddir)}
    except Exception as e:
        detail = traceback.format_exc(limit=12)
        msg = f'[sync-data] 同步失败: {e}'
        warnings.append(msg)
        print(msg)
        print(detail)
        if fail_soft:
            return {'ok': False, 'warnings': warnings, 'error': str(e), 'data_dir': str(ddir)}
        raise
