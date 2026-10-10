"""Stitch the three captured windows into one demo video, plus one video per window."""
import json
import subprocess
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
DEST = Path(sys.argv[2])
FONT = "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
FONT_R = "/usr/share/fonts/opentype/inter/Inter-Regular.otf"
timing = json.loads((OUT / "timing.json").read_text())
marks = {m: t for t, m in json.loads((OUT / "marks.json").read_text())}
shift = timing["t0"] - timing["start"]  # marks are from the script start, video from the first frame
at = lambda key: next(t for m, t in marks.items() if m.startswith(key)) + shift
total = timing["end"] - timing["start"]

g1, g1end = at("UI game 1: start"), at("UI game 1: shared")
burst, g2 = at("burst: "), at("UI game 2: start")
g2end = at("UI game 2: shared")
fast = [(g1 + 2, g1end - 1), (g2 + 2, g2end - 1)]  # played at 2x; the moments around them in real time

captions = [
    (0, g1, "Everything runs on one machine: game, backend, database and readout (docker compose, no cloud)."),
    (g1, g1end, "1 · A player plays on a phone. Each tap is sent to the backend and saved to the database (right)."),
    (g1end, burst, "The manager's readout (bottom left) refreshes: the game just played is counted."),
    (burst, g2, "2 · 14 more games arrive through the same game API, labelled as synthetic test plays."),
    (g2, g2end, "3 · A manager reads the slides while another game is played. Groups under 10 stay hidden."),
    (g2end, total + 1, "The readout now counts the second game too. Every number comes from the backend's database."),
]


def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,")


def run(args):
    print(" ".join(args[:6]), "...")
    subprocess.run(args, check=True)


# 1. each window as constant-rate video, all starting at the same moment
SIZES = {"game": (844, 390), "readout": (960, 636), "console": (960, 1080)}
for n, (w, h) in SIZES.items():  # the odd frame can arrive at another size: fit every one to its window
    fit = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(OUT / f"{n}.txt"),
         "-vf", f"{fit},fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(OUT / f"{n}.mp4")])

# 2. the three side by side, with labels and captions, in real time
chip = f"fontfile={FONT}:fontsize=20:fontcolor=white:box=1:boxcolor=0x111827CC:boxborderw=8"
labels = (f"drawtext={chip}:text='{esc('PLAYER · the game on a phone')}':x=12:y=12,"
          f"drawtext={chip}:text='{esc('MANAGER · Space Readout (signed in)')}':x=12:y=456,")
badges = "".join(f"drawtext={chip}:text='2× speed':x=960-tw-28:y=12:enable='between(t,{a:.2f},{b:.2f})'," for a, b in fast)
caps = "".join(
    f"drawtext=fontfile={FONT_R}:fontsize=26:fontcolor=white:x=(w-tw)/2:y=1080+(60-th)/2:"
    f"text='{esc(c)}':enable='between(t,{a:.2f},{b:.2f})',"
    for a, b, c in captions)
graph = (
    "[0:v]scale=960:444,setsar=1[g];[1:v]setsar=1[r];[2:v]setsar=1[c];"
    "[g][r]vstack[left];[left][c]hstack[top];"
    f"[top]pad=1920:1140:0:0:color=0x111827,{labels}{badges}{caps}"
    "drawbox=x=0:y=1080:w=1920:h=2:color=0x374151:t=fill[v]")
run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT / "game.mp4"), "-i", str(OUT / "readout.mp4"), "-i", str(OUT / "console.mp4"),
     "-filter_complex", graph, "-map", "[v]", "-t", f"{total:.2f}", "-r", "25", "-c:v", "libx264", "-preset", "medium",
     "-crf", "21", "-pix_fmt", "yuv420p", str(OUT / "realtime.mp4")])

# 3. speed up the long stretches of gameplay
cuts, t = [], 0.0
for a, b in fast:
    cuts += [(t, a, 1), (a, b, 2)]
    t = b
cuts.append((t, total, 1))
parts = "".join(f"[0:v]trim={a:.3f}:{b:.3f},setpts=(PTS-STARTPTS)/{k}[p{i}];" for i, (a, b, k) in enumerate(cuts))
graph = parts + "".join(f"[p{i}]" for i in range(len(cuts))) + f"concat=n={len(cuts)}:v=1:a=0,fps=25[v]"
DEST.mkdir(parents=True, exist_ok=True)
run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT / "realtime.mp4"), "-filter_complex", graph, "-map", "[v]",
     "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
     str(DEST / "floor-tycoon-demo.mp4")])
for n, name in (("game", "1-game"), ("console", "2-backend-console"), ("readout", "3-readout")):
    run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT / f"{n}.mp4"), "-c:v", "libx264", "-preset", "medium", "-crf", "22",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(DEST / f"{name}.mp4")])
print("speed-ups:", [(round(a), round(b)) for a, b in fast], "total real", round(total))
