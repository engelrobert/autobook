import random

from PIL import Image, ImageFilter

from autobook.analyze.dedupe import duplicate_groups
from autobook.analyze.quality import analyze_image, hamming
from autobook.select.scoring import final_score, local_scores
from autobook.synth import draw_scene


def test_blur_lowers_sharpness_and_phash_stays_close():
    rng = random.Random(1)
    sharp = draw_scene(rng, (800, 600), 42)
    blurred = sharp.filter(ImageFilter.GaussianBlur(6))
    a, b = analyze_image(sharp), analyze_image(blurred)
    assert a["sharpness"] > b["sharpness"] + 1.0
    assert hamming(a["phash"], b["phash"]) <= 12


def test_screenshot_detection_by_name_and_format():
    im = Image.new("RGB", (390, 844), (240, 240, 240))
    assert analyze_image(im, path="/x/Screenshot 2025-01-01.png")["screenshot"] == 1
    assert analyze_image(im, path="/x/IMG_1.png", camera=None, fmt="PNG")["screenshot"] == 1
    assert analyze_image(im, path="/x/IMG_1.jpg", camera="Pixel 8", fmt="JPEG")["screenshot"] == 0


def _p(i, t, ph):
    return {"id": f"p{i}", "taken": f"2025-06-01T10:{t // 60:02d}:{t % 60:02d}", "phash": ph}


def test_bursts_and_copies_are_grouped_but_different_scenes_are_not():
    base = "f0f0f0f0f0f0f0f0"
    near = "f0f0f0f0f0f0f0f1"  # 1 bit off
    far = "0f0f0f0f0f0f0f0f"
    photos = [_p(1, 0, base), _p(2, 1, near), _p(3, 600, far), _p(4, 3000, near)]
    g = duplicate_groups(photos)
    assert g["p1"] == g["p2"] == g["p4"]  # burst + resized copy much later
    assert g["p3"] == "p3"


def test_local_score_penalises_blur_and_screenshots():
    def ph(i, sharp, shot=0):
        return {"id": f"p{i}", "event_id": "e", "sharpness": sharp, "brightness": 0.5, "clip_low": 0.0,
                "clip_high": 0.0, "colorfulness": 40, "screenshot": shot}
    scores = local_scores([ph(1, 9.0), ph(2, 8.5), ph(3, 2.0), ph(4, 9.0, shot=1)] + [ph(i, 8 + i / 10) for i in range(5, 20)])
    assert scores["p3"]["blur"] and not scores["p1"]["blur"]
    assert scores["p1"]["local_score"] > scores["p3"]["local_score"]
    assert scores["p4"]["local_score"] < scores["p1"]["local_score"] - 0.4


def test_final_score_uses_ai_and_flags():
    good = {"tech": 8, "aesthetic": 8, "interest": 9, "flags": [], "tags": ["people"]}
    blink = dict(good, flags=["eyes_closed"])
    assert final_score(0.5, good) > final_score(0.5, blink)
    assert final_score(0.5, good, ["people"]) > final_score(0.5, good)
    assert final_score(0.42, None) == 0.42
