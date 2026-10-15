"""Messages réels du canal « Signaux trader elite »."""
from sniper.rules import parse
from sniper.symbols import canonical, find_symbol, mt5_base_name


def test_sell_market_without_sl():
    s = parse("Sell V10 lot 2")
    assert s.kind == "open" and s.symbol == "V10"
    assert s.orders[0].side == "sell" and s.orders[0].type == "market" and s.orders[0].lot == 2
    assert s.sl_price is None and s.sl_usd is None


def test_sl_alone_is_update():
    s = parse("Sl 4942.54")
    assert s.kind == "update" and s.sl_price == 4942.54 and s.symbol is None


def test_two_buy_limits_without_symbol_go_to_llm():
    # Le symbole (V10) n'est que sur l'image du message précédent.
    assert parse("Buy limit at ;\n4911.638\nLot 1\n\nBuy limit at 4907.058\nLot 1\n\nSL4900") is None


def test_two_buy_limits_with_symbol():
    s = parse("V10\nBuy limit at ;\n4911.638\nLot 1\n\nBuy limit at 4907.058\nLot 0.5\n\nSL4900")
    assert s.kind == "open" and s.symbol == "V10" and s.sl_price == 4900
    assert [(o.type, o.price, o.lot) for o in s.orders] == [("limit", 4911.638, 1), ("limit", 4907.058, 0.5)]


def test_triggered_buy_now():
    s = parse("Déclenché pour ceux qui n’ont pas pris , buy now\nLot 0.02\nV75\nSl 8.5$")
    assert s.kind == "open" and s.symbol == "V75"
    assert s.orders[0].type == "market" and s.orders[0].lot == 0.02
    assert s.sl_usd == 8.5 and s.sl_price is None


def test_tps_in_dollars():
    s = parse("TP 1 +15$\nTP 2 + 30$\nTP 3 +55$")
    assert s.kind == "update" and [tp.usd for tp in s.tps] == [15, 30, 55]


def test_tp_price_not_confused_with_index():
    s = parse("TP 4950.5")
    assert s.tps[0].price == 4950.5


def test_results_ignored():
    for msg in ["Coupons SL hit", "Bilan lundi\nV50s\nTP1 et TP2 hit\nRR 1: 5", "+8$🍾", "Bilan mardi\nV75\nSl -1R"]:
        assert parse(msg).kind == "ignore", msg


def test_advice_and_forecast_go_to_llm():
    for msg in [
        "MA PRÉVISION POUR IPC\n\nBUY USDXXX\nSELL XXXUSD\n\nMais perso je vous conseillerais d’observer",
        "On attend un peu à la clôture de la bougie m15",
        "Bonjour traders 🍾\nIPC dans 15min\nTrès risqué et More pips à vous",
    ]:
        assert parse(msg) is None, msg


def test_decimal_comma():
    s = parse("V10 buy limit at 4907,58 lot 1 sl 4900")
    assert s.orders[0].price == 4907.58


def test_symbols():
    assert find_symbol("c'est le v75") == "V75"
    assert find_symbol("Ceux qui sont dans le V50s vous pouvez clôturer") == "V50S"
    assert find_symbol("crash 500") == "C500"
    assert find_symbol("B1000 buy") == "B1000"
    assert find_symbol("Lot 1 TP 1 +15$ m15") is None
    assert canonical("Volatility 10 Index") == "V10"
    assert mt5_base_name("V50S") == "Volatility 50 (1s) Index"
    assert mt5_base_name("C500") == "Crash 500 Index"
