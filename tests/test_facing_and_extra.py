"""Center In vs Center Out by camera direction, and the Access Road folder."""

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
def test_meta_is_read(tmp_path):
    p = make_dji(tmp_path / "a.jpg", "0.00", -123.4, 42.1, -83.2)  # GPS still read, unused
    m = read_photo_meta(p)
    assert (m.pitch, m.yaw, m.lat, m.lon) == (0.0, -123.4, 42.1, -83.2)


def test_longtitude_misspelling(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b'drone-dji:GimbalPitchDegree="0" drone-dji:GpsLatitude="1.5" drone-dji:GpsLongtitude="-2.5"')
    assert read_photo_meta(p).lon == -2.5


def to_dji(deg):
    return (deg + 180) % 360 - 180


def orbit_yaws(start, n, jitter=0):
    """A full orbit: start near ``start`` and come back around to it."""
    import random
    rnd = random.Random(n + start)
    return [to_dji(start + i * 360 / n + rnd.uniform(-jitter, jitter)) for i in range(n + 1)]


def shoot(folder, yaws, pitch="0.00", first=1):
    return [make_dji(folder / f"DJI_{first + i:04}.JPG", pitch, y).name for i, y in enumerate(yaws)]


def sort_names(folder):
    return {r.source.name: r for r in sort_photos([folder], CATS, dry_run=True, **KW)}


def test_two_orbits_split_at_the_flip(tmp_path):
    f = tmp_path / "002"
    ins = shoot(f, orbit_yaws(0, 16, jitter=12))                 # ~north, around to ~north
    outs = shoot(f, orbit_yaws(180, 16, jitter=12), first=100)   # flips to ~south, around
    res = sort_names(f)
    assert [res[n].category for n in ins] == ["Center In"] * len(ins)
    assert [res[n].category for n in outs] == ["Center Out"] * len(outs)
    assert res[outs[0]].rule.startswith("second orbit")


def test_starting_directions_are_not_hard_limits(tmp_path):
    f = tmp_path / "003"
    ins = shoot(f, orbit_yaws(25, 10, jitter=10))    # started a bit east of north
    outs = shoot(f, orbit_yaws(230, 10, jitter=10), first=50)
    res = sort_names(f)
    assert all(res[n].category == "Center In" for n in ins)
    assert all(res[n].category == "Center Out" for n in outs)


def test_coarse_orbit_steps_are_not_mistaken_for_the_flip(tmp_path):
    f = tmp_path / "004"
    ins = shoot(f, orbit_yaws(0, 4))          # 90 degree steps, only one orbit
    res = sort_names(f)
    assert all(res[n].category == "Center In" for n in ins)


def test_other_angles_in_between_dont_matter(tmp_path):
    f = tmp_path / "005"
    shoot(f, orbit_yaws(0, 8), first=1)
    shoot(f, [10, 120, -100], pitch="-40.00", first=20)   # tower flight shots
    outs = shoot(f, orbit_yaws(180, 8), first=40)
    res = sort_names(f)
    assert all(res[n].category == "Center Out" for n in outs)
    assert res["DJI_0020.JPG"].category == "Tower Flight"


def test_no_direction_data_stays_center_in(tmp_path):
    f = tmp_path / "006"
    make_dji(f / "DJI_0001.JPG", "0.00")
    make_dji(f / "DJI_0002.JPG", "0.00")
    assert {r.category for r in sort_photos([f], CATS, dry_run=True, **KW)} == {"Center In"}


def test_access_road_created_and_never_sorted(tmp_path):
    f = tmp_path / "005"
    shoot(f, orbit_yaws(0, 4))
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
