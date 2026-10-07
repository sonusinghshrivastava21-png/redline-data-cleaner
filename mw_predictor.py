"""MW Predictor for a condensing steam turbine.

Takes main steam (MS) flow, pressure and temperature plus condenser vacuum,
uses IAPWS-IF97 steam properties to find the thermodynamic states across the
turbine, and predicts generator output (MW).

Model (single expansion, MS inlet -> condenser):
    1. Inlet state      h1, s1 = f(P_ms, T_ms)
    2. Exhaust pressure P_c from condenser vacuum and barometric pressure
    3. Isentropic end   h2s    = f(P_c, s1)
    4. Actual end       h2     = h1 - eta_turbine * (h1 - h2s)
    5. Power            MW     = m * (h1 - h2) * eta_mech * eta_gen / 1000

Turbine isentropic efficiency is an adjustable assumption. Use
`calibrate_efficiency` with a known operating point (actual MW) to back out
the effective efficiency of your machine, which also absorbs losses the
simple model ignores (extractions for feed heating, gland leakage, etc.).
"""

import argparse
from dataclasses import dataclass

from iapws import IAPWS97

STD_ATM_BAR = 1.01325
KGCM2_TO_BAR = 0.980665
MMHG_TO_BAR = 1.01325 / 760.0

PRESSURE_UNITS = ("kg/cm2g", "kg/cm2a", "barg", "bara", "MPa", "kPa")
VACUUM_UNITS = ("mmHg", "kg/cm2", "kPa_abs", "bara", "mmHg_abs")


def to_bar_abs(value, unit, baro_bar=STD_ATM_BAR):
    """Convert a steam pressure reading to bar absolute."""
    if unit == "kg/cm2g":
        return value * KGCM2_TO_BAR + baro_bar
    if unit == "kg/cm2a":
        return value * KGCM2_TO_BAR
    if unit == "barg":
        return value + baro_bar
    if unit == "bara":
        return value
    if unit == "MPa":
        return value * 10.0
    if unit == "kPa":
        return value / 100.0
    raise ValueError(f"Unknown pressure unit {unit!r}; use one of {PRESSURE_UNITS}")


def vacuum_to_bar_abs(value, unit, baro_bar=STD_ATM_BAR):
    """Convert a condenser vacuum reading to absolute exhaust pressure (bar).

    'mmHg' and 'kg/cm2' are vacuum (below atmosphere) readings; 'kPa_abs',
    'bara' and 'mmHg_abs' are already absolute pressures.
    """
    if unit == "mmHg":
        p = baro_bar - value * MMHG_TO_BAR
    elif unit == "kg/cm2":
        p = baro_bar - value * KGCM2_TO_BAR
    elif unit == "kPa_abs":
        p = value / 100.0
    elif unit == "bara":
        p = value
    elif unit == "mmHg_abs":
        p = value * MMHG_TO_BAR
    else:
        raise ValueError(f"Unknown vacuum unit {unit!r}; use one of {VACUUM_UNITS}")
    if p <= 0.006:
        raise ValueError(
            f"Condenser pressure {p:.4f} bar(a) is at/below the triple point; "
            "check the vacuum reading, unit and barometric pressure"
        )
    return p


@dataclass
class TurbineResult:
    flow_tph: float
    p_inlet_bara: float
    t_inlet_c: float
    p_exhaust_bara: float
    t_exhaust_c: float
    h_inlet: float          # kJ/kg
    s_inlet: float          # kJ/kg.K
    h_exhaust_isentropic: float
    h_exhaust: float
    x_exhaust: float        # dryness fraction (1.0 if superheated)
    isentropic_drop: float  # kJ/kg
    actual_drop: float      # kJ/kg
    turbine_eff: float
    mech_eff: float
    gen_eff: float
    shaft_mw: float
    mw: float
    steam_rate: float       # kg/kWh
    heat_rate: float        # kJ/kWh, turbine cycle (from MS enthalpy to sat. liquid at condenser)

    def report(self):
        return "\n".join([
            "=== MW Predictor ===",
            f"MS flow               : {self.flow_tph:10.2f} t/h",
            f"MS pressure           : {self.p_inlet_bara:10.3f} bar(a)",
            f"MS temperature        : {self.t_inlet_c:10.2f} °C",
            f"Condenser pressure    : {self.p_exhaust_bara * 100:10.3f} kPa(a)"
            f"  (Tsat {self.t_exhaust_c:.2f} °C)",
            "--- Steam states ---",
            f"h inlet               : {self.h_inlet:10.2f} kJ/kg",
            f"s inlet               : {self.s_inlet:10.4f} kJ/kg.K",
            f"h exhaust (isentropic): {self.h_exhaust_isentropic:10.2f} kJ/kg",
            f"h exhaust (actual)    : {self.h_exhaust:10.2f} kJ/kg",
            f"Exhaust dryness       : {self.x_exhaust:10.4f}",
            f"Isentropic drop       : {self.isentropic_drop:10.2f} kJ/kg",
            f"Actual drop           : {self.actual_drop:10.2f} kJ/kg",
            "--- Assumptions ---",
            f"Turbine isentropic eff: {self.turbine_eff * 100:10.2f} %",
            f"Mechanical eff        : {self.mech_eff * 100:10.2f} %",
            f"Generator eff         : {self.gen_eff * 100:10.2f} %",
            "--- Result ---",
            f"Shaft power           : {self.shaft_mw:10.3f} MW",
            f"Predicted MW          : {self.mw:10.3f} MW",
            f"Steam rate            : {self.steam_rate:10.3f} kg/kWh",
            f"Turbine heat rate     : {self.heat_rate:10.1f} kJ/kWh"
            f" ({self.heat_rate / 4.1868:.1f} kcal/kWh)",
        ])


def _check_eff(name, value):
    if not 0.0 < value <= 1.0:
        raise ValueError(f"{name} must be in (0, 1], got {value}")


def predict_mw(flow_tph, p_ms_bara, t_ms_c, p_cond_bara,
               turbine_eff=0.85, mech_eff=0.99, gen_eff=0.985):
    """Predict generator MW from MS conditions and condenser pressure.

    Pressures are bar absolute, temperature °C, flow t/h. Efficiencies are
    fractions (0.85 = 85 %).
    """
    for name, v in (("turbine_eff", turbine_eff), ("mech_eff", mech_eff), ("gen_eff", gen_eff)):
        _check_eff(name, v)
    if flow_tph < 0:
        raise ValueError("MS flow cannot be negative")
    if p_cond_bara >= p_ms_bara:
        raise ValueError("Condenser pressure must be below MS pressure")

    inlet = IAPWS97(P=p_ms_bara / 10.0, T=t_ms_c + 273.15)
    if inlet.x < 1.0 or inlet.phase == "Liquid":
        raise ValueError(
            f"MS at {p_ms_bara:.2f} bar(a) / {t_ms_c:.1f} °C is not superheated "
            f"(Tsat = {IAPWS97(P=p_ms_bara / 10.0, x=1).T - 273.15:.1f} °C)"
        )

    p_c = p_cond_bara / 10.0
    end_s = IAPWS97(P=p_c, s=inlet.s)
    h2s = end_s.h
    h2 = inlet.h - turbine_eff * (inlet.h - h2s)
    end = IAPWS97(P=p_c, h=h2)
    sat_liq = IAPWS97(P=p_c, x=0)

    m_kgs = flow_tph * 1000.0 / 3600.0
    shaft_mw = m_kgs * (inlet.h - h2) / 1000.0 * mech_eff
    mw = shaft_mw * gen_eff

    if mw > 0:
        steam_rate = flow_tph * 1000.0 / (mw * 1000.0)
        heat_rate = steam_rate * (inlet.h - sat_liq.h)
    else:
        steam_rate = heat_rate = float("inf")

    return TurbineResult(
        flow_tph=flow_tph,
        p_inlet_bara=p_ms_bara,
        t_inlet_c=t_ms_c,
        p_exhaust_bara=p_cond_bara,
        t_exhaust_c=sat_liq.T - 273.15,
        h_inlet=inlet.h,
        s_inlet=inlet.s,
        h_exhaust_isentropic=h2s,
        h_exhaust=h2,
        x_exhaust=min(end.x, 1.0),
        isentropic_drop=inlet.h - h2s,
        actual_drop=inlet.h - h2,
        turbine_eff=turbine_eff,
        mech_eff=mech_eff,
        gen_eff=gen_eff,
        shaft_mw=shaft_mw,
        mw=mw,
        steam_rate=steam_rate,
        heat_rate=heat_rate,
    )


def calibrate_efficiency(actual_mw, flow_tph, p_ms_bara, t_ms_c, p_cond_bara,
                         mech_eff=0.99, gen_eff=0.985):
    """Back-calculate the effective turbine isentropic efficiency from a known MW.

    MW is linear in turbine efficiency, so this is a direct solve.
    """
    ref = predict_mw(flow_tph, p_ms_bara, t_ms_c, p_cond_bara, 1.0, mech_eff, gen_eff)
    if ref.mw <= 0:
        raise ValueError("Isentropic MW is zero; cannot calibrate")
    eff = actual_mw / ref.mw
    if not 0.0 < eff <= 1.0:
        raise ValueError(
            f"Calibrated efficiency {eff:.3f} is outside (0, 1]; "
            "check the inputs, units or mech/gen efficiencies"
        )
    return eff


def main(argv=None):
    ap = argparse.ArgumentParser(description="Predict turbine MW from MS conditions and condenser vacuum")
    ap.add_argument("--flow", type=float, required=True, help="MS flow, t/h")
    ap.add_argument("--pressure", type=float, required=True, help="MS pressure")
    ap.add_argument("--pressure-unit", default="kg/cm2g", choices=PRESSURE_UNITS)
    ap.add_argument("--temp", type=float, required=True, help="MS temperature, °C")
    ap.add_argument("--vacuum", type=float, required=True, help="Condenser vacuum reading")
    ap.add_argument("--vacuum-unit", default="mmHg", choices=VACUUM_UNITS,
                    help="mmHg / kg/cm2 = vacuum below atmosphere; *_abs / bara = absolute")
    ap.add_argument("--baro", type=float, default=STD_ATM_BAR, help="Barometric pressure, bar (default 1.01325)")
    ap.add_argument("--turbine-eff", type=float, default=85.0, help="Turbine isentropic efficiency, %% (default 85)")
    ap.add_argument("--mech-eff", type=float, default=99.0, help="Mechanical efficiency, %% (default 99)")
    ap.add_argument("--gen-eff", type=float, default=98.5, help="Generator efficiency, %% (default 98.5)")
    ap.add_argument("--actual-mw", type=float,
                    help="Measured MW: back-calculate the turbine efficiency that matches it")
    ap.add_argument("--sweep", action="store_true", help="Also print MW across a range of turbine efficiencies")
    args = ap.parse_args(argv)

    p_ms = to_bar_abs(args.pressure, args.pressure_unit, args.baro)
    p_c = vacuum_to_bar_abs(args.vacuum, args.vacuum_unit, args.baro)
    mech, gen = args.mech_eff / 100.0, args.gen_eff / 100.0

    eff = args.turbine_eff / 100.0
    if args.actual_mw is not None:
        eff = calibrate_efficiency(args.actual_mw, args.flow, p_ms, args.temp, p_c, mech, gen)
        print(f"Calibrated turbine efficiency from {args.actual_mw:.3f} MW: {eff * 100:.2f} %\n")

    print(predict_mw(args.flow, p_ms, args.temp, p_c, eff, mech, gen).report())

    if args.sweep:
        print("\n--- Efficiency sweep ---")
        print(f"{'Turbine eff %':>14} {'MW':>10}")
        for pct in range(70, 95, 2):
            r = predict_mw(args.flow, p_ms, args.temp, p_c, pct / 100.0, mech, gen)
            print(f"{pct:>14} {r.mw:>10.3f}")


if __name__ == "__main__":
    main()
