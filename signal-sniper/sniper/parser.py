"""Combine la voie rapide (règles) et Claude, avec la mémoire des derniers messages du canal."""
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from . import rules
from .llm import LLMParser
from .models import Signal


@dataclass
class Incoming:
    id: int
    date: datetime
    text: str
    photo: bytes | None
    edited: bool = False
    forwarded: bool = False


@dataclass
class _Ctx:
    at: float
    line: str
    symbol: str | None


class SignalParser:
    def __init__(self, cfg, llm: LLMParser):
        self.cfg = cfg
        self.llm = llm
        self.history: deque[_Ctx] = deque(maxlen=8)

    def _context(self) -> str:
        horizon = time.time() - self.cfg.context_minutes * 60
        return "\n".join(c.line for c in self.history if c.at >= horizon)

    async def parse(self, msg: Incoming, trades_summary: str) -> Signal:
        if not msg.photo and not msg.forwarded:
            sig = rules.parse(msg.text)
            if sig is not None:
                return sig
        if not msg.text.strip() and not msg.photo:
            return Signal(kind="ignore", summary="Message vide")
        return await self.llm.parse(msg.text, msg.photo, self._context(), trades_summary)

    def remember(self, msg: Incoming, sig: Signal) -> None:
        tag = "[image] " if msg.photo else ""
        tag += "[transféré] " if msg.forwarded else ""
        line = f"- {msg.date:%H:%M} {tag}{msg.text.strip()[:300]!r} -> {sig.kind}" + (
            f" symbole={sig.symbol}" if sig.symbol else "")
        self.history.append(_Ctx(at=time.time(), line=line, symbol=sig.symbol))
