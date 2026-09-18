"""Live camera canvas for drawing and replacing the four control regions."""

import cv2
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton, QWidget

from .regions import NAMES, save_regions, valid_regions


class RegionCanvas(QWidget):
    def __init__(self, regions, parent=None):
        super().__init__(parent)
        self.regions = dict(regions)
        self.selected = "left"
        self.image = QImage()
        self.start = self.end = None
        self.setMinimumSize(480, 320)

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width()-size.width())/2, (self.height()-size.height())/2, size.width(), size.height())

    def normalized(self, point, clamp=False):
        rect = self.image_rect()
        if rect.isEmpty() or (not clamp and not rect.contains(point)):
            return None
        return (max(0, min(1, (point.x()-rect.x())/rect.width())),
                max(0, min(1, (point.y()-rect.y())/rect.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = self.normalized(event.position())
            self.end = self.start

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = self.normalized(event.position(), True)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start is not None:
            self.end = self.normalized(event.position(), True)
            x, y = self.start
            u, v = self.end
            if abs(x-u) >= .04 and abs(y-v) >= .04:
                self.regions[self.selected] = [min(x,u), min(y,v), max(x,u), max(y,v)]
            self.start = self.end = None
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101620"))
        rect = self.image_rect()
        if rect.isEmpty():
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Canlı kamera bekleniyor…")
            return
        painter.drawImage(rect, self.image)
        boxes = dict(self.regions)
        if self.start is not None and self.end is not None:
            x,y = self.start
            u,v = self.end
            boxes[self.selected] = [min(x,u),min(y,v),max(x,u),max(y,v)]
        for name, (x,y,u,v) in boxes.items():
            painter.setPen(QPen(QColor("#ffcf68" if name == self.selected else "#7de0ce"), 3))
            box = QRectF(rect.x()+x*rect.width(), rect.y()+y*rect.height(), (u-x)*rect.width(), (v-y)*rect.height())
            painter.drawRect(box)
            painter.drawText(box.adjusted(6, 4, -2, -2), Qt.AlignmentFlag.AlignTop, NAMES[name])


class RegionsDialog(QDialog):
    def __init__(self, pipeline, parent=None):
        super().__init__(parent)
        self.pipeline = pipeline
        self.setWindowTitle("Kontrol bölgelerini çiz")
        self.resize(900, 740)
        layout = QVBoxLayout(self)
        instruction = QLabel("Bölgeyi seç, görüntüde fareyle sürükleyerek çiz. Aynı bölgeyi yeniden çizerek taşı veya boyutlandır.\n"
                             "Sol=A, sağ=D, baş üstü=W. Çene–omuz–göğüs arasına NİŞAN dikdörtgenini koy.\n"
                             "Ağ işaretindeki elinin BİLEĞİ kutuya girince çalışır. Nişan için iki bilek de nişan kutusunda olmalı. Bölgeler çakışmamalı.")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        self.selector = QComboBox()
        for key, name in NAMES.items():
            self.selector.addItem(name, key)
        layout.addWidget(self.selector)
        self.canvas = RegionCanvas(pipeline.regions)
        layout.addWidget(self.canvas, 1)
        self.selector.currentIndexChanged.connect(self.select)
        self.message = QLabel("Girdiler kapalı. Dört bölgeyi de kendin yerleştir.")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.save = QPushButton("Dört bölgeyi kaydet")
        self.save.clicked.connect(self.commit)
        layout.addWidget(self.save)
        cancel = QPushButton("İptal · eski bölgeleri koru")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(50)

    def select(self):
        self.canvas.selected = self.selector.currentData()
        self.canvas.update()

    def refresh(self):
        if self.pipeline.stop_event.is_set() or self.pipeline.error:
            self.save.setEnabled(False)
            self.message.setText("Kamera durdu; yeniden açıp bölgeleri düzenle.")
            return
        item = self.pipeline.frames.get()
        if item is not None:
            rgb = cv2.cvtColor(cv2.flip(item[1], 1), cv2.COLOR_BGR2RGB)
            h,w,_ = rgb.shape
            self.canvas.image = QImage(rgb.data, w,h,rgb.strides[0],QImage.Format.Format_RGB888).copy()
            self.canvas.update()
        valid = valid_regions(self.canvas.regions)
        self.save.setEnabled(not self.canvas.image.isNull() and valid)
        count = len(self.canvas.regions)
        self.message.setText("Dört bölge hazır. Kaydedince oyun girdilerini ayrıca etkinleştir." if valid else
                             f"{count}/4 bölge çizildi. " + ("Sıradaki bölgeyi seçip çiz." if count < 4 else
                             "Bölgeler çakışıyor veya çok küçük; birini seçip yeniden çiz."))

    def commit(self):
        try:
            save_regions(self.pipeline.regions_path, self.canvas.regions)
        except (OSError, ValueError) as exc:
            self.message.setText(str(exc))
            return
        self.pipeline.regions = dict(self.canvas.regions)
        self.accept()

    def done(self, result):
        self.timer.stop()
        super().done(result)
