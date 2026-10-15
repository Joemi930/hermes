"""Traduction du jargon de Festo (V75, V50s, C500, B1000...) vers les symboles MT5 Deriv."""
import re

V_NUMS = {10, 15, 25, 30, 50, 75, 90, 100, 150, 200, 250, 300}
CB_NUMS = {150, 300, 500, 600, 900, 1000}
FX = {"eur", "usd", "gbp", "jpy", "chf", "aud", "nzd", "cad"}

_SYN = re.compile(
    r"\b(volatility|vol|v|crash|c|boom|b)\s?(\d{2,4})(s|\s?\(1s\)|\s1s)?(?![\w.])", re.I
)
_GOLD = re.compile(r"\b(xauusd|xau|gold|l'or)\b", re.I)
_FX = re.compile(r"\b([a-z]{3})/?([a-z]{3})\b", re.I)


def find_symbol(text: str) -> str | None:
    """Renvoie le premier code canonique trouvé dans le texte, sinon None."""
    for m in _SYN.finditer(text):
        fam, num, one_s = m.group(1).lower(), int(m.group(2)), m.group(3)
        if fam in ("volatility", "vol", "v") and num in V_NUMS:
            return f"V{num}S" if one_s else f"V{num}"
        if fam in ("crash", "c") and num in CB_NUMS:
            return f"C{num}"
        if fam in ("boom", "b") and num in CB_NUMS:
            return f"B{num}"
    if _GOLD.search(text):
        return "XAUUSD"
    for m in _FX.finditer(text):
        a, b = m.group(1).lower(), m.group(2).lower()
        if a in FX and b in FX and a != b:
            return (a + b).upper()
    return None


def canonical(code: str | None) -> str | None:
    """Normalise un code renvoyé par Claude ("Volatility 75 Index" -> "V75")."""
    if not code:
        return None
    return find_symbol(code) or code.strip().upper()


def mt5_base_name(code: str) -> str:
    """V75 -> 'Volatility 75 Index' (le suffixe éventuel du serveur, ex. '.0', est géré par l'exécuteur)."""
    if m := re.fullmatch(r"V(\d+)(S?)", code):
        return f"Volatility {m.group(1)} (1s) Index" if m.group(2) else f"Volatility {m.group(1)} Index"
    if m := re.fullmatch(r"C(\d+)", code):
        return f"Crash {m.group(1)} Index"
    if m := re.fullmatch(r"B(\d+)", code):
        return f"Boom {m.group(1)} Index"
    return code
