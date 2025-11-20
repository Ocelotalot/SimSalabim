"""Image format detection.

Reimplementation of the deprecated stdlib :mod:`imghdr` module for
environments where it is no longer available (Python 3.13+). The functions
match the behavior of the original module closely to satisfy third-party
libraries that still import :mod:`imghdr`.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

__all__ = ["what"]


def what(file: os.PathLike | str | bytes, h: Optional[bytes] = None) -> Optional[str]:
    """Return a string describing the image type.

    This function mirrors the public API of the deprecated stdlib ``imghdr``
    module. It inspects a filename or byte stream and returns a short format
    string such as ``"jpeg"`` or ``"png"`` when the type is detected.
    """

    if h is None:
        if isinstance(file, (str, bytes, os.PathLike)):
            try:
                with open(file, "rb") as f:
                    h = f.read(32)
            except OSError:
                return None
        else:
            try:
                h = file.read(32)
            except Exception:
                return None
    elif isinstance(h, memoryview):
        h = h.tobytes()

    for func in _tests:
        res = func(h)
        if res:
            return res
    return None


def _test_jpeg(h: bytes) -> Optional[str]:
    if h[6:10] in (b"JFIF", b"Exif") or h.startswith(b"\xff\xd8"):
        return "jpeg"
    return None


def _test_png(h: bytes) -> Optional[str]:
    if h.startswith(b"\211PNG\r\n\032\n"):
        return "png"
    return None


def _test_gif(h: bytes) -> Optional[str]:
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def _test_tiff(h: bytes) -> Optional[str]:
    if h[:4] in (b"MM\x00*", b"II*\x00"):
        return "tiff"
    return None


def _test_rgb(h: bytes) -> Optional[str]:
    if h.startswith(b"\x01\xda") or h.startswith(b"\xda\x01"):
        return "rgb"
    return None


def _test_pbm(h: bytes) -> Optional[str]:
    if h.startswith(b"P1"):
        return "pbm"
    return None


def _test_pgm(h: bytes) -> Optional[str]:
    if h.startswith(b"P2"):
        return "pgm"
    return None


def _test_ppm(h: bytes) -> Optional[str]:
    if h.startswith(b"P3"):
        return "ppm"
    return None


def _test_rast(h: bytes) -> Optional[str]:
    if h.startswith(b"\x59\xA6\x6A\x95"):
        return "rast"
    return None


def _test_xbm(h: bytes) -> Optional[str]:
    if h.startswith(b"#define "):
        return "xbm"
    return None


def _test_bmp(h: bytes) -> Optional[str]:
    if h.startswith(b"BM"):
        return "bmp"
    return None


def _test_webp(h: bytes) -> Optional[str]:
    if h.startswith(b"RIFF") and h[8:12] == b"WEBP":
        return "webp"
    return None


def _test_exr(h: bytes) -> Optional[str]:
    if h.startswith(b"\x76\x2f\x31\x01"):
        return "exr"
    return None


def _test_blf(h: bytes) -> Optional[str]:
    if h.startswith(b"BLP2"):
        return "blp"
    return None


def _test_jpx(h: bytes) -> Optional[str]:
    if h.startswith(b"\0\0\0\x0cjP  \r\n\x87\n"):
        return "jpx"
    return None


def _test_jxr(h: bytes) -> Optional[str]:
    if h.startswith(b"II\xbc\x01"):
        return "jxr"
    return None


def _test_pcx(h: bytes) -> Optional[str]:
    if h.startswith(b"\x0a\x05\x01\x08"):
        return "pcx"
    return None


def _test_heif(h: bytes) -> Optional[str]:
    if h[4:12] == b"ftypmif1" or h[4:12] == b"ftypheic":
        return "heif"
    if h[4:12] == b"ftypheix" or h[4:12] == b"ftyphevc":
        return "hevc"
    return None


def _test_ktx(h: bytes) -> Optional[str]:
    if h.startswith(b"\xabKTX 11\xbb\r\n\x1a\n"):
        return "ktx"
    return None


_tests: Iterable = (
    _test_jpeg,
    _test_png,
    _test_gif,
    _test_tiff,
    _test_rgb,
    _test_pbm,
    _test_pgm,
    _test_ppm,
    _test_rast,
    _test_xbm,
    _test_bmp,
    _test_webp,
    _test_exr,
    _test_blf,
    _test_jpx,
    _test_jxr,
    _test_pcx,
    _test_heif,
    _test_ktx,
)
