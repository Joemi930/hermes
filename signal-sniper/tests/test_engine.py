"""Scénarios réels du canal, rejoués contre un faux MT5."""
import asyncio
import dataclasses
from datetime import datetime, timezone

from sniper.config import load_config
from sniper.engine import Engine
from sniper.executor import Executor, SymbolSpec
from sniper.models import Order, Signal
from sniper.parser import Incoming, SignalParser


class FakeMT5Executor(Executor):
    """Imite Deriv MT5 : V10 (tick 0.001 / 0.001$), V75 (tick 0.01 / 0.01$)."""

    SPECS = {
        "Volatility 10 Index": SymbolSpec("Volatility 10 Index", 3, 0.001, 0.001, 0.001, 0.5, 100, 0.01, 1),
        "Volatility 75 Index": SymbolSpec("Volatility 75 Index", 2, 0.01, 0.01, 0.01, 0.001, 10, 0.001, 1),
    }

    def __init__(self, cfg):
        super().__init__(cfg)
        self.prices = {"Volatility 10 Index": 4930.0, "Volatility 75 Index": 40998.52}
        self.pos, self.ords, self.sent, self.n = [], [], [], 100

    def resolve_symbol(self, code):
        return {"V10": "Volatility 10 Index", "V75": "Volatility 75 Index"}[code]

    def spec(self, name):
        return self.SPECS[name]

    def tick(self, name):
        p = self.prices[name]
        return p, p

    def open_market(self, spec, side, volume, sl, tp, comment):
        self.n += 1
        self.pos.append({"ticket": self.n, "identifier": self.n, "symbol": spec.name, "side": side, "type": 0 if side == "buy" else 1,
                         "volume": volume, "price_open": self.prices[spec.name], "price_current": self.prices[spec.name],
                         "sl": sl, "tp": tp, "profit": 0, "comment": comment})
        self.sent.append(("market", side, volume, sl, tp))
        return self.n, self.prices[spec.name]

    def place_pending(self, spec, side, kind, price, volume, sl, tp, comment):
        self.n += 1
        self.ords.append({"ticket": self.n, "symbol": spec.name, "price_open": price, "sl": sl, "tp": tp,
                          "volume": volume, "comment": comment})
        self.sent.append((kind, side, price, volume, sl, tp))
        return self.n, price

    def modify_position(self, spec, pos, sl, tp):
        pos["sl"] = sl if sl is not None else pos["sl"]
        pos["tp"] = tp if tp is not None else pos["tp"]
        self.sent.append(("modify", pos["ticket"], pos["sl"], pos["tp"]))
        return pos["ticket"], 0

    def modify_order(self, spec, order, sl, tp):
        order["sl"], order["tp"] = sl, tp
        return order["ticket"], 0

    def close_position(self, spec, pos, volume=None):
        self.pos.remove(pos)
        return pos["ticket"], 0

    def cancel_order(self, order):
        self.ords.remove(order)
        return order["ticket"], 0

    def positions(self):
        return self.pos

    def orders(self):
        return self.ords

    def equity(self):
        return 1000.0

    def realized_profit(self, position_id):
        return 8.0


class FakeLLM:
    def __init__(self, sig):
        self.sig = sig

    async def parse(self, *a):
        return self.sig


def make(tmp_path, llm_sig=None):
    cfg = dataclasses.replace(load_config(), data_dir=str(tmp_path), mode="trade")
    ex = FakeMT5Executor(cfg)
    notes = []

    class N:
        async def send(self, text):
            notes.append(text)

    eng = Engine(cfg, ex, N(), SignalParser(cfg, FakeLLM(llm_sig)))
    return eng, ex, notes


def msg(i, text, photo=None):
    return Incoming(id=i, date=datetime.now(timezone.utc), text=text, photo=photo)


def test_sell_then_late_sl(tmp_path):
    eng, ex, notes = make(tmp_path)
    asyncio.run(eng.on_channel_message(msg(1, "Sell V10 lot 2")))
    kind, side, vol, sl, tp = ex.sent[0]
    assert (kind, side, vol) == ("market", "sell", 2.0)
    assert round(sl, 3) == 4935.0  # SL de secours 10$ / (2 lots * 1$/pt) = 5 points au-dessus
    asyncio.run(eng.on_channel_message(msg(2, "Sl 4942.54")))
    assert ex.pos[0]["sl"] == 4942.54
    assert eng.trades[0].awaiting_sl_until is None


def test_buy_limits_from_image_context_then_trigger_duplicate(tmp_path):
    # Claude a lu l'image (V75) : buy limit 40836.61, lot 0.02, SL 8.5$
    sig = Signal(kind="open", symbol="V75", orders=[Order(side="buy", type="limit", price=40836.61, lot=0.02)],
                 sl_usd=8.5, confidence=0.9, summary="Buy limit V75", source="llm")
    eng, ex, notes = make(tmp_path, sig)
    asyncio.run(eng.on_channel_message(msg(10, "Placez ça , avec le lot 0.02\nC’est le V75\nSl à 8.5$", photo=b"jpg")))
    kind, side, price, vol, sl, tp = ex.sent[0]
    assert (kind, price, vol) == ("limit", 40836.61, 0.02)
    assert round(sl, 2) == round(40836.61 - 425, 2)
    # Le prix n'a pas atteint la limite, Festo dit "buy now" : on annule la limite et on achète au marché
    asyncio.run(eng.on_channel_message(msg(11, "Déclenché pour ceux qui n’ont pas pris , buy now\nLot 0.02\nV75\nSl 8.5$")))
    assert not ex.ords and ex.sent[-1][0] == "market"
    # Rejoué une 2e fois : doublon -> ignoré
    n = len(ex.sent)
    asyncio.run(eng.on_channel_message(msg(12, "buy now V75 lot 0.02 sl 8.5$")))
    assert len(ex.sent) == n and "Déjà en position" in notes[-1]
    # TP en $ : TP1 +15$ = +750 points, dernier TP (+55$) posé chez le broker
    asyncio.run(eng.on_channel_message(msg(13, "TP 1 +15$\nTP 2 + 30$\nTP 3 +55$")))
    p = ex.pos[0]
    assert round(p["tp"], 2) == round(40998.52 + 2750, 2)
    # Le prix touche TP1 -> SL remonté au prix d'entrée
    p["price_current"] = 40998.52 + 760
    asyncio.run(eng._monitor_once())
    assert p["sl"] == p["price_open"] and "TP1" in notes[-1]
    # Position fermée par le broker -> notification du résultat
    ex.pos.clear()
    eng.trades[-1].created -= 60
    asyncio.run(eng._monitor_once())
    assert "+8.00 $" in notes[-1]


def test_results_and_low_confidence(tmp_path):
    sig = Signal(kind="open", symbol="V10", orders=[Order(side="buy", type="market")], confidence=0.4,
                 summary="?", source="llm")
    eng, ex, notes = make(tmp_path, sig)
    asyncio.run(eng.on_channel_message(msg(20, "Coupons SL hit")))
    assert not notes and not ex.sent
    asyncio.run(eng.on_channel_message(msg(21, "On attend un peu à la clôture de la bougie m15")))
    assert not ex.sent and "pas clair" in notes[-1]
