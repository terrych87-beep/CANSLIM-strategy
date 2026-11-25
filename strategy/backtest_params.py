# 模块说明
# 输入参数：表单 mapping、--strategy-param KEY=VALUE、SimpleNamespace config
# 输出参数：dict / 更新后的 config（StructCfg、MacdDivCfg、震荡阈值等）
# 中文映射：
#   - ALL_PARAM_FIELDS / PARAM_GROUPS -> 网页表单字段
#   - OSC_RULES_MODEL_LONG/SHORT -> 震荡多空分设模型
# 用途：统一策略参数默认值与 CLI/网页解析
# 使用说明：default_strategy_params()；parse_strategy_params_from_mapping()

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from typing import Any

from strategy.macd_divergence import MacdDivCfg
from strategy.structure_module import StructCfg

# 震荡开仓（trading_conditions）
OSC_TRADE_FIELDS: list[dict[str, Any]] = [
    {
        'name': 'OSC_RULES_MODEL_LONG',
        'label': '震荡多·规则模型',
        'hint': 'v1=ref_close；v2=ref_low',
        'type': 'str',
        'default': 'v1',
        'step': '',
    },
    {
        'name': 'OSC_RULES_MODEL_SHORT',
        'label': '震荡空·规则模型',
        'hint': 'v1=ref_close；v2=ref_high',
        'type': 'str',
        'default': 'v2',
        'step': '',
    },
    {
        'name': 'OSC_NEAR_REF_THRESHOLD_LONG',
        'label': '震荡多·贴近参考点阈值',
        'hint': '小数，0.009 表示 0.9%',
        'type': 'float',
        'default': 0.009,
        'step': '0.0001',
    },
    {
        'name': 'OSC_BREAK_REF_THRESHOLD_LONG',
        'label': '震荡多·突破参考点阈值',
        'hint': '小数，0.015 表示 1.5%',
        'type': 'float',
        'default': 0.015,
        'step': '0.0001',
    },
    {
        'name': 'OSC_NEAR_REF_THRESHOLD_SHORT',
        'label': '震荡空·贴近参考点阈值',
        'hint': '小数，0.005 表示 0.5%',
        'type': 'float',
        'default': 0.005,
        'step': '0.0001',
    },
    {
        'name': 'OSC_BREAK_REF_THRESHOLD_SHORT',
        'label': '震荡空·突破参考点阈值',
        'hint': '小数，0.01 表示 1%',
        'type': 'float',
        'default': 0.01,
        'step': '0.0001',
    },
    {
        'name': 'BREAKTHROUGH_WINDOW_BARS',
        'label': '震荡等待窗口（30分钟K根数）',
        'hint': '背离确认后最多等待多少根 bar 开仓',
        'type': 'int',
        'default': 18,
        'step': '1',
    },
]

# CLI / 扫参兼容：旧名同时覆盖多空阈值（不进网页表单）
LEGACY_OSC_FIELD_SPECS: dict[str, dict[str, Any]] = {
    'OSC_NEAR_REF_THRESHOLD': {
        'type': 'float',
        'default': 0.005,
        'targets': ('OSC_NEAR_REF_THRESHOLD_LONG', 'OSC_NEAR_REF_THRESHOLD_SHORT'),
    },
    'OSC_BREAK_REF_THRESHOLD': {
        'type': 'float',
        'default': 0.01,
        'targets': ('OSC_BREAK_REF_THRESHOLD_LONG', 'OSC_BREAK_REF_THRESHOLD_SHORT'),
    },
}

OSC_THRESHOLD_LONG_SHORT_NAMES = (
    'OSC_NEAR_REF_THRESHOLD_LONG',
    'OSC_BREAK_REF_THRESHOLD_LONG',
    'OSC_NEAR_REF_THRESHOLD_SHORT',
    'OSC_BREAK_REF_THRESHOLD_SHORT',
)

OSC_RULES_MODEL_DIRECTIONAL_NAMES = (
    'OSC_RULES_MODEL_LONG',
    'OSC_RULES_MODEL_SHORT',
)

# MACD / 背离（MacdDivCfg）
MACD_FIELDS: list[dict[str, Any]] = [
    {'name': 'MACD_FAST', 'label': 'MACD 快线', 'type': 'int', 'default': 12, 'step': '1'},
    {'name': 'MACD_SLOW', 'label': 'MACD 慢线', 'type': 'int', 'default': 26, 'step': '1'},
    {'name': 'MACD_SIGNAL', 'label': 'MACD 信号线', 'type': 'int', 'default': 9, 'step': '1'},
    {'name': 'MIN_BAND_LEN', 'label': 'MACD 波段最小长度', 'type': 'int', 'default': 3, 'step': '1'},
    {'name': 'MIN_PRICE_MOVE', 'label': 'MACD 波段最小价格变动', 'type': 'float', 'default': 0.0, 'step': '0.0001'},
    {
        'name': 'PREV_COMPARE_MIN_BAND_LEN',
        'label': '前序同向柱最小长度',
        'hint': '从后往前找前序同向波段，长度须 >= 该值（默认 4）',
        'type': 'int',
        'default': 4,
        'step': '1',
    },
]

# 中枢 / 结构（StructCfg）
STRUCT_FIELDS: list[dict[str, Any]] = [
    {'name': 'STRUCT_ATR_N', 'label': 'ATR 周期', 'type': 'int', 'default': 14, 'step': '1'},
    {'name': 'STRUCT_ATR_K', 'label': 'ZigZag ATR 倍数', 'type': 'float', 'default': 1.2, 'step': '0.1'},
    {'name': 'STRUCT_MIN_CONFIRM_GAP', 'label': '笔确认最小间隔', 'type': 'int', 'default': 1, 'step': '1'},
    {'name': 'STRUCT_MIN_STROKE_LEN', 'label': '最小笔长度', 'type': 'int', 'default': 2, 'step': '1'},
    {
        'name': 'STRUCT_FLOAT_EPS',
        'label': '浮点比较容差',
        'hint': '结构价比较用，默认 1e-8',
        'type': 'float',
        'default': 1e-8,
        'step': 'any',
    },
    {
        'name': 'STRUCT_CENTER_BREAK',
        'label': '中枢突破失效比例',
        'hint': '小数，0.02 表示 2%',
        'type': 'float',
        'default': 0.02,
        'step': '0.001',
    },
    {'name': 'STRUCT_WINDOW', 'label': '结构回溯窗口（交易日）', 'type': 'int', 'default': 320, 'step': '1'},
]

PARAM_GROUPS: list[dict[str, Any]] = [
    {'id': 'osc', 'title': '震荡开仓参数', 'fields': OSC_TRADE_FIELDS},
    {'id': 'macd', 'title': 'MACD / 背离参数', 'fields': MACD_FIELDS},
    {'id': 'struct', 'title': '中枢 / 结构参数', 'fields': STRUCT_FIELDS},
]

ALL_PARAM_FIELDS: list[dict[str, Any]] = OSC_TRADE_FIELDS + MACD_FIELDS + STRUCT_FIELDS


# default_strategy_params 方法说明
# 输入参数：无
# 输出参数：dict，全部可配置策略参数的默认值
# 用途：回测/扫参未指定参数时的基准
def default_strategy_params() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for dc in (StructCfg(), MacdDivCfg()):
        for f in fields(dc):
            out[f.name] = getattr(dc, f.name)
    for spec in OSC_TRADE_FIELDS:
        out[spec['name']] = spec['default']
    return out


def _cast_value(spec: dict[str, Any], raw: Any) -> Any:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return spec['default']
    if spec['type'] == 'str':
        return str(raw).strip()
    if spec['type'] == 'int':
        return int(float(raw))
    return float(raw)


def _apply_legacy_osc_aliases(out: dict[str, Any], data: dict[str, Any]) -> None:
    """OSC_NEAR/BREAK_REF_THRESHOLD 同时写入多空四套阈值（扫参兼容）。"""
    for legacy_name, spec in LEGACY_OSC_FIELD_SPECS.items():
        if legacy_name not in data or data[legacy_name] in (None, ''):
            continue
        try:
            val = _cast_value(spec, data[legacy_name])
        except (TypeError, ValueError):
            continue
        for target in spec['targets']:
            out[target] = val


def _normalize_rules_model(raw: Any, default: str) -> str:
    m = str(raw if raw is not None and str(raw).strip() else default).strip().lower()
    return 'v1' if m == 'v1' else 'v2'


def _apply_legacy_osc_rules_model(out: dict[str, Any], data: dict[str, Any]) -> None:
    if 'OSC_RULES_MODEL' not in data or data['OSC_RULES_MODEL'] in (None, ''):
        return
    m = _normalize_rules_model(data['OSC_RULES_MODEL'], 'v1')
    out['OSC_RULES_MODEL_LONG'] = m
    out['OSC_RULES_MODEL_SHORT'] = m


def _apply_directional_osc_rules_model(out: dict[str, Any], data: dict[str, Any]) -> None:
    by_name = {s['name']: s for s in OSC_TRADE_FIELDS}
    for name in OSC_RULES_MODEL_DIRECTIONAL_NAMES:
        if name not in data or data[name] in (None, ''):
            continue
        spec = by_name.get(name)
        default = str(spec['default']) if spec else 'v1'
        out[name] = _normalize_rules_model(data[name], default)


def _apply_directional_osc_overrides(out: dict[str, Any], data: dict[str, Any]) -> None:
    by_name = {s['name']: s for s in OSC_TRADE_FIELDS}
    for name in OSC_THRESHOLD_LONG_SHORT_NAMES:
        if name not in data or data[name] in (None, ''):
            continue
        spec = by_name.get(name)
        if not spec:
            continue
        try:
            out[name] = _cast_value(spec, data[name])
        except (TypeError, ValueError):
            pass


# parse_struct_params_from_mapping 方法说明
# 输入参数：data（仅 StructCfg 字段）
# 输出参数：dict，类型转换后的结构参数（含 StructCfg 全部字段默认值）
# 用途：结构可视化网页表单
def parse_struct_params_from_mapping(data: dict[str, Any]) -> dict[str, Any]:
    base = StructCfg()
    out: dict[str, Any] = {f.name: getattr(base, f.name) for f in fields(StructCfg)}
    by_name = {s['name']: s for s in STRUCT_FIELDS}
    for name, spec in by_name.items():
        if name in data and data[name] not in (None, ''):
            try:
                out[name] = _cast_value(spec, data[name])
            except (TypeError, ValueError):
                pass
    return out


# parse_strategy_params_from_mapping 方法说明
# 输入参数：data（字段名 -> 字符串值）
# 输出参数：dict，类型转换后的策略参数
# 用途：网页表单 / 映射表解析
def parse_strategy_params_from_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """从表单/CLI 键值解析，缺省用默认值。"""
    out = default_strategy_params()
    by_name = {s['name']: s for s in ALL_PARAM_FIELDS}
    for name, spec in by_name.items():
        if name in data and data[name] not in (None, ''):
            try:
                out[name] = _cast_value(spec, data[name])
            except (TypeError, ValueError):
                pass
    # 旧名覆盖多空 → 定向参数再覆盖单侧
    _apply_legacy_osc_rules_model(out, data)
    _apply_directional_osc_rules_model(out, data)
    _apply_legacy_osc_aliases(out, data)
    _apply_directional_osc_overrides(out, data)
    return out


# parse_strategy_param_strings 方法说明
# 输入参数：items（KEY=VALUE 字符串列表）
# 输出参数：dict，解析后的策略参数
# 用途：run_backtest.py --strategy-param 多次传参
def parse_strategy_param_strings(items: list[str]) -> dict[str, Any]:
    """解析 --strategy-param NAME=VALUE 列表。"""
    raw: dict[str, Any] = {}
    for item in items:
        s = str(item).strip()
        if not s or '=' not in s:
            continue
        k, v = s.split('=', 1)
        raw[k.strip()] = v.strip()
    return parse_strategy_params_from_mapping(raw)


# apply_strategy_params_to_config 方法说明
# 输入参数：config（SimpleNamespace 等）、params（参数字典）
# 输出参数：config（已 setattr 写入参数）
# 用途：将解析结果写入策略 config 对象
def apply_strategy_params_to_config(config: Any, params: dict[str, Any]) -> Any:
    for k, v in params.items():
        setattr(config, k, v)
    return config
