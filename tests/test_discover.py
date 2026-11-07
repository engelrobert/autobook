from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from autobook.discover.metadata import content_id, parse_gps, read_metadata
from autobook.discover.walk import scan
from autobook.synth import make_exif


def _jpeg(path: Path, size=(400, 300), exif=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (120, 80, 40)).save(path, "JPEG", **({"exif": exif} if exif else {}))
    return path


def test_exif_time_offset_and_gps_sign(tmp_path):
    p = _jpeg(tmp_path / "a.jpg", exif=make_exif(datetime(2025, 5, 3, 14, 2, 1), -33.86, 151.21))
    m = read_metadata(p)
    assert m.taken == "2025-05-03T14:02:01" and m.ts_source == "exif" and m.tz == "+01:00"
    assert m.lat == pytest.approx(-33.86, abs=1e-4) and m.lon == pytest.approx(151.21, abs=1e-4)
    assert m.camera == "SynthCam X1"


def test_missing_exif_falls_back_to_mtime(tmp_path):
    import os

    p = _jpeg(tmp_path / "b.jpg")
    ts = datetime(2024, 1, 2, 3, 4, 5).timestamp()
    os.utime(p, (ts, ts))
    m = read_metadata(p)
    assert m.ts_source == "mtime" and m.taken == "2024-01-02T03:04:05" and m.lat is None


def test_orientation_swaps_dimensions(tmp_path):
    p = _jpeg(tmp_path / "c.jpg", size=(400, 300), exif=make_exif(None, None, None, orientation=6))
    m = read_metadata(p)
    assert (m.width, m.height) == (300, 400)


def test_heic_is_read(tmp_path):
    import pillow_heif

    pillow_heif.register_heif_opener()
    p = tmp_path / "d.heic"
    Image.new("RGB", (320, 240), (10, 200, 30)).save(p, format="HEIF",
                                                     exif=make_exif(datetime(2025, 1, 1, 8, 0, 0), 1.0, 2.0).tobytes())
    m = read_metadata(p)
    assert m.format in ("HEIF", "HEIC") and m.taken.startswith("2025-01-01") and m.ts_source == "exif"


def test_corrupt_file_raises(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not an image" * 200)
    with pytest.raises(ValueError):
        read_metadata(p)


def test_gps_zero_and_garbage_are_ignored():
    assert parse_gps({1: "N", 2: (0.0, 0.0, 0.0), 3: "E", 4: (0.0, 0.0, 0.0)}) == (None, None)
    assert parse_gps({}) == (None, None)
    assert parse_gps({1: "N", 2: "bad", 3: "E", 4: (1.0, 0.0, 0.0)}) == (None, None)


def test_content_id_is_stable_and_content_based(tmp_path):
    a = _jpeg(tmp_path / "x" / "one.jpg")
    b = tmp_path / "y" / "renamed.jpg"
    b.parent.mkdir()
    b.write_bytes(a.read_bytes())
    assert content_id(a) == content_id(b) and content_id(a).startswith("p") and len(content_id(a)) == 16


def test_walk_skips_hidden_tiny_and_excluded(tmp_path):
    _jpeg(tmp_path / "Ünïcödé ordner" / "Foto 1.JPG")
    _jpeg(tmp_path / ".hidden" / "x.jpg")
    _jpeg(tmp_path / "@eaDir" / "y.jpg")
    _jpeg(tmp_path / "work" / "z.jpg")
    (tmp_path / "tiny.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "notes.txt").write_text("x")
    res = scan([tmp_path], exclude=[tmp_path / "work"])
    names = [p.name for p in res.files]
    assert names == ["Foto 1.JPG"]
    assert any("too small" in why for _, why in res.skipped)
