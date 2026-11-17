"""Rooftop sectors: separate Downlook N folders, Center In/Out per sector."""

import math

import pytest

from drone_sorter.pitch_reader import read_photo_meta
from drone_sorter.sorter import (
    DEFAULT_CONFIG, categories_from_config, extra_folders_from_config,
    facing_rules_from_config, rooftop_from_config, rules_from_config, sector_rule_from_config,
    sort_photos,
)
from helpers import make_dji

CFG = DEFAULT_CONFIG
CATS = categories_from_config(CFG)
KW = dict(rules=rules_from_config(CFG), facing_rules=facing_rules_from_config(CFG),
          extra_folders=extra_folders_from_config(CFG), sector_rule=sector_rule_from_config(CFG),
          rooftop=rooftop_from_config(CFG))


def dji(deg):
    return (deg + 180) % 360 - 180


class Shooter:
    """Writes photos in shooting order with a running clock."""

    def __init__(self, folder, whole_seconds=True):
        self.folder, self.n, self.t, self.whole = folder, 1, 0.0, whole_seconds

    def shot(self, pitch, yaw=0.0, gap=0.7):
        self.t += gap
        t = math.floor(self.t) if self.whole else round(self.t, 2)
        name = f"DJI_{self.n:04}.JPG"
        make_dji(self.folder / name, pitch, dji(yaw), taken=t)
        self.n += 1
        return name

    def burst(self, pitch, n, yaw=0.0, gap=1.5):
        return [self.shot(pitch, yaw, gap) for _ in range(n)]

    def arc(self, start, degrees, n, gap=0.7, first_gap=None):
        names = []
        for i in range(n):
            g = first_gap if (i == 0 and first_gap is not None) else gap
            names.append(self.shot("0.00", start + degrees * i / (n - 1), g))
        return names

    def sector(self, start_yaw, uplook=True, pause=5.0, n=20):
        down = self.burst("-35.00", 3, start_yaw, gap=4)
        ins = self.arc(start_yaw, 180, n, first_gap=6)
        outs = self.arc(start_yaw + 180 + 5, 175, n, first_gap=pause)   # carries on turning, no flip
        ups = self.burst("+20.00", 2, start_yaw, gap=6) if uplook else []
        return down, ins, outs, ups


def run(folder):
    return {r.source.name: r for r in sort_photos([folder], CATS, dry_run=True, **KW)}


def check(res, names, cat):
    got = [res[n].category for n in names]
    assert got == [cat] * len(names), (cat, got)


def test_three_sectors_with_uplooks(tmp_path):
    s = Shooter(tmp_path / "Roof")
    sectors = [s.sector(70), s.sector(200), s.sector(310)]
    res = run(tmp_path / "Roof")
    for n, (down, ins, outs, ups) in enumerate(sectors, 1):
        check(res, down, f"Downlook {n}")
        check(res, ins, "Center In")
        check(res, outs, "Center Out")
        check(res, ups, "Uplook")
    assert "pause" in res[sectors[1][2][0]].rule
    assert not any(r.category == "Downlook" for r in res.values())


def test_sectors_without_uplooks(tmp_path):
    s = Shooter(tmp_path / "Roof")
    sectors = [s.sector(a, uplook=False) for a in (10, 140, 260)]
    res = run(tmp_path / "Roof")
    for n, (down, ins, outs, _) in enumerate(sectors, 1):
        check(res, down, f"Downlook {n}")
        check(res, ins, "Center In")
        check(res, outs, "Center Out")


@pytest.mark.parametrize("pause,whole", [(5.0, True), (3.0, False), (12.0, True)])
def test_pause_lengths(tmp_path, pause, whole):
    s = Shooter(tmp_path / "Roof", whole_seconds=whole)
    a = s.sector(45, pause=pause)
    b = s.sector(225, pause=pause)
    res = run(tmp_path / "Roof")
    for down, ins, outs, _ in (a, b):
        check(res, ins, "Center In")
        check(res, outs, "Center Out")


def test_small_hiccup_is_not_the_pause(tmp_path):
    s = Shooter(tmp_path / "Roof")
    s.burst("-35.00", 2, gap=4)
    first = s.arc(0, 170, 20, first_gap=6)
    second = s.arc(172, 180, 20, first_gap=2)     # only a 2 s hiccup
    s.burst("-35.00", 2, gap=4)
    s.arc(90, 350, 30, first_gap=6)
    res = run(tmp_path / "Roof")
    check(res, first + second, "Center In")
    assert "no Center Out found" in res[first[0]].rule


def test_tower_site_unchanged(tmp_path):
    s = Shooter(tmp_path / "Tower")
    tower = s.burst("-40.00", 4)
    down = s.burst("-35.00", 3)
    ins = [s.shot("0.00", y) for y in range(0, 361, 30)]
    outs = [s.shot("0.00", 180 + y) for y in range(0, 361, 30)]     # flips ~180
    raise_ = [s.shot(p) for p in ("-45.00", "-45.00", "-38.0", "-35.0", "-30.0", "-20.00")]
    res = run(tmp_path / "Tower")
    check(res, tower, "Tower Flight")
    check(res, down, "Downlook")                  # one set: plain Downlook, no Sector 2
    check(res, ins, "Center In")
    check(res, outs, "Center Out")
    check(res, raise_, "Compound Flight")         # the -35 in the raise isn't a sector


def test_shot_order_off_means_no_sectors(tmp_path):
    s = Shooter(tmp_path / "Roof")
    s.sector(0)
    s.sector(180)
    kw = dict(KW, rules=[], sector_rule=None)
    res = sort_photos([tmp_path / "Roof"], CATS, dry_run=True, **kw)
    assert not any(r.category.startswith("Downlook ") for r in res)


def test_rerun_skips_sector_folders(tmp_path):
    s = Shooter(tmp_path / "Roof")
    s.sector(0)
    s.sector(180)
    first = sort_photos([tmp_path / "Roof"], CATS, **KW)
    assert (tmp_path / "Roof" / "Downlook 2").is_dir()
    again = sort_photos([tmp_path / "Roof"], CATS, **KW)
    assert len(again) == len(first)
    assert all(not r.source.parent.name.startswith("Downlook ") for r in again)


def test_time_from_xmp_with_fraction(tmp_path):
    p = make_dji(tmp_path / "a.jpg", "0.00", taken=12.5)
    q = make_dji(tmp_path / "b.jpg", "0.00", taken=15.25)
    assert read_photo_meta(q).taken - read_photo_meta(p).taken == pytest.approx(2.75)


def exif_jpeg(stamp: str, sub: str) -> bytes:
    """Minimal JPEG with an EXIF block holding DateTimeOriginal + SubSecTimeOriginal."""
    import struct
    date = stamp.encode() + b"\x00"            # 20 bytes, stored at an offset
    subb = sub.encode() + b"\x00"              # <= 4 bytes, stored inline
    ifd0 = 8
    exif_ifd = ifd0 + 2 + 12 + 4
    date_off = exif_ifd + 2 + 2 * 12 + 4
    tiff = b"II*\x00" + struct.pack("<I", ifd0)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif_ifd) + struct.pack("<I", 0)
    tiff += struct.pack("<H", 2)
    tiff += struct.pack("<HHII", 0x9003, 2, len(date), date_off)
    tiff += struct.pack("<HHI", 0x9291, 2, len(subb)) + subb.ljust(4, b"\x00")
    tiff += struct.pack("<I", 0) + date
    app1 = b"Exif\x00\x00" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1


def test_time_from_exif_with_subseconds(tmp_path):
    times = []
    for i, (stamp, sub) in enumerate((("2026:11:14 10:00:01", "300"), ("2026:11:14 10:00:04", "050"))):
        f = tmp_path / f"{i}.jpg"
        f.write_bytes(exif_jpeg(stamp, sub) + b'drone-dji:GimbalPitchDegree="0.00"')
        times.append(read_photo_meta(f).taken)
    assert times[1] - times[0] == pytest.approx(2.75)


def test_rooftop_renames_compound_to_bso_and_adds_cable_run(tmp_path):
    roof = tmp_path / "Roof"
    s = Shooter(roof)
    s.sector(0)
    s.sector(180)
    bso = [s.shot("-45.00"), s.shot("-30.0"), s.shot("-20.00")]
    res = {r.source.name: r for r in sort_photos([roof], CATS, **KW)}
    check(res, bso, "BSO")
    assert (roof / "BSO").is_dir() and not (roof / "Compound Flight").exists()
    assert (roof / "Cable Run").is_dir() and (roof / "Access Road").is_dir()
    assert (roof / "Downlook 1").is_dir() and (roof / "Downlook 2").is_dir()
    assert not (roof / "Downlook").exists()
    # re-run doesn't pick up anything from BSO / Downlook N / Cable Run
    (roof / "Cable Run" / "IMG_0001.JPG").write_bytes(b"phone")
    again = sort_photos([roof], CATS, **KW)
    assert {r.source.parent for r in again} == {roof}


def test_tower_keeps_compound_flight_and_no_cable_run(tmp_path):
    tower = tmp_path / "Tower"
    s = Shooter(tower)
    s.burst("-35.00", 3)
    s.arc(0, 360, 12)
    comp = [s.shot("-45.00"), s.shot("-20.00")]
    res = {r.source.name: r for r in sort_photos([tower], CATS, **KW)}
    check(res, comp, "Compound Flight")
    assert not (tower / "Cable Run").exists() and not (tower / "BSO").exists()


def test_settings_from_6_8_get_downlook_names(tmp_path):
    import json
    from drone_sorter.sorter import ensure_config_file
    p = tmp_path / "categories.json"
    p.write_text(json.dumps({"config_version": 2, "categories": [],
                             "sector_rule": {"category": "Downlook", "between": "Center In",
                                             "folder": "Sector {n}"}}))
    ensure_config_file(p)
    cfg = json.loads(p.read_text())
    assert cfg["sector_rule"]["folder"] == "Downlook {n}" and cfg["config_version"] == 3


def tower_set(s, start=0, pause_in_middle=None):
    """Downlook, then a full ~360 Center In orbit, a ~180 flip, a full Center Out orbit."""
    down = s.burst("-35.00", 3, gap=4)
    ins = []
    for i, y in enumerate(range(0, 361, 20)):
        gap = pause_in_middle if (pause_in_middle and i == 9) else 0.7
        ins.append(s.shot("0.00", start + y, gap if i else 6))
    outs = [s.shot("0.00", start + 180 + y, 0.7 if y else 3) for y in range(0, 361, 20)]
    ups = s.burst("+20.00", 2, gap=6)
    return down, ins, outs, ups


def test_tower_shot_twice_is_not_a_rooftop(tmp_path):
    t = tmp_path / "Tower"
    s = Shooter(t)
    s.burst("-40.00", 4)
    a = tower_set(s, start=0)
    b = tower_set(s, start=90)
    s.shot("-45.00"); s.shot("-20.00")
    res = {r.source.name: r for r in sort_photos([t], CATS, **KW)}
    for down, ins, outs, ups in (a, b):
        check(res, down, "Downlook")          # one Downlook folder, no Downlook 1/2
        check(res, ins, "Center In")
        check(res, outs, "Center Out")
        check(res, ups, "Uplook")
    assert (t / "Compound Flight").is_dir()
    assert not (t / "BSO").exists() and not (t / "Cable Run").exists()
    assert not (t / "Downlook 1").exists()


@pytest.mark.parametrize("sets", [1, 2])
def test_pause_never_splits_center_in_on_towers(tmp_path, sets):
    t = tmp_path / "Tower"
    s = Shooter(t)
    groups = [tower_set(s, start=30 * i, pause_in_middle=15) for i in range(sets)]
    res = run(t)
    for down, ins, outs, ups in groups:
        check(res, ins, "Center In")          # the 15 s pause mid-orbit is ignored
        check(res, outs, "Center Out")


def test_tower_single_orbit_with_pause_stays_center_in(tmp_path):
    t = tmp_path / "Tower"
    s = Shooter(t)
    s.burst("-35.00", 3)
    ins = s.arc(0, 350, 30, gap=0.7)
    ins += s.arc(352, 5, 3, first_gap=20)     # long pause near the end
    res = run(t)
    check(res, ins, "Center In")
