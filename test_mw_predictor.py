import pytest

from mw_predictor import (
    calibrate_efficiency,
    calibrate_iplp_flow_factor,
    hp_eff_from_crh_temp,
    predict_mw,
    predict_mw_reheat,
    to_bar_abs,
    vacuum_to_bar_abs,
)


def test_unit_conversions():
    assert to_bar_abs(0, "kg/cm2g") == pytest.approx(1.01325)
    assert to_bar_abs(10, "MPa") == pytest.approx(100)
    assert vacuum_to_bar_abs(10, "kPa_abs") == pytest.approx(0.1)
    assert vacuum_to_bar_abs(690, "mmHg") == pytest.approx(1.01325 * 70 / 760)


def test_full_vacuum_rejected():
    with pytest.raises(ValueError):
        vacuum_to_bar_abs(760, "mmHg")


def test_matches_steam_tables():
    # 100 bar / 500 °C expanded isentropically to 0.1 bar (textbook Rankine case)
    r = predict_mw(3.6, 100, 500, 0.1, 1.0, 1.0, 1.0)
    assert r.h_inlet == pytest.approx(3373.7, abs=2)
    assert r.s_inlet == pytest.approx(6.5966, abs=0.005)
    assert r.x_exhaust == pytest.approx(0.793, abs=0.005)
    # 1 kg/s * isentropic drop
    assert r.mw * 1000 == pytest.approx(r.isentropic_drop, rel=1e-9)


def test_mw_scales_with_flow_and_efficiency():
    base = predict_mw(400, 128.5, 535, 0.09, 0.85).mw
    assert predict_mw(200, 128.5, 535, 0.09, 0.85).mw == pytest.approx(base / 2)
    assert predict_mw(400, 128.5, 535, 0.09, 0.80).mw < base
    # better vacuum (lower back pressure) gives more MW
    assert predict_mw(400, 128.5, 535, 0.07, 0.85).mw > base


def test_calibration_round_trip():
    r = predict_mw(400, 128.5, 535, 0.09, 0.82)
    assert calibrate_efficiency(r.mw, 400, 128.5, 535, 0.09) == pytest.approx(0.82)


def test_wet_inlet_rejected():
    with pytest.raises(ValueError):
        predict_mw(100, 100, 300, 0.1)  # below Tsat (~311 °C)

REHEAT = (1795, 159.88, 541, 42.53, 40.40, 534, 0.153)


def test_kgcm2_abs_vacuum():
    assert vacuum_to_bar_abs(0.156, "kg/cm2_abs") == pytest.approx(0.156 * 0.980665)


def test_reheat_sections_add_up():
    r = predict_mw_reheat(*REHEAT)
    assert r.mw == pytest.approx((r.hp_mw + r.iplp_mw) * r.gen_eff)
    m = 1795 / 3.6
    assert r.hp_mw == pytest.approx(m * (r.h_ms - r.h_crh) / 1000 * 0.99)
    assert r.iplp_mw == pytest.approx(m * 0.9 * 0.85 * (r.h_hrh - r.h_exhaust) / 1000 * 0.99)


def test_reheat_beats_no_reheat_drop():
    # reheat raises total enthalpy drop per kg compared with straight expansion
    r = predict_mw_reheat(*REHEAT, hp_eff=0.85, iplp_eff=0.85)
    straight = predict_mw(1795, 159.88, 541, 0.153, 0.85)
    assert (r.h_ms - r.h_crh) + (r.h_hrh - r.h_exhaust) > straight.actual_drop


def test_reheat_calibration_round_trip():
    r = predict_mw_reheat(*REHEAT, iplp_flow_factor=0.8)
    assert calibrate_iplp_flow_factor(r.mw, *REHEAT) == pytest.approx(0.8)


def test_hp_eff_from_crh_temp_round_trip():
    r = predict_mw_reheat(*REHEAT, hp_eff=0.87)
    assert hp_eff_from_crh_temp(159.88, 541, 42.53, r.t_crh_c) == pytest.approx(0.87, abs=1e-4)


def test_reheat_pressure_order_checked():
    with pytest.raises(ValueError):
        predict_mw_reheat(1795, 159.88, 541, 40.0, 42.0, 534, 0.153)
