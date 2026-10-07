# ObnovaDat — Deleted File Recovery Tool

A cross-platform (Windows / Linux) tool that recovers deleted files from a hard
disk, SSD, USB stick, memory card, or a disk image. It ships with a simple
desktop app, an advanced desktop app, and a command-line interface.

The source drive is **only ever read, never written to**, so scanning a device
cannot damage or overwrite the data you are trying to recover.

---

## What it is

When you delete a file, the data is usually **not erased immediately** — the
operating system just marks that space as "free". Until something new is written
over it, the original bytes are still physically on the disk and can be brought
back.

ObnovaDat finds those files using **file carving**: it reads the raw device byte
by byte and looks for the known "signatures" of files (the fixed header and
footer bytes that identify a JPEG, PNG, PDF, ZIP, MP4, and so on). When it finds
a valid start and end, it extracts the file. Because this works on the raw
bytes, it is **independent of the filesystem** — NTFS, FAT32, exFAT and ext4 are
all supported the same way.

## What it can recover

**Base types** (clear header + footer, very reliable):
`JPG`, `PNG`, `GIF`, `PDF`, `ZIP` (which also covers `DOCX`, `XLSX`, `PPTX`).

**Extended types:**
`MP4`, `MP3`, `GZIP`, `RAR`, `7Z`, `SQLite`, legacy `DOC`, `WAV`, and Windows
executables (`EXE`/PE).

## How it works (technical)

- **Streaming carver** — the device is read sequentially in aligned 4 MB blocks
  (sector-aligned so raw reads also work on Windows). Signatures that cross a
  block boundary are handled with a small carry buffer, so no match is missed.
- **Header/footer matching** — for types with a reliable footer (JPEG, PNG, PDF,
  ZIP…) the file is cut exactly from header to footer. For header-only types the
  carver reads up to a per-type size limit.
- **Exact trimming** — `MP4` files are trimmed to their real length by walking
  the top-level atom/box table; `EXE`/PE files are trimmed using the PE section
  table (and the certificate/signature directory). This removes trailing junk
  and gives byte-accurate output.
- **Validation** — `EXE` candidates are verified to be real PE binaries (the
  `MZ` header must point to a valid `PE\0\0` signature), which filters out the
  many false `MZ` matches found in random data.
- **Embedded dates** — the advanced app reads the date stored *inside* each file
  (EXIF for photos, `/CreationDate` for PDF, the central-directory date for
  ZIP/Office, and the `mvhd` creation time for MP4), so you can filter results by
  time period. Note this is the file's own creation date, **not** the deletion
  date (carving cannot know the deletion date — that lives in the filesystem
  metadata, not in the file itself).

## Project layout

| File | Purpose |
|------|---------|
| `obnova_dat.py` | Core carving engine + command-line interface |
| `obnova_meta.py` | Reads embedded dates (EXIF / PDF / ZIP / MP4) and file categories |
| `Obnova dat PRO.pyw` | Advanced desktop app (type + date filter, in-app preview, selective restore) |
| `Obnova dat.pyw` | Simple desktop app (pick a source, scan, done) |
| `Spustit PRO (Windows).bat` | Launcher for the advanced app on Windows |
| `Spustit obnovu (Windows).bat` | Launcher for the simple app on Windows |
| `NAVOD.md` | User guide (Slovak) |

---

## Requirements

- **Python 3.8+** (no third-party packages required; uses only the standard
  library + Tkinter, which ships with Python).
- Optional: **Pillow** (`pip install pillow`) for nicer in-app JPEG previews.
- **Administrator / root privileges** to read a raw device.

## Usage

### Desktop app (recommended)

**Windows:** double-click **`Obnova dat PRO.pyw`** (or
`Spustit PRO (Windows).bat`). Approve the UAC prompt (needed to read the disk),
then:

1. Pick the **disk / USB** (or an image file).
2. Tick the **file types** to look for (Photos, Videos, Documents, Archives,
   Music, Executables, Databases).
3. Optionally set a **date range** (applies to files that carry an embedded
   date).
4. Click **Scan**. Found files appear in a table and can be previewed safely in
   the app (`.exe` files are **never executed** — only their metadata is shown).
5. Tick the files you want and click **Restore selected** to copy them to a
   folder of your choice. Unticked files are discarded.

**Linux:**

```bash
sudo python3 "Obnova dat PRO.pyw"
```

### Command line

```bash
# list available disks/partitions
python obnova_dat.py --list

# Windows: recover from a USB partition to another drive (run as Administrator)
python obnova_dat.py \\.\E: -o D:\recovered

# Windows: recover from a whole physical disk
python obnova_dat.py \\.\PhysicalDrive1 -o D:\recovered

# Linux: recover from a device (run with sudo)
sudo python3 obnova_dat.py /dev/sdb -o ~/recovered

# recover from a disk image, enable extended types
python obnova_dat.py disk.img -o recovered --all

# only specific extensions
python obnova_dat.py disk.img -o recovered --only jpg,png,pdf
```

| Flag | Meaning |
|------|---------|
| `--list` | list available disks |
| `-o DIR` | output directory for recovered files |
| `--all` | enable extended types (mp4, mp3, archives, sqlite, exe, …) |
| `--only ext1,ext2` | only the given extensions |
| `--max-open N` | max simultaneously open carves (default 300) |
| `-q` | quiet (no progress output) |

Recovered files are written as `obnovene_000001.jpg`, `obnovene_000002.png`, …

---

## Important notes & limitations

- **Always write the output to a different drive than the source.** Writing new
  files onto the same drive you are recovering from can overwrite the very data
  you are trying to save. As soon as you realise something was deleted, stop
  using that drive.
- **Original file names and folders cannot be recovered** by carving — names are
  stored in the filesystem, not inside the file, so results are numbered.
- Some hits may be **incomplete** (if the file was already partly overwritten) or
  **false positives** (random data that happens to look like a file header).
  Just delete those.
- Heavily **fragmented** files (scattered across the disk) may not be
  reconstructed in full.
- Scanning a large disk reads the whole device, so it can take a while.

## Build a standalone .exe (optional)

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name "ObnovaDat" "Obnova dat PRO.pyw"
```

## License

Released under the MIT License — see [LICENSE](LICENSE).
