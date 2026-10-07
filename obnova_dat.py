#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
obnova_dat.py — nastroj na obnovu vymazanych suborov (file carving).

Funguje na Windows aj Linux, cita disk / USB / oddiel / image bajt po bajte
a podla znamych signatur (hlavicka + pripadne paticka) vyreze subory, ktore
este neboli prepisane. ZDROJ CITA LEN NA CITANIE — nikdy nan nezapisuje.

Pouzitie (priklady):
  # vypis dostupnych diskov
  python obnova_dat.py --list

  # obnova z USB na Linuxe (treba root), vysledok do priecinka obnovene
  sudo python3 obnova_dat.py /dev/sdb -o obnovene

  # obnova z fyzickeho disku na Windows (treba spustit ako Administrator)
  python obnova_dat.py \\.\PhysicalDrive1 -o D:\obnovene

  # obnova z oddielu E: na Windows
  python obnova_dat.py \\.\E: -o D:\obnovene

  # obnova z image suboru vytvoreneho cez dd
  python obnova_dat.py disk.img -o obnovene --all

DOLEZITE:
  * Vystupny priecinok (-o) musi byt na INOM disku nez zdroj, inak zapis
    noveho suboru moze prepisat prave to, co chces obnovit.
  * Citanie surovych diskov vyzaduje administratorske / root opravnenia.
"""

import argparse
import os
import sys
import platform
import subprocess

# --- definicia typov suborov -------------------------------------------------
# header        : bajty na zaciatku suboru
# footer        : bajty na konci (None = bez spolahlivej paticky -> rezeme po max_size)
# max_size      : horna hranica velkosti (ochrana proti nekonecnemu rastu)
# min_size      : mensie vysledky zahodime (najskor falosny nalez)
# header_offset : o kolko bajtov pred najdenym vzorom realne zacina subor
#                 (napr. MP4 ma 'ftyp' az na 5. bajte)

class FileType:
    def __init__(self, name, ext, header, footer=None,
                 max_size=50 * 1024 * 1024, min_size=1024, header_offset=0,
                 footer_pad=0, category="ine", validate=None, trim=None):
        self.name = name
        self.ext = ext
        self.header = header
        self.footer = footer
        self.max_size = max_size
        self.min_size = min_size
        self.header_offset = header_offset
        self.footer_pad = footer_pad  # kolko bajtov za paticku este pribalit
        self.category = category      # fotky/videa/dokumenty/archivy/hudba/exe/databazy
        self.validate = validate      # volitelna funkcia (cesta)->bool po dorezani
        self.trim = trim              # volitelna funkcia (cesta)->int presna dlzka


def _pe_validate(path):
    """Overi, ci MZ subor je naozaj Windows PE (exe/dll) — znizi falosne nalezy."""
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
    """Vrati presnu dlzku PE suboru (exe/dll) z tabulky sekcii, alebo None."""
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
        # tabulka certifikatov (digitalny podpis) je pripojena na konci
        # data directory #4 (Security) je v optional header
        magic = _le(b, pe + 24, 2)
        dd = pe + 24 + (112 if magic == 0x20b else 96)   # offset data directories
        cert_off = _le(b, dd + 4 * 8, 4)
        cert_size = _le(b, dd + 4 * 8 + 4, 4)
        if cert_off and cert_size:
            end = max(end, cert_off + cert_size)
        return end if end > 0 else None
    except (OSError, IndexError):
        return None


def trim_mp4(path):
    """Vrati presnu dlzku MP4/MOV suboru pospajanim top-level boxov, alebo None."""
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
                if bsize == 1:            # 64-bit velkost
                    ext = f.read(8)
                    bsize = _be(ext, 0, 8)
                elif bsize == 0:          # box po koniec suboru
                    return size
                if bsize < 8:
                    break
                pos += bsize
            return pos if pos > 0 else None
    except (OSError, IndexError):
        return None


# Zakladna sada — typy s jasnou hlavickou aj patickou (malo falosnych nalezov)
BASE_TYPES = [
    FileType("jpeg", "jpg", b"\xFF\xD8\xFF", b"\xFF\xD9",
             max_size=30 * 1024 * 1024, min_size=4 * 1024, category="fotky"),
    FileType("png", "png", b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82",
             max_size=60 * 1024 * 1024, min_size=100, category="fotky"),
    FileType("gif", "gif", b"GIF8", b"\x00\x3B",
             max_size=20 * 1024 * 1024, min_size=256, category="fotky"),
    FileType("pdf", "pdf", b"%PDF-", b"%%EOF",
             max_size=200 * 1024 * 1024, min_size=1024, footer_pad=2,
             category="dokumenty"),
    # ZIP pokryva aj docx / xlsx / pptx / odt / apk / jar
    FileType("zip", "zip", b"PK\x03\x04", b"PK\x05\x06",
             max_size=500 * 1024 * 1024, min_size=256, footer_pad=18,
             category="dokumenty"),
]

# Rozsirena sada (--all) — viac typov, ale aj viac falosnych nalezov
EXTRA_TYPES = [
    FileType("mp4", "mp4", b"ftyp", None, header_offset=4,
             max_size=512 * 1024 * 1024, min_size=64 * 1024, category="videa",
             trim=trim_mp4),
    FileType("mp3", "mp3", b"ID3", None,
             max_size=30 * 1024 * 1024, min_size=16 * 1024, category="hudba"),
    FileType("gzip", "gz", b"\x1f\x8b\x08", None,
             max_size=100 * 1024 * 1024, min_size=256, category="archivy"),
    FileType("rar", "rar", b"Rar!\x1a\x07", None,
             max_size=500 * 1024 * 1024, min_size=1024, category="archivy"),
    FileType("7z", "7z", b"7z\xbc\xaf\x27\x1c", None,
             max_size=500 * 1024 * 1024, min_size=1024, category="archivy"),
    FileType("sqlite", "sqlite", b"SQLite format 3\x00", None,
             max_size=100 * 1024 * 1024, min_size=512, category="databazy"),
    FileType("doc_ole", "doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", None,
             max_size=100 * 1024 * 1024, min_size=4 * 1024, category="dokumenty"),
    FileType("wav", "wav", b"RIFF", None, header_offset=0,
             max_size=200 * 1024 * 1024, min_size=4 * 1024, category="hudba"),
    # Spustitelne subory (Windows PE: exe/dll). 'MZ' je caste -> overujeme PE hlavickou.
    FileType("exe", "exe", b"MZ", None,
             max_size=128 * 1024 * 1024, min_size=4 * 1024, category="exe",
             validate=_pe_validate, trim=trim_pe),
]

BLOCK = 4 * 1024 * 1024       # citame po 4 MB (nasobok sektora -> OK aj na Windows)
SECTOR = 4096                 # zarovnanie pre surove citanie


# --- pomocne funkcie ---------------------------------------------------------

def list_disks():
    """Vypise dostupne disky / oddiely podla operacneho systemu."""
    sysname = platform.system()
    print("=== Dostupne disky ===\n")
    if sysname == "Windows":
        print("Fyzicke disky pouzivaj ako:  \\\\.\\PhysicalDrive0, \\\\.\\PhysicalDrive1, ...")
        print("Oddiely (pismena) ako:       \\\\.\\C:  \\\\.\\E:  ...\n")
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Disk | Format-Table Number,FriendlyName,"
                 "@{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}},BusType -Auto"],
                capture_output=True, text=True, timeout=30)
            print(out.stdout or out.stderr)
        except Exception as e:
            print("(Get-Disk zlyhal: %s)" % e)
    else:  # Linux / macOS
        print("Zariadenia pouzivaj ako:  /dev/sdb, /dev/sdb1, /dev/nvme0n1, ...\n")
        if os.path.exists("/proc/partitions"):
            print(open("/proc/partitions").read())
        else:
            try:
                out = subprocess.run(["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT"],
                                     capture_output=True, text=True, timeout=30)
                print(out.stdout or out.stderr)
            except Exception as e:
                print("(nepodarilo sa zistit zariadenia: %s)" % e)


def open_source(path):
    """Otvori zdroj len na citanie. Vrati file objekt."""
    # 'rb' funguje pre image subory aj surove zariadenia (\\.\PhysicalDriveN aj /dev/sdX)
    return open(path, "rb", buffering=0)


def source_size(path, fh):
    """Pokusi sa zistit celkovu velkost zdroja (pre percenta). Moze vratit None."""
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


# --- jadro: streamovaci carver ----------------------------------------------

class Carve:
    """Jeden rozpracovany (otvoreny) subor, do ktoreho prudovo zapisujeme."""
    __slots__ = ("ft", "path", "fh", "size", "tail")

    def __init__(self, ft, path):
        self.ft = ft
        self.path = path
        self.fh = open(path, "wb")
        self.size = 0
        self.tail = b""   # posledne bajty (kvoli hladaniu paticky cez hranicu blokov)


def list_sources():
    """Vrati zoznam (popis, cesta) dostupnych diskov/oddielov pre GUI."""
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
                    items.append(("Oddiel %s: %s (%.0f GB)" % (letter, label.strip(), gb),
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
    pos = 0                      # absolutny offset zaciatku 'data' v zdroji
    state = {"counter": 0, "recovered": 0}
    stats = {}                   # typ -> pocet
    active = []                  # zoznam otvorenych Carve

    def commit(c):
        """Zavrie subor, overi ho a bud ulozi (s premenovaním) alebo zahodi.
        Vracia True ak bol ulozeny."""
        c.fh.close()
        ft = c.ft
        # orez na presnu dlzku (mp4/exe), ak to typ vie
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
        final = os.path.join(out_dir, "obnovene_%06d.%s" % (state["counter"], ft.ext))
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

    _log(log_cb, "Citam zdroj: %s" % src_path)
    if total:
        _log(log_cb, "Velkost zdroja: %s" % human(total))
    _log(log_cb, "Vystup: %s" % os.path.abspath(out_dir))
    _log(log_cb, "Typy: %s" % ", ".join(t.ext for t in types))

    while True:
        if should_stop and should_stop():
            _log(log_cb, "Zastavene pouzivatelom.")
            break
        try:
            data = fh.read(BLOCK)
        except OSError as e:
            # niektore surove disky hadzu chybu az na konci — berieme ako koniec
            _log(log_cb, "(citanie skoncilo: %s)" % e)
            break
        if not data:
            break

        # --- A) nakrmime uz otvorene subory novymi datami, hladame paticku ---
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
                    continue  # subor dokonceny, nepridavame do 'still'
                else:
                    c.fh.write(data)
                    c.size += len(data)
                    if c.size > ft.max_size:
                        discard(c)  # prerastol -> zahodime
                        continue
                    c.tail = (c.tail + data)[-(len(ft.footer) + ft.footer_pad):]
                    still.append(c)
            else:
                # bez paticky: rezeme po max_size
                c.fh.write(data)
                c.size += len(data)
                if c.size >= ft.max_size:
                    commit(c)
                    continue
                still.append(c)
        active = still

        # --- B) hladame nove hlavicky v buf (carry + data) ---
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
                # dedup cez hranicu: ber len hlavicky, ktore neboli cele uz
                # v predchadzajucom bloku
                if jabs + len(ft.header) <= pos:
                    continue
                hdr_abs = jabs - ft.header_offset
                if hdr_abs < 0:
                    continue
                # zaciatok suboru v ramci buf
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
                # hned skus paticku v tomto kuse
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
                sys.stdout.write("\rSpracovane: %s / %s (%.1f %%)  obnovene: %d   "
                                 % (human(pos), human(total), pct, state["recovered"]))
            else:
                sys.stdout.write("\rSpracovane: %s   obnovene: %d   "
                                 % (human(pos), state["recovered"]))
            sys.stdout.flush()

        if max_files and state["recovered"] >= max_files:
            _log(log_cb, "Dosiahnuty limit %d suborov — koncim." % max_files)
            break

    # koniec zdroja — dokoncime este otvorene subory bez paticky / bez konca
    for c in active:
        commit(c)

    fh.close()
    _log(log_cb, "")
    _log(log_cb, "=== HOTOVO ===")
    _log(log_cb, "Obnovenych suborov: %d" % state["recovered"])
    for ext, n in sorted(stats.items()):
        _log(log_cb, "   %-6s %d" % (ext, n))
    _log(log_cb, "Najdes ich v: %s" % os.path.abspath(out_dir))
    _log(log_cb, "Pozn.: carving nevie obnovit povodne nazvy suborov a niektore "
                 "nalezy mozu byt falosne alebo neuplne.")
    return {"recovered": state["recovered"], "stats": stats,
            "out_dir": os.path.abspath(out_dir)}


# --- CLI ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Obnova vymazanych suborov z disku / USB / image (file carving).")
    ap.add_argument("source", nargs="?",
                    help="zdroj: zariadenie (/dev/sdb, \\\\.\\PhysicalDrive1, \\\\.\\E:) "
                         "alebo image subor (disk.img)")
    ap.add_argument("-o", "--output", default="obnovene",
                    help="priecinok pre obnovene subory (MUSI byt na inom disku!)")
    ap.add_argument("--list", action="store_true",
                    help="vypis dostupne disky a skonci")
    ap.add_argument("--all", action="store_true",
                    help="zapni aj rozsirene typy (mp4, mp3, zip archivy, sqlite, ...)")
    ap.add_argument("--only", default=None,
                    help="iba vybrane pripony oddelene ciarkou, napr. jpg,png,pdf")
    ap.add_argument("--max-open", type=int, default=300,
                    help="max. pocet sucasne rozpracovanych suborov (default 300)")
    ap.add_argument("-q", "--quiet", action="store_true", help="bez priebeznych vypisov")
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
            print("Ziadny zo zadanych typov nepoznam. Dostupne: %s"
                  % ", ".join(sorted({t.ext for t in BASE_TYPES + EXTRA_TYPES})))
            return 1

    # bezpecnostna kontrola: vystup nesmie byt na zdroji
    try:
        if os.path.isfile(args.source):
            src_abs = os.path.abspath(args.source)
            out_abs = os.path.abspath(args.output)
            if out_abs.startswith(os.path.dirname(src_abs)):
                print("VAROVANIE: vystupny priecinok je pri zdrojovom image. "
                      "Odporucam iny disk.")
    except OSError:
        pass

    try:
        run_carver(args.source, args.output, types,
                   max_open=args.max_open, quiet=args.quiet)
    except PermissionError:
        print("\nCHYBA: nedostatocne opravnenia na citanie zdroja.")
        print("Linux: spusti cez 'sudo'.  Windows: spusti terminal ako Administrator.")
        return 2
    except FileNotFoundError:
        print("\nCHYBA: zdroj '%s' sa nenasiel. Skontroluj nazov (pozri --list)."
              % args.source)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
