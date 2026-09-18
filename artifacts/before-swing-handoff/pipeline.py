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
from .gestures import Decision
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
        self.tolerance = 0.018

        self.capture_fps = 0.0
        self.actual = ""
        camera_id = hashlib.sha256(str(getattr(camera, "path", camera.name)).encode()).hexdigest()[:12]
        self.profile_path = ROOT / "settings" / f"calibration-{camera_id}.json"
        self.profile = None
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
            reset_epoch = self.guard.reset_epoch
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
                if reset_epoch != self.guard.reset_epoch:
                    gestures.clear_transients()
                    reset_epoch = self.guard.reset_epoch
                decision = gestures.update(hands, body, stamp, self.threshold, labels)
                if self.stop_event.is_set():
                    break
                self.guard.publish_decision(Decision() if reset_epoch != self.guard.reset_epoch else decision, stamp)
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
        if "heart" in debug:
            hx,hy = debug["heart"]
            cv2.drawMarker(frame,(int(hx*width),int(hy*height)),(230,150,100),cv2.MARKER_DIAMOND,18,2)
        if "pointer" in debug:
            x,y = debug["pointer"]
            ax,ay = debug["pointer_anchor"]
            slop = debug["pointer_slop"]
            cv2.drawMarker(frame, (int(x*width),int(y*height)), (100,240,255), cv2.MARKER_CROSS,24,2)
            cv2.rectangle(frame,(int((ax-slop)*width),int((ay-slop)*height)),
                          (int((ax+slop)*width),int((ay+slop)*height)),(80,230,150),1)
        Pipeline.draw_movement(frame, decision)
        if decision:
            cv2.rectangle(frame, (0, 0), (width, 58), (18, 22, 30), -1)
            cv2.putText(frame, f"SWING {int(decision.swing)}   AIM {int(decision.aim)}   HANDS {len(hands)}/2",
                        (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 240, 240), 1, cv2.LINE_AA)
            cv2.putText(frame, f"W {int(decision.forward)} E {int(decision.gadget)} F {int(decision.web_attack)}   MOUSE {decision.mouse_dx:+.2f}/{decision.mouse_dy:+.2f} SW {decision.look_x:+.2f}",
                        (10, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (110, 230, 220), 1, cv2.LINE_AA)

    @staticmethod
    def draw_movement(frame, decision):
        if decision is None:
            return
        height,width = frame.shape[:2]
        debug = decision.debug
        if decision.swing or decision.swing_exit:
            cv2.putText(frame,"SWING: WASD KAPALI",(12,height-16),cv2.FONT_HERSHEY_SIMPLEX,.55,(100,210,255),2)
            return
        if "move_anchor" not in debug:
            cv2.putText(frame,"WASD: ACIK AVUC ILE MERKEZ BELIRLE",(12,height-16),cv2.FONT_HERSHEY_SIMPLEX,.48,(170,190,200),1)
            return
        ax,ay = debug["move_anchor"]
        hx,hy = debug["move_hand"]
        threshold = debug["move_threshold"]
        x1,x2 = int((ax-threshold)*width),int((ax+threshold)*width)
        y1,y2 = int((ay-threshold)*height),int((ay+threshold)*height)
        neutral = (110,190,210)
        active = (90,255,110)
        for x in (x1,x2):
            cv2.line(frame,(x,58),(x,height-34),neutral,1)
        for y in (y1,y2):
            cv2.line(frame,(0,y),(width-1,y),neutral,1)
        cv2.rectangle(frame,(x1,y1),(x2,y2),neutral,2)
        cv2.drawMarker(frame,(int(ax*width),int(ay*height)),neutral,cv2.MARKER_CROSS,16,2)
        cv2.line(frame,(int(ax*width),int(ay*height)),(int(hx*width),int(hy*height)),(255,230,110),2)
        cv2.circle(frame,(int(hx*width),int(hy*height)),7,(255,230,110),-1)
        labels = [("W",int(ax*width),y1-14,decision.forward),
                  ("S",int(ax*width),y2+27,decision.backward),
                  ("A",x1-28,int(ay*height),decision.steer<0),
                  ("D",x2+12,int(ay*height),decision.steer>0)]
        for text,x,y,on in labels:
            cv2.putText(frame,text,(max(4,min(width-30,x)),max(78,min(height-40,y))),
                        cv2.FONT_HERSHEY_SIMPLEX,.85,active if on else neutral,2)
        owner = debug.get("move_owner","?")
        dx,dy = debug.get("move_offset",(0,0))
        cv2.putText(frame,f"ACIK EL {owner} | dx {dx:+.2f} dy {dy:+.2f} | ESİK 0.22".replace("İ","I"),
                    (12,height-16),cv2.FONT_HERSHEY_SIMPLEX,.45,neutral,1)

    def stop(self):
        self.stop_event.set()
        self.guard.configure(False)

    def alive(self):
        return self.capture_thread.is_alive() or self.infer_thread.is_alive()
