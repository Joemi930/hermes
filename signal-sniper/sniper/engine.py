"""Cerveau du bot : transforme un Signal en ordres MT5, suit les trades, applique les garde-fous."""
import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import levels
from .executor import Executor, MT5Error
from .models import Signal
from .notifier import Notifier
from .parser import Incoming, SignalParser

log = logging.getLogger(__name__)
SIDE_FR = {"buy": "Achat", "sell": "Vente"}


@dataclass
class Trade:
    id: int  # id du message Telegram du signal
    code: str  # V75, V10...
    symbol: str  # nom MT5
    side: str
    signal_lot: float  # lot de Festo, sert à convertir ses $ en distance de prix
    tickets: list[int] = field(default_factory=list)
    sl_price: float | None = None
    sl_usd: float | None = None
    tps: list[dict] = field(default_factory=list)  # [{"price":..,"usd":..}]
    stages: dict[str, int] = field(default_factory=dict)  # palier TP atteint par position
    created: float = field(default_factory=time.time)
    awaiting_sl_until: float | None = None
    had_position: bool = False
    closed: bool = False


class Engine:
    def __init__(self, cfg, executor: Executor | None, notifier: Notifier, parser: SignalParser):
        self.cfg = cfg
        self.ex = executor
        self.notify = notifier.send
        self.parser = parser
        self.paused = False
        self.trades: list[Trade] = []
        self._day: str | None = None
        self._day_start_equity: float | None = None
        self._loss_alerted = False
        self._state_file = os.path.join(cfg.data_dir, "trades.json")
        self._handled: dict[int, str] = {}  # id message -> kind
        self._load()

    # --- persistance ---------------------------------------------------------------------------
    def _load(self):
        if os.path.exists(self._state_file):
            with open(self._state_file) as f:
                self.trades = [Trade(**t) for t in json.load(f)]

    def _save(self):
        keep = [t for t in self.trades if not t.closed or time.time() - t.created < 86400]
        self.trades = keep
        with open(self._state_file, "w") as f:
            json.dump([asdict(t) for t in keep], f, indent=1)

    def _open_trades(self) -> list[Trade]:
        return [t for t in self.trades if not t.closed]

    def trades_summary(self) -> str:
        return "\n".join(
            f"- {SIDE_FR[t.side]} {t.code} (signal {t.id}, il y a {int((time.time() - t.created) / 60)} min)"
            for t in self._open_trades())

    async def _x(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    # --- entrée : un message du canal ----------------------------------------------------------
    async def on_channel_message(self, msg: Incoming) -> None:
        if msg.edited and self._handled.get(msg.id) not in (None, "info", "ignore"):
            await self.notify(f"✏️ Festo a modifié un signal déjà traité :\n{msg.text[:300]}\n(vérifie manuellement)")
            return
        t0 = time.perf_counter()
        try:
            sig = await self.parser.parse(msg, self.trades_summary())
        except Exception as e:
            log.exception("Analyse échouée")
            await self.notify(f"❗ Analyse impossible d'un message :\n{msg.text[:300]}\nErreur : {e}")
            return
        self.parser.remember(msg, sig)
        self._handled[msg.id] = sig.kind
        log.info("msg %s analysé en %.0f ms via %s : %s", msg.id, (time.perf_counter() - t0) * 1000,
                 sig.source, sig.model_dump_json())

        if sig.kind == "ignore":
            return
        if sig.kind == "info":
            if sig.summary:
                await self.notify(f"ℹ️ Festo : {sig.summary}")
            return
        if sig.confidence < self.cfg.min_confidence:
            await self.notify(f"⚠️ Signal pas clair, NON exécuté (confiance {sig.confidence:.0%}) :\n"
                              f"{msg.text[:300]}\n→ compris : {sig.summary}")
            return
        age = (datetime.now(timezone.utc) - msg.date).total_seconds()
        if age > self.cfg.max_signal_age_s and sig.kind == "open":
            await self.notify(f"⏰ Signal trop ancien ({int(age)} s), non exécuté : {sig.summary}")
            return
        if self.cfg.mode == "notify" or self.ex is None:
            await self.notify(f"📣 SIGNAL : {sig.summary}\n\n{msg.text[:400]}")
            return
        if self.paused and sig.kind == "open":
            await self.notify(f"⏸️ Bot en pause, signal non exécuté : {sig.summary}")
            return
        try:
            await {"open": self._open, "update": self._update, "close": self._close,
                   "cancel": self._cancel}[sig.kind](sig, msg)
        except MT5Error as e:
            await self.notify(f"❌ MT5 : {e}\nSignal : {sig.summary}")
        except Exception as e:
            log.exception("Exécution échouée")
            await self.notify(f"❌ Erreur inattendue : {e}\nSignal : {sig.summary}")
        finally:
            self._save()

    # --- helpers -------------------------------------------------------------------------------
    def _target_trades(self, code: str | None) -> list[Trade]:
        """Trades visés par une mise à jour : ceux du symbole cité, sinon le dernier trade récent."""
        live = self._open_trades()
        if code:
            return [t for t in live if t.code == code]
        horizon = time.time() - self.cfg.context_minutes * 60
        recent = [t for t in live if t.created >= horizon]
        return recent[-1:] if recent else []

    def _belongs(self, t: Trade, item: dict) -> bool:
        return item.get("identifier", item["ticket"]) in t.tickets or item["ticket"] in t.tickets \
            or (item.get("comment") or "").startswith(f"snp{t.id}")

    def _levels_for(self, t: Trade, spec, entry: float) -> tuple[float | None, list[float]]:
        sl = t.sl_price
        if sl is None and t.sl_usd:
            sl = levels.sl_level(entry, t.side, levels.usd_to_distance(
                t.sl_usd, t.signal_lot, spec.tick_value, spec.tick_size))
        tps = []
        for tp in t.tps:
            if tp.get("price") is not None:
                tps.append(tp["price"])
            elif tp.get("usd") is not None:
                tps.append(levels.tp_level(entry, t.side, levels.usd_to_distance(
                    tp["usd"], t.signal_lot, spec.tick_value, spec.tick_size)))
        return sl, tps

    # --- actions -------------------------------------------------------------------------------
    async def _open(self, sig: Signal, msg: Incoming):
        if not sig.symbol or not sig.orders:
            await self.notify(f"⚠️ Signal incomplet (symbole ou ordre manquant) : {sig.summary}")
            return
        name = await self._x(self.ex.resolve_symbol, sig.symbol)
        spec = await self._x(self.ex.spec, name)
        side = sig.orders[0].side
        all_market = all(o.type == "market" for o in sig.orders)

        # "Déclenché pour ceux qui n'ont pas pris, buy now" : ne pas doubler une position déjà prise.
        recent = [t for t in self._open_trades()
                  if t.code == sig.symbol and t.side == side and time.time() - t.created < 6 * 3600]
        if all_market and recent:
            positions = await self._x(self.ex.positions)
            if any(self._belongs(t, p) for t in recent for p in positions):
                await self.notify(f"👌 Déjà en position sur {sig.symbol}, « {sig.summary} » ignoré (doublon).")
                return
            orders = await self._x(self.ex.orders)
            for t in recent:
                for o in orders:
                    if self._belongs(t, o):
                        await self._x(self.ex.cancel_order, o)
                t.closed = True

        if len(self._open_trades()) >= self.cfg.max_open_trades:
            await self.notify(f"🛑 Limite de {self.cfg.max_open_trades} trades ouverts atteinte, signal ignoré.")
            return

        first_lot = sig.orders[0].lot or self.cfg.default_lot or spec.vmin
        trade = Trade(id=msg.id, code=sig.symbol, symbol=name, side=side, signal_lot=first_lot,
                      sl_price=sig.sl_price, sl_usd=sig.sl_usd, tps=[tp.model_dump() for tp in sig.tps])
        no_sl = sig.sl_price is None and sig.sl_usd is None
        bid, ask = await self._x(self.ex.tick, name)
        lines = []
        for o in sig.orders:
            signal_lot = o.lot or self.cfg.default_lot or spec.vmin
            volume = self.ex.normalize_volume(spec, signal_lot * self.cfg.lot_multiplier)
            market_price = ask if side == "buy" else bid
            kind = o.type
            # Limit déjà franchie par le prix : on entre au marché (prix égal ou meilleur).
            if kind == "limit" and ((side == "buy" and ask <= o.price) or (side == "sell" and bid >= o.price)):
                kind = "market"
            entry = market_price if kind == "market" else o.price
            sl, tps = self._levels_for(trade, spec, entry)
            if no_sl:
                sl = levels.sl_level(entry, side, levels.usd_to_distance(
                    self.cfg.emergency_sl_usd, volume, spec.tick_value, spec.tick_size))
            tp = levels.broker_tp(tps, self.cfg.tp_mode)
            comment = f"snp{msg.id}"
            if kind == "market":
                ticket, price = await self._x(self.ex.open_market, spec, side, volume, sl, tp, comment)
                lines.append(f"{SIDE_FR[side]} {name} {volume} au marché @ {price}")
            else:
                ticket, price = await self._x(self.ex.place_pending, spec, side, kind, o.price, volume, sl, tp, comment)
                lines.append(f"{SIDE_FR[side]} {kind.upper()} {name} {volume} @ {o.price}")
            trade.tickets.append(ticket)
            lines[-1] += f" | SL {sl:.{spec.digits}f}" + (f" | TP {tp:.{spec.digits}f}" if tp else "")
        if no_sl:
            trade.awaiting_sl_until = time.time() + self.cfg.sl_wait_seconds
            lines.append(f"(pas de SL dans le signal → SL de secours {self.cfg.emergency_sl_usd}$ en attendant)")
        self.trades.append(trade)
        prefix = "🧪 [SIMULATION] " if self.cfg.mode == "dry_run" else "✅ "
        await self.notify(prefix + "\n".join(lines))

    async def _update(self, sig: Signal, msg: Incoming):
        targets = self._target_trades(sig.symbol)
        if not targets:
            await self.notify(f"⚠️ Mise à jour SL/TP reçue mais aucun trade à modifier : {sig.summary}")
            return
        positions, orders = await self._x(self.ex.positions), await self._x(self.ex.orders)
        for t in targets:
            if sig.sl_price is not None or sig.sl_usd is not None:
                t.sl_price, t.sl_usd, t.awaiting_sl_until = sig.sl_price, sig.sl_usd, None
            if sig.tps:
                t.tps = [tp.model_dump() for tp in sig.tps]
            spec = await self._x(self.ex.spec, t.symbol)
            done = []
            for p in positions:
                if self._belongs(t, p):
                    sl, tps = self._levels_for(t, spec, p["price_open"])
                    await self._x(self.ex.modify_position, spec, p, sl, levels.broker_tp(tps, self.cfg.tp_mode))
                    done.append(f"position {p['ticket']}: SL {sl} TP {levels.broker_tp(tps, self.cfg.tp_mode)}")
            for o in orders:
                if self._belongs(t, o):
                    sl, tps = self._levels_for(t, spec, o["price_open"])
                    await self._x(self.ex.modify_order, spec, o, sl, levels.broker_tp(tps, self.cfg.tp_mode))
                    done.append(f"ordre {o['ticket']}: SL {sl} TP {levels.broker_tp(tps, self.cfg.tp_mode)}")
            await self.notify(f"🔧 {t.code} mis à jour ({sig.summary})\n" + ("\n".join(done) or "(rien d'ouvert)"))

    async def _close(self, sig: Signal, msg: Incoming):
        targets = self._open_trades() if sig.close_all else self._target_trades(sig.symbol)
        if not targets:
            await self.notify(f"⚠️ Ordre de fermeture reçu mais rien à fermer : {sig.summary}")
            return
        positions, orders = await self._x(self.ex.positions), await self._x(self.ex.orders)
        for t in targets:
            spec = await self._x(self.ex.spec, t.symbol)
            for p in positions:
                if self._belongs(t, p):
                    await self._x(self.ex.close_position, spec, p)
            for o in orders:
                if self._belongs(t, o):
                    await self._x(self.ex.cancel_order, o)
        await self.notify(f"🚪 Fermeture demandée par Festo : {', '.join(t.code for t in targets)}")

    async def _cancel(self, sig: Signal, msg: Incoming):
        targets = self._target_trades(sig.symbol)
        orders = await self._x(self.ex.orders)
        n = 0
        for t in targets:
            for o in orders:
                if self._belongs(t, o):
                    await self._x(self.ex.cancel_order, o)
                    n += 1
        await self.notify(f"🗑️ {n} ordre(s) en attente annulé(s) ({sig.summary})")

    # --- surveillance continue -----------------------------------------------------------------
    async def monitor_loop(self):
        while True:
            try:
                await self._monitor_once()
            except Exception:
                log.exception("Erreur de surveillance")
                await asyncio.sleep(5)
            await asyncio.sleep(1)

    async def _monitor_once(self):
        await self._check_daily_loss()
        live = self._open_trades()
        if not live or self.cfg.mode == "dry_run":
            return
        positions, orders = await self._x(self.ex.positions), await self._x(self.ex.orders)
        changed = False
        for t in live:
            mine = [p for p in positions if self._belongs(t, p)]
            pending = [o for o in orders if self._belongs(t, o)]
            if mine and not t.had_position:
                t.had_position, changed = True, True
                await self.notify(f"🎯 Ordre {t.code} déclenché ({len(mine)} position(s)).")
            if not mine and not pending and time.time() - t.created > 15:
                t.closed, changed = True, True
                if t.had_position:
                    profit = sum([await self._x(self.ex.realized_profit, tk) for tk in t.tickets])
                    await self.notify(f"{'💰' if profit >= 0 else '🔻'} Trade {t.code} clôturé : {profit:+.2f} $")
                continue
            if t.awaiting_sl_until and time.time() > t.awaiting_sl_until:
                t.awaiting_sl_until, changed = None, True
                await self.notify(f"⚠️ Aucun SL reçu pour {t.code} : le SL de secours "
                                  f"({self.cfg.emergency_sl_usd}$) reste en place.")
            if mine and t.tps and self.cfg.tp_mode in ("final_be", "partial"):
                changed |= await self._manage_tps(t, mine)
        if changed:
            self._save()

    async def _manage_tps(self, t: Trade, mine: list[dict]) -> bool:
        spec = await self._x(self.ex.spec, t.symbol)
        changed = False
        for p in mine:
            _, tps = self._levels_for(t, spec, p["price_open"])
            stage = levels.stage_reached(t.side, p["price_current"], tps)
            key = str(p["identifier"])
            if stage <= t.stages.get(key, 0):
                continue
            t.stages[key], changed = stage, True
            if self.cfg.tp_mode == "partial":
                vol = levels.partial_volume(p["volume"], stage, len(tps), spec.vstep, spec.vmin)
                if vol:
                    await self._x(self.ex.close_position, spec, p, vol)
            better = (p["sl"] or 0) < p["price_open"] if t.side == "buy" else (p["sl"] or 1e18) > p["price_open"]
            if stage >= 1 and better:
                await self._x(self.ex.modify_position, spec, p, p["price_open"], None)
            await self.notify(f"🏁 {t.code} TP{stage} atteint → SL au prix d'entrée"
                              + (" + coupe partielle" if self.cfg.tp_mode == "partial" else ""))
        return changed

    async def _check_daily_loss(self):
        if not self.ex or self.cfg.max_daily_loss_usd <= 0 or self.cfg.mode != "trade":
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        equity = await self._x(self.ex.equity)
        if today != self._day:
            self._day, self._day_start_equity, self._loss_alerted = today, equity, False
        loss = self._day_start_equity - equity
        if loss >= self.cfg.max_daily_loss_usd and not self._loss_alerted:
            self.paused, self._loss_alerted = True, True
            await self.notify(f"🛑 Perte du jour {loss:.2f}$ ≥ limite {self.cfg.max_daily_loss_usd}$ : "
                              "bot mis en PAUSE (envoie /resume pour relancer).")

    # --- commandes (dans tes « Messages enregistrés » Telegram) ---------------------------------
    async def on_command(self, text: str) -> str:
        cmd = text.strip().lower()
        if cmd == "/pause":
            self.paused = True
            return "⏸️ Pause : les nouveaux signaux seront seulement notifiés."
        if cmd == "/resume":
            self.paused = False
            return "▶️ Reprise : exécution automatique active."
        if cmd == "/status":
            eq = f"{await self._x(self.ex.equity):.2f}$" if self.ex else "n/a"
            return (f"Mode {self.cfg.mode} | {'PAUSE' if self.paused else 'actif'} | équité {eq}\n"
                    f"Trades ouverts :\n{self.trades_summary() or '(aucun)'}")
        if cmd == "/closeall" and self.ex:
            await self._close(Signal(kind="close", close_all=True, summary="/closeall manuel"), None)
            self._save()
            return "🚪 Tout fermé."
        return "Commandes : /status /pause /resume /closeall"
