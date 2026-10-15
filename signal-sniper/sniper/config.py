"""Configuration chargée depuis les variables d'environnement (.env)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _float(name: str, default: float) -> float:
    v = _str(name)
    return float(v) if v else default


def _int(name: str, default: int) -> int:
    v = _str(name)
    return int(v) if v else default


def _bool(name: str, default: bool) -> bool:
    v = _str(name).lower()
    return default if not v else v in ("1", "true", "yes", "oui", "on")


@dataclass(frozen=True)
class Config:
    # Telegram (compte utilisateur, pas un bot)
    tg_api_id: int
    tg_api_hash: str
    tg_session: str
    tg_channel: str
    # Claude
    anthropic_model: str
    anthropic_effort: str
    anthropic_fallbacks: bool
    # MT5 (pont mt5linux)
    mt5_host: str
    mt5_port: int
    mt5_login: int | None
    mt5_password: str
    mt5_server: str
    # Mode : trade | dry_run | notify
    mode: str
    # Gestion du risque
    lot_multiplier: float
    default_lot: float
    max_lot: float
    max_open_trades: int
    max_daily_loss_usd: float
    emergency_sl_usd: float
    sl_wait_seconds: int
    tp_mode: str  # final_be | partial | final | first
    deviation_points: int
    max_signal_age_s: int
    context_minutes: int
    min_confidence: float
    magic: int
    # Notifications
    notify_telegram_saved: bool
    callmebot_phone: str
    callmebot_apikey: str
    notify_webhook_url: str
    data_dir: str


def load_config() -> Config:
    data_dir = _str("DATA_DIR", "./data")
    os.makedirs(data_dir, exist_ok=True)
    login = _str("MT5_LOGIN")
    mode = _str("MODE", "trade").lower()
    if mode not in ("trade", "dry_run", "notify"):
        raise ValueError(f"MODE invalide: {mode} (trade | dry_run | notify)")
    tp_mode = _str("TP_MODE", "final_be").lower()
    if tp_mode not in ("final_be", "partial", "final", "first"):
        raise ValueError(f"TP_MODE invalide: {tp_mode}")
    return Config(
        tg_api_id=_int("TG_API_ID", 0),
        tg_api_hash=_str("TG_API_HASH"),
        tg_session=os.path.join(data_dir, _str("TG_SESSION", "sniper")),
        tg_channel=_str("TG_CHANNEL"),
        anthropic_model=_str("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        anthropic_effort=_str("ANTHROPIC_EFFORT", "low"),
        anthropic_fallbacks=_bool("ANTHROPIC_FALLBACKS", True),
        mt5_host=_str("MT5_HOST", "localhost"),
        mt5_port=_int("MT5_PORT", 8001),
        mt5_login=int(login) if login else None,
        mt5_password=_str("MT5_PASSWORD"),
        mt5_server=_str("MT5_SERVER"),
        mode=mode,
        lot_multiplier=_float("LOT_MULTIPLIER", 1.0),
        default_lot=_float("DEFAULT_LOT", 0.0),
        max_lot=_float("MAX_LOT", 5.0),
        max_open_trades=_int("MAX_OPEN_TRADES", 5),
        max_daily_loss_usd=_float("MAX_DAILY_LOSS_USD", 0.0),
        emergency_sl_usd=_float("EMERGENCY_SL_USD", 10.0),
        sl_wait_seconds=_int("SL_WAIT_SECONDS", 120),
        tp_mode=tp_mode,
        deviation_points=_int("DEVIATION_POINTS", 500),
        max_signal_age_s=_int("MAX_SIGNAL_AGE_S", 180),
        context_minutes=_int("CONTEXT_MINUTES", 30),
        min_confidence=_float("MIN_CONFIDENCE", 0.7),
        magic=_int("MAGIC", 930_001),
        notify_telegram_saved=_bool("NOTIFY_TELEGRAM_SAVED", True),
        callmebot_phone=_str("CALLMEBOT_PHONE"),
        callmebot_apikey=_str("CALLMEBOT_APIKEY"),
        notify_webhook_url=_str("NOTIFY_WEBHOOK_URL"),
        data_dir=data_dir,
    )
