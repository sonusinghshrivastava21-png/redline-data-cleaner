"""Vacuum event analyzer for 1-second plant historian CSVs.

Watches condenser vacuum given as absolute pressure (kg/cm2 abs). A rise in
absolute pressure means vacuum is getting worse. For every rise the tool looks
at the rows after it and reports:

    * how much the load (MW) dropped, and how long after the rise started
    * how much the coal flow went up to bring the load back, and the lag
    * where load and coal settled at the end of the observation window
    * MW lost and extra coal per 0.01 kg/cm2 of vacuum rise
    * specific coal (t/MWh) before vs after, i.e. the heat-rate penalty

Event detection (all windows are in seconds, using the timestamp column):
    1. Smooth vacuum with a rolling median (--smooth) to drop sensor noise.
    2. A rise is flagged when smoothed vacuum is at least --rise above its
       minimum over the previous --rise-window seconds.
    3. The event starts at the last moment vacuum was within 20 % of --rise
       of that minimum. Baselines for vacuum, load and coal
       are the mean over --baseline seconds before the start.
    4. The response is observed for --observe seconds after the start; the
       "settled" values are the mean over the last --settle seconds of it.
    5. The next event is looked for only after the observation window ends,
       so one disturbance is not counted twice.
"""

import argparse
import sys

import numpy as np
import pandas as pd

VAC_KEYS = ("vac", "condenser", "cond_press", "back_press")
LOAD_KEYS = ("mw", "load", "gen_power", "active_power")
COAL_KEYS = ("coal", "feeder", "fuel")
TIME_KEYS = ("time", "date", "stamp")


def _find_column(columns, keys, what):
    for key in keys:
        for col in columns:
            if key in col.lower():
                return col
    raise ValueError(
        f"Could not find a {what} column automatically; pass it explicitly. "
        f"Columns in file: {list(columns)}"
    )


def load_csv(path, time_col=None, vac_col=None, load_col=None, coal_cols=None,
             time_format=None, dayfirst=False, max_gap=10):
    """Read the CSV and return a frame indexed by time with vac/load/coal columns.

    Several coal columns (e.g. one per feeder) are summed into total coal.
    Gaps of up to `max_gap` rows are interpolated; longer gaps stay NaN.
    """
    raw = pd.read_csv(path)
    raw.columns = [str(c).strip() for c in raw.columns]

    vac_col = vac_col or _find_column(raw.columns, VAC_KEYS, "vacuum")
    load_col = load_col or _find_column(raw.columns, LOAD_KEYS, "load (MW)")
    if not coal_cols:
        coal_cols = [_find_column(raw.columns, COAL_KEYS, "coal flow")]
    missing = [c for c in [vac_col, load_col, *coal_cols] if c not in raw.columns]
    if missing:
        raise ValueError(f"Column(s) not in file: {missing}. Columns in file: {list(raw.columns)}")

    try:
        time_col = time_col or _find_column(raw.columns, TIME_KEYS, "timestamp")
        index = pd.to_datetime(raw[time_col], format=time_format, dayfirst=dayfirst, errors="coerce")
    except ValueError:
        # no timestamp column: assume one row per second
        index = pd.to_datetime(np.arange(len(raw)), unit="s")

    def num(col):
        return pd.to_numeric(raw[col].astype(str).str.strip(), errors="coerce").to_numpy()

    df = pd.DataFrame({
        "vac": num(vac_col),
        "load": num(load_col),
        "coal": np.nansum([num(c) for c in coal_cols], axis=0),
    }, index=index)
    if len(coal_cols) > 1:
        # nansum turns all-NaN rows into 0; put the NaN back
        all_nan = np.all([np.isnan(num(c)) for c in coal_cols], axis=0)
        df.loc[all_nan, "coal"] = np.nan

    df = df[df.index.notna()].sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.interpolate(limit=max_gap, limit_area="inside")
    df.attrs["columns"] = {"vac": vac_col, "load": load_col, "coal": coal_cols}
    return df


def _window(df, start, end):
    return df.loc[(df.index >= start) & (df.index <= end)]


def find_events(df, rise=0.005, rise_window=120, smooth=5, observe=600,
                baseline=60, settle=60, coal_threshold=None):
    """Detect vacuum rises and measure the load / coal response after each one."""
    vac_s = df["vac"].rolling(f"{smooth}s", min_periods=1).median()
    roll_min = vac_s.rolling(f"{rise_window}s", min_periods=1).min()
    flagged = (vac_s - roll_min) >= rise

    events = []
    next_allowed = df.index[0] if len(df) else None
    for t in flagged.index[flagged.to_numpy()]:
        if t < next_allowed:
            continue
        # start of the rise = last moment vacuum was still near its window minimum
        look = vac_s.loc[t - pd.Timedelta(seconds=rise_window):t]
        start = look.index[(look <= look.min() + 0.2 * rise).to_numpy()][-1]
        ev = _measure(df, vac_s, start, observe, baseline, settle, coal_threshold)
        if ev is not None:
            events.append(ev)
        next_allowed = start + pd.Timedelta(seconds=observe)

    out = pd.DataFrame(events)
    if len(out):
        out.insert(0, "event", range(1, len(out) + 1))
    return out


def _measure(df, vac_s, start, observe, baseline, settle, coal_threshold):
    sec = pd.Timedelta(seconds=1)
    pre = _window(df, start - pd.Timedelta(seconds=baseline), start)
    post = _window(df, start, start + pd.Timedelta(seconds=observe))
    if pre[["vac", "load", "coal"]].dropna().empty or post[["load", "coal"]].dropna().empty:
        return None
    end = post.index[-1]
    tail = _window(df, end - pd.Timedelta(seconds=settle), end)
    vac_post = vac_s.loc[post.index]

    vac0, load0, coal0 = pre["vac"].mean(), pre["load"].mean(), pre["coal"].mean()
    vac_peak_t = vac_post.idxmax()
    load_min_t = post["load"].idxmin()
    coal_max_t = post["coal"].idxmax()

    vac_rise = vac_post.max() - vac0
    load_drop = load0 - post["load"].min()
    coal_inc = post["coal"].max() - coal0
    load_end, coal_end, vac_end = tail["load"].mean(), tail["coal"].mean(), tail["vac"].mean()

    # coal "responded" once it is clearly above its pre-event level
    thr = coal_threshold if coal_threshold is not None else max(3 * pre["coal"].std(ddof=0), 0.1)
    above = post.index[(post["coal"] - coal0).to_numpy() >= thr]
    coal_resp_t = above[0] if len(above) else pd.NaT

    def secs(t):
        return np.nan if pd.isna(t) else (t - start) / sec

    per = vac_rise / 0.01 if vac_rise > 0 else np.nan
    return {
        "start_time": start,
        "vac_before": vac0,
        "vac_peak": vac_post.max(),
        "vac_rise": vac_rise,
        "vac_peak_after_s": secs(vac_peak_t),
        "vac_end": vac_end,
        "load_before_mw": load0,
        "load_min_mw": post["load"].min(),
        "load_drop_mw": load_drop,
        "load_min_after_s": secs(load_min_t),
        "load_end_mw": load_end,
        "load_end_change_mw": load_end - load0,
        "coal_before": coal0,
        "coal_max": post["coal"].max(),
        "coal_increase": coal_inc,
        "coal_response_after_s": secs(coal_resp_t),
        "coal_max_after_s": secs(coal_max_t),
        "coal_end": coal_end,
        "coal_end_increase": coal_end - coal0,
        "mw_drop_per_0.01_vac": load_drop / per,
        "coal_inc_per_0.01_vac": (coal_end - coal0) / per,
        "specific_coal_before": coal0 / load0 if load0 else np.nan,
        "specific_coal_end": coal_end / load_end if load_end else np.nan,
        "load_recovered": bool(load_end >= load0 - 0.25 * load_drop) if load_drop > 0 else True,
    }


def print_summary(events, df, rise):
    cols = df.attrs.get("columns", {})
    print(f"Data: {len(df)} rows, {df.index[0]} -> {df.index[-1]}")
    if cols:
        print(f"Columns: vacuum={cols['vac']!r}, load={cols['load']!r}, coal={cols['coal']!r}")
    if events.empty:
        print(f"No vacuum rise of >= {rise} kg/cm2 abs found.")
        return
    print(f"\n{len(events)} vacuum rise event(s) (>= {rise} kg/cm2 abs):\n")
    show = events[[
        "event", "start_time", "vac_before", "vac_peak", "vac_rise",
        "load_before_mw", "load_drop_mw", "load_min_after_s", "load_end_change_mw",
        "coal_before", "coal_increase", "coal_response_after_s", "coal_end_increase",
    ]]
    with pd.option_context("display.width", 250, "display.max_columns", None,
                           "display.float_format", "{:.3f}".format):
        print(show.to_string(index=False))
    print("\nAverages over all events:")
    print(f"  vacuum rise               {events['vac_rise'].mean():.4f} kg/cm2 abs")
    print(f"  max load drop             {events['load_drop_mw'].mean():.2f} MW")
    print(f"  max coal increase         {events['coal_increase'].mean():.2f}")
    print(f"  settled coal increase     {events['coal_end_increase'].mean():.2f}")
    print(f"  MW drop per 0.01 vac      {events['mw_drop_per_0.01_vac'].mean():.2f} MW")
    print(f"  coal inc per 0.01 vac     {events['coal_inc_per_0.01_vac'].mean():.2f}")
    print(f"  coal response delay       {events['coal_response_after_s'].mean():.0f} s")
    print(f"  load recovered            {int(events['load_recovered'].sum())}/{len(events)} events")


def plot_events(df, events, out_dir, before=120, observe=600):
    import os

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(out_dir, exist_ok=True)
    for _, ev in events.iterrows():
        s = ev["start_time"]
        w = _window(df, s - pd.Timedelta(seconds=before), s + pd.Timedelta(seconds=observe))
        x = (w.index - s) / pd.Timedelta(seconds=1)
        fig, axes = plt.subplots(3, 1, sharex=True, figsize=(10, 7))
        for ax, col, label, color in zip(
            axes, ["vac", "load", "coal"],
            ["Vacuum (kg/cm2 abs)", "Load (MW)", "Coal flow"],
            ["tab:red", "tab:blue", "tab:brown"],
        ):
            ax.plot(x, w[col], color=color, lw=1)
            ax.set_ylabel(label)
            ax.axvline(0, color="grey", ls="--", lw=0.8)
            ax.grid(alpha=0.3)
        axes[0].set_title(
            f"Event {ev['event']}  {s}  |  vac +{ev['vac_rise']:.4f}  "
            f"load -{ev['load_drop_mw']:.1f} MW  coal +{ev['coal_increase']:.1f}"
        )
        axes[-1].set_xlabel("seconds from start of vacuum rise")
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"event_{ev['event']:03d}.png"), dpi=110)
        plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Find condenser vacuum rises (kg/cm2 abs) in 1-s plant data and "
                    "measure the load drop and coal increase that follow")
    ap.add_argument("csv", help="Plant data CSV (one row per second)")
    ap.add_argument("--time-col", help="Timestamp column (auto-detected; if none, 1 row = 1 s)")
    ap.add_argument("--vac-col", help="Vacuum column, kg/cm2 abs (auto-detected: name containing 'vac')")
    ap.add_argument("--load-col", help="Load column, MW (auto-detected: 'mw' / 'load')")
    ap.add_argument("--coal-col", action="append", dest="coal_cols",
                    help="Coal flow column; repeat for several feeders to sum them (auto-detected: 'coal')")
    ap.add_argument("--time-format", help="strftime format of the timestamp, e.g. '%%d-%%m-%%Y %%H:%%M:%%S'")
    ap.add_argument("--dayfirst", action="store_true", help="Timestamps are day-first (DD-MM-YYYY)")
    ap.add_argument("--rise", type=float, default=0.005,
                    help="Minimum vacuum rise to count as an event, kg/cm2 abs (default 0.005)")
    ap.add_argument("--rise-window", type=int, default=120,
                    help="The rise must happen within this many seconds (default 120)")
    ap.add_argument("--smooth", type=int, default=5, help="Rolling-median smoothing of vacuum, s (default 5)")
    ap.add_argument("--observe", type=int, default=600,
                    help="Seconds after the rise start to watch load and coal (default 600)")
    ap.add_argument("--baseline", type=int, default=60, help="Seconds before the rise used as baseline (default 60)")
    ap.add_argument("--settle", type=int, default=60,
                    help="Last seconds of the observation window averaged as 'end' values (default 60)")
    ap.add_argument("--coal-threshold", type=float,
                    help="Coal rise that counts as 'coal responded' (default: 3x baseline std dev)")
    ap.add_argument("--out", default="vacuum_events.csv", help="Output CSV of events (default vacuum_events.csv)")
    ap.add_argument("--plot-dir", help="Save a vacuum/load/coal PNG per event here (needs matplotlib)")
    args = ap.parse_args(argv)

    try:
        df = load_csv(args.csv, args.time_col, args.vac_col, args.load_col, args.coal_cols,
                      args.time_format, args.dayfirst)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if df.empty:
        print("Error: no usable rows in the CSV", file=sys.stderr)
        return 2

    events = find_events(df, args.rise, args.rise_window, args.smooth, args.observe,
                         args.baseline, args.settle, args.coal_threshold)
    print_summary(events, df, args.rise)
    if not events.empty:
        events.to_csv(args.out, index=False, float_format="%.4f")
        print(f"\nEvent table written to {args.out}")
        if args.plot_dir:
            plot_events(df, events, args.plot_dir, args.baseline * 2, args.observe)
            print(f"Plots written to {args.plot_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
