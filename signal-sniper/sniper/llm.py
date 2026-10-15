"""Voie IA : Claude lit le message (+ l'image du graphique) et renvoie un JSON strict."""
import base64
import json
import logging

from anthropic import AsyncAnthropic

from .models import Signal
from .symbols import canonical

log = logging.getLogger(__name__)

SYSTEM = """Tu analyses en temps réel les messages du canal Telegram de signaux de trading de Festo (broker Deriv, MetaTrader 5).
Ta sortie déclenche de VRAIS ordres : en cas de doute, choisis "info" ou baisse "confidence". N'invente jamais un prix ou un symbole.

Jargon :
- V = Volatility, C = Crash, B = Boom. Le nombre suit : V10, V25, V75, V100, C500, B1000. Un "s" collé = version (1s) : V50s -> "V50S".
- Codes de symbole attendus : "V75", "V50S", "C500", "B1000", ou forex/or : "XAUUSD", "USDJPY"... (null si inconnu).
- Sur les graphiques : "Volatility 75 Index", "VOLATILITY_10_INDEX", "Constant Volatility of 10%" = V10 ; "of 75%" = V75 ; "(1s)" = version S.
- "Sl" = stop loss. "Sl à 8.5$" = SL en dollars (sl_usd=8.5). "SL4900" = SL au prix (sl_price=4900). "Sl -1R" = résultat, pas un ordre.
- "TP 1 +15$" = take profit en dollars (usd=15). Les TP multiples sont des paliers d'UNE SEULE position (pas plusieurs ordres).
- "Lot 0.02" = taille de l'ordre de Festo. "buy now" / "Déclenché ... buy now" = achat au marché immédiat.
- "Buy limit at X" / "Sell limit" / "Buy stop" = ordre en attente au prix X. Plusieurs lignes "Buy limit" = plusieurs ordres.

Types de sortie (kind) :
- "open"   : nouvel ordre à passer. orders[] obligatoire (side, type market|limit|stop, price si limit/stop, lot si donné).
- "update" : ajout/modification de SL ou de TP sur le trade en cours (souvent envoyé juste après le signal).
- "close"  : fermer une position ("Coupons", "clôturez", "fermez", "vous pouvez clôturer"). close_all=true si tout fermer.
- "cancel" : annuler les ordres en attente ("annulez les buy limit").
- "info"   : annonce, avertissement news (IPC/CPI), prévision, conseil ("je vous conseillerais d'observer"), attente
             ("on attend la clôture de la bougie"), ou image qui annonce un signal à venir ("PLACEZ les Buy Limit suivant").
             Pour une image annonce, renseigne quand même "symbol" : le message suivant donnera souvent les prix sans le symbole.
- "ignore" : résultats, bilans, "+8$", "SL hit", "TP hit", messages transférés de résultats, bavardage.

Règles :
- Si le message ne contient pas de symbole, utilise le contexte récent (symbole du message/image précédent) uniquement si c'est clairement la suite.
- Prix sur l'image : ne lis un prix (ligne BUY LIMIT, étiquette) que si la légende demande de placer l'ordre et que l'étiquette est lisible ;
  sinon mets price=null et confidence <= 0.5.
- Une prévision conditionnelle ("BUY USDXXX SELL XXXUSD ... observer") n'est PAS un ordre : kind="info".
- summary : une phrase courte en français pour la notification (ex. "Achat V75 0.02 au marché, SL 8.5$").
- confidence : 0..1, ta certitude que l'action est exactement celle voulue par Festo."""

_NULLABLE_NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "symbol", "orders", "sl_price", "sl_usd", "tps", "close_all", "confidence", "summary"],
    "properties": {
        "kind": {"type": "string", "enum": ["open", "update", "close", "cancel", "info", "ignore"]},
        "symbol": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "orders": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["side", "type", "price", "lot"],
                "properties": {
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "type": {"type": "string", "enum": ["market", "limit", "stop"]},
                    "price": _NULLABLE_NUM,
                    "lot": _NULLABLE_NUM,
                },
            },
        },
        "sl_price": _NULLABLE_NUM,
        "sl_usd": _NULLABLE_NUM,
        "tps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["price", "usd"],
                "properties": {"price": _NULLABLE_NUM, "usd": _NULLABLE_NUM},
            },
        },
        "close_all": {"type": "boolean"},
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
    },
}


def _media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class LLMParser:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = AsyncAnthropic()  # lit ANTHROPIC_API_KEY

    async def parse(self, text: str, photo: bytes | None, context: str, trades: str) -> Signal:
        content: list[dict] = []
        if photo:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": _media_type(photo),
                           "data": base64.standard_b64encode(photo).decode()},
            })
        content.append({"type": "text", "text": (
            f"Contexte (messages récents du canal, du plus ancien au plus récent) :\n{context or '(aucun)'}\n\n"
            f"Trades ouverts par le bot :\n{trades or '(aucun)'}\n\n"
            f"NOUVEAU MESSAGE{' (avec image ci-dessus)' if photo else ''} :\n{text or '(pas de texte)'}"
        )})

        extra: dict = {}
        if self.cfg.anthropic_fallbacks:
            extra = {"extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
                     "extra_body": {"fallbacks": "default"}}
        resp = await self.client.messages.create(
            model=self.cfg.anthropic_model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.cfg.anthropic_effort,
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            **extra,
        )
        if resp.stop_reason != "end_turn":
            log.warning("Claude stop_reason=%s", resp.stop_reason)
            return Signal(kind="info", confidence=0.0, source="llm",
                          summary=f"Analyse IA impossible ({resp.stop_reason})")
        raw = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(raw)
        data["symbol"] = canonical(data.get("symbol"))
        return Signal(**data, source="llm")
