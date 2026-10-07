import pytest

from mw_predictor import (
    calibrate_efficiency,
    predict_mw,
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
