"""回测报告落盘模块（CSV / JSON / 文本）。

输入参数：
  - output_dir、rows（dict 列表）、filename
输出参数：
  - Path（写入文件路径）；write_bundle 返回各文件名 -> Path 映射
用途：
  - 将 trades、run_log、equity、state_snapshots 等写入 outputs/backtest/

使用说明：
  - ReportWriter(output_dir).write_bundle(bundle) 一次性写出全套报告
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from engine.display_format import (
    EQUITY_HEADER_MAP,
    STATE_HEADER_MAP,
    format_action,
    format_equity_row,
    format_event,
    format_number,
    format_reason,
    format_side,
    format_state_row,
    format_trade_row,
    translate_headers,
)


class ReportWriter:
    # 模块说明
    # 输入参数：output_dir（回测输出目录）
    # 输出参数：ReportWriter 实例
    # 用途：统一写出 trades/run_log/equity/state 等验收文件
    TRADE_HEADER_MAP = {
        'datetime': '交易时间',
        'action': '动作',
        'side': '方向',
        'contract': '合约',
        'lots': '手数',
        'price': '成交价',
        'signal_price': '信号价',
        'wait_bars': '等待bar数',
        'commission': '手续费',
        'pnl': '盈亏金额',
        'pnl_ratio': '盈亏比例',
        'total_pnl': '移仓两段盈亏',
        'reason': '原因',
        'trigger_detail': '触发条件',
        'position_type': '持仓类型',
        'price_mode': '价格模式',
        'available_cash': '可用资金',
        'margin_used': '占用保证金',
        'total_value': '总权益',
        'old_contract': '旧合约',
        'new_contract': '新合约',
        'old_price': '旧合约价',
        'new_price': '新合约价',
    }

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        if not rows:
            return [], []
        fieldnames: list[str] = []
        seen = set()
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        normalized = [{k: row.get(k) for k in fieldnames} for row in rows]
        return normalized, fieldnames

    # write_csv 方法说明
    # 输入参数：filename、rows；可选 header_map、row_formatter
    # 输出参数：Path，写出文件路径
    # 用途：通用 CSV 写出（表头可中文化）
    def write_csv(self, filename: str, rows: Iterable[dict[str, Any]], *, header_map: dict[str, str] | None = None, row_formatter=None) -> Path:
        rows = list(rows)
        path = self.output_dir / filename
        if not rows:
            path.write_text('', encoding='utf-8')
            return path
        normalized, fieldnames = self._normalize_rows(rows)
        if row_formatter:
            normalized = [row_formatter(row) for row in normalized]
        if header_map:
            zh_fieldnames = translate_headers(fieldnames, header_map)
            out_rows = [{header_map.get(k, k): row.get(k) for k in fieldnames} for row in normalized]
            with path.open('w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=zh_fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(out_rows)
        else:
            with path.open('w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(normalized)
        return path

    # write_trade_csv 方法说明
    # 输入参数：filename、rows（成交记录）
    # 输出参数：Path
    # 用途：写出 trades.csv（含中文表头与格式化）
    def write_trade_csv(self, filename: str, rows: Iterable[dict[str, Any]]) -> Path:
        rows = list(rows)
        path = self.output_dir / filename
        if not rows:
            path.write_text('', encoding='utf-8')
            return path
        normalized, fieldnames = self._normalize_rows(rows)
        formatted_rows = [format_trade_row(row) for row in normalized]
        zh_fieldnames = translate_headers(fieldnames, self.TRADE_HEADER_MAP)
        translated_rows = []
        for row in formatted_rows:
            translated = {}
            for k, v in row.items():
                translated[self.TRADE_HEADER_MAP.get(k, k)] = v
            translated_rows.append(translated)
        with path.open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=zh_fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(translated_rows)
        return path

    # write_run_log 方法说明
    # 输入参数：filename、rows（运行日志）
    # 输出参数：Path
    # 用途：写出 run_log.csv
    def write_run_log(self, filename: str, rows: Iterable[dict[str, Any]]) -> Path:
        path = self.output_dir / filename
        rows = list(rows)
        lines: list[str] = []
        current_day = None
        for row in rows:
            dt = row.get('datetime') or row.get('dt') or ''
            day = str(dt)[:10] if dt else ''
            if day and day != current_day:
                current_day = day
                lines.append('')
                lines.append(f'==================== {current_day} ====================')

            kind = row.get('kind')
            if kind == 'range':
                rp = row.get('range_pct')
                rp_s = f'{float(rp) * 100:.2f}%' if rp is not None else rp
                lines.append(
                    f"{dt} - INFO  - 【区间】上沿={format_number(row.get('range_high'))} 下沿={format_number(row.get('range_low'))} "
                    f"上沿日={row.get('range_high_dt')} 下沿日={row.get('range_low_dt')} 幅度={rp_s}"
                )
                lines.append(f"{dt} - INFO  - 【位置】相对中枢={row.get('relative_center', '中枢外')} 相对区间={row.get('range_position')} 在区间内={row.get('range_state')}")
                continue
            if kind == 'center':
                if row.get('has_center'):
                    lines.append(f"{dt} - INFO  - 【中枢】存在中枢")
                    lines.append(f"{dt} - INFO  -   ZG={row.get('ZG')} ZD={row.get('ZD')} 扩展次数={row.get('extend_cnt')} 起止={row.get('start_dt')}→{row.get('end_dt')}")
                    lines.append(f"{dt} - INFO  -   中枢高低点日期: high_dt={row.get('high_dt')} low_dt={row.get('low_dt')}")
                else:
                    lines.append(f"{dt} - INFO  - 【中枢】当前无中枢，打印最近三笔（含临时笔）")
                continue
            if kind == 'stroke':
                dir_text = '上' if row.get('dir') == 'up' else '下'
                confirmed_text = '确认' if row.get('confirmed') else '临时'
                lines.append(f"{dt} - INFO  -   笔{row.get('idx')} {dir_text} {row.get('start_dt')}→{row.get('end_dt')} {confirmed_text}")
                continue

            event = format_event(row.get('event') or '')
            action_raw = row.get('action') or ''
            action = format_action(action_raw) if action_raw in ('open', 'close') else format_event(action_raw)
            reason = format_reason(row.get('reason') or '')
            signal_price = format_number(row.get('signal_price'))
            trade_price = format_number(row.get('trade_price'))
            wait_bars = row.get('wait_bars')
            if action_raw == 'run_point_state':
                lines.append(row.get('state_line') or '')
                continue
            if action_raw == 'after_market_close':
                lines.append(
                    f"{dt} - INFO  - [收盘] 原因={reason} 持仓合约={row.get('position_contract')} 持仓手数={row.get('position_amount')} "
                    f"入场价={format_number(row.get('entry_price'))} 锚定价={format_number(row.get('logical_anchor_entry'))} 收盘估价={format_number(row.get('mark_price'))} "
                    f"浮动盈亏={format_number(row.get('floating_pnl'))} 总权益={format_number(row.get('total_value'))} 可用资金={format_number(row.get('available_cash'))} "
                    f"占用保证金={format_number(row.get('margin_used'))} 移仓累计盈亏={format_number(row.get('roll_carry_points'))} 持仓原因={format_reason(row.get('position_reason'))}"
                )
                continue
            if action_raw in ('roll_failed', 'roll_contract'):
                if action_raw == 'roll_contract' and row.get('reason') == 'roll check':
                    continue
                lines.append(f"{dt} - INFO  - [{event}] 动作={action} 原因={reason} 信号价={signal_price} 成交价={trade_price} 等待bar={wait_bars}")
                continue
            if action_raw == 'run_point':
                continue
            lines.append(f"{dt} - INFO  - [{event}] 动作={action} 原因={reason} 信号价={signal_price} 成交价={trade_price} 等待bar={wait_bars}")

            if row.get('top_divergence_count') is not None or row.get('bottom_divergence_count') is not None:
                if row.get('top_divergence_count') or row.get('bottom_divergence_count'):
                    lines.append(
                        f"{dt} - INFO  -   背离: 顶计数={row.get('top_divergence_count')} 底计数={row.get('bottom_divergence_count')}"
                    )
            if row.get('day_lock'):
                dl = row.get('day_lock') or {}
                if bool(dl.get('ban_long')) or bool(dl.get('ban_short')):
                    lines.append(
                        f"{dt} - INFO  -   当日锁: 禁多={'是' if dl.get('ban_long') else '否'} 禁空={'是' if dl.get('ban_short') else '否'}"
                    )
            if row.get('switch_pending') is not None or row.get('switch_stage') is not None:
                if row.get('switch_pending') or row.get('switch_stage') not in (None, '', 'done'):
                    lines.append(
                        f"{dt} - INFO  -   移仓: 待处理={row.get('switch_pending')} 阶段={row.get('switch_stage')}"
                    )

        if not lines:
            path.write_text('', encoding='utf-8')
        else:
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def write_text(self, filename: str, lines: Iterable[str]) -> Path:
        path = self.output_dir / filename
        text = '\n'.join([str(x) for x in lines])
        if text and not text.endswith('\n'):
            text += '\n'
        path.write_text(text, encoding='utf-8')
        return path

    def write_json(self, filename: str, payload: Any) -> Path:
        path = self.output_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        return path

    # write_bundle 方法说明
    # 输入参数：bundle（trades/run_log/equity/state_snapshots/summary 等键）
    # 输出参数：dict[str, Path]，各输出文件路径
    # 用途：回测结束一次性落盘全套报告
    def write_bundle(self, bundle: dict[str, Any]) -> dict[str, Path]:
        """统一输出回测核心文件。"""
        out: dict[str, Path] = {}
        out['run_log'] = self.write_run_log('run_log.log', bundle.get('run_log', []))
        out['trades'] = self.write_trade_csv('trades.csv', bundle.get('trades', []))
        out['equity'] = self.write_csv('equity.csv', bundle.get('equity', []), header_map=EQUITY_HEADER_MAP, row_formatter=format_equity_row)
        out['state_snapshots'] = self.write_csv('state_snapshots.csv', bundle.get('state_snapshots', []), header_map=STATE_HEADER_MAP, row_formatter=format_state_row)
        out['summary'] = self.write_json('summary.json', bundle.get('summary', {}))
        return out
