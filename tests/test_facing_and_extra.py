"""Center In vs Center Out by camera direction, and the Access Road folder."""

import math

from drone_sorter.pitch_reader import read_photo_meta
from drone_sorter.sorter import (
    DEFAULT_CONFIG, categories_from_config, extra_folders_from_config,
    facing_rules_from_config, rules_from_config, sort_photos,
)
from helpers import make_dji

CFG = DEFAULT_CONFIG
KW = dict(rules=rules_from_config(CFG), facing_rules=facing_rules_from_config(CFG),
          extra_folders=extra_folders_from_config(CFG))
CATS = categories_from_config(CFG)
TOWER = (42.5, -83.4)


def orbit_point(angle_deg, radius_m=40):
    """Drone position on a circle around the tower; angle 0 = north of it."""
    a = math.radians(angle_deg)
    lat = TOWER[0] + radius_m * math.cos(a) / 111_320
    lon = TOWER[1] + radius_m * math.sin(a) / (111_320 * math.cos(math.radians(TOWER[0])))
    return lat, lon


def to_yaw(deg):  # DJI reports -180..180
    return (deg + 180) % 360 - 180


def shoot_orbit(folder, start, pitch, facing_in, n=12, start_at=0, radius=40):
    names = []
    for i in range(n):
        ang = start_at + i * 360 / n
        lat, lon = orbit_point(ang, radius)
        toward = (ang + 180) % 360            # bearing from drone to tower
        yaw = toward if facing_in else toward + 180
        names.append(make_dji(folder / f"DJI_{start + i:04}.JPG", pitch, to_yaw(yaw), lat, lon).name)
    return names


def test_meta_is_read(tmp_path):
    p = make_dji(tmp_path / "a.jpg", "0.00", -123.4, 42.1, -83.2)
    m = read_photo_meta(p)
    assert (m.pitch, m.yaw, m.lat, m.lon) == (0.0, -123.4, 42.1, -83.2)


def test_longtitude_misspelling(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b'drone-dji:GimbalPitchDegree="0" drone-dji:GpsLatitude="1.5" drone-dji:GpsLongtitude="-2.5"')
    assert read_photo_meta(p).lon == -2.5


def test_center_in_and_out_split(tmp_path):
    f = tmp_path / "002"
    ins = shoot_orbit(f, 1, "0.00", facing_in=True, start_at=0)      # starts north
    outs = shoot_orbit(f, 100, "0.00", facing_in=False, start_at=180, radius=25)  # starts south
    res = {r.source.name: r for r in sort_photos([f], CATS, dry_run=True, **KW)}
    assert all(res[n].category == "Center In" for n in ins)
    assert all(res[n].category == "Center Out" for n in outs)
    assert res[outs[0]].rule == "camera faces away from tower"


def test_works_with_only_part_of_an_orbit_and_other_shots(tmp_path):
    f = tmp_path / "003"
    shoot_orbit(f, 1, "-40.00", facing_in=True, n=8)                 # tower flight around it
    outs = shoot_orbit(f, 50, "0.00", facing_in=False, n=3, start_at=90)  # only 3 out shots
    res = {r.source.name: r for r in sort_photos([f], CATS, dry_run=True, **KW)}
    assert all(res[n].category == "Center Out" for n in outs)


def test_no_gps_stays_center_in(tmp_path):
    f = tmp_path / "004"
    make_dji(f / "DJI_0001.JPG", "0.00")
    res = sort_photos([f], CATS, dry_run=True, **KW)
    assert res[0].category == "Center In"


def test_access_road_created_and_never_sorted(tmp_path):
    f = tmp_path / "005"
    shoot_orbit(f, 1, "0.00", facing_in=True, n=4)
    sub = f / "Battery 2"
    make_dji(sub / "DJI_0100.JPG", "-90.00")
    sort_photos([f], CATS, **KW)
    assert (f / "Access Road").is_dir() and (sub / "Access Road").is_dir()
    # GoPro photos dropped in later are left alone on the next run
    (f / "Access Road" / "GOPR0001.JPG").write_bytes(b"gopro")
    res = sort_photos([f], CATS, **KW)
    assert all("Access Road" not in r.source.parts for r in res)
    assert (f / "Access Road" / "GOPR0001.JPG").read_bytes() == b"gopro"


def test_preview_creates_no_folders(tmp_path):
    f = tmp_path / "006"
    make_dji(f / "DJI_0001.JPG", "0.00")
    sort_photos([f], CATS, dry_run=True, **KW)
    assert not (f / "Access Road").exists()
