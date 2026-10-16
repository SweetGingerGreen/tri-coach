"""
Pick the photos taken during an activity and export upload-ready copies.

Capture time comes from EXIF DateTimeOriginal (plus OffsetTimeOriginal when
the camera wrote it), never from file dates: AirDrop and copies reset those.
HEIC files are converted with macOS ``sips``, which keeps EXIF.

Exported copies are rotated upright, resized, and re-encoded as JPEG with no
EXIF at all, so GPS coordinates never reach Strava.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
HEIF_SUFFIXES = {".heic", ".heif"}
EXIF_IFD = 0x8769
TAG_DATETIME = 0x0132
TAG_DATETIME_ORIGINAL = 0x9003
TAG_OFFSET_TIME_ORIGINAL = 0x9011
EXIF_TIME_FORMAT = "%Y:%m:%d %H:%M:%S"
MAX_EDGE_PX = 2048


@dataclass
class Photo:
    path: Path
    taken_at: dt.datetime


class _Readable:
    """Context manager yielding a path Pillow can open (HEIC -> temp JPEG)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._tmpdir: str | None = None

    def __enter__(self) -> Path:
        if self.path.suffix.lower() not in HEIF_SUFFIXES:
            return self.path
        if shutil.which("sips") is None:
            raise RuntimeError("HEIC needs macOS `sips`; convert the photo to JPEG first.")
        self._tmpdir = tempfile.mkdtemp(prefix="tri-coach-heic-")
        out = Path(self._tmpdir) / (self.path.stem + ".jpg")
        subprocess.run(
            ["sips", "-s", "format", "jpeg", str(self.path), "--out", str(out)],
            check=True,
            capture_output=True,
        )
        return out

    def __exit__(self, *exc: object) -> None:
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)


def _parse_offset(raw: str) -> dt.timezone | None:
    raw = raw.strip().rstrip("\x00")
    if len(raw) != 6 or raw[0] not in "+-" or raw[3] != ":":
        return None
    sign = 1 if raw[0] == "+" else -1
    return dt.timezone(sign * dt.timedelta(hours=int(raw[1:3]), minutes=int(raw[4:6])))


def capture_time(path: Path, assume_tz: dt.tzinfo) -> dt.datetime | None:
    """EXIF capture time as an aware datetime, or None when the file has none."""
    try:
        with _Readable(path) as readable, Image.open(readable) as image:
            exif = image.getexif()
    except (OSError, subprocess.CalledProcessError, RuntimeError):
        return None
    sub = exif.get_ifd(EXIF_IFD)
    raw = sub.get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME)
    if not raw:
        return None
    try:
        naive = dt.datetime.strptime(str(raw).strip().rstrip("\x00"), EXIF_TIME_FORMAT)
    except ValueError:
        return None
    tz = _parse_offset(str(sub.get(TAG_OFFSET_TIME_ORIGINAL, ""))) or assume_tz
    return naive.replace(tzinfo=tz)


def find_photos(
    dirs: list[Path],
    start: dt.datetime,
    end: dt.datetime,
    before: dt.timedelta = dt.timedelta(minutes=30),
    after: dt.timedelta = dt.timedelta(minutes=60),
) -> tuple[list[Photo], list[Path]]:
    """Return (photos inside [start - before, end + after], images with no capture time).

    ``start`` and ``end`` must be timezone-aware. Photos without an EXIF offset
    are read in ``start``'s timezone. Only the top level of each dir is scanned.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    lo, hi = start - before, end + after
    matched: list[Photo] = []
    undated: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            taken_at = capture_time(path, start.tzinfo)
            if taken_at is None:
                undated.append(path)
            elif lo <= taken_at <= hi:
                matched.append(Photo(path, taken_at))
    matched.sort(key=lambda photo: photo.taken_at)
    return matched, undated


def export_photo(source: Path, out_path: Path, max_edge: int = MAX_EDGE_PX) -> Path:
    """Write an upright, resized JPEG copy with every EXIF tag (GPS included) dropped."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with _Readable(source) as readable, Image.open(readable) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
    upright.thumbnail((max_edge, max_edge))
    upright.save(out_path, "JPEG", quality=90, optimize=True)
    return out_path
