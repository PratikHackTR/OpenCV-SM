"""User-drawn regions in normalized, mirrored camera coordinates."""

import json
from math import isfinite
from pathlib import Path

NAMES = {"left": "SOL · A", "right": "SAĞ · D", "forward": "ÜST / İLERİ · W", "aim": "NİŞAN · Sağ tık"}


def valid_regions(regions):
    if not isinstance(regions, dict) or set(regions) != set(NAMES):
        return False
    for rect in regions.values():
        if not isinstance(rect, (tuple, list)) or len(rect) != 4:
            return False
        if any(not isinstance(v, (float, int)) or not isfinite(v) or not 0 <= v <= 1 for v in rect):
            return False
        if rect[2] - rect[0] < .04 or rect[3] - rect[1] < .04:
            return False
    boxes = list(regions.values())
    for i, a in enumerate(boxes):
        for b in boxes[i+1:]:
            if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                return False
    return True


def contains(rect, hand):
    # Wrist is stable during finger opening/closing.
    return rect[0] <= hand[0].x <= rect[2] and rect[1] <= hand[0].y <= rect[3]


def load_regions(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if valid_regions(data) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_regions(path, regions):
    if not valid_regions(regions):
        raise ValueError("Dört bölgeyi yeterli büyüklükte ve birbirine değmeden çiz.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(regions, indent=2), encoding="utf-8")
    temporary.replace(path)
