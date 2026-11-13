"""Build tiny fake DJI photos with an embedded XMP packet."""

from pathlib import Path

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def xmp(pitch, element_form=False):
    if pitch is None:
        body = '<rdf:Description drone-dji:FlightPitchDegree="+3.10"/>'
    elif element_form:
        body = (f"<rdf:Description><drone-dji:GimbalPitchDegree>{pitch}"
                f"</drone-dji:GimbalPitchDegree></rdf:Description>")
    else:
        body = (f'<rdf:Description drone-dji:FlightPitchDegree="+3.10" '
                f'drone-dji:GimbalPitchDegree="{pitch}" '
                f'drone-dji:GimbalYawDegree="-12.30"/>')
    return f'<x:xmpmeta xmlns:x="adobe:ns:meta/">{body}</x:xmpmeta>'.encode()


def make_jpg(path: Path, pitch, element_form=False, payload=b"img", padding=0):
    """padding puts filler bytes *before* the XMP (simulates a DNG layout)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SOI + b"\x00" * padding + xmp(pitch, element_form) + payload + EOI)
    return path
