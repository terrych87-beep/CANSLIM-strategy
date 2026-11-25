from __future__ import annotations

from typing import Optional

from .portfolio import Portfolio


class Broker:
    # 模块说明
    # 输入参数：portfolio（组合账户）
    # 输出参数：Broker 实例
    # 中文映射：
    #   - portfolio -> 组合账户
    #   - Broker -> 撮合代理
    # 用途：提供统一的开平仓撮合入口
    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio

    # open_position 方法说明
    # 输入参数：contract（合约），side（方向），amount（数量），price（价格），position_type（持仓类型）
    # 输出参数：dict（开仓结果）
    # 中文映射：
    #   - contract -> 合约代码
    #   - side -> 持仓方向
    #   - amount -> 持仓数量
    #   - price -> 成交价格
    #   - position_type -> 持仓类型
    def open_position(self, contract: str, side: str, amount: int, price: float, position_type: Optional[str] = None) -> dict:
        return self.portfolio.open_position(contract, side, amount, price, position_type=position_type)

    def open_position_with_retry(
        self,
        contract: str,
        side: str,
        amount: int,
        price: float,
        position_type: Optional[str] = None,
    ) -> dict:
        """开仓失败且因资金不足时，逐手减 1 重试（贴近聚宽能开多少开多少）。"""
        requested = max(0, int(amount))
        lots = requested
        last: dict = {'action': 'open', 'opened': False, 'reason': 'insufficient_cash', 'amount': 0}
        while lots >= 1:
            last = self.portfolio.open_position(contract, side, lots, price, position_type=position_type)
            if last.get('opened'):
                out = dict(last)
                out['requested_amount'] = requested
                return out
            if last.get('reason') != 'insufficient_cash':
                return dict(last)
            lots -= 1
        out = dict(last)
        out['requested_amount'] = requested
        return out

    # close_position 方法说明
    # 输入参数：price（平仓价格）
    # 输出参数：dict（平仓结果）
    # 中文映射：
    #   - price -> 平仓价格
    #   - pnl -> 盈亏
    #   - closed -> 是否平仓成功
    def close_position(self, price: float) -> dict:
        return self.portfolio.close_position(price)

    # mark_to_market 方法说明
    # 输入参数：price（当前价格）
    # 输出参数：None
    # 中文映射：
    #   - price -> 当前价格
    def mark_to_market(self, price: Optional[float]) -> None:
        self.portfolio.mark_to_market(price)
