"""Guided, frame-backed calibration. Only numeric landmarks are persisted."""

import json
from math import dist, isfinite
from pathlib import Path
from statistics import median


def finger_features(hand):
    def point(p):
        return p.x, p.y, getattr(p, "z", 0.0)
    return [min(2.5, dist(point(hand[0]), point(hand[n * 4 + 4])) /
                max(dist(point(hand[0]), point(hand[n * 4 + 2])), 0.01)) for n in range(1, 5)]


def head_yaw(pose):
    for left, right in ((7, 8), (2, 5)):
        if all(pose[i].visibility >= 0.5 for i in (0, left, right)):
            width = abs(pose[left].x - pose[right].x)
            if width > 0.035:
                return (pose[0].x - (pose[left].x + pose[right].x) / 2) / width
    return None


def observe(hands, pose):
    if not pose or not all(pose[i].visibility >= 0.6 for i in (0, 11, 12)):
        return None
    width = max(abs(pose[11].x - pose[12].x), 0.10)
    shoulder = (pose[11].y + pose[12].y) / 2
    return {"y": (pose[0].y + shoulder) / 2, "center": (pose[11].x + pose[12].x) / 2,
            "scale": width, "yaw": (getattr(pose[11], "z", 0) - getattr(pose[12], "z", 0)) / width,
            "head": (pose[0].y - shoulder) / width, "head_yaw": head_yaw(pose),
            "hands": [{"features": finger_features(h), "y": (h[0].y - shoulder) / width,
                       "up": (h[0].y - (h[8].y + h[20].y) / 2) / width} for h in hands]}


STEPS = [
    ("neutral", "1 / 5 · Nötr duruş", "Kameraya düz bak. Başın ve omuzların görünsün. Rahat dur ve 1,2 saniye kıpırdama."),
    ("swing", "2 / 5 · Yukarı ağ", "Bir elini omuz üstüne kaldır. İşaret ve serçe parmak yukarı, orta ve yüzük parmak kapalı olsun."),
    ("aim", "3 / 5 · Ağ işareti örneği", "İki elini ayırıp rahat göğüs hizasında ağ işareti yap. Bu adım ağ şeklini öğretir; nişan için yüz hizasında iki parmak kullanılır."),
    ("open", "4 / 5 · Açık el", "İki avucunu nişan yüksekliğinde aç; dört parmağın da açık olsun. Bu örnek alkış için açık avucu öğretir."),
    ("fist", "5 / 5 · Kapalı el", "Aynı yükseklikte iki elini yumruk yap. Sıkmadan sabit tut. Tamamlanınca profil kaydedilir."),
]


class GuidedCalibration:
    def __init__(self):
        self.active = False
        self.index = 0
        self.samples = []
        self.collected = {}
        self.capture_at = None
        self.progress = 0.0
        self.message = ""
        self.profile = None

    def start(self):
        self.__init__()
        self.active = True
        self.message = "Kalibrasyon boyunca oyun girdileri kapalı. Hazır olduğunda örnek al."

    def begin_sample(self, now):
        if self.active:
            self.samples = []
            self.capture_at = now + 2
            self.progress = 0
            self.message = "Harekete hazırlan: 2 saniye"

    def state(self):
        _, title, instruction = STEPS[min(self.index, len(STEPS) - 1)]
        return {"active": self.active, "title": title if self.active else "Kalibrasyon tamamlandı",
                "instruction": instruction, "message": self.message, "progress": self.progress,
                "sampling": self.capture_at is not None, "complete": self.profile is not None}

    def update(self, observation, now):
        if not self.active or self.capture_at is None:
            return
        if now < self.capture_at:
            self.message = f"Harekete hazırlan: {self.capture_at - now:.1f} saniye"
            return
        step = STEPS[self.index][0]
        problem = self.validate_observation(step, observation)
        if problem:
            self.samples.clear()
            self.progress = 0
            self.message = problem
            return
        if self.samples and now - self.samples[-1][0] > 0.35:
            self.samples.clear()
        self.samples.append((now, observation))
        self.progress = min(1, (now - self.samples[0][0]) / 1.2)
        self.message = "Örnek alınıyor; sabit tut."
        if self.progress < 1 or len(self.samples) < 6:
            return
        values = [sample[1] for sample in self.samples]
        if max(v["center"] for v in values) - min(v["center"] for v in values) > values[0]["scale"] * 0.15:
            self.samples.clear()
            self.progress = 0
            self.message = "Gövden hareket etti; bu adımı sabit tutarak tekrarla."
            return
        self.collected[step] = values
        self.capture_at = None
        self.samples.clear()
        self.progress = 0
        if self.index < len(STEPS) - 1:
            self.index += 1
            self.message = "Önceki adım alındı. Hazır olduğunda örnek al."
            return
        try:
            self.profile = self.build_profile()
        except ValueError as exc:
            self.message = str(exc) + " Bu adımı yeniden örnekle."
            return
        self.active = False
        self.progress = 1
        self.message = "Profil kaydedilmeye hazır. Kontrolleri ayrıca etkinleştir."

    def validate_observation(self, step, obs):
        if obs is None:
            return "Yüz ve omuzlar görünmüyor; kameranın karşısına geç."
        if step == "swing" and not any(h["y"] < -0.12 and h["up"] > 0.10 for h in obs["hands"]):
            return "Bir elini omuz üstüne kaldır; parmaklarını yukarı yönelt."
        if step in ("aim", "open", "fist") and len(obs["hands"]) != 2:
            return "İki el bulunamadı. Elleri ayır ve ikisini de kadraja al."
        if step == "aim" and any(h["y"] < -0.35 for h in obs["hands"]):
            return "Nişan ellerini omuz hizasına veya altına indir."
        return ""

    def build_profile(self):
        neutral = self.collected["neutral"]
        profile = {"version": 1, "neutral": {k: median((v[k] if v[k] is not None else 0.0) for v in neutral)
                   for k in ("y", "center", "scale", "yaw", "head", "head_yaw")}}
        # Retain schema compatibility with existing camera profiles.
        profile.update(head_left=-0.4, head_right=0.4, head_gate=0.55)
        templates = {}
        for step, kind in (("swing", "web"), ("aim", "web"), ("open", "open"), ("fist", "fist")):
            values = self.collected[step]
            count = 1 if step == "swing" else 2
            for index in range(count):
                features = [(next(h for h in v["hands"] if h["y"] < -0.12 and h["up"] > 0.10)
                             if step == "swing" else v["hands"][index])["features"] for v in values]
                template = [median(row[i] for row in features) for i in range(4)]
                templates.setdefault(kind, []).append(template)
        profile["templates"] = templates
        heights = [h["y"] for v in self.collected["aim"] for h in v["hands"]]
        profile["aim_min"] = max(-0.4, min(heights) - 0.35)
        profile["aim_max"] = max(heights) + 0.40
        # Reject ambiguous training rather than learning open palms as web poses.
        for index, web in enumerate(templates["web"]):
            if any(dist(web, other) < 0.22 for kind in ("open", "fist") for other in templates[kind]):
                self.index = 1 if index == 0 else 2
                raise ValueError("Ağ, açık el ve yumruk örnekleri çok benzer.")
        return profile


def save_profile(path, profile):
    path = Path(path)
    path.parent.mkdir(exist_ok=True, parents=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_profile(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") != 1 or abs(data["head_left"]) < 0.1 or abs(data["head_right"]) < 0.1:
            return None
        if data["head_left"] * data["head_right"] >= 0:
            return None
        for key in ("y", "center", "scale", "yaw", "head", "head_yaw"):
            if not isinstance(data["neutral"][key], (int, float)) or not isfinite(data["neutral"][key]):
                return None
        if not 0.05 <= data["neutral"]["scale"] <= 1:
            return None
        if not 0.3 <= data["head_gate"] <= 0.85 or not -0.5 <= data["aim_min"] < data["aim_max"] <= 8:
            return None
        if not all(isfinite(data[key]) for key in ("head_left", "head_right", "aim_min", "aim_max")):
            return None
        if set(data["templates"]) != {"web", "open", "fist"}:
            return None
        for examples in data["templates"].values():
            if not 1 <= len(examples) <= 4:
                return None
            if any(len(row) != 4 or any(not isinstance(x, (float, int)) or not isfinite(x) or not 0 <= x <= 2.5 for x in row) for row in examples):
                return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
