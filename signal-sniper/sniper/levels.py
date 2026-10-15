"""Calculs purs : conversion $ -> distance de prix, niveaux SL/TP, paliers de TP."""


def usd_to_distance(usd: float, lot: float, tick_value: float, tick_size: float) -> float:
    """Distance de prix qui représente `usd` dollars pour `lot` lots."""
    per_unit = lot * tick_value / tick_size  # $ gagnés/perdus pour 1.0 de mouvement de prix
    return usd / per_unit


def sl_level(entry: float, side: str, distance: float) -> float:
    return entry - distance if side == "buy" else entry + distance


def tp_level(entry: float, side: str, distance: float) -> float:
    return entry + distance if side == "buy" else entry - distance


def broker_tp(levels: list[float], tp_mode: str) -> float | None:
    """TP posé chez le broker : le premier en mode 'first', sinon le dernier."""
    if not levels:
        return None
    return levels[0] if tp_mode == "first" else levels[-1]


def stage_reached(side: str, price: float, levels: list[float]) -> int:
    """Nombre de paliers TP déjà touchés par le prix courant."""
    n = 0
    for lvl in levels:
        if (side == "buy" and price >= lvl) or (side == "sell" and price <= lvl):
            n += 1
        else:
            break
    return n


def partial_volume(volume: float, stage: int, n_levels: int, step: float, vmin: float) -> float:
    """Volume à couper au palier `stage` (1-indexé) pour répartir également sur les TP restants.

    Renvoie 0 si la coupe passerait sous le volume minimum ou au dernier palier (géré par le TP broker).
    """
    if stage >= n_levels:
        return 0.0
    remaining_levels = n_levels - stage + 1
    vol = volume / remaining_levels
    vol = int(vol / step) * step
    if vol < vmin or volume - vol < vmin:
        return 0.0
    return round(vol, 8)
