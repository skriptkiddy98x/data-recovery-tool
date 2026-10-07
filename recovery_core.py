#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
recovery_core.py — deleted-file recovery engine (file carving) + CLI.

Works on Windows and Linux. Reads a disk / USB / partition / image byte by
byte and, using known signatures (header + optional footer), extracts files
that have not been overwritten yet. The SOURCE IS READ-ONLY — it is never
written to.

Examples:
  # list available disks
  python recovery_core.py --list

  # recover from a USB on Linux (needs root), output to ./recovered
  sudo python3 recovery_core.py /dev/sdb -o recovered

  # recover from a physical disk on Windows (run as Administrator)
  python recovery_core.py \\.\PhysicalDrive1 -o D:\recovered

  # recover from the E: partition on Windows
  python recovery_core.py \\.\E: -o D:\recovered

  # recover from a disk image created with dd
  python recovery_core.py disk.img -o recovered --all

IMPORTANT:
  * The output directory (-o) must be on a DIFFERENT drive than the source,
    otherwise writing a recovered file may overwrite what you are recovering.
  * Reading raw devices requires administrator / root privileges.
"""

import argparse
import os
import sys
import platform
import subprocess

# --- file-type definitions ---------------------------------------------------
# header        : bytes at the start of the file
# footer        : bytes at the end (None = no reliable footer -> cut at max_size)
# max_size      : upper size limit (guards against unbounded growth)
# min_size      : smaller results are discarded (likely false positives)
# header_offset : how many bytes before the matched pattern the file really
#                 starts (e.g. MP4 has 'ftyp' at byte 5)

class FileType:
    def __init__(self, name, ext, header, footer=None,
                 max_size=50 * 1024 * 1024, min_size=1024, header_offset=0,
                 footer_pad=0, category="other", validate=None, trim=None):
        self.name = name
        self.ext = ext
        self.header = header
        self.footer = footer
        self.max_size = max_size
        self.min_size = min_size
        self.header_offset = header_offset
        self.footer_pad = footer_pad  # extra bytes to include after the footer
        self.category = category      # photos/videos/documents/archives/music/exe/databases
        self.validate = validate      # optional (path)->bool after trimming
        self.trim = trim              # optional (path)->int exact length


def _pe_validate(path):
    """Confirm an MZ file is a real Windows PE (exe/dll) — cuts false matches."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        if head[:2] != b"MZ" or len(head) < 0x40:
            return False
        e_lfanew = int.from_bytes(head[0x3C:0x40], "little")
        return 0 < e_lfanew < len(head) - 4 and head[e_lfanew:e_lfanew + 4] == b"PE\x00\x00"
    except OSError:
        return False


def _le(b, o, n):
    return int.from_bytes(b[o:o + n], "little")


def _be(b, o, n):
    return int.from_bytes(b[o:o + n], "big")


def trim_pe(path):
    """Return the exact length of a PE file (exe/dll) from its section table."""
    try:
        with open(path, "rb") as f:
            b = f.read(2 * 1024 * 1024)
        if b[:2] != b"MZ":
            return None
        pe = _le(b, 0x3C, 4)
        if b[pe:pe + 4] != b"PE\x00\x00":
            return None
        nsec = _le(b, pe + 6, 2)
        opt = _le(b, pe + 20, 2)
        sect = pe + 24 + opt
        end = sect + nsec * 40
        for k in range(nsec):
            e = sect + k * 40
            raw_size = _le(b, e + 16, 4)
            raw_ptr = _le(b, e + 20, 4)
            if raw_ptr and raw_size:
                end = max(end, raw_ptr + raw_size)
        # the certificate table (digital signature) is appended at the end;
        # data directory #4 (Security) lives in the optional header
        magic = _le(b, pe + 24, 2)
        dd = pe + 24 + (112 if magic == 0x20b else 96)   # data directories offset
        cert_off = _le(b, dd + 4 * 8, 4)
        cert_size = _le(b, dd + 4 * 8 + 4, 4)
        if cert_off and cert_size:
            end = max(end, cert_off + cert_size)
        return end if end > 0 else None
    except (OSError, IndexError):
        return None


def trim_mp4(path):
    """Return the exact length of an MP4/MOV file by walking top-level boxes."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            pos = 0
            first = True
            while pos + 8 <= size:
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                bsize = _be(hdr, 0, 4)
                btype = hdr[4:8]
                if not all(32 <= ch < 127 for ch in btype):
                    break
                if first and btype != b"ftyp":
                    return None
                first = False
                if bsize == 1:            # 64-bit size
                    ext = f.read(8)
                    bsize = _be(ext, 0, 8)
                elif bsize == 0:          # box extends to end of file
                    return size
                if bsize < 8:
                    break
                pos += bsize
            return pos if pos > 0 else None
    except (OSError, IndexError):
        return None


# Base set — types with a clear header AND footer (few false positives)
BASE_TYPES = [
    FileType("jpeg", "jpg", b"\xFF\xD8\xFF", b"\xFF\xD9",
             max_size=30 * 1024 * 1024, min_size=4 * 1024, category="photos"),
    FileType("png", "png", b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82",
             max_size=60 * 1024 * 1024, min_size=100, category="photos"),
    FileType("gif", "gif", b"GIF8", b"\x00\x3B",
             max_size=20 * 1024 * 1024, min_size=256, category="photos"),
    FileType("pdf", "pdf", b"%PDF-", b"%%EOF",
             max_size=200 * 1024 * 1024, min_size=1024, footer_pad=2,
             category="documents"),
    # ZIP also covers docx / xlsx / pptx / odt / apk / jar
    FileType("zip", "zip", b"PK\x03\x04", b"PK\x05\x06",
             max_size=500 * 1024 * 1024, min_size=256, footer_pad=18,
             category="documents"),
]

# Extended set (--all) — more types, but also more false positives
EXTRA_TYPES = [
    FileType("mp4", "mp4", b"ftyp", None, header_offset=4,
             max_size=512 * 1024 * 1024, min_size=64 * 1024, category="videos",
             trim=trim_mp4),
    FileType("mp3", "mp3", b"ID3", None,
             max_size=30 * 1024 * 1024, min_size=16 * 1024, category="music"),
    FileType("gzip", "gz", b"\x1f\x8b\x08", None,
             max_size=100 * 1024 * 1024, min_size=256, category="archives"),
    FileType("rar", "rar", b"Rar!\x1a\x07", None,
             max_size=500 * 1024 * 1024, min_size=1024, category="archives"),
    FileType("7z", "7z", b"7z\xbc\xaf\x27\x1c", None,
             max_size=500 * 1024 * 1024, min_size=1024, category="archives"),
    FileType("sqlite", "sqlite", b"SQLite format 3\x00", None,
             max_size=100 * 1024 * 1024, min_size=512, category="databases"),
    FileType("doc_ole", "doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", None,
             max_size=100 * 1024 * 1024, min_size=4 * 1024, category="documents"),
    FileType("wav", "wav", b"RIFF", None, header_offset=0,
             max_size=200 * 1024 * 1024, min_size=4 * 1024, category="music"),
    # Executables (Windows PE: exe/dll). 'MZ' is common -> verified via PE header.
    FileType("exe", "exe", b"MZ", None,
             max_size=128 * 1024 * 1024, min_size=4 * 1024, category="exe",
             validate=_pe_validate, trim=trim_pe),
]

BLOCK = 4 * 1024 * 1024       # read in 4 MB chunks (sector multiple -> OK on Windows)
SECTOR = 4096                 # alignment for raw reads


# --- helpers -----------------------------------------------------------------

def list_disks():
    """Print available disks / partitions for the current OS."""
    sysname = platform.system()
    print("=== Available disks ===\n")
    if sysname == "Windows":
        print(r"Physical disks: use \\.\PhysicalDrive0, \\.\PhysicalDrive1, ...")
        print(r"Partitions (letters): use \\.\C:  \\.\E:  ...")
        print()
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Disk | Format-Table Number,FriendlyName,"
                 "@{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}},BusType -Auto"],
                capture_output=True, text=True, timeout=30)
            print(out.stdout or out.stderr)
        except Exception as e:
            print("(Get-Disk failed: %s)" % e)
    else:  # Linux / macOS
        print("Devices: use /dev/sdb, /dev/sdb1, /dev/nvme0n1, ...\n")
        if os.path.exists("/proc/partitions"):
            print(open("/proc/partitions").read())
        else:
            try:
                out = subprocess.run(["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT"],
                                     capture_output=True, text=True, timeout=30)
                print(out.stdout or out.stderr)
            except Exception as e:
                print("(could not list devices: %s)" % e)


def open_source(path):
    """Open the source read-only. Returns a file object."""
    # 'rb' works for image files and raw devices (\\.\PhysicalDriveN and /dev/sdX)
    return open(path, "rb", buffering=0)


def source_size(path, fh):
    """Try to determine the total source size (for percentages). May return None."""
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
    except OSError:
        pass
    try:
        cur = fh.seek(0, os.SEEK_CUR)
        end = fh.seek(0, os.SEEK_END)
        fh.seek(cur, os.SEEK_SET)
        if end > 0:
            return end
    except OSError:
        pass
    return None


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024
    return "%.1f PB" % n


# --- core: streaming carver --------------------------------------------------

class Carve:
    """One in-progress (open) file that we stream bytes into."""
    __slots__ = ("ft", "path", "fh", "size", "tail")

    def __init__(self, ft, path):
        self.ft = ft
        self.path = path
        self.fh = open(path, "wb")
        self.size = 0
        self.tail = b""   # trailing bytes (to find a footer across block boundaries)


def list_sources():
    """Return a list of (label, path) of available disks/partitions for the GUI."""
    items = []
    if platform.system() == "Windows":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Disk | ForEach-Object { \"$($_.Number)|$($_.FriendlyName)|$($_.Size)\" }"],
                capture_output=True, text=True, timeout=30)
            for line in out.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) == 3 and parts[0].isdigit():
                    num, name, size = parts
                    try:
                        gb = int(size) / (1024 ** 3)
                    except ValueError:
                        gb = 0
                    items.append(("Disk %s — %s (%.0f GB)" % (num, name.strip(), gb),
                                  r"\\.\PhysicalDrive%s" % num))
        except Exception:
            pass
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Volume | Where-Object DriveLetter | ForEach-Object "
                 "{ \"$($_.DriveLetter)|$($_.FileSystemLabel)|$($_.Size)\" }"],
                capture_output=True, text=True, timeout=30)
            for line in out.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) == 3 and parts[0]:
                    letter, label, size = parts
                    try:
                        gb = int(size) / (1024 ** 3)
                    except ValueError:
                        gb = 0
                    items.append(("Partition %s: %s (%.0f GB)" % (letter, label.strip(), gb),
                                  r"\\.\%s:" % letter))
        except Exception:
            pass
    else:
        try:
            out = subprocess.run(["lsblk", "-pnro", "NAME,SIZE,TYPE,MODEL"],
                                 capture_output=True, text=True, timeout=30)
            for line in out.stdout.splitlines():
                f = line.split(None, 3)
                if len(f) >= 3 and f[2] in ("disk", "part"):
                    name, size, typ = f[0], f[1], f[2]
                    model = f[3] if len(f) > 3 else ""
                    items.append(("%s %s %s %s" % (typ, name, size, model).strip(), name))
        except Exception:
            pass
    return items


def _log(log_cb, msg):
    if log_cb:
        log_cb(msg)
    else:
        print(msg)


def _safe_call(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return False


def run_carver(src_path, out_dir, types, max_open=300, quiet=False,
               progress_cb=None, log_cb=None, should_stop=None,
               per_file_cb=None, max_files=None):
    os.makedirs(out_dir, exist_ok=True)

    fh = open_source(src_path)
    total = source_size(src_path, fh)
    try:
        fh.seek(0, os.SEEK_SET)
    except OSError:
        pass

    max_header = max(len(t.header) + t.header_offset for t in types)
    carry = b""
    pos = 0                      # absolute offset of the start of 'data' in source
    state = {"counter": 0, "recovered": 0}
    stats = {}                   # ext -> count
    active = []                  # list of open Carve objects

    def commit(c):
        """Close the file, validate it, then save (renamed) or discard it.
        Returns True if it was saved."""
        c.fh.close()
        ft = c.ft
        # trim to the exact length (mp4/exe) if the type supports it
        if ft.trim:
            real = _safe_call(ft.trim, c.path)
            if isinstance(real, int) and 0 < real < c.size:
                try:
                    with open(c.path, "r+b") as tf:
                        tf.truncate(real)
                    c.size = real
                except OSError:
                    pass
        ok = c.size >= ft.min_size and (ft.validate is None
                                        or _safe_call(ft.validate, c.path))
        if not ok:
            try:
                os.remove(c.path)
            except OSError:
                pass
            return False
        state["counter"] += 1
        state["recovered"] += 1
        stats[ft.ext] = stats.get(ft.ext, 0) + 1
        final = os.path.join(out_dir, "recovered_%06d.%s" % (state["counter"], ft.ext))
        try:
            os.replace(c.path, final)
        except OSError:
            return False
        if per_file_cb:
            try:
                per_file_cb(final, ft)
            except Exception:
                pass
        return True

    def discard(c):
        c.fh.close()
        try:
            os.remove(c.path)
        except OSError:
            pass

    _log(log_cb, "Reading source: %s" % src_path)
    if total:
        _log(log_cb, "Source size: %s" % human(total))
    _log(log_cb, "Output: %s" % os.path.abspath(out_dir))
    _log(log_cb, "Types: %s" % ", ".join(t.ext for t in types))

    while True:
        if should_stop and should_stop():
            _log(log_cb, "Stopped by user.")
            break
        try:
            data = fh.read(BLOCK)
        except OSError as e:
            # some raw devices throw an error at the very end — treat as EOF
            _log(log_cb, "(read ended: %s)" % e)
            break
        if not data:
            break

        # --- A) feed already-open files with new data, look for the footer ---
        still = []
        for c in active:
            ft = c.ft
            if ft.footer:
                search = c.tail + data
                idx = search.find(ft.footer)
                if idx != -1:
                    end_in_data = idx + len(ft.footer) + ft.footer_pad - len(c.tail)
                    end_in_data = max(0, min(end_in_data, len(data)))
                    c.fh.write(data[:end_in_data])
                    c.size += end_in_data
                    commit(c)
                    continue  # file finished, not added back to 'still'
                else:
                    c.fh.write(data)
                    c.size += len(data)
                    if c.size > ft.max_size:
                        discard(c)  # grew too large -> discard
                        continue
                    c.tail = (c.tail + data)[-(len(ft.footer) + ft.footer_pad):]
                    still.append(c)
            else:
                # no footer: cut at max_size
                c.fh.write(data)
                c.size += len(data)
                if c.size >= ft.max_size:
                    commit(c)
                    continue
                still.append(c)
        active = still

        # --- B) look for new headers in buf (carry + data) ---
        buf = carry + data
        buf_start = pos - len(carry)
        for ft in types:
            if len(active) >= max_open:
                break
            start = 0
            while True:
                j = buf.find(ft.header, start)
                if j == -1:
                    break
                start = j + 1
                jabs = buf_start + j
                # dedup across the boundary: only take headers that were not
                # already fully present in the previous block
                if jabs + len(ft.header) <= pos:
                    continue
                hdr_abs = jabs - ft.header_offset
                if hdr_abs < 0:
                    continue
                # start of the file within buf
                s = hdr_abs - buf_start
                if s < 0:
                    continue
                if len(active) >= max_open:
                    break
                tmp = os.path.join(out_dir, ".part_%d_%d.tmp"
                                   % (state["counter"], len(active)))
                try:
                    c = Carve(ft, tmp)
                except OSError:
                    continue
                piece = buf[s:]
                # try to find the footer right away in this chunk
                if ft.footer:
                    fidx = piece.find(ft.footer, len(ft.header))
                    if fidx != -1:
                        end = fidx + len(ft.footer) + ft.footer_pad
                        end = min(end, len(piece))
                        c.fh.write(piece[:end])
                        c.size += end
                        commit(c)
                        continue
                    else:
                        c.fh.write(piece)
                        c.size += len(piece)
                        c.tail = piece[-(len(ft.footer) + ft.footer_pad):]
                        active.append(c)
                else:
                    c.fh.write(piece)
                    c.size += len(piece)
                    active.append(c)

        carry = buf[-max(max_header, 64):]
        pos += len(data)

        if progress_cb:
            progress_cb(pos, total, state["recovered"])
        elif not quiet:
            if total:
                pct = 100.0 * pos / total
                sys.stdout.write("\rProcessed: %s / %s (%.1f %%)  recovered: %d   "
                                 % (human(pos), human(total), pct, state["recovered"]))
            else:
                sys.stdout.write("\rProcessed: %s   recovered: %d   "
                                 % (human(pos), state["recovered"]))
            sys.stdout.flush()

        if max_files and state["recovered"] >= max_files:
            _log(log_cb, "Reached limit of %d files — stopping." % max_files)
            break

    # end of source — finalize files still open (footer-less / no end found)
    for c in active:
        commit(c)

    fh.close()
    _log(log_cb, "")
    _log(log_cb, "=== DONE ===")
    _log(log_cb, "Recovered files: %d" % state["recovered"])
    for ext, n in sorted(stats.items()):
        _log(log_cb, "   %-6s %d" % (ext, n))
    _log(log_cb, "Find them in: %s" % os.path.abspath(out_dir))
    _log(log_cb, "Note: carving cannot recover original file names and some hits "
                 "may be false or incomplete.")
    return {"recovered": state["recovered"], "stats": stats,
            "out_dir": os.path.abspath(out_dir)}


# --- CLI ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Recover deleted files from a disk / USB / image (file carving).")
    ap.add_argument("source", nargs="?",
                    help="source: device (/dev/sdb, \\\\.\\PhysicalDrive1, \\\\.\\E:) "
                         "or an image file (disk.img)")
    ap.add_argument("-o", "--output", default="recovered",
                    help="directory for recovered files (MUST be on another drive!)")
    ap.add_argument("--list", action="store_true",
                    help="list available disks and exit")
    ap.add_argument("--all", action="store_true",
                    help="also enable extended types (mp4, mp3, archives, sqlite, exe, ...)")
    ap.add_argument("--only", default=None,
                    help="only the given extensions, comma-separated, e.g. jpg,png,pdf")
    ap.add_argument("--max-open", type=int, default=300,
                    help="max simultaneously open carves (default 300)")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    args = ap.parse_args()

    if args.list:
        list_disks()
        return 0

    if not args.source:
        ap.print_help()
        return 1

    types = list(BASE_TYPES)
    if args.all:
        types += EXTRA_TYPES
    if args.only:
        wanted = {x.strip().lower() for x in args.only.split(",")}
        types = [t for t in (BASE_TYPES + EXTRA_TYPES) if t.ext in wanted]
        if not types:
            print("None of the given types is known. Available: %s"
                  % ", ".join(sorted({t.ext for t in BASE_TYPES + EXTRA_TYPES})))
            return 1

    # safety check: output must not be on the source
    try:
        if os.path.isfile(args.source):
            src_abs = os.path.abspath(args.source)
            out_abs = os.path.abspath(args.output)
            if out_abs.startswith(os.path.dirname(src_abs)):
                print("WARNING: output directory is next to the source image. "
                      "A different drive is recommended.")
    except OSError:
        pass

    try:
        run_carver(args.source, args.output, types,
                   max_open=args.max_open, quiet=args.quiet)
    except PermissionError:
        print("\nERROR: insufficient privileges to read the source.")
        print("Linux: run with 'sudo'.  Windows: run the terminal as Administrator.")
        return 2
    except FileNotFoundError:
        print("\nERROR: source '%s' not found. Check the name (see --list)."
              % args.source)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
