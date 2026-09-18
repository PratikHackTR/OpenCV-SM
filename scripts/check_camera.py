"""Bounded camera/inference check. Frames stay in memory; input stays disabled."""

import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
from cv2_enumerate_cameras import enumerate_cameras
from webmotion.keyboard import InputGuard
from webmotion.pipeline import Pipeline


def main():
    cameras = list(enumerate_cameras(cv2.CAP_DSHOW))
    if not cameras:
        print("No camera available")
        return
    cv2.setNumThreads(1)
    guard = InputGuard()
    guard.start()
    pipeline = Pipeline(cameras[0], guard)
    seen = []
    last = None
    try:
        pipeline.start()
        until = time.monotonic() + 8
        while time.monotonic() < until and not pipeline.error:
            view = pipeline.views.get()
            if view is not None and view is not last:
                seen.append((view.fps, view.latency_ms, view.inference_ms))
                last = view
            time.sleep(0.04)
    finally:
        pipeline.stop()
        guard.close()
        pipeline.infer_thread.join(timeout=2)
        pipeline.capture_thread.join(timeout=1)
    result = {"camera": cameras[0].name, "processed_frames": len(seen), "format": pipeline.actual,
              "error": pipeline.error, "input_enabled": guard.enabled,
              "worker_stopped": not pipeline.alive()}
    if last:
        result.update(inference_fps=round(last.fps, 1), capture_fps=round(last.capture_fps, 1),
                      latency_ms=round(last.latency_ms, 1), detected_hands=last.hands, detected_pose=last.pose)
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts/camera-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
