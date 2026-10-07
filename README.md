# redline-data-cleaner
AI-assisted data cleaning tool — detects nulls, whitespace, duplicates"


## MW Predictor (`mw_predictor.py`)

Predicts steam turbine generator output from:

- MS (main steam) flow — t/h
- MS pressure — kg/cm²(g) by default (`--pressure-unit` also accepts kg/cm2a, barg, bara, MPa, kPa)
- MS temperature — °C
- Condenser vacuum — mmHg vacuum by default (`--vacuum-unit` also accepts kg/cm2 vacuum, kPa_abs, bara, mmHg_abs)

Steam properties come from IAPWS-IF97 (`iapws` package). The model expands steam
from the MS state to condenser pressure:

```
h1, s1 = f(P_ms, T_ms)          inlet state
h2s    = f(P_cond, s1)          isentropic exhaust
h2     = h1 - η_t (h1 - h2s)    actual exhaust
MW     = ṁ (h1 - h2) η_mech η_gen / 1000
```

Turbine isentropic efficiency (default 85 %), mechanical (99 %) and generator (98.5 %)
efficiencies are adjustable assumptions.

```bash
pip install -r requirements.txt
python mw_predictor.py --flow 400 --pressure 130 --temp 535 --vacuum 690
python mw_predictor.py --flow 400 --pressure 130 --temp 535 --vacuum 690 --turbine-eff 80 --sweep
# back-calculate the effective efficiency from a measured load
python mw_predictor.py --flow 400 --pressure 130 --temp 535 --vacuum 690 --actual-mw 110
```

The model is a single straight expansion, so it ignores extractions for feed
heating, reheat and gland leakage. Using `--actual-mw` on a known operating
point gives an effective efficiency that absorbs those losses; reuse that value
for prediction at other loads.
