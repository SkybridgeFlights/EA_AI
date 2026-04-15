# src/mt5_symbol_info.py
from __future__ import annotations
from dataclasses import dataclass
import MetaTrader5 as mt5


@dataclass
class SymbolSpec:
    name: str
    point: float
    digits: int
    tick_size: float
    tick_value: float
    vol_min: float
    vol_step: float
    vol_max: float
    contract_size: float


def get_symbol_spec(symbol: str) -> SymbolSpec:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    info = mt5.symbol_info(symbol)
    if info is None:
        mt5.shutdown()
        raise RuntimeError(f"symbol_info() is None for {symbol}")

    if not info.visible:
        mt5.symbol_select(symbol, True)

    spec = SymbolSpec(
        name=symbol,
        point=float(info.point),
        digits=int(info.digits),
        tick_size=float(info.trade_tick_size),
        tick_value=float(info.trade_tick_value),
        vol_min=float(info.volume_min),
        vol_step=float(info.volume_step),
        vol_max=float(info.volume_max),
        contract_size=float(info.trade_contract_size),
    )

    mt5.shutdown()
    return spec