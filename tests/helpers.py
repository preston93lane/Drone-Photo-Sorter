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


def make_dji(path: Path, pitch, yaw=None, lat=None, lon=None, payload=b"img"):
    """Fake DJI photo with gimbal pitch, camera direction and GPS."""
    attrs = [f'drone-dji:GimbalPitchDegree="{pitch}"']
    if yaw is not None:
        attrs.append(f'drone-dji:GimbalYawDegree="{yaw:+.2f}"')
    if lat is not None:
        attrs.append(f'drone-dji:GpsLatitude="{lat:+.8f}" drone-dji:GpsLongitude="{lon:+.8f}"')
    body = f'<rdf:Description {" ".join(attrs)}/>'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SOI + f"<x:xmpmeta>{body}</x:xmpmeta>".encode() + payload + EOI)
    return path
