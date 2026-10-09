# sample/

This folder should hold `play-log-sample.json` and `scenarios.json` (see the top-level README). They are not in the repo yet.

- The API loads the scenario library from `sample/scenarios.json` (or `SCENARIOS_PATH`).
- `tests/test_replay.py` replays `play-log-sample.json` once both files are here. Until then it runs on the synthetic files in `tests/fixtures/`, which are labelled as synthetic sample data.
