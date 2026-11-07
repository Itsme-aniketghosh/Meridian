# Rachna's pass-bar rows, for Model-Pipeline/PASS-BARS.md

These are my rows for Chaitali's `PASS-BARS.md`. They were written before any run on the real table, and the rules
that pick each option are in [decisions.md](decisions.md). One bar changed before any run, and says why: the
shuffled-label bar first read "within 0.02 of the positive rate", but at N ≈ 2,750 random scores alone land up to
0.02 above it (97.5th percentile), so that bar would fail about 1 time in 40 on luck. It now compares against what
chance reaches on the same rows.

| What | Bar | Measured by |
|---|---|---|
| Model ships | 2024 PR-AUC beats lines × files, with the paired bootstrap interval of the difference above 0. Otherwise the rule stays | Rochan's one 2024 run, Nov 12 |
| Model determinism | Same data, config and seed: every `val` prediction within 1e-9, laptop and image (decision 9) | `test_models.py`, `test_parallel.py`; the image run against a laptop run |
| No 2024 in training | No `test` row reaches any fit, transformer, calibrator or tuning fold. 100% | `test_train.py`, `test_models.py`, `test_tune.py` |
| Model sanity | Shuffled labels: `val` PR-AUC below the 99th percentile random scores reach on the same rows (`chance_ceiling`), and ROC-AUC within 0.5 ± 0.03, median of 3 shuffles | `test_models.py` |
| Seed noise | 5 seeds of the chosen config, mean and SD of `val` PR-AUC in `reports/seed_noise.json` | `train.py --seed-noise` (decision 7) |
| One command | `python -m model.run_all` in the image runs every step in order and stops at the first failure, naming it | `test_run_all.py`; the image at integration |
| Jev | Same problem twice: same pick. Jev down or over the spend cap: rules pick, the run completes. Beats rules on cost at an equal pass rate (within 2 points) | `test_jev.py`, decision 13 |
| P9 with the model | Replay: median alerts per push ≤ 1, precision ≥ 0.80. A model that fails to load falls back to lines × files and says so | `test_pushcheck.py`, decision 15 |
