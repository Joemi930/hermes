"""Format unique d'un signal, qu'il vienne des règles ou de Claude."""
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["open", "update", "close", "cancel", "info", "ignore"]


class Order(BaseModel):
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop"]
    price: float | None = None  # obligatoire pour limit/stop
    lot: float | None = None  # lot de Festo (avant multiplicateur)


class TakeProfit(BaseModel):
    price: float | None = None  # niveau de prix
    usd: float | None = None  # ou gain en $ (calculé sur le lot de Festo)


class Signal(BaseModel):
    kind: Kind
    symbol: str | None = None  # code canonique : V75, V50S, C500, B1000, XAUUSD...
    orders: list[Order] = Field(default_factory=list)
    sl_price: float | None = None
    sl_usd: float | None = None
    tps: list[TakeProfit] = Field(default_factory=list)
    close_all: bool = False
    confidence: float = 1.0
    summary: str = ""
    source: Literal["rules", "llm"] = "rules"
