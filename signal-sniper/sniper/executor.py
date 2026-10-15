"""Passage d'ordres sur MetaTrader 5 via le pont mt5linux (MT5 tourne sous Wine dans Docker).

Toutes les méthodes sont synchrones (appels RPyC) : le moteur les appelle via asyncio.to_thread.
"""
import logging
import threading
import time
from dataclasses import dataclass

from .symbols import mt5_base_name

log = logging.getLogger(__name__)

OK_RETCODES = {10008, 10009, 10010}  # PLACED, DONE, DONE_PARTIAL


class MT5Error(Exception):
    pass


@dataclass
class SymbolSpec:
    name: str
    digits: int
    point: float
    tick_value: float
    tick_size: float
    vmin: float
    vmax: float
    vstep: float
    filling_mode: int


class Executor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dry_run = cfg.mode == "dry_run"
        self._lock = threading.RLock()
        self._mt5 = None
        self._names: list[str] | None = None
        self._resolved: dict[str, str] = {}
        self._fake_ticket = int(time.time())

    # --- connexion -----------------------------------------------------------------------------
    def connect(self):
        from mt5linux import MetaTrader5

        mt5 = MetaTrader5(host=self.cfg.mt5_host, port=self.cfg.mt5_port)
        kwargs = {}
        if self.cfg.mt5_login:
            kwargs = {"login": self.cfg.mt5_login, "password": self.cfg.mt5_password, "server": self.cfg.mt5_server}
        if not mt5.initialize(**kwargs):
            raise MT5Error(f"Connexion MT5 impossible : {mt5.last_error()}")
        self._mt5 = mt5
        info = mt5.account_info()
        log.info("MT5 connecté : compte %s, serveur %s, solde %s", info.login, info.server, info.balance)

    def _call(self, fn):
        with self._lock:
            try:
                if self._mt5 is None:
                    self.connect()
                return fn(self._mt5)
            except (EOFError, ConnectionError, OSError) as e:
                log.warning("Pont MT5 perdu (%s), reconnexion…", e)
                self._mt5 = None
                self.connect()
                return fn(self._mt5)

    # --- symboles ------------------------------------------------------------------------------
    def resolve_symbol(self, code: str) -> str:
        if code in self._resolved:
            return self._resolved[code]
        base = mt5_base_name(code).lower()

        def _resolve(mt5):
            if self._names is None:
                self._names = [s.name for s in mt5.symbols_get()]
            exact = [n for n in self._names if n.lower() == base]
            cands = exact or [n for n in self._names if n.lower().startswith(base)]
            if not cands:
                raise MT5Error(f"Symbole introuvable sur ton MT5 : {mt5_base_name(code)}")
            name = min(cands, key=len)  # gère un suffixe serveur comme ".0"
            mt5.symbol_select(name, True)
            return name

        name = self._call(_resolve)
        self._resolved[code] = name
        return name

    def spec(self, name: str) -> SymbolSpec:
        def _spec(mt5):
            i = mt5.symbol_info(name)
            if i is None:
                raise MT5Error(f"symbol_info({name}) vide : {mt5.last_error()}")
            return SymbolSpec(name, i.digits, i.point, i.trade_tick_value, i.trade_tick_size,
                              i.volume_min, i.volume_max, i.volume_step, i.filling_mode)
        return self._call(_spec)

    def tick(self, name: str) -> tuple[float, float]:
        def _tick(mt5):
            t = mt5.symbol_info_tick(name)
            return t.bid, t.ask
        return self._call(_tick)

    def normalize_volume(self, spec: SymbolSpec, lot: float) -> float:
        lot = min(lot, self.cfg.max_lot, spec.vmax)
        steps = int(round(lot / spec.vstep, 6))
        vol = max(steps * spec.vstep, spec.vmin)
        return round(vol, 8)

    @staticmethod
    def normalize_price(spec: SymbolSpec, price: float | None) -> float | None:
        return None if price is None else round(price, spec.digits)

    def _filling(self, mt5, spec: SymbolSpec) -> int:
        if spec.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        if spec.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    # --- envoi ---------------------------------------------------------------------------------
    def _send(self, build_request) -> tuple[int, float]:
        """Renvoie (ticket, prix). build_request(mt5) construit la requête."""
        def _go(mt5):
            req = build_request(mt5)
            if self.dry_run:
                self._fake_ticket += 1
                log.info("[DRY RUN] order_send %s", req)
                return self._fake_ticket, req.get("price", 0.0)
            res = mt5.order_send(req)
            if res is None:
                raise MT5Error(f"order_send a échoué : {mt5.last_error()}")
            if res.retcode not in OK_RETCODES:
                raise MT5Error(f"Ordre refusé ({res.retcode}) : {res.comment}")
            return res.order, res.price or req.get("price", 0.0)
        return self._call(_go)

    def open_market(self, spec, side, volume, sl, tp, comment) -> tuple[int, float]:
        def build(mt5):
            bid, ask = self.tick(spec.name)
            req = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": spec.name, "volume": float(volume),
                "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
                "price": ask if side == "buy" else bid, "deviation": self.cfg.deviation_points,
                "magic": self.cfg.magic, "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(mt5, spec),
            }
            if sl:
                req["sl"] = self.normalize_price(spec, sl)
            if tp:
                req["tp"] = self.normalize_price(spec, tp)
            return req
        return self._send(build)

    def place_pending(self, spec, side, kind, price, volume, sl, tp, comment) -> tuple[int, float]:
        def build(mt5):
            types = {
                ("buy", "limit"): mt5.ORDER_TYPE_BUY_LIMIT, ("sell", "limit"): mt5.ORDER_TYPE_SELL_LIMIT,
                ("buy", "stop"): mt5.ORDER_TYPE_BUY_STOP, ("sell", "stop"): mt5.ORDER_TYPE_SELL_STOP,
            }
            req = {
                "action": mt5.TRADE_ACTION_PENDING, "symbol": spec.name, "volume": float(volume),
                "type": types[(side, kind)], "price": self.normalize_price(spec, price),
                "magic": self.cfg.magic, "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }
            if sl:
                req["sl"] = self.normalize_price(spec, sl)
            if tp:
                req["tp"] = self.normalize_price(spec, tp)
            return req
        return self._send(build)

    def modify_position(self, spec, pos: dict, sl: float | None, tp: float | None):
        def build(mt5):
            return {
                "action": mt5.TRADE_ACTION_SLTP, "symbol": spec.name, "position": pos["ticket"],
                "sl": self.normalize_price(spec, sl) if sl is not None else pos["sl"],
                "tp": self.normalize_price(spec, tp) if tp is not None else pos["tp"],
                "magic": self.cfg.magic,
            }
        return self._send(build)

    def modify_order(self, spec, order: dict, sl: float | None, tp: float | None):
        def build(mt5):
            return {
                "action": mt5.TRADE_ACTION_MODIFY, "order": order["ticket"], "symbol": spec.name,
                "price": order["price_open"],
                "sl": self.normalize_price(spec, sl) if sl is not None else order["sl"],
                "tp": self.normalize_price(spec, tp) if tp is not None else order["tp"],
                "type_time": mt5.ORDER_TIME_GTC,
            }
        return self._send(build)

    def close_position(self, spec, pos: dict, volume: float | None = None):
        def build(mt5):
            bid, ask = self.tick(spec.name)
            is_buy = pos["type"] == mt5.POSITION_TYPE_BUY
            return {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": spec.name, "position": pos["ticket"],
                "volume": float(volume or pos["volume"]),
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "price": bid if is_buy else ask, "deviation": self.cfg.deviation_points,
                "magic": self.cfg.magic, "comment": "sniper close", "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(mt5, spec),
            }
        return self._send(build)

    def cancel_order(self, order: dict):
        return self._send(lambda mt5: {"action": mt5.TRADE_ACTION_REMOVE, "order": order["ticket"]})

    # --- lecture -------------------------------------------------------------------------------
    def positions(self) -> list[dict]:
        def _pos(mt5):
            out = []
            for p in mt5.positions_get() or ():
                if p.magic != self.cfg.magic:
                    continue
                out.append({"ticket": p.ticket, "identifier": p.identifier, "symbol": p.symbol,
                            "side": "buy" if p.type == mt5.POSITION_TYPE_BUY else "sell", "type": p.type,
                            "volume": p.volume, "price_open": p.price_open, "price_current": p.price_current,
                            "sl": p.sl, "tp": p.tp, "profit": p.profit, "comment": p.comment})
            return out
        return self._call(_pos)

    def orders(self) -> list[dict]:
        def _ord(mt5):
            return [{"ticket": o.ticket, "symbol": o.symbol, "price_open": o.price_open, "sl": o.sl,
                     "tp": o.tp, "volume": o.volume_current, "comment": o.comment}
                    for o in (mt5.orders_get() or ()) if o.magic == self.cfg.magic]
        return self._call(_ord)

    def equity(self) -> float:
        return self._call(lambda mt5: mt5.account_info().equity)

    def realized_profit(self, position_id: int) -> float:
        def _p(mt5):
            deals = mt5.history_deals_get(position=position_id) or ()
            return sum(d.profit + d.commission + d.swap for d in deals)
        return self._call(_p)
