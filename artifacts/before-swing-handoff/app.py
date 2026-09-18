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
                              QFrame, QProgressBar, QCheckBox, QScrollArea, QMessageBox, QTabWidget)

from .keyboard import InputGuard
from .pipeline import Pipeline
from .diagnostics import integrity_level
from .calibration_ui import CalibrationDialog
from .cameras import ip_camera


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
QTabWidget::pane { border: none; }
QTabBar::tab { background: #202c40; padding: 9px 15px; border-radius: 5px; margin-right: 4px; }
QTabBar::tab:selected { background: #344662; color: #7de0ce; }
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
        self.calibration_dialog = None
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
        self.debug_status = label("Debug: kamera bekleniyor", "muted")
        self.debug_status.setToolTip("Parmak oranlarının sırası: işaret, orta, yüzük, serçe. web=ağ, open=açık, fist=yumruk, other=belirsiz.")
        left.addWidget(self.debug_status)
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
        self.ipcam_label = label("IPCam yayın adresi", "muted")
        self.ipcam_url = QLineEdit("http://192.168.1.3:8080/video")
        self.ipcam_url.setPlaceholderText("http://kamera:8080/video veya rtsp://…")
        self.ipcam_url.setClearButtonEnabled(True)
        side.addWidget(self.ipcam_label)
        side.addWidget(self.ipcam_url)
        self.ipcam_label.hide()
        self.ipcam_url.hide()
        self.cameras.currentIndexChanged.connect(self.camera_selection_changed)
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
        tabs = QTabWidget()
        side.addWidget(tabs, 1)
        motion_page = QWidget()
        side = QVBoxLayout(motion_page)
        side.setContentsMargins(0, 8, 0, 0)
        side.setSpacing(8)
        tabs.addTab(motion_page, "Hareketler")
        side.addSpacing(8)
        side.addWidget(label("02  HAREKETLER", "eyebrow"))
        side.addWidget(label("Ağ eli havada → Swing + sağ/sol bakış\nİki parmak → Nişan · Kapat → E × 2\nTek işaret parmağı → Serbest kamera\nAçık diğer el → Merkezden WASD\nHulk alkışı → F · Yüksel → Zıpla\nYumruk → Saldırı · Eğil/doğrul → Kaçın", "muted"))
        guide = QPushButton("Hareket rehberi")
        guide.clicked.connect(self.show_guide)
        side.addWidget(guide)
        self.calibrate = QPushButton("Takibi sıfırla")
        self.calibrate.clicked.connect(self.reset_calibration)
        side.addWidget(self.calibrate)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        side.addWidget(self.progress)
        self.calibration_status = label("El işaretleri doğrudan tanınır; kalibrasyon gerekmez.", "muted")
        side.addWidget(self.calibration_status)
        side.addWidget(label("Zıplama hassasiyeti  ·  düşük → yüksek", "muted"))
        self.sensitivity = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity.setRange(1, 100)
        self.sensitivity.setValue(65)
        side.addWidget(self.sensitivity)
        side.addWidget(label("El ile kamera hızı  ·  yavaş → hızlı", "muted"))
        self.camera_speed = QSlider(Qt.Orientation.Horizontal)
        self.camera_speed.setRange(100, 1400)
        self.camera_speed.setValue(450)
        side.addWidget(self.camera_speed)
        side.addWidget(label("El titreme toleransı  ·  az → çok", "muted"))
        self.motion_tolerance = QSlider(Qt.Orientation.Horizontal)
        self.motion_tolerance.setRange(5, 50)
        self.motion_tolerance.setValue(18)
        side.addWidget(self.motion_tolerance)
        self.invert_steering = QCheckBox("El ile bakış yönünü ters çevir")
        side.addWidget(self.invert_steering)
        self.smooth_preview = QCheckBox("Akıcı önizleme (kamera hızında)")
        self.smooth_preview.setChecked(True)
        side.addWidget(self.smooth_preview)
        self.debug_overlay = QCheckBox("Debug çizimleri ve koşullar")
        self.debug_overlay.setChecked(True)
        side.addWidget(self.debug_overlay)
        side.addWidget(label("Debug açıkken çizimler algılanan kareyle birlikte gösterilir.", "muted"))
        side.addStretch()
        game_page = QWidget()
        side = QVBoxLayout(game_page)
        side.setContentsMargins(0, 8, 0, 0)
        side.setSpacing(8)
        tabs.insertTab(0, game_page, "Oyun")
        tabs.setCurrentIndex(0)
        side.addWidget(label("03  OYUN BAĞLANTISI", "eyebrow"))
        self.target = QLineEdit("Spider-Man.exe")
        self.target.setPlaceholderText("Oyunun işlem dosyası adı.exe")
        side.addWidget(self.target)
        side.addWidget(self.calibrate)
        side.addWidget(self.progress)
        side.addWidget(self.calibration_status)
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

    def show_guide(self):
        QMessageBox.information(self, "Hareket rehberi",
            "Kalibrasyon gerekmez. El işaretlerini kadrajın herhangi bir yerinde yap.\n\n"
            "NİŞAN: İşaret ve orta parmak açık, kalanlar kapalı. Başladığın el konumu merkez olur. Merkezden uzaklaştıkça kamera hızlanır; merkeze dönünce durur. İki parmağı kapatınca iki ayrı E basışı gönderilir. Tekrar açıp kapatarak tekrarla. Avucunu açıp kısa süre tutarak nişandan çık.\n\n"
            "KAMERA: Tek işaret parmağını uzatıp tut; başlangıç merkezinden sağ/sol/yukarı/aşağı bak. Parmağı kapatınca bırakır.\n\n"
            "YÜRÜME: Diğer elini açık avuç tut. Açtığın yer nötrdür. Yukarı W, aşağı S, sola A, sağa D. Merkeze getirince durur; eli kapatınca bırakır. Nişan ve swing ile birlikte kullanılabilir.\n\n"
            "SWING: Yukarı ağ işaretini tut; Space ardından Shift basılır. Aynı el kamera yönünü verir. Swing boyunca diğer tuşlar kapalıdır. Ağ elini son ağ işaretinden 2 saniye içinde yumruk yap: Shift tutulurken Space basılır, ardından Shift bırakılır. Yeni ağ işaretiyle devam et.\n\n"
            "F: Elleri ayırıp Hulk gibi hızla birbirine getir. Parmak şekli önemli değil. Her tekrar için elleri yeniden ayır.\n\n"
            "SALDIRI: Fare/yürüme işareti dışında yumruğunu savur.\n\n"
            "SPACE: Nötr duruştan ani yukarı yükseliş.\n"
            "CTRL: Eğil ve doğrul; bu hareket Space üretmez.\n\n"
            "F8 bütün tuşları ve fare düğmelerini bırakıp kontrolleri kapatır.")

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
            if camera == "ipcam":
                camera = ip_camera(self.ipcam_url.text())
            self.last_view = None
            self.pipeline = Pipeline(camera, self.guard)
            self.pipeline.start()
            self.start_button.setText("Durdur")
            self.cameras.setEnabled(False)
            self.ipcam_url.setEnabled(False)
            self.camera_status.setText("IPCam yayınına bağlanılıyor…" if self.cameras.currentData() == "ipcam" else "Kamera açılıyor…")
        except Exception as exc:
            self.camera_status.setText(str(exc))
            self.stop_camera()

    def camera_selection_changed(self):
        selected = self.cameras.currentData() == "ipcam"
        self.ipcam_label.setVisible(selected)
        self.ipcam_url.setVisible(selected)

    def stop_camera(self):
        if self.pipeline:
            self.pipeline.stop()
        if self.calibration_dialog and self.calibration_dialog.isVisible():
            self.calibration_dialog.close()
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
        self.ipcam_url.setEnabled(True)

    def reset_calibration(self):
        if self.pipeline:
            self.arm.setChecked(False)
            self.guard.configure(False)
            self.pipeline.recalibrate.set()

    def editing_controls(self):
        return bool(self.calibration_dialog and self.calibration_dialog.isVisible())

    def arm_changed(self, checked):
        target = self.target.text().strip()
        calibrating = self.editing_controls()
        if checked and (not self.pipeline or not target.lower().endswith(".exe") or calibrating):
            self.arm.setChecked(False)
            self.input_status.setText("Kamerayı açın, oyun .exe adını girin.")
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
        self.guard.camera_speed = self.camera_speed.value()
        self.guard.invert_x = self.invert_steering.isChecked()
        self.retiring = [p for p in self.retiring if p.alive()]
        self.start_button.setEnabled(not self.retiring and (self.pipeline is not None or self.cameras.count() > 0))
        self.calibrate.setEnabled(self.pipeline is not None)
        if not self.scan_results.empty():
            cameras, error = self.scan_results.get_nowait()
            previous = self.cameras.currentData()
            self.cameras.blockSignals(True)
            self.cameras.clear()
            selected = 0
            for camera in cameras:
                self.cameras.addItem(camera.name, camera)
                if previous and camera.path == getattr(previous, "path", None):
                    selected = self.cameras.count() - 1
            self.cameras.addItem("IPCam", "ipcam")
            if previous == "ipcam":
                selected = self.cameras.count() - 1
            self.cameras.setCurrentIndex(selected)
            self.cameras.blockSignals(False)
            self.camera_selection_changed()
            self.scanning = False
            self.refresh_button.setEnabled(True)
            self.camera_status.setText((f"{error} · IPCam kullanılabilir" if error else f"{len(cameras)} yerel kamera · IPCam kullanılabilir"))
        if self.arm.isChecked() and not self.guard.enabled:
            self.arm.setChecked(False)
        self.input_status.setText(self.guard.error or self.guard.status)
        self.send_status.setText(f"Windows'un kabul ettiği girdi: {self.guard.backend.sent_count}\nBu sayaç oyunun işlediğini doğrulamaz.")
        calibrating = self.editing_controls()
        self.arm.setEnabled(not calibrating)
        self.calibrate.setEnabled(bool(self.pipeline))
        self.test_button.setEnabled(not calibrating and self.guard.test_due is None and self.guard.test_until is None)
        self.debug_status.setVisible(self.debug_overlay.isChecked())
        self.smooth_preview.setEnabled(not self.debug_overlay.isChecked())
        if not self.pipeline:
            return
        if self.pipeline.error:
            message = self.pipeline.error
            logging.error(message)
            self.stop_camera()
            self.camera_status.setText(message)
            return
        self.pipeline.tolerance = self.motion_tolerance.value() / 1000
        self.pipeline.threshold = 0.20 - self.sensitivity.value() * 0.0018
        view = self.pipeline.views.get()
        if self.smooth_preview.isChecked() and not self.debug_overlay.isChecked() and not self.isMinimized():
            item = self.pipeline.frames.get()
            if item is not None and item[0] != getattr(self, "preview_stamp", None):
                self.preview_stamp = item[0]
                preview_frame = cv2.flip(item[1], 1)
                Pipeline.draw_movement(preview_frame, view.decision if view is not None else None)
                self.render_preview(preview_frame)
        if view is None or view is self.last_view:
            return
        self.last_view = view
        self.camera_status.setText("Canlı · " + self.pipeline.actual)
        if not self.isMinimized() and (self.debug_overlay.isChecked() or not self.smooth_preview.isChecked()):
            self.render_preview(view.frame)
        self.fps_label.setText(f"{view.capture_fps:.0f} / {view.fps:.0f} FPS")
        self.latency.setText(f"{view.latency_ms:.0f} ms")
        self.tracking.setText(f"El: {view.hands}  ·  Gövde: {'var' if view.pose else 'yok'}")
        self.detail.setText(f"Kamera / Algılama FPS · model {view.inference_ms:.0f} ms · gecikme: kare okuma → karar\n{view.decision.description()}")
        debug = view.decision.debug
        hands_text = " | ".join(f"El {i + 1}: {shape} [{', '.join(f'{v:.2f}' for v in debug.get('fingers', [])[i])}]"
                                for i, shape in enumerate(debug.get("shapes", []))) or "El bulunamadı"
        self.debug_status.setText(f"SWING: {debug.get('swing_reason', '—')}\nNİŞAN: {debug.get('aim_reason', '—')}\n"
                                 f"YÖN: {debug.get('motion_reason', '—')}\n{hands_text}")
        self.progress.setValue(100 if view.decision.calibrated else round(view.decision.progress * 100))
        self.calibration_status.setText("El kontrolü hazır · zıplama için yüz ve omuzlar görünmeli.")

    def render_preview(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))

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
