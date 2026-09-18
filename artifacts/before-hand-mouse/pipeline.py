"""Separate capture and inference workers with one latest-frame slot each."""

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import logging
import hashlib
import queue
import threading
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from .gestures import Gestures
from .models import ROOT, MODELS
from .calibration import GuidedCalibration, observe, load_profile, save_profile
from .gestures import Decision
from .regions import load_regions
from .cameras import IPCamera, open_camera


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
        self.tolerance = 0.05

        self.capture_fps = 0.0
        self.actual = ""
        camera_id = hashlib.sha256(str(getattr(camera, "path", camera.name)).encode()).hexdigest()[:12]
        self.profile_path = ROOT / "settings" / f"calibration-{camera_id}.json"
        self.profile = load_profile(self.profile_path)
        self.regions_path = ROOT / "settings" / f"regions-{camera_id}.json"
        self.regions = load_regions(self.regions_path)
        self.calibration_commands = queue.Queue()
        self.calibration_state = Latest()
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
            cap = open_camera(self.camera, self.width, self.height, self.fps)
            if not cap.isOpened():
                if isinstance(self.camera, IPCamera):
                    raise RuntimeError("IPCam açılamadı. Yayın adresini, kamera uygulamasını ve ağ bağlantısını kontrol edin.")
                raise RuntimeError("Kamera açılamadı. Başka uygulama kullanıyor olabilir.")
            self.actual = f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))} × {int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} · sürücü {cap.get(cv2.CAP_PROP_FPS):.0f} FPS"
            previous = time.monotonic()
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                now = time.monotonic()
                if not ok:
                    if isinstance(self.camera, IPCamera):
                        raise RuntimeError("IPCam yayını kesildi veya yanıt vermiyor. Bağlantıyı kontrol edip yeniden başlatın.")
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
        workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="inference")
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
            if self.profile:
                gestures.apply_profile(self.profile)
            calibration = GuidedCalibration()
            saved_profile = None
            last_stamp = last_timestamp_ms = 0
            previous = time.monotonic()
            fps = 0.0
            last_report = 0.0
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
                while not self.calibration_commands.empty():
                    command = self.calibration_commands.get_nowait()
                    if command == "start":
                        calibration.start()
                    elif command == "sample":
                        calibration.begin_sample(time.monotonic())
                    elif command == "cancel" and calibration.active:
                        calibration.active = False
                        calibration.capture_at = None
                        if self.profile:
                            gestures.apply_profile(self.profile)
                        else:
                            gestures.reset()
                frame = cv2.flip(original, 1)
                if frame.shape[1] > 640:
                    frame = cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp_ms = max(int(stamp * 1000), last_timestamp_ms + 1)
                last_timestamp_ms = timestamp_ms
                started = time.monotonic()
                hand_future = workers.submit(hand.detect_for_video, image, timestamp_ms)
                pose_future = workers.submit(pose.detect_for_video, image, timestamp_ms)
                hand_result = hand_future.result()
                hands = hand_result.hand_landmarks
                bodies = pose_future.result().pose_landmarks
                inference_ms = (time.monotonic() - started) * 1000
                body = bodies[0] if bodies else []
                labels = [items[0].category_name for items in hand_result.handedness]
                gestures.tolerance = self.tolerance
                if gestures.regions is not self.regions:
                    gestures.clear_transients()
                    gestures.regions = self.regions
                decision = gestures.update(hands, body, stamp, self.threshold, labels)
                calibration.update(observe(hands, body), stamp)
                if calibration.profile is not None and calibration.profile is not saved_profile:
                    self.profile = calibration.profile
                    gestures.apply_profile(self.profile)
                    saved_profile = self.profile
                    try:
                        save_profile(self.profile_path, self.profile)
                        calibration.message = "Profil kaydedildi. Önce nötr duruşa dön; sonra kontrolleri etkinleştir."
                    except OSError as exc:
                        calibration.message = f"Profil bu oturumda hazır, diske yazılamadı: {exc}"
                        logging.exception("Calibration profile write failed")
                self.calibration_state.put(calibration.state())
                if self.stop_event.is_set():
                    break
                self.guard.publish_decision(Decision() if calibration.active else decision, stamp)
                self.draw(frame, hands, body, decision)
                now = time.monotonic()
                fps = 0.9 * fps + 0.1 / max(now - previous, 0.001)
                previous = now
                self.views.put(View(frame, fps, (now - stamp) * 1000, inference_ms,
                                    decision, len(hands), bool(body), self.capture_fps))
                self.status = "Canlı"
                if now - last_report > 3:
                    logging.info("Tracking camera_fps=%.1f inference_fps=%.1f model_ms=%.1f age_ms=%.1f hands=%d pose=%s mode=%s",
                                 self.capture_fps, fps, inference_ms, (now - stamp) * 1000, len(hands), bool(body), decision.mode)
                    last_report = now
        except Exception as exc:
            self.error = str(exc)
            self.stop_event.set()
        finally:
            workers.shutdown(wait=True, cancel_futures=True)
            if hand is not None:
                hand.close()
            if pose is not None:
                pose.close()

    @staticmethod
    def draw(frame, hands, body, decision=None):
        height, width = frame.shape[:2]

        def point(p):
            return int(p.x * width), int(p.y * height)

        debug = decision.debug if decision else {}
        if "aim_zone" in debug:
            top, bottom = [max(0, min(height - 1, int(y * height))) for y in debug["aim_zone"]]
            cv2.rectangle(frame, (8, top), (width - 8, bottom), (200, 160, 60), 1)
            cv2.putText(frame, "AIM REGION", (12, min(height - 8, top + 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 80), 1)
        if "shoulder_y" in debug:
            sy = max(0, min(height - 1, int(debug["shoulder_y"] * height)))
            cv2.line(frame, (0, sy), (width - 1, sy), (80, 80, 240), 1)
        for index, hand in enumerate(hands):
            for finger in range(5):
                chain = [0] + list(range(finger * 4 + 1, finger * 4 + 5))
                for a, b in zip(chain, chain[1:]):
                    cv2.line(frame, point(hand[a]), point(hand[b]), (110, 220, 255), 2, cv2.LINE_AA)
            for p in hand:
                cv2.circle(frame, point(p), 3, (245, 245, 245), -1, cv2.LINE_AA)
            shape = debug.get("shapes", ["?"] * len(hands))[index]
            x, y = point(hand[0])
            cv2.putText(frame, f"H{index + 1}: {shape.upper()}", (max(2, min(x, width - 140)), max(20, min(y, height - 8))),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 220, 100), 2, cv2.LINE_AA)
        if body:
            for a, b in ((11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24)):
                if body[a].visibility > 0.6 and body[b].visibility > 0.6:
                    cv2.line(frame, point(body[a]), point(body[b]), (170, 210, 90), 2, cv2.LINE_AA)
            if body[0].visibility > 0.6:
                cv2.circle(frame, point(body[0]), 5, (170, 210, 90), -1)
            for i in (2, 5, 7, 8):
                if body[i].visibility > 0.5:
                    cv2.circle(frame, point(body[i]), 3, (200, 150, 255), -1)
        for name, rect in debug.get("regions", {}).items():
            x,y,u,v = rect
            color = (80, 240, 100) if debug.get("active_region") == name or name == "aim" and decision.aim else (100, 180, 240)
            cv2.rectangle(frame, (int(x*width),int(y*height)), (int(u*width),int(v*height)), color, 2)
            cv2.putText(frame, {"left":"A", "right":"D", "forward":"W", "aim":"AIM"}[name],
                        (int(x*width)+4,int(y*height)+22), cv2.FONT_HERSHEY_SIMPLEX,.6,color,2)
        if decision:
            cv2.rectangle(frame, (0, 0), (width, 58), (18, 22, 30), -1)
            cv2.putText(frame, f"SWING {int(decision.swing)}   AIM {int(decision.aim)}   HANDS {len(hands)}/2",
                        (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 240, 240), 1, cv2.LINE_AA)
            cv2.putText(frame, f"A/D {decision.steer:+.0f} W {int(decision.forward)}   HEAD {decision.look_x:+.2f} / {decision.look_y:+.2f}",
                        (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (110, 230, 220), 1, cv2.LINE_AA)

    def stop(self):
        self.stop_event.set()
        self.guard.configure(False)

    def alive(self):
        return self.capture_thread.is_alive() or self.infer_thread.is_alive()
