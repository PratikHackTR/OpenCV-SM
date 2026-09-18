"""Fullscreen camera console. Workers never touch Qt widgets."""

import logging
import ctypes
import subprocess
import queue
import sys
import threading

import cv2
from cv2_enumerate_cameras import enumerate_cameras
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton,
                              QComboBox, QLineEdit, QSlider, QVBoxLayout, QHBoxLayout,
                              QFrame, QProgressBar, QCheckBox, QScrollArea)

from .keyboard import InputGuard
from .pipeline import Pipeline
from .diagnostics import integrity_level


STYLE = """
QMainWindow, QWidget { background: #0c1019; color: #edf1fa; font-family: 'Segoe UI'; font-size: 14px; }
QFrame#card { background: #151c2a; border: 1px solid #263247; border-radius: 14px; }
QFrame#card QLabel, QFrame#card QCheckBox { background: transparent; }
QLabel#title { font-size: 30px; font-weight: 750; }
QLabel#eyebrow { color: #fa5267; font-size: 12px; font-weight: 700; }
QLabel#muted { color: #95a6be; font-size: 13px; }
QLabel#metric { font-size: 24px; font-weight: 650; color: #7de0ce; }
QLabel#preview { background: #080b12; border: 1px solid #263247; border-radius: 14px; color: #95a6be; }
QPushButton { background: #24324a; border: 1px solid #344662; border-radius: 8px; padding: 11px 15px; font-weight: 600; }
QPushButton:hover { background: #304360; }
QPushButton:disabled { color: #69778c; background: #192231; }
QPushButton#primary { background: #df3c55; border: 1px solid #f2566d; color: white; }
QPushButton#primary:hover { background: #ef4c64; }
QComboBox, QLineEdit { background: #0c1320; border: 1px solid #344662; border-radius: 7px; padding: 10px; }
QComboBox QAbstractItemView { background: #182237; selection-background-color: #304360; }
QProgressBar { background: #0b111c; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #65d7c0; border-radius: 4px; }
QSlider::groove:horizontal { height: 5px; background: #304360; }
QSlider::handle:horizontal { background: #7de0ce; width: 16px; margin: -6px 0; border-radius: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #61738e; border-radius: 4px; background: #0c1320; }
QCheckBox::indicator:checked { background: #65d7c0; border: 2px solid #b4f9e9; }
QScrollBar:vertical { background: #0c1019; width: 8px; }
QScrollBar::handle:vertical { background: #344662; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""


def label(text, name=None):
    result = QLabel(text)
    if name:
        result.setObjectName(name)
    result.setWordWrap(True)
    return result


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WebMotion — Camera Controller")
        self.resize(1280, 800)
        self.guard = InputGuard()
        self.guard.start()
        self.pipeline = None
        self.retiring = []
        self.scan_results = queue.Queue()
        self.scanning = False
        self.last_view = None
        self.build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self, activated=self.showNormal)
        QShortcut(QKeySequence("F8"), self, activated=lambda: self.arm.setChecked(False))
        self.refresh()

    def build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("OPENCV  /  MOTION CONTROLS", "eyebrow"))
        titles.addWidget(label("WebMotion", "title"))
        titles.addWidget(label("Kameranın karşısına geç. Hareketin oyuna dönüşsün.", "muted"))
        header.addLayout(titles, 1)
        fullscreen = QPushButton("Tam ekran  ·  F11")
        fullscreen.clicked.connect(self.toggle_fullscreen)
        header.addWidget(fullscreen)
        close = QPushButton("Çıkış")
        close.clicked.connect(self.close)
        header.addWidget(close)
        outer.addLayout(header)
        content = QHBoxLayout()
        content.setSpacing(20)
        left = QVBoxLayout()
        self.preview = label("KAMERA BEKLENİYOR\n\nBir kamera seçip canlı görüntüyü başlat.", "preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(360, 240)
        from PySide6.QtWidgets import QSizePolicy
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        left.addWidget(self.preview, 1)
        metrics = QHBoxLayout()
        self.fps_label = label("— FPS", "metric")
        self.latency = label("— ms", "metric")
        self.tracking = label("El: —  ·  Gövde: —", "muted")
        metrics.addWidget(self.fps_label)
        metrics.addWidget(self.latency)
        metrics.addWidget(self.tracking)
        left.addLayout(metrics)
        self.detail = label("640 × 480 hedef · 60 FPS istek · en güncel kare işleme", "muted")
        left.addWidget(self.detail)
        content.addLayout(left, 1)
        panel = QFrame()
        panel.setObjectName("card")
        panel.setFixedWidth(350)
        side = QVBoxLayout(panel)
        side.setContentsMargins(20, 20, 20, 20)
        side.setSpacing(8)
        side.addWidget(label("01  KAMERA", "eyebrow"))
        self.cameras = QComboBox()
        side.addWidget(self.cameras)
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Yenile")
        self.refresh_button.clicked.connect(self.refresh)
        self.start_button = QPushButton("Kamerayı başlat")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.toggle_camera)
        row.addWidget(self.refresh_button)
        row.addWidget(self.start_button)
        side.addLayout(row)
        self.camera_status = label("Kameralar aranıyor…", "muted")
        side.addWidget(self.camera_status)
        side.addSpacing(8)
        side.addWidget(label("02  HAREKETLER", "eyebrow"))
        side.addWidget(label("SHIFT  ·  Sallan / koş"))
        side.addWidget(label("Elini omuz üstüne kaldır. Ağ atma işareti veya kapalı yumruk: basılı tut. Elini aç ya da indir: bırak.", "muted"))
        side.addWidget(label("SPACE  ·  Zıpla / ağ çekişi"))
        side.addWidget(label("Kalibrasyondan sonra hafifçe eğilip doğrul veya başını ve gövdeni kısa bir hareketle yukarı kaldır.", "muted"))
        self.calibrate = QPushButton("Duruşu kalibre et")
        self.calibrate.clicked.connect(self.reset_calibration)
        side.addWidget(self.calibrate)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        side.addWidget(self.progress)
        self.calibration_status = label("Kamera açılınca 1,2 saniye sabit dur.", "muted")
        side.addWidget(self.calibration_status)
        side.addWidget(label("Zıplama hassasiyeti  ·  düşük → yüksek", "muted"))
        self.sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity.setRange(1, 100)
        self.sensitivity.setValue(65)
        side.addWidget(self.sensitivity)
        side.addSpacing(8)
        side.addWidget(label("03  OYUN BAĞLANTISI", "eyebrow"))
        self.target = QLineEdit("Spider-Man.exe")
        self.target.setPlaceholderText("Oyunun işlem dosyası adı.exe")
        side.addWidget(self.target)
        self.arm = QCheckBox("Oyuna tuş göndermeyi etkinleştir")
        self.arm.toggled.connect(self.arm_changed)
        side.addWidget(self.arm)
        self.input_status = label("Kontroller kapalı", "muted")
        side.addWidget(self.input_status)
        self.send_status = label("Windows'un kabul ettiği girdi: 0", "muted")
        side.addWidget(self.send_status)
        self.test_button = QPushButton("5 saniye sonra SPACE testi")
        self.test_button.clicked.connect(self.test_input)
        side.addWidget(self.test_button)
        self.elevate_button = QPushButton("Yönetici olarak yeniden aç")
        self.elevate_button.clicked.connect(self.restart_elevated)
        self.elevate_button.setVisible((self.guard.backend.integrity.get("rid") or 0) < 0x3000)
        side.addWidget(self.elevate_button)
        side.addWidget(label("Yalnızca seçilen oyun ön plandayken çalışır.\nF8: kontrolleri her yerden kapat.", "muted"))
        side.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setFixedWidth(372)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content.addWidget(scroll)
        outer.addLayout(content, 1)
        outer.addWidget(label("YEREL İŞLEME   ·   Video kaydedilmez   ·   ESC tam ekrandan çıkar   ·   Oyun için Alt+Tab", "muted"))

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def refresh(self):
        if self.scanning:
            return
        self.scanning = True
        self.refresh_button.setEnabled(False)
        self.camera_status.setText("Kameralar aranıyor…")

        def scan():
            try:
                self.scan_results.put((list(enumerate_cameras(cv2.CAP_DSHOW)), None))
            except Exception as exc:
                self.scan_results.put(([], str(exc)))
        threading.Thread(target=scan, daemon=True, name="camera-discovery").start()

    def toggle_camera(self):
        if self.pipeline:
            self.stop_camera()
            return
        camera = self.cameras.currentData()
        if camera is None:
            return
        try:
            self.last_view = None
            self.pipeline = Pipeline(camera, self.guard)
            self.pipeline.start()
            self.start_button.setText("Durdur")
            self.cameras.setEnabled(False)
        except Exception as exc:
            self.camera_status.setText(str(exc))
            self.stop_camera()

    def stop_camera(self):
        self.arm.setChecked(False)
        if self.pipeline:
            self.pipeline.stop()
            self.retiring.append(self.pipeline)
            self.pipeline = None
        self.last_view = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText("KAMERA DURDURULDU")
        self.fps_label.setText("— FPS")
        self.latency.setText("— ms")
        self.tracking.setText("El: —  ·  Gövde: —")
        self.progress.setValue(0)
        self.start_button.setText("Kamerayı başlat")
        self.cameras.setEnabled(True)

    def reset_calibration(self):
        if self.pipeline:
            self.pipeline.recalibrate.set()

    def arm_changed(self, checked):
        target = self.target.text().strip()
        if checked and (not self.pipeline or not target.lower().endswith(".exe")):
            self.arm.setChecked(False)
            self.input_status.setText("Önce kamerayı başlatın ve oyun .exe adını girin.")
            return
        self.guard.configure(checked, target)
        self.target.setEnabled(not checked)

    def test_input(self):
        target = self.target.text().strip()
        if not target.lower().endswith(".exe"):
            self.guard.error = "Önce hedef oyunun .exe adını girin."
            return
        self.arm.setChecked(False)
        self.guard.schedule_test(target)

    def restart_elevated(self):
        from .models import ROOT
        # Release held keys before Windows switches to the UAC secure desktop.
        self.arm.setChecked(False)
        self.guard.configure(False)
        self.guard.close()
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(None, "runas", sys.executable,
                                    subprocess.list2cmdline(["-m", "webmotion.app"]), str(ROOT), 1)
        if result and result > 32:
            self.close()
        else:
            self.guard = InputGuard()
            if self.pipeline:
                self.pipeline.guard = self.guard
            self.guard.start()
            self.guard.error = "Yönetici başlatma onaylanmadı; kontroller kapalı."

    def tick(self):
        self.retiring = [p for p in self.retiring if p.alive()]
        self.start_button.setEnabled(not self.retiring and (self.pipeline is not None or self.cameras.count() > 0))
        self.calibrate.setEnabled(self.pipeline is not None)
        if not self.scan_results.empty():
            cameras, error = self.scan_results.get_nowait()
            previous = self.cameras.currentData()
            self.cameras.clear()
            for camera in cameras:
                self.cameras.addItem(camera.name, camera)
                if previous and camera.path == previous.path:
                    self.cameras.setCurrentIndex(self.cameras.count() - 1)
            self.scanning = False
            self.refresh_button.setEnabled(True)
            self.camera_status.setText(error or (f"{len(cameras)} kamera bulundu" if cameras else "Kamera bulunamadı. Kamerayı bağlayıp Yenile'ye basın."))
        if self.arm.isChecked() and not self.guard.enabled:
            self.arm.setChecked(False)
        self.input_status.setText(self.guard.error or self.guard.status)
        self.send_status.setText(f"Windows'un kabul ettiği girdi: {self.guard.backend.sent_count}\nBu sayaç oyunun işlediğini doğrulamaz.")
        self.test_button.setEnabled(self.guard.test_due is None and self.guard.test_until is None)
        if not self.pipeline:
            return
        if self.pipeline.error:
            message = self.pipeline.error
            logging.error(message)
            self.stop_camera()
            self.camera_status.setText(message)
            return
        self.pipeline.threshold = 0.20 - self.sensitivity.value() * 0.0018
        view = self.pipeline.views.get()
        if view is None or view is self.last_view:
            return
        self.last_view = view
        self.camera_status.setText("Canlı · " + self.pipeline.actual)
        if not self.isMinimized():
            rgb = cv2.cvtColor(view.frame, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
        self.fps_label.setText(f"{view.fps:.0f} FPS")
        self.latency.setText(f"{view.latency_ms:.0f} ms")
        self.tracking.setText(f"El: {view.hands}  ·  Gövde: {'var' if view.pose else 'yok'}")
        self.detail.setText(f"Kamera {view.capture_fps:.0f} FPS · model {view.inference_ms:.0f} ms · gecikme: kare okuma → karar\nHareket: {'SHIFT' if view.decision.swing else 'bekleniyor'}{' + SPACE' if view.decision.jump else ''}")
        self.progress.setValue(100 if view.decision.calibrated else round(view.decision.progress * 100))
        self.calibration_status.setText("Duruş hazır · hareket edebilirsin" if view.decision.calibrated else "Yüzün ve omuzların görünsün; 1,2 saniye sabit dur.")

    def closeEvent(self, event):
        self.timer.stop()
        self.stop_camera()
        self.guard.close()
        for pipeline in self.retiring:
            if pipeline.infer_thread.is_alive():
                pipeline.infer_thread.join(timeout=1.5)
            if pipeline.capture_thread.is_alive():
                pipeline.capture_thread.join(timeout=0.5)
        event.accept()


def main():
    from .models import ROOT
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / "logs/webmotion.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    logging.info("WebMotion starting integrity=%s", integrity_level())
    cv2.setNumThreads(1)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    window = Window()
    window.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
