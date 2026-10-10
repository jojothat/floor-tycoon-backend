# sample/

Sample data, labelled as sample, and the game's catalogue.

- `play-log-sample.json`: a real play from the current build. `tests/test_replay.py` replays it through the API.
- `scenarios.json`: the 61 scenarios. The API deals from this file by default (`SCENARIOS_PATH` overrides it). It is the same library as `readout/mock-api/scenarios.json`, with option names, items and the game's questions for the hand-built scenarios.
- `items.json`: the game's item catalogue with categories, for the read API's `space-brief` (`ITEMS_PATH`).
- `palettes.json`: the game's 10 palettes, for the read API's `meta` (`PALETTES_PATH`).
