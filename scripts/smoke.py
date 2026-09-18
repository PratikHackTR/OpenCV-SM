"""Local startup checks; no camera capture and no keyboard injection."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import cv2
import mediapipe as mp
import numpy as np
from cv2_enumerate_cameras import enumerate_cameras
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QTabWidget

from webmotion.app import Window, STYLE
from webmotion.keyboard import WindowsInput, INPUT
from webmotion.pipeline import Pipeline, View
from webmotion.gestures import Decision
from webmotion.calibration import GuidedCalibration
import ctypes


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    devices = list(enumerate_cameras(cv2.CAP_DSHOW))
    result = {"camera_names": [d.name for d in devices], "input_structure_size": ctypes.sizeof(INPUT),
              "foreground_readable": bool(WindowsInput().foreground())}
    assert ctypes.sizeof(INPUT) == 40
    blank = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((480, 640, 3), dtype=np.uint8))
    with vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(ROOT / "models/hand_landmarker.task")),
        running_mode=vision.RunningMode.VIDEO)) as model:
        result["blank_hands"] = len(model.detect_for_video(blank, 1).hand_landmarks)
    with vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(ROOT / "models/pose_landmarker_lite.task")),
        running_mode=vision.RunningMode.VIDEO)) as model:
        result["blank_poses"] = len(model.detect_for_video(blank, 1).pose_landmarks)
    app = QApplication([])
    for name in ("segoeui.ttf", "segoeuib.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
    app.setStyleSheet(STYLE)
    window = Window()
    window.resize(1440, 900)
    window.show()

    def finish():
        result["ui_camera_count"] = window.cameras.count()
        window.cameras.setCurrentIndex(window.cameras.findData("ipcam"))
        assert window.ipcam_url.isVisible()
        window.ipcam_url.setText("http://192.168.1.3:8080/video")
        window.scan_results.put(([], None))
        window.tick()
        assert window.cameras.currentData() == "ipcam"
        assert window.ipcam_url.text() == "http://192.168.1.3:8080/video"
        assert window.cameras.count() == 1
        window.ipcam_url.setText("invalid URL")
        window.toggle_camera()
        assert window.pipeline is None
        assert "Geçerli" in window.camera_status.text()
        window.ipcam_url.setText("http://192.168.1.3:8080/video")
        result["ipcam_selection_refresh_validation"] = True
        window.camera_status.setText("IPCam kullanılabilir")
        assert not window.guard.enabled
        assert not window.guard.held
        window.grab().save(str(artifacts / "ui-preview.png"))
        tabs = window.findChild(QTabWidget)
        tabs.setCurrentIndex(1)
        app.processEvents()
        assert window.motion_tolerance.isVisible()
        assert window.motion_tolerance.value() == 18
        window.grab().save(str(artifacts / "motion-preview.png"))
        tabs.setCurrentIndex(0)
        if devices:
            window.pipeline = Pipeline(devices[0], window.guard)
            window.reset_calibration()
            assert window.pipeline.recalibrate.is_set()
            assert window.calibration_dialog is None
            assert not window.guard.enabled
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            window.pipeline.frames.put((1, frame))
            window.pipeline.views.put(View(frame,30,0,0,Decision(calibrated=True),0,True,30))
            window.tick()
            assert window.arm.isEnabled()
            assert not hasattr(window, "regions_dialog")
            result["no_calibration_wizard_or_region_editor"] = True
        window.close()
        result["ui_closed"] = not window.guard.is_alive()
        (artifacts / "smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        app.quit()

    QTimer.singleShot(1500, finish)
    app.exec()


if __name__ == "__main__":
    main()
