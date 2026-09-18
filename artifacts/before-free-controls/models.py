"""Fetch versioned upstream models once, outside the capture loop."""

from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
MODELS = {
    "hand_landmarker.task": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
    "pose_landmarker_lite.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
}


def download():
    folder = ROOT / "models"
    folder.mkdir(exist_ok=True)
    for name, url in MODELS.items():
        target = folder / name
        if target.exists() and zipfile.is_zipfile(target):
            continue
        print(f"Downloading {name} ...", flush=True)
        temporary = target.with_suffix(".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                expected = response.headers.get("Content-Length")
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if expected and temporary.stat().st_size != int(expected):
                raise IOError(f"Incomplete model download: {name}")
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip() is not None:
                    raise IOError(f"Corrupt model archive: {name}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    download()
