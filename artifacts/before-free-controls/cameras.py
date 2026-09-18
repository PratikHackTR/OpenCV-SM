"""Network camera sources share the existing capture and tracking pipeline."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import cv2


@dataclass(frozen=True)
class IPCamera:
    url: str = field(repr=False)
    name: str = "IPCam"

    @property
    def path(self):
        return self.url


def ip_camera(text):
    url = text.strip()
    try:
        parts = urlsplit(url)
        valid = parts.scheme.lower() in {"http", "https", "rtsp", "rtsps"} and bool(parts.hostname)
        _ = parts.port
    except ValueError:
        valid = False
    if not valid or any(c.isspace() for c in url):
        raise ValueError("Geçerli bir HTTP(S) veya RTSP yayın adresi girin.")
    return IPCamera(url)


def open_camera(camera, width, height, fps):
    if isinstance(camera, IPCamera):
        # Network settings belong to the streaming camera, not DirectShow.
        return cv2.VideoCapture(camera.url, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000,
        ])
    cap = cv2.VideoCapture(camera.index, camera.backend)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap
