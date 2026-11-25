from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    # 数据结构说明
    # 输入参数：contract（合约代码），side（方向），amount（数量），entry_price（入场价），position_type（持仓类型）
    # 输出参数：Position 实例
    # 中文映射：
    #   - contract -> 合约代码
    #   - side -> 持仓方向
    #   - amount -> 持仓数量
    #   - entry_price -> 入场价
    #   - position_type -> 持仓类型
    # 用途：描述当前持仓信息
    contract: Optional[str] = None
    side: Optional[str] = None
    amount: int = 0
    entry_price: Optional[float] = None
    position_type: Optional[str] = None


class Portfolio:
    # 模块说明
    # 输入参数：initial_cash（初始资金）
    # 输出参数：Portfolio 实例
    # 中文映射：
    #   - initial_cash -> 初始资金
    #   - cash -> 现金
    #   - available_cash -> 可用资金
    #   - margin_used -> 已用保证金
    #   - floating_pnl -> 浮动盈亏
    #   - total_value -> 总权益
    # 用途：管理账户资金、持仓与盈亏
    CONTRACT_MULTIPLIER = 300.0
    MARGIN_RATIO = 0.15
    COMMISSION_RATE = 0.000023

    def __init__(self, initial_cash: float = 10_000_000.0):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.available_cash = float(initial_cash)
        self.margin_used = 0.0
        self.floating_pnl = 0.0
        self.total_value = float(initial_cash)
        self.position = Position()

    # _commission 方法说明
    # 输入参数：price（价格），amount（数量）
    # 输出参数：float（手续费）
    # 中文映射：
    #   - price -> 价格
    #   - amount -> 数量
    #   - commission -> 手续费
    # 用途：按固定费率计算手续费
    @staticmethod
    def _commission(price: float, amount: int) -> float:
        return float(price) * int(amount) * Portfolio.CONTRACT_MULTIPLIER * Portfolio.COMMISSION_RATE

    @staticmethod
    def _margin(price: float, amount: int) -> float:
        return abs(float(price) * int(amount) * Portfolio.CONTRACT_MULTIPLIER) * Portfolio.MARGIN_RATIO

    @staticmethod
    def _cash_per_lot(price: float) -> float:
        contract_value = float(price) * Portfolio.CONTRACT_MULTIPLIER
        return contract_value * (Portfolio.MARGIN_RATIO + Portfolio.COMMISSION_RATE)

    # open_position 方法说明
    # 输入参数：contract（合约），side（方向），amount（数量），price（价格），position_type（持仓类型）
    # 输出参数：dict（开仓结果）
    # 中文映射：
    #   - opened -> 是否开仓成功
    #   - commission -> 手续费
    #   - margin -> 保证金
    #   - cash -> 现金
    #   - available_cash -> 可用资金
    #   - total_value -> 总权益
    # 用途：执行开仓并更新账户状态
    def open_position(self, contract: str, side: str, amount: int, price: float, position_type: Optional[str] = None) -> dict:
        trade_price = float(price)
        lots = int(amount)
        commission = self._commission(trade_price, lots)
        margin = self._margin(trade_price, lots)
        if margin + commission > self.available_cash:
            return {'action': 'open', 'opened': False, 'reason': 'insufficient_cash', 'commission': commission, 'price': trade_price, 'amount': lots}

        self.cash -= commission
        self.position = Position(contract=contract, side=side, amount=lots, entry_price=trade_price, position_type=position_type)
        self.margin_used += margin
        self.available_cash = self.cash - self.margin_used
        self.total_value = self.cash + self.floating_pnl
        return {
            'action': 'open',
            'opened': True,
            'contract': contract,
            'side': side,
            'amount': lots,
            'price': trade_price,
            'position_type': position_type,
            'commission': commission,
        }

    # close_position 方法说明
    # 输入参数：price（平仓价格）
    # 输出参数：dict（平仓结果）
    # 中文映射：
    #   - price -> 平仓价格
    #   - pnl -> 盈亏
    #   - commission -> 手续费
    #   - closed -> 是否平仓成功
    # 用途：执行平仓并更新账户状态
    def close_position(self, price: float) -> dict:
        if self.position.amount <= 0:
            return {'action': 'close', 'closed': False, 'commission': 0.0}
        trade_price = float(price)
        entry = self.position.entry_price or trade_price
        commission = self._commission(trade_price, self.position.amount)
        pnl = (trade_price - float(entry)) * self.position.amount * 300.0 if self.position.side == 'long' else (float(entry) - trade_price) * self.position.amount * 300.0
        self.cash += pnl
        self.cash -= commission
        self.position = Position()
        self.margin_used = 0.0
        self.available_cash = self.cash
        self.total_value = self.cash
        return {'action': 'close', 'closed': True, 'pnl': pnl, 'price': trade_price, 'commission': commission}

    # mark_to_market 方法说明
    # 输入参数：price（当前价格）
    # 输出参数：None
    # 中文映射：
    #   - price -> 当前价格
    #   - floating_pnl -> 浮动盈亏
    #   - total_value -> 总权益
    # 用途：按当前价格更新持仓浮动盈亏
    def mark_to_market(self, price: Optional[float]) -> None:
        if price is None or self.position.amount <= 0 or self.position.entry_price is None:
            self.floating_pnl = 0.0
            self.total_value = self.cash
            return
        if self.position.side == 'long':
            self.floating_pnl = (float(price) - float(self.position.entry_price)) * self.position.amount * 300.0
        else:
            self.floating_pnl = (float(self.position.entry_price) - float(price)) * self.position.amount * 300.0
        self.total_value = self.cash + self.floating_pnl
