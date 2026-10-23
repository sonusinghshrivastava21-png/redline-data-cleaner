import numpy as np
import pandas as pd
import pytest

from vacuum_event_analyzer import find_events, load_csv, main


def synthetic(n=3600, event_at=1200, seed=0):
    """1-s data: vacuum steps up 0.02 at `event_at`, load dips 6 MW then
    recovers as coal rises by 4 t/h after a 30 s lag."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    after = np.clip(t - event_at, 0, None)
    vac = 0.10 + np.where(t >= event_at, 0.02 * (1 - np.exp(-after / 20)), 0)
    coal = 250 + np.where(t >= event_at + 30, 4 * (1 - np.exp(-(after - 30).clip(0) / 60)), 0)
    load = 500 - np.where(t >= event_at, 6 * np.exp(-after / 150) * (1 - np.exp(-after / 10)), 0)
    return pd.DataFrame({
        "Timestamp": pd.date_range("2026-10-01 10:00:00", periods=n, freq="s").strftime("%d-%m-%Y %H:%M:%S"),
        "Condenser Vacuum (kg/cm2a)": vac + rng.normal(0, 0.0003, n),
        "Gen Load MW": load + rng.normal(0, 0.2, n),
        "Coal Feeder A": coal / 2 + rng.normal(0, 0.1, n),
        "Coal Feeder B": coal / 2 + rng.normal(0, 0.1, n),
    })


@pytest.fixture
def csv_path(tmp_path):
    p = tmp_path / "plant.csv"
    synthetic().to_csv(p, index=False)
    return p


def test_detects_single_event_and_measures_response(csv_path):
    df = load_csv(csv_path, coal_cols=["Coal Feeder A", "Coal Feeder B"], dayfirst=True)
    assert df.attrs["columns"]["vac"] == "Condenser Vacuum (kg/cm2a)"
    ev = find_events(df, rise=0.005, observe=600)
    assert len(ev) == 1
    e = ev.iloc[0]
    assert abs((e["start_time"] - pd.Timestamp("2026-10-01 10:20:00")).total_seconds()) <= 5
    assert e["vac_rise"] == pytest.approx(0.02, abs=0.002)
    assert e["load_drop_mw"] == pytest.approx(5.5, abs=1.0)
    assert e["coal_end_increase"] == pytest.approx(4, abs=0.6)
    assert 25 <= e["coal_response_after_s"] <= 60
    assert e["load_recovered"]
    assert e["specific_coal_end"] > e["specific_coal_before"]


def test_no_event_when_vacuum_flat(tmp_path):
    d = synthetic()
    d["Condenser Vacuum (kg/cm2a)"] = 0.10
    p = tmp_path / "flat.csv"
    d.to_csv(p, index=False)
    assert find_events(load_csv(p, dayfirst=True)).empty


def test_cli_writes_event_csv(csv_path, tmp_path, capsys):
    out = tmp_path / "events.csv"
    rc = main([str(csv_path), "--dayfirst", "--coal-col", "Coal Feeder A",
               "--coal-col", "Coal Feeder B", "--out", str(out)])
    assert rc == 0
    assert "1 vacuum rise event" in capsys.readouterr().out
    assert len(pd.read_csv(out)) == 1


def test_missing_column_reported(tmp_path, capsys):
    p = tmp_path / "bad.csv"
    pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_csv(p, index=False)
    assert main([str(p)]) == 2
    assert "vacuum" in capsys.readouterr().err
