"""Non-modal wizard keeps the camera preview visible while sampling."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QProgressBar


class CalibrationDialog(QDialog):
    def __init__(self, pipeline, parent=None):
        super().__init__(parent)
        self.pipeline = pipeline
        self.setWindowTitle("Adım adım kalibrasyon")
        self.setMinimumWidth(430)
        self.setModal(False)
        layout = QVBoxLayout(self)
        self.title = QLabel("Kalibrasyon hazırlanıyor…")
        self.title.setObjectName("eyebrow")
        self.instruction = QLabel("Kamera açık kalmalı. Kalibrasyon boyunca oyun girdileri kapalı.")
        self.instruction.setWordWrap(True)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.progress = QProgressBar()
        self.sample = QPushButton("Hazırım · 2 saniye sonra örnek al")
        self.sample.clicked.connect(lambda: pipeline.calibration_commands.put("sample"))
        self.close_button = QPushButton("İptal")
        self.close_button.clicked.connect(self.close)
        for widget in (self.title, self.instruction, self.message, self.progress, self.sample, self.close_button):
            layout.addWidget(widget)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)
        pipeline.calibration_state.put(None)
        pipeline.calibration_commands.put("start")

    def refresh(self):
        state = self.pipeline.calibration_state.get()
        if self.pipeline.error or self.pipeline.stop_event.is_set():
            self.message.setText(self.pipeline.error or "Kamera durduruldu.")
            self.sample.setEnabled(False)
            return
        if not state:
            return
        self.title.setText(state["title"])
        self.instruction.setText(state["instruction"] if state["active"] else "Nötr duruşa dön. Bu pencereyi kapatıp kontrolleri etkinleştirebilirsin.")
        self.message.setText(state["message"])
        self.progress.setValue(round(state["progress"] * 100))
        self.sample.setEnabled(state["active"] and not state["sampling"])
        self.close_button.setText("Tamam" if state["complete"] else "İptal")

    def closeEvent(self, event):
        self.timer.stop()
        self.pipeline.calibration_commands.put("cancel")
        super().closeEvent(event)
