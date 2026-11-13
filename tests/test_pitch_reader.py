import pytest

from drone_sorter.pitch_reader import FIRST_CHUNK, CHUNK, parse_pitch, read_gimbal_pitch
from helpers import make_jpg


@pytest.mark.parametrize("raw,expected", [
    ("-40.00", -40.0), ("+20.00", 20.0), ("0.00", 0.0), ("-90", -90.0), ("-34.7", -34.7),
])
def test_attribute_form(tmp_path, raw, expected):
    assert read_gimbal_pitch(make_jpg(tmp_path / "a.jpg", raw)) == expected


def test_element_form(tmp_path):
    assert read_gimbal_pitch(make_jpg(tmp_path / "a.jpg", "-25.00", element_form=True)) == -25.0


def test_missing_tag_returns_none(tmp_path):
    # FlightPitchDegree must NOT be mistaken for the gimbal pitch
    assert read_gimbal_pitch(make_jpg(tmp_path / "a.jpg", None)) is None


def test_late_xmp_found_by_full_scan(tmp_path):
    p = make_jpg(tmp_path / "a.dng", "-45.00", padding=FIRST_CHUNK + CHUNK + 50)
    assert read_gimbal_pitch(p, full_scan=False) is None
    assert read_gimbal_pitch(p) == -45.0


@pytest.mark.parametrize("offset", range(-60, 10, 7))
def test_tag_split_across_chunk_boundary(tmp_path, offset):
    p = make_jpg(tmp_path / "a.dng", "-20.00", padding=FIRST_CHUNK + offset)
    assert read_gimbal_pitch(p) == -20.0


def test_parse_pitch_bytes():
    assert parse_pitch(b'drone-dji:GimbalPitchDegree = "-35.00"') == -35.0
    assert parse_pitch(b"nothing here") is None
