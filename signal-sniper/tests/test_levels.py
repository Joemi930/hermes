from sniper.levels import broker_tp, partial_volume, sl_level, stage_reached, tp_level, usd_to_distance


def test_sl_8_5_usd_on_v75_lot_0_02():
    # V75 sur Deriv MT5 : tick_size 0.01, tick_value 0.01 $ par lot -> 1 $ par point et par lot.
    d = usd_to_distance(8.5, 0.02, tick_value=0.01, tick_size=0.01)
    assert round(d, 6) == 425.0
    assert sl_level(40998.52, "buy", d) == 40998.52 - 425
    assert tp_level(40998.52, "buy", usd_to_distance(15, 0.02, 0.01, 0.01)) == 40998.52 + 750


def test_tp_stages_and_partials():
    levels = [110.0, 120.0, 130.0]
    assert stage_reached("buy", 115, levels) == 1
    assert stage_reached("sell", 95, [90.0]) == 0
    assert broker_tp(levels, "final_be") == 130.0 and broker_tp(levels, "first") == 110.0
    assert partial_volume(0.03, 1, 3, 0.001, 0.001) == 0.01
    assert partial_volume(0.02, 2, 3, 0.001, 0.001) == 0.01
    assert partial_volume(0.01, 3, 3, 0.001, 0.001) == 0.0
    assert partial_volume(1.0, 1, 3, 0.5, 0.5) == 0.0  # coupe sous le lot minimum -> on ne coupe pas
