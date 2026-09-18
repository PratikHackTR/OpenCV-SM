"""Separate capture and inference workers with one latest-frame slot each."""

from dataclasses import dataclass
import threading
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from .gestures import Gestures
from .models import ROOT, MODELS


class Latest:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = None

    def put(self, value):
        with self.lock:
            self.value = value

    def get(self):
        with self.lock:
            return self.value


@dataclass
class View:
    frame: object
    fps: float
    latency_ms: float
    inference_ms: float
    decision: object
    hands: int
    pose: bool
    capture_fps: float


class Pipeline:
    def __init__(self, camera, guard, width=640, height=480, fps=60):
        self.camera, self.guard = camera, guard
        self.width, self.height, self.fps = width, height, fps
        self.frames, self.views = Latest(), Latest()
        self.stop_event = threading.Event()
        self.recalibrate = threading.Event()
        self.error = ""
        self.status = "Kamera açılıyor…"
        self.threshold = 0.085
        self.capture_fps = 0.0
        self.actual = ""
        self.capture_thread = threading.Thread(target=self.capture, daemon=True, name="camera")
        self.infer_thread = threading.Thread(target=self.infer, daemon=True, name="landmarks")

    def start(self):
        missing = [name for name in MODELS if not (ROOT / "models" / name).exists()]
        if missing:
            raise FileNotFoundError("Modeller eksik. Önce setup.cmd çalıştırın: " + ", ".join(missing))
        self.capture_thread.start()
        self.infer_thread.start()

    def capture(self):
        cap = None
        try:
            cap = cv2.VideoCapture(self.camera.index, self.camera.backend)
            if not cap.isOpened():
                raise RuntimeError("Kamera açılamadı. Başka uygulama kullanıyor olabilir.")
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.actual = f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))} × {int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} · sürücü {cap.get(cv2.CAP_PROP_FPS):.0f} FPS"
            previous = time.monotonic()
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                now = time.monotonic()
                if not ok:
                    raise RuntimeError("Kamera akışı kesildi. Yenile ile kamerayı yeniden seçin.")
                self.capture_fps = 0.9 * self.capture_fps + 0.1 / max(now - previous, 0.001)
                previous = now
                self.frames.put((now, frame))
        except Exception as exc:
            self.error = str(exc)
            self.stop_event.set()
        finally:
            if cap is not None:
                cap.release()

    def infer(self):
        hand = pose = None
        try:
            hand = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(ROOT / "models/hand_landmarker.task")),
                running_mode=vision.RunningMode.VIDEO, num_hands=2,
                min_hand_detection_confidence=0.6, min_tracking_confidence=0.6))
            pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(ROOT / "models/pose_landmarker_lite.task")),
                running_mode=vision.RunningMode.VIDEO, num_poses=1,
                min_pose_detection_confidence=0.6, min_tracking_confidence=0.6))
            gestures = Gestures()
            last_stamp = last_timestamp_ms = 0
            previous = time.monotonic()
            fps = 0.0
            while not self.stop_event.is_set():
                item = self.frames.get()
                if item is None or item[0] == last_stamp:
                    self.stop_event.wait(0.002)
                    continue
                stamp, original = item
                last_stamp = stamp
                if time.monotonic() - stamp > 0.25:
                    continue
                if self.recalibrate.is_set():
                    gestures.reset()
                    self.recalibrate.clear()
                frame = cv2.flip(original, 1)
                if frame.shape[1] > 640:
                    frame = cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = max(int(stamp * 1000), last_timestamp_ms + 1)
                last_timestamp_ms = timestamp_ms
                started = time.monotonic()
                hands = hand.detect_for_video(image, timestamp_ms).hand_landmarks
                bodies = pose.detect_for_video(image, timestamp_ms).pose_landmarks
                inference_ms = (time.monotonic() - started) * 1000
                body = bodies[0] if bodies else []
                decision = gestures.update(hands, body, stamp, self.threshold)
                if self.stop_event.is_set():
                    break
                self.guard.publish(decision.swing, decision.jump, stamp)
                self.draw(frame, hands, body)
                now = time.monotonic()
                fps = 0.9 * fps + 0.1 / max(now - previous, 0.001)
                previous = now
                self.views.put(View(frame, fps, (now - stamp) * 1000, inference_ms,
                                    decision, len(hands), bool(body), self.capture_fps))
                self.status = "Canlı"
        except Exception as exc:
            self.error = str(exc)
            self.stop_event.set()
        finally:
            if hand is not None:
                hand.close()
            if pose is not None:
                pose.close()

    @staticmethod
    def draw(frame, hands, body):
        height, width = frame.shape[:2]

        def point(p):
            return int(p.x * width), int(p.y * height)

        for hand in hands:
            for finger in range(5):
                chain = [0] + list(range(finger * 4 + 1, finger * 4 + 5))
                for a, b in zip(chain, chain[1:]):
                    cv2.line(frame, point(hand[a]), point(hand[b]), (110, 220, 255), 2, cv2.LINE_AA)
            for p in hand:
                cv2.circle(frame, point(p), 3, (245, 245, 245), -1, cv2.LINE_AA)
        if body:
            for a, b in ((11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24)):
                if body[a].visibility > 0.6 and body[b].visibility > 0.6:
                    cv2.line(frame, point(body[a]), point(body[b]), (170, 210, 90), 2, cv2.LINE_AA)
            if body[0].visibility > 0.6:
                cv2.circle(frame, point(body[0]), 5, (170, 210, 90), -1)

    def stop(self):
        self.stop_event.set()
        self.guard.configure(False)

    def alive(self):
        return self.capture_thread.is_alive() or self.infer_thread.is_alive()
