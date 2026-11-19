#!/usr/bin/env python3
"""Turn the 11-09 annotation-only units into video units: question + frames, no annotations.

Every model gets the same cached frames for a question, so the comparison is fair.
Reads the text-only units (units/<dataset>_<type>.jsonl from the 11-09 run, which carry
`meta` and `ground_truth`) and writes:

  <out>/video_units/<dataset>_<type>.jsonl   one record per question (see VideoUnit below)
  <out>/frames/<unit_id>/<i>_<t>.jpg          the sampled frames, longest side <= --max-side

Which stretch of video each question gets (the "window"):
  JIGSAWS VTS                 the whole recording (the question asks where in it)
  JIGSAWS D1/D2, MB140 D*      the span the question names ("during 0:01-1:29")
  JIGSAWS D5                  the whole recording ("this trainee's performance")
  MB140 B1, B4                the event span with 30 s either side
  MB140 B5, B6                a 180 s window (longer if the event is) holding the whole event,
                              its start placed at a seeded random offset so the answer time
                              cannot be read off the window's position
  MB140 B3                    the whole operation (the question ranks the video's risks)
  Endoscapes B1/B2/B3/B7      the 30 s up to and including the keyframe the question names
  Endoscapes B5               20 s either side of the keyframe

Frames: 1 per second inside the window; if that is more than --max-frames, --max-frames
spread evenly. Each frame keeps its time in the recording's own clock, which the prompt
shows next to it, so a model can answer with times in the question's clock.

python3 -I scripts/zs_video/prep_video_units.py --units units/*.jsonl --out . \
    --jigsaws-root /mnt/sun/shared/datasets/surgical_skill/JIGSAWS \
    --mb140-root /mnt/sun/shared/datasets/surgical_skill/MultiBypass140 \
    --endoscapes-root /mnt/sun/shared/datasets/surgical_skill/Endoscapes2023
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

SEED = 20261117
ENDOSCAPES_FPS = 25  # frame numbers in Endoscapes file names count 25 fps frames
VIDEO_EXT = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".m4v", ".mpg", ".mpeg"}
CONTEXT = {
    "JIGSAWS": "This is a recording of the JIGSAWS {task} task, performed with a da Vinci "
               "surgical robot on a bench-top training model.",
    "MultiBypass140": "This is a recording of a laparoscopic Roux-en-Y gastric bypass.",
    "Endoscapes2023": "This is a recording of a laparoscopic cholecystectomy.",
}
TIME = r"\d+:\d{2}(?::\d{2})?(?:\.\d+)?"


# --------------------------------------------------------------------------- time
def to_seconds(label: str) -> float:
    parts = [float(p) for p in label.split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def clock(t: float, hours: bool) -> str:
    """The recording's clock: h:mm:ss when the question uses hours, else m:ss (minutes may
    pass 59, as in the MB140 B questions). Tenths only when the time is not whole."""
    t = round(t, 1)
    whole = abs(t - round(t)) < 1e-6
    sec = int(round(t)) if whole else t
    if hours:
        h, rem = divmod(int(t), 3600)
        m, s = divmod(rem, 60)
        frac = "" if whole else f"{t - int(t):.1f}"[1:]
        return f"{h}:{m:02d}:{s:02d}{frac}"
    m, s = divmod(sec, 60)
    return f"{int(m)}:{int(s):02d}" if whole else f"{int(m)}:{s:04.1f}"


def span_in_question(question: str) -> tuple[float, float] | None:
    m = re.search(rf"({TIME})\s*[-–]\s*({TIME})", question)
    return (to_seconds(m.group(1)), to_seconds(m.group(2))) if m else None


# --------------------------------------------------------------------------- sources
class Sources:
    def __init__(self, args):
        self.jigsaws = Path(args.jigsaws_root)
        self.mb140 = Path(args.mb140_root)
        self.endoscapes = Path(args.endoscapes_root)
        self.cache = Path(args.out) / "video_index.json"
        self._mb_index: dict[str, str] | None = None
        self._es_dirs: dict[str, dict[str, list[int]]] | None = None

    # JIGSAWS: <root>/<Task>/video/<trial>_capture1.avi (left camera)
    def jigsaws_video(self, task: str, trial: str) -> Path:
        for cand in (self.jigsaws / task / "video" / f"{trial}_capture1.avi",
                     self.jigsaws / task / "videos" / f"{trial}_capture1.avi"):
            if cand.exists():
                return cand
        hits = sorted(self.jigsaws.glob(f"{task}/**/{trial}_capture1.*"))
        if not hits:
            raise FileNotFoundError(f"no JIGSAWS video for {trial} under {self.jigsaws / task}")
        return hits[0]

    # MultiBypass140: walk once for video files, keyed by stem (e.g. BBP30, SBP01)
    def mb140_video(self, video_id: str) -> Path:
        if self._mb_index is None:
            index = json.loads(self.cache.read_text()) if self.cache.exists() else {}
            if not index:
                for dirpath, dirnames, filenames in os.walk(self.mb140):
                    # skip extracted-frame folders (thousands of jpgs, no videos) and .git
                    dirnames[:] = [d for d in dirnames
                                   if not d.lower().startswith(("frame", "image", "."))]
                    for f in filenames:
                        p = Path(dirpath) / f
                        if p.suffix.lower() in VIDEO_EXT:
                            index.setdefault(p.stem, str(p))
                self.cache.write_text(json.dumps(index, indent=1, sort_keys=True))
            self._mb_index = index
        for key in (video_id, video_id.upper(), video_id.lower()):
            if key in self._mb_index:
                return Path(self._mb_index[key])
        raise FileNotFoundError(f"no MultiBypass140 video {video_id!r} under {self.mb140} "
                                f"({len(self._mb_index)} videos indexed in {self.cache})")

    # Endoscapes2023: 1 fps jpgs <root>/<split>/<vid>_<frame>.jpg, frame at 25 fps
    def endoscapes_frames(self, video_id: str) -> dict[int, Path]:
        if self._es_dirs is None:
            self._es_dirs = {}
            for d in sorted(p for p in self.endoscapes.iterdir() if p.is_dir()):
                by_vid: dict[str, list[int]] = {}
                for name in os.listdir(d):
                    m = re.fullmatch(r"(\d+)_(\d+)\.jpg", name)
                    if m:
                        by_vid.setdefault(m.group(1), []).append(int(m.group(2)))
                if by_vid:
                    self._es_dirs[str(d)] = by_vid
        out: dict[int, Path] = {}
        for d, by_vid in self._es_dirs.items():
            for fr in by_vid.get(str(video_id), []):
                out.setdefault(fr, Path(d) / f"{video_id}_{fr}.jpg")
        if not out:
            raise FileNotFoundError(f"no Endoscapes frames for video {video_id} under {self.endoscapes}")
        return out


def video_duration(path: Path) -> float:
    try:
        import cv2  # noqa: PLC0415
        cap = cv2.VideoCapture(str(path))
        n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if n > 0 and fps > 0:
            return n / fps
    except ImportError:
        pass
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- windows
def window_for(u: dict, src: Sources, rng: random.Random) -> dict:
    ds, tid, meta, q = u["dataset"], u["template_id"], u.get("meta", {}), u["question"]
    if ds == "JIGSAWS":
        trial = meta["trial_id"]
        # the D units spell the task "Knot Tying"; folders and trial ids use "Knot_Tying"
        task = (meta.get("task") or trial.rsplit("_", 1)[0]).replace(" ", "_")
        path = src.jigsaws_video(task, trial)
        dur = video_duration(path)
        span = span_in_question(q) if tid != "VTS" else None
        if span:
            a, b, rule = span[0], span[1], "the span the question names"
        else:
            a, b, rule = 0.0, dur, "the whole recording"
        return {"kind": "video", "path": str(path), "start_s": a, "end_s": min(b, dur),
                "duration_s": dur, "rule": rule, "hours": False,
                "context": CONTEXT[ds].format(task=task.replace("_", " "))}

    if ds == "MultiBypass140":
        path = src.mb140_video(meta["video_id"])
        dur = video_duration(path)
        if tid.startswith("D"):
            # The question's own span. Not meta start_s/end_s: for D5 those hold the phase
            # that went best (the answer), while the question asks about the whole operation.
            span = span_in_question(q)
            a, b = span if span else (meta["start_s"], meta["end_s"])
            return {"kind": "video", "path": str(path), "start_s": float(a), "end_s": float(min(b, dur)),
                    "duration_s": dur, "rule": "the span the question names", "hours": True,
                    "context": CONTEXT[ds]}
        if tid == "B3":
            return {"kind": "video", "path": str(path), "start_s": 0.0, "end_s": dur, "duration_s": dur,
                    "rule": "the whole operation", "hours": False, "context": CONTEXT[ds]}
        label = meta["timestamp_or_span"]
        times = [to_seconds(t) for t in re.findall(TIME, label)]
        s, e = times[0], times[-1]
        if tid in ("B1", "B4"):
            a, b, rule = s - 30, e + 30, "the event with 30 s either side"
        else:  # B5, B6: the model must find the event, so hide it at a random offset
            length = max(180.0, (e - s) + 60.0)
            lead = rng.uniform(30.0, length - (e - s) - 30.0)
            a, b, rule = s - lead, s - lead + length, "a 180 s window holding the event at a random offset"
        a, b = shift_into(a, b, dur)
        return {"kind": "video", "path": str(path), "start_s": a, "end_s": b, "duration_s": dur,
                "rule": rule, "hours": False, "context": CONTEXT[ds]}

    if ds == "Endoscapes2023":
        vid = str(meta["video_id"])
        frame = int(meta.get("manifest_item", {}).get("frame") or meta["source_annotation_detail"]["frame"])
        frames = src.endoscapes_frames(vid)
        t = frame / ENDOSCAPES_FPS
        dur = max(frames) / ENDOSCAPES_FPS
        if tid == "B5":
            a, b, rule = t - 20, t + 20, "20 s either side of the keyframe"
        else:
            a, b, rule = t - 30, t, "the 30 s up to and including the keyframe"
        a, b = shift_into(a, b, dur)
        return {"kind": "frames", "video_id": vid, "start_s": a, "end_s": b, "duration_s": dur,
                "keyframe": frame, "rule": rule, "hours": False, "context": CONTEXT[ds]}
    raise ValueError(f"unknown dataset {ds}")


def shift_into(a: float, b: float, dur: float) -> tuple[float, float]:
    if a < 0:
        a, b = 0.0, b - a
    if b > dur:
        a, b = max(0.0, a - (b - dur)), dur
    return a, b


def sample_times(a: float, b: float, fps: float, cap: int) -> list[float]:
    n = int(math.floor((b - a) * fps)) + 1
    if n <= cap:
        return [round(a + i / fps, 1) for i in range(n) if a + i / fps <= b + 1e-6]
    step = (b - a) / cap
    return [round(a + (i + 0.5) * step, 1) for i in range(cap)]


# --------------------------------------------------------------------------- frames
def have_cv2() -> bool:
    try:
        import cv2  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


def resize_and_write(img, out: Path, max_side: int) -> tuple[int, int]:
    import cv2  # noqa: PLC0415
    h, w = img.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return img.shape[1], img.shape[0]


def ffmpeg_frame(src: str, t: float | None, out: Path, max_side: int) -> bool:
    """Fallback without OpenCV: one frame at t (or a still image when t is None)."""
    scale = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    # one decoder thread: several workers run at once on a shared node
    cmd = ["ffmpeg", "-v", "error", "-y", "-threads", "1"] + \
          (["-ss", f"{t:.3f}"] if t is not None else []) + \
          ["-i", src, "-frames:v", "1", "-vf", scale, "-q:v", "3", "-threads", "1", str(out)]
    return subprocess.run(cmd, capture_output=True).returncode == 0 and out.exists()


def extract(job: dict) -> dict:
    """Write one unit's frames; returns the frame list. Runs in a worker process."""
    cv = have_cv2()
    if cv:
        import cv2  # noqa: PLC0415
    w = job["window"]
    out_dir = Path(job["frames_dir"])
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    frames, missing = [], []
    if w["kind"] == "video":
        cap = cv2.VideoCapture(w["path"]) if cv else None
        fps = cap.get(cv2.CAP_PROP_FPS) if cv else None
        for i, t in enumerate(job["times"]):
            p = out_dir / f"{i:03d}_{t:.1f}.jpg"
            if cv:
                # seek by frame number: more reliable than POS_MSEC across codecs
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
                ok, img = cap.read()
                if not ok:
                    missing.append(t)
                    continue
                size = resize_and_write(img, p, job["max_side"])
            else:
                if not ffmpeg_frame(w["path"], t, p, job["max_side"]):
                    missing.append(t)
                    continue
                size = None
            frames.append({"t": t, "path": str(p), "size": size})
        if cap is not None:
            cap.release()
    else:
        avail = job["endoscapes_frames"]
        nums = sorted(int(k) for k in avail)
        used: set[int] = set()
        for i, t in enumerate(job["times"]):
            want = round(t * ENDOSCAPES_FPS)
            near = min(nums, key=lambda n: abs(n - want))
            if abs(near - want) > ENDOSCAPES_FPS // 2 or near in used:
                missing.append(t)
                continue
            used.add(near)
            t_real = round(near / ENDOSCAPES_FPS, 1)
            p = out_dir / f"{i:03d}_{t_real:.1f}.jpg"
            if cv:
                img = cv2.imread(avail[str(near)])
                if img is None:
                    missing.append(t)
                    continue
                size = resize_and_write(img, p, job["max_side"])
            else:
                if not ffmpeg_frame(avail[str(near)], None, p, job["max_side"]):
                    missing.append(t)
                    continue
                size = None
            frames.append({"t": t_real, "path": str(p), "size": size})
    return {"unit_id": job["unit_id"], "frames": frames, "missing": missing}


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--units", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jigsaws-root", default="/mnt/sun/shared/datasets/surgical_skill/JIGSAWS")
    ap.add_argument("--mb140-root", default="/mnt/sun/shared/datasets/surgical_skill/MultiBypass140")
    ap.add_argument("--endoscapes-root", default="/mnt/sun/shared/datasets/surgical_skill/Endoscapes2023")
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--max-frames", type=int, default=64)
    ap.add_argument("--max-side", type=int, default=640)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only", nargs="*", default=None, help="unit ids to (re)build")
    ap.add_argument("--limit-per-type", type=int, default=None, help="first N per file (smoke tests)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "video_units").mkdir(exist_ok=True)
    src = Sources(args)

    per_file: dict[str, list[dict]] = {}
    jobs = []
    for path in args.units:
        units = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if args.limit_per_type:
            units = units[: args.limit_per_type]
        for u in units:
            if args.only and u["unit_id"] not in args.only:
                continue
            rng = random.Random(f"{SEED}-{u['unit_id']}")
            try:
                w = window_for(u, src, rng)
            except (FileNotFoundError, KeyError, ValueError, TypeError) as exc:
                print(f"SKIP {u['unit_id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            times = sample_times(w["start_s"], w["end_s"], args.fps, args.max_frames)
            vu = {
                "unit_id": u["unit_id"], "dataset": u["dataset"], "template_id": u["template_id"],
                "question": u["question"], "context": w.pop("context"), "window": w,
                "sampling": {"fps": args.fps, "max_frames": args.max_frames, "max_side": args.max_side},
                "ground_truth": u["ground_truth"],
                "text_only_meta": {k: v for k, v in u.get("meta", {}).items()
                                   if k in ("trial_id", "task", "video_id", "center", "phase", "start_s",
                                            "end_s", "answer_span", "timestamp_or_span", "question_kind",
                                            "recording_length_s", "selection_id")},
            }
            per_file.setdefault(path.stem, []).append(vu)
            job = {"unit_id": u["unit_id"], "window": w, "times": times, "max_side": args.max_side,
                   "frames_dir": str(args.out / "frames" / u["unit_id"])}
            if w["kind"] == "frames":
                job["endoscapes_frames"] = {str(k): str(v) for k, v in
                                            src.endoscapes_frames(w["video_id"]).items()}
            jobs.append(job)

    print(f"{len(jobs)} units to extract", flush=True)
    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for i, r in enumerate(ex.map(extract, jobs), 1):
            results[r["unit_id"]] = r
            if i % 25 == 0 or i == len(jobs):
                print(f"  extracted {i}/{len(jobs)}", flush=True)

    for stem, vus in per_file.items():
        out = args.out / "video_units" / f"{stem}.jsonl"
        kept = 0
        with out.open("w", encoding="utf-8") as fh:
            for vu in vus:
                r = results[vu["unit_id"]]
                if not r["frames"]:
                    print(f"SKIP {vu['unit_id']}: no frame could be read", file=sys.stderr)
                    continue
                hours = vu["window"]["hours"]
                vu["frames"] = [{**f, "label": clock(f["t"], hours)} for f in r["frames"]]
                vu["frames_missing"] = r["missing"]
                fh.write(json.dumps(vu, ensure_ascii=False) + "\n")
                kept += 1
        print(f"{out}: {kept} of {len(vus)} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
