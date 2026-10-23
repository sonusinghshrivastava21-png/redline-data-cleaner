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

### Reheat mode

For reheat units, pass `--crh-pressure`, `--hrh-pressure` and `--hrh-temp`
(pressures in `--pressure-unit`). The model then splits the turbine:

```
HP:     MS -> CRH pressure at η_HP, full MS flow
IP/LP:  HRH -> condenser at η_IPLP, flow = MS flow × reheat fraction × IP/LP flow factor
MW    = (HP work + IP/LP work) × η_mech × η_gen
```

- `--hp-eff` (default 85 %), or `--crh-temp` to compute HP efficiency from the measured CRH temperature
- `--iplp-eff` (default 90 %)
- `--rh-flow-frac`: reheat flow as % of MS flow, after HP heater extractions (default 90 %)
- `--iplp-flow-factor`: average IP/LP flow as % of reheat flow, after deaerator and LP heater extractions (default 85 %). `--actual-mw` solves for this factor.

```bash
python mw_predictor.py --flow 1795 --pressure 162 --temp 541 \
  --vacuum 0.156 --vacuum-unit kg/cm2_abs \
  --crh-pressure 42.33 --hrh-pressure 40.16 --hrh-temp 534 --actual-mw 559
```

## Vacuum event analyzer (`vacuum_event_analyzer.py`)

Reads a continuous 1-second plant CSV and finds every rise in condenser vacuum
(absolute pressure, kg/cm² abs; a higher value means worse vacuum). For each rise
it reads the rows that follow and reports:

- how much load (MW) dropped, and how many seconds after the rise
- how much coal flow went up to hold the load, how long before coal responded,
  and where it settled
- whether load recovered, MW lost and extra coal per 0.01 kg/cm² of vacuum rise
- specific coal (coal / MW) before vs after the rise

```bash
pip install -r requirements.txt
python vacuum_event_analyzer.py plant.csv \
  --vac-col "COND VAC" --load-col "GEN MW" \
  --coal-col "FEEDER A" --coal-col "FEEDER B" --coal-col "FEEDER C" \
  --dayfirst --rise 0.005 --observe 600 --plot-dir plots
```

Columns are auto-detected by name (`vac`, `mw`/`load`, `coal`, `time`) when
not given. Repeat `--coal-col` to add up several feeders. If there is no time
column, the tool treats each row as 1 second.

| Option | Default | Meaning |
|---|---|---|
| `--rise` | 0.005 | Smallest vacuum rise (kg/cm² abs) that counts as an event |
| `--rise-window` | 120 s | Time span the rise has to happen within |
| `--smooth` | 5 s | Rolling-median filter on vacuum |
| `--baseline` | 60 s | Time before the rise averaged as the "before" values |
| `--observe` | 600 s | Time after the rise to watch load and coal |
| `--settle` | 60 s | Last part of the observe window averaged as the "end" values |
| `--coal-threshold` | 3σ of baseline | Coal rise that counts as "coal responded" |

The tool writes one row per event to `--out` (default `vacuum_events.csv`).
With `--plot-dir`, it also saves a vacuum, load and coal chart for each event
(needs matplotlib).
