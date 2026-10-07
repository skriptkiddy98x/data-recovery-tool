#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recovery_meta.py — reads the date EMBEDDED inside a file (not the deletion date,
which carving cannot know). Works for: JPEG (EXIF), PDF, ZIP/docx/xlsx, MP4.
For other types it returns None (date unknown).
"""

import re
import struct
import datetime


def _exif_date(path):
    """DateTimeOriginal / DateTime from JPEG EXIF -> datetime or None."""
    try:
        with open(path, "rb") as f:
            data = f.read(256 * 1024)  # EXIF is near the start of the file
    except OSError:
        return None
    i = data.find(b"Exif\x00\x00")
    if i == -1:
        return None
    tiff = data[i + 6:]
    if len(tiff) < 8:
        return None
    endian = tiff[:2]
    if endian == b"II":
        bo = "<"
    elif endian == b"MM":
        bo = ">"
    else:
        return None

    def u16(o): return struct.unpack(bo + "H", tiff[o:o + 2])[0]
    def u32(o): return struct.unpack(bo + "I", tiff[o:o + 4])[0]

    try:
        ifd0 = u32(4)
        found = {}

        def read_ifd(off):
            if off <= 0 or off + 2 > len(tiff):
                return None
            n = u16(off)
            exif_ptr = None
            for k in range(n):
                e = off + 2 + k * 12
                if e + 12 > len(tiff):
                    break
                tag = u16(e)
                typ = u16(e + 2)
                cnt = u32(e + 4)
                if tag in (0x0132, 0x9003, 0x9004) and typ == 2:  # ASCII dates
                    voff = e + 8
                    if cnt > 4:
                        voff = u32(e + 8)
                    s = tiff[voff:voff + cnt].split(b"\x00", 1)[0]
                    found[tag] = s.decode("ascii", "ignore")
                elif tag == 0x8769:  # pointer to the Exif sub-IFD
                    exif_ptr = u32(e + 8)
            return exif_ptr

        ptr = read_ifd(ifd0)
        if ptr:
            read_ifd(ptr)
        raw = found.get(0x9003) or found.get(0x0132) or found.get(0x9004)
        if raw:
            # format "YYYY:MM:DD HH:MM:SS"
            m = re.match(r"(\d{4})\D(\d{2})\D(\d{2})[ T](\d{2})\D(\d{2})\D(\d{2})", raw)
            if m:
                y, mo, d, h, mi, s = map(int, m.groups())
                try:
                    return datetime.datetime(y, mo, d, h, mi, s)
                except ValueError:
                    return None
    except (struct.error, IndexError):
        return None
    return None


def _pdf_date(path):
    try:
        with open(path, "rb") as f:
            data = f.read(2 * 1024 * 1024)
    except OSError:
        return None
    m = re.search(rb"/CreationDate\s*\(\s*D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?",
                  data)
    if not m:
        m = re.search(rb"/ModDate\s*\(\s*D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?",
                      data)
    if m:
        g = [int(x) if x else 0 for x in m.groups()]
        try:
            return datetime.datetime(g[0], g[1] or 1, g[2] or 1, g[3], g[4], g[5])
        except ValueError:
            return None
    return None


def _zip_date(path):
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            dts = [i.date_time for i in z.infolist() if i.date_time[0] >= 1980]
        if dts:
            y, mo, d, h, mi, s = max(dts)
            return datetime.datetime(y, mo, d, h, mi, s)
    except Exception:
        return None
    return None


def _mp4_date(path):
    """creation_time from the mvhd atom (seconds since 1904-01-01)."""
    EPOCH_1904 = datetime.datetime(1904, 1, 1)
    try:
        with open(path, "rb") as f:
            data = f.read(4 * 1024 * 1024)
    except OSError:
        return None
    i = data.find(b"mvhd")
    if i == -1:
        return None
    try:
        version = data[i + 4]
        if version == 0:
            secs = struct.unpack(">I", data[i + 8:i + 12])[0]
        else:
            secs = struct.unpack(">Q", data[i + 8:i + 16])[0]
        if 0 < secs < 20 * 365 * 24 * 3600 * 10:  # sane range
            return EPOCH_1904 + datetime.timedelta(seconds=secs)
    except (struct.error, IndexError):
        return None
    return None


_READERS = {
    "jpg": _exif_date,
    "pdf": _pdf_date,
    "zip": _zip_date,
    "docx": _zip_date,
    "mp4": _mp4_date,
}


def extract_date(path, ext):
    """Return the datetime embedded in the file, or None if it can't be read."""
    reader = _READERS.get(ext.lower())
    if not reader:
        return None
    try:
        return reader(path)
    except Exception:
        return None


# mapping: user-facing category -> which extensions
CATEGORIES = {
    "photos": ["jpg", "png", "gif"],
    "videos": ["mp4"],
    "documents": ["pdf", "zip", "doc"],
    "archives": ["zip", "rar", "7z", "gz"],
    "music": ["mp3", "wav"],
    "exe": ["exe"],
    "databases": ["sqlite"],
}

CATEGORY_LABELS = {
    "photos": "🖼  Photos (JPG, PNG, GIF)",
    "videos": "🎬  Videos (MP4)",
    "documents": "📄  Documents (PDF, Word, Excel)",
    "archives": "🗜  Archives (ZIP, RAR, 7z)",
    "music": "🎵  Music (MP3, WAV)",
    "exe": "⚙  Executables (EXE)",
    "databases": "🗃  Databases (SQLite)",
}


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        ext = p.rsplit(".", 1)[-1]
        print(p, "->", extract_date(p, ext))
