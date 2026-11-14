import pytest

from drone_sorter import updater
from drone_sorter.updater import Release, is_newer, parse_version, release_from_api


def test_versions():
    assert parse_version("v6.3.0") == (6, 3, 0)
    assert parse_version("6.10") == (6, 10, 0)
    assert is_newer("v6.10.0", "6.9.9") and not is_newer("v6.3.0", "6.3.0")


def api(tag, assets=True, **extra):
    data = {"tag_name": tag, "assets": [
        {"name": "DronePitchSorter.exe", "browser_download_url": "http://x/e.exe", "size": 10}
    ] if assets else []}
    data.update(extra)
    return data


def test_release_from_api():
    assert release_from_api(api("v99.0.0")).version == "99.0.0"
    assert release_from_api(api("v0.1.0")) is None               # older
    assert release_from_api(api("v99.0.0", assets=False)) is None
    assert release_from_api(api("v99.0.0", prerelease=True)) is None


@pytest.fixture
def fake_exe(tmp_path, monkeypatch):
    exe = tmp_path / "DronePitchSorter.exe"
    exe.write_bytes(b"MZ old version")
    monkeypatch.setattr(updater, "current_exe", lambda: exe)
    return exe


def test_install_swaps_exe(tmp_path, fake_exe):
    new = tmp_path / "download.exe"
    new.write_bytes(b"MZ new version!")
    updater.download_and_install(Release("99.0.0", new.as_uri(), new.stat().st_size))
    assert fake_exe.read_bytes() == b"MZ new version!"
    assert (tmp_path / "DronePitchSorter.old.exe").read_bytes() == b"MZ old version"


@pytest.mark.parametrize("content,size", [(b"<html>not an exe", 0), (b"MZ partial", 9999)])
def test_bad_download_leaves_current_exe_alone(tmp_path, fake_exe, content, size):
    bad = tmp_path / "download.exe"
    bad.write_bytes(content)
    with pytest.raises(OSError):
        updater.download_and_install(Release("99.0.0", bad.as_uri(), size))
    assert fake_exe.read_bytes() == b"MZ old version"
    assert not (tmp_path / "DronePitchSorter.new.exe").exists()


def test_network_error_means_no_update(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    assert updater.check_for_update() is None
