import io
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

from drone_sorter import shrink
from drone_sorter.pitch_reader import read_photo_meta

pytestmark = pytest.mark.skipif(not shrink.is_available(), reason="jpegtran not installed")

XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
       b'drone-dji:GimbalPitchDegree="-40.00" drone-dji:GimbalYawDegree="+12.50" '
       b'drone-dji:GpsLatitude="+42.10000000" drone-dji:GpsLongitude="-83.20000000"/>'
       b'</rdf:RDF></x:xmpmeta>')


def real_jpg(path, size=(640, 480), seed=0):
    """A real, decodable JPG with a DJI-style XMP block (APP1)."""
    import random
    rnd = random.Random(seed)
    img = Image.new("RGB", size)
    img.putdata([(rnd.randrange(256), (x * 3) % 256, (x // 7) % 256) for x in range(size[0] * size[1])])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, optimize=False)
    data = buf.getvalue()
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + XMP
    app1 = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    path.write_bytes(data[:2] + app1 + data[2:])
    return path


def pixels(p):
    with Image.open(p) as im:
        return im.tobytes()


def test_shrinks_with_identical_pixels_and_dji_data(tmp_path):
    p = real_jpg(tmp_path / "DJI_0001.JPG")
    before_px, before_meta, before_size = pixels(p), read_photo_meta(p), p.stat().st_size
    res = shrink.shrink_file(p)
    assert res.status == "shrunk" and res.after < before_size
    assert p.stat().st_size == res.after
    assert pixels(p) == before_px
    assert read_photo_meta(p) == before_meta and before_meta.pitch == -40.0
    assert not list(tmp_path.glob("*.tmp"))


def test_second_run_leaves_file_alone(tmp_path):
    p = real_jpg(tmp_path / "a.jpg")
    shrink.shrink_file(p)
    data = p.read_bytes()
    res = shrink.shrink_file(p)
    assert res.status == "unchanged" and p.read_bytes() == data


def test_failed_pixel_check_keeps_original(tmp_path, monkeypatch):
    p = real_jpg(tmp_path / "a.jpg")
    original = p.read_bytes()
    monkeypatch.setattr(shrink, "same_pixels", lambda a, b: False)
    res = shrink.shrink_file(p)
    assert res.status == "error" and p.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_broken_file_keeps_original(tmp_path):
    p = tmp_path / "broken.jpg"
    p.write_bytes(b"\xff\xd8 not really a jpeg")
    res = shrink.shrink_file(p)
    assert res.status == "error" and p.read_bytes() == b"\xff\xd8 not really a jpeg"


def test_shrink_files_and_estimate(tmp_path):
    files = [real_jpg(tmp_path / f"DJI_{i:04}.JPG", seed=i) for i in range(5)]
    frac, total, saved = shrink.estimate_savings(files)
    assert 0 < frac < 1 and saved > 0
    assert all(f.stat().st_size for f in files)  # estimate changes nothing
    results = shrink.shrink_files(files, workers=2)
    assert [r.status for r in results] == ["shrunk"] * 5
