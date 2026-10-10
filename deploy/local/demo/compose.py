"""Stitch the three captured windows into one demo video, plus one video per window."""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(sys.argv[1])
DEST = Path(sys.argv[2])
SPEED = 4  # gameplay speed-up in the combined video
FONTS = ["/usr/share/fonts/opentype/inter/Inter-SemiBold.otf", "/System/Library/Fonts/HelveticaNeue.ttc",
         "/System/Library/Fonts/Helvetica.ttc", "DejaVuSans.ttf"]
timing = json.loads((OUT / "timing.json").read_text())
marks = {m: t for t, m in json.loads((OUT / "marks.json").read_text())}
shift = timing["t0"] - timing["start"]  # marks are from the script start, video from the first frame
at = lambda key: next(t for m, t in marks.items() if m.startswith(key)) + shift
total = timing["end"] - timing["start"]

g1, g1end = at("UI game 1: start"), at("UI game 1: shared")
burst, g2 = at("burst: "), at("UI game 2: start")
g2end = at("UI game 2: shared")
fast = [(g1 + 2, g1end - 1), (g2 + 2, g2end - 1)]  # played at SPEED x; the moments around them in real time

captions = [
    (0, g1, "Everything runs on one machine: game, backend, database and readout (docker compose, no cloud)."),
    (g1, g1end, "1 · A player plays on a phone. Each tap is sent to the backend and saved to the database (right)."),
    (g1end, burst, "The manager's readout (bottom left) refreshes: the game just played is counted."),
    (burst, g2, "2 · 14 more games arrive through the same game API, labelled as synthetic test plays."),
    (g2, g2end, "3 · A manager reads the slides while another game is played. Groups under 10 stay hidden."),
    (g2end, total + 1, "The readout now counts the second game too. Every number comes from the backend's database."),
]


def font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def text_png(name, text, size, box):
    """Text on a dark box as a PNG, overlaid by ffmpeg (no drawtext needed, so any ffmpeg build works)."""
    f = font(size)
    x0, y0, x1, y1 = f.getbbox(text)
    img = Image.new("RGBA", (x1 - x0 + 2 * box, y1 - y0 + 2 * box), (17, 24, 39, 214))
    ImageDraw.Draw(img).text((box - x0, box - y0), text, font=f, fill="white")
    path = OUT / f"text-{name}.png"
    img.save(path)
    return path


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
on = lambda a, b: f":enable='between(t,{a:.2f},{b:.2f})'"
texts = [(text_png("player", "PLAYER · the game on a phone", 20, 8), "12", "12", ""),
         (text_png("manager", "MANAGER · Space Readout (signed in)", 20, 8), "12", "456", "")]
texts += [(text_png(f"fast{i}", f"{SPEED}× speed", 20, 8), "960-w-28", "12", on(a, b)) for i, (a, b) in enumerate(fast)]
texts += [(text_png(f"cap{i}", c, 26, 14), "(W-w)/2", "1080+(60-h)/2", on(a, b)) for i, (a, b, c) in enumerate(captions)]
graph = ("[0:v]scale=960:444,setsar=1[g];[1:v]setsar=1[r];[2:v]setsar=1[c];"
         "[g][r]vstack[left];[left][c]hstack[top];"
         "[top]pad=1920:1140:0:0:color=0x111827,drawbox=x=0:y=1080:w=1920:h=2:color=0x374151:t=fill[o0]")
for i, (_, x, y, when) in enumerate(texts):
    graph += f";[o{i}][{i + 3}:v]overlay=x={x}:y={y}{when}[o{i + 1}]"
inputs = [a for n in ("game", "readout", "console") for a in ("-i", str(OUT / f"{n}.mp4"))]
inputs += [a for p, *_ in texts for a in ("-i", str(p))]
run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", f"[o{len(texts)}]", "-t", f"{total:.2f}",
     "-r", "25", "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", str(OUT / "realtime.mp4")])

# 3. speed up the long stretches of gameplay
cuts, t = [], 0.0
for a, b in fast:
    cuts += [(t, a, 1), (a, b, SPEED)]
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
