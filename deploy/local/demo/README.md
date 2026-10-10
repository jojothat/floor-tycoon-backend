# Demo recording

`demo.sh` records a video showing that the game, the backend and the readout are connected:

- **Top left:** a game played on a phone-sized screen.
- **Right:** the backend console, in plain English, as each tap is saved.
- **Bottom left:** the manager readout, counting the games as they finish.

It plays one game through the game's screens, sends 14 more through the same game API, then plays a second game while the readout is scrolled. It takes about 3 minutes and gives a video of about 100 seconds (gameplay at 2× speed). It also gives one real-time video per window.

## Run it

You need Docker, Node 18+, Python 3 and ffmpeg. From `deploy/local`:

```sh
docker compose stop        # the demo uses the same ports; your data is kept
MANAGER=<login> MANAGER_PASSWORD=<password> ./demo/demo.sh
docker compose up -d       # the real stack again
```

`MANAGER` is a readout login from `./init.sh <name>`.

The videos land in `deploy/local/demo/out/`.

## What it touches

- **A separate copy of the stack.** The demo runs as its own Docker project (`floor-tycoon-demo`) with its own empty database. The script deletes that copy, and its data, when it finishes. The real stack's database is never used.
- **Labelled test data.** Every game in the demo is labelled as a synthetic test play: `synthetic-demo-ui` for the two played through the screens, `synthetic-seed` for the 14 sent through the API. The readout shows that label.
