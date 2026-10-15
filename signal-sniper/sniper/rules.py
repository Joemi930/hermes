"""Voie rapide (~1 ms) : reconnaît les signaux au format habituel de Festo sans appeler Claude.

Renvoie None dès qu'il y a un doute : le message part alors chez Claude, qui voit aussi le contexte.
"""
import re

from .models import Order, Signal, TakeProfit
from .symbols import find_symbol

NUM = r"(\d+(?:\.\d+)?)"
SIDES = {
    "buy": "buy", "achat": "buy", "achetez": "buy", "acheter": "buy",
    "sell": "sell", "vente": "sell", "vendez": "sell", "vendre": "sell",
}
ORDER_RE = re.compile(
    r"\b(buy|sell|achat|achetez|acheter|vente|vendez|vendre)\b"
    r"(?:\s+(limit|stop|now|maintenant|market))?"
    r"(?:\s*(?:at|à|@|:|;))*\s*" + NUM + "?"
)
LOT_RE = re.compile(r"\blots?\s*[:=]?\s*" + NUM)
SL_RE = re.compile(r"\bs\.?\s?l\s*(?:(?:à|a|at|:|=|@)\s*)?" + NUM + r"\s*(\$|usd)?")
TP_RE = re.compile(r"\btp\s*(?:(\d)(?=[\s:=]))?\s*[:=]?\s*(\+)?\s*" + NUM + r"\s*(\$|usd)?")

# Messages de résultat : on ignore sans dépenser d'appel IA.
IGNORE_RES = [
    re.compile(r"^\s*bilan"),
    re.compile(r"^\s*[+-]\s?\d+(?:\.\d+)?\s?\$"),
    re.compile(r"\b(?:sl|tp\s?\d?)(?:\s+(?:et|and|&)\s+tp\s?\d)*\s+hit\b"),
]
# Mots qui signalent un conseil, une prévision ou une condition : Claude doit trancher.
GUARD_RE = re.compile(r"prévision|prevision|conseil|observ|attend|risqu|peut[- ]?être|\bsi\b|analyse|\?")


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'")
    return re.sub(r"(\d),(\d)", r"\1.\2", t)


def _sl(t: str) -> tuple[float | None, float | None]:
    m = SL_RE.search(t)
    if not m:
        return None, None
    value = float(m.group(1))
    return (None, value) if m.group(2) else (value, None)


def _tps(t: str) -> list[TakeProfit]:
    tps = []
    for m in TP_RE.finditer(t):
        value = float(m.group(3))
        is_usd = bool(m.group(2) or m.group(4))
        tps.append(TakeProfit(usd=value) if is_usd else TakeProfit(price=value))
    return tps


def parse(text: str) -> Signal | None:
    t = normalize(text)
    if not t.strip():
        return None
    for rx in IGNORE_RES:
        if rx.search(t):
            return Signal(kind="ignore", summary="Résultat / bilan", confidence=1.0)
    if GUARD_RE.search(t):
        return None

    symbol = find_symbol(t)
    sl_price, sl_usd = _sl(t)
    tps = _tps(t)
    matches = list(ORDER_RE.finditer(t))

    if matches:
        if not symbol:
            return None  # le symbole est peut-être sur l'image précédente -> Claude + contexte
        global_lot = LOT_RE.search(t)
        orders = []
        for i, m in enumerate(matches):
            side = SIDES[m.group(1)]
            typ = m.group(2)
            price = float(m.group(3)) if m.group(3) else None
            end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
            lot_m = LOT_RE.search(t, m.start(), end) or global_lot
            lot = float(lot_m.group(1)) if lot_m else None
            if typ in ("limit", "stop"):
                if price is None:
                    return None
                orders.append(Order(side=side, type=typ, price=price, lot=lot))
            else:
                if price is not None:
                    return None  # "buy 4900" sans limit/stop : ambigu
                orders.append(Order(side=side, type="market", lot=lot))
        if len({o.side for o in orders}) > 1:
            return None
        return Signal(
            kind="open", symbol=symbol, orders=orders, sl_price=sl_price, sl_usd=sl_usd,
            tps=tps, confidence=0.95, summary=text.strip()[:200],
        )

    if sl_price is not None or sl_usd is not None or tps:
        return Signal(
            kind="update", symbol=symbol, sl_price=sl_price, sl_usd=sl_usd, tps=tps,
            confidence=0.95, summary=text.strip()[:200],
        )
    return None
