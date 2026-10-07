#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RecoveryTool PRO — desktop app (double-click).

Click Scan -> pick a disk/USB or an image file -> choose types (photos, videos,
documents, exe, ...) -> optional date range -> Scan. Found files appear in a
table and can be previewed inside the app (a safe preview — .exe files are NEVER
executed). Tick what you want and click Restore selected. Unticked files are
discarded.

The date is read FROM INSIDE each file (photo EXIF, PDF, ZIP/Word, MP4). Types
without an embedded date show the date as "unknown".
"""

import os
import sys
import shutil
import tempfile
import threading
import queue
import platform
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import recovery_core as core
import recovery_meta as meta

try:
    from PIL import Image, ImageTk          # optional (nicer JPEG preview)
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False


def ensure_admin_windows():
    if platform.system() != "Windows" or os.environ.get("OBNOVA_ELEVATED") == "1":
        return
    try:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
        os.environ["OBNOVA_ELEVATED"] = "1"
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, '"%s"' % os.path.abspath(__file__), None, 1)
        if rc > 32:
            sys.exit(0)
    except Exception:
        pass


def jpeg_dimensions(path):
    """JPEG dimensions from the SOF marker (no libraries)."""
    try:
        with open(path, "rb") as f:
            f.read(2)
            while True:
                b = f.read(1)
                if not b:
                    return None
                if b != b"\xff":
                    continue
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if marker and marker[0] in (0xC0, 0xC1, 0xC2, 0xC3):
                    f.read(3)
                    h = int.from_bytes(f.read(2), "big")
                    w = int.from_bytes(f.read(2), "big")
                    return (w, h)
                ln = int.from_bytes(f.read(2), "big")
                f.seek(ln - 2, 1)
    except Exception:
        return None


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.sources = []
        self.quarantine = None
        self.rows = {}        # item_id -> {path, ext, size, date}
        self.checked = set()
        self._preview_img = None

        root.title("Deleted File Recovery — PRO")
        root.geometry("1000x680")
        root.minsize(900, 600)

        top = ttk.Frame(root)
        top.pack(fill="x", padx=10, pady=8)

        # --- Source ---
        src = ttk.LabelFrame(top, text="1) Where to recover from")
        src.pack(fill="x", pady=4)
        self.src_mode = tk.StringVar(value="disk")
        ttk.Radiobutton(src, text="Disk / USB:", variable=self.src_mode, value="disk",
                        command=self._state).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.cmb = ttk.Combobox(src, state="readonly", width=48)
        self.cmb.grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(src, text="🔄 Scan for disks/USB", command=self.load_sources).grid(
            row=0, column=2, padx=6)
        ttk.Radiobutton(src, text="File (image):", variable=self.src_mode, value="file",
                        command=self._state).grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(src, textvariable=self.file_var)
        self.file_entry.grid(row=1, column=1, sticky="we", padx=4)
        self.file_btn = ttk.Button(src, text="Browse…", command=self.pick_file)
        self.file_btn.grid(row=1, column=2, padx=6)
        src.columnconfigure(1, weight=1)

        # --- Types + date ---
        mid = ttk.Frame(top)
        mid.pack(fill="x", pady=4)

        typ = ttk.LabelFrame(mid, text="2) What to look for")
        typ.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.cat_vars = {}
        cats = list(meta.CATEGORY_LABELS.items())
        for idx, (key, label) in enumerate(cats):
            v = tk.BooleanVar(value=(key in ("photos", "documents")))
            self.cat_vars[key] = v
            ttk.Checkbutton(typ, text=label, variable=v).grid(
                row=idx // 2, column=idx % 2, sticky="w", padx=8, pady=2)

        dat = ttk.LabelFrame(mid, text="3) Date from content (optional)")
        dat.pack(side="left", fill="y")
        ttk.Label(dat, text="From (YYYY-MM-DD):").grid(row=0, column=0, sticky="e", padx=6, pady=3)
        self.date_from = ttk.Entry(dat, width=14)
        self.date_from.grid(row=0, column=1, padx=6)
        ttk.Label(dat, text="To (YYYY-MM-DD):").grid(row=1, column=0, sticky="e", padx=6, pady=3)
        self.date_to = ttk.Entry(dat, width=14)
        self.date_to.grid(row=1, column=1, padx=6)
        self.incl_undated = tk.BooleanVar(value=True)
        ttk.Checkbutton(dat, text="include files without a date",
                        variable=self.incl_undated).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6, pady=3)

        # --- Action buttons ---
        act = ttk.Frame(top)
        act.pack(fill="x", pady=6)
        self.scan_btn = ttk.Button(act, text="🔍  Scan / Recover", command=self.start)
        self.scan_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(act, text="Select all", command=lambda: self.check_all(True)).pack(side="left", padx=(16, 4))
        ttk.Button(act, text="Deselect", command=lambda: self.check_all(False)).pack(side="left")
        self.restore_btn = ttk.Button(act, text="💾  Restore selected",
                                      command=self.restore, state="disabled")
        self.restore_btn.pack(side="right")

        self.pb = ttk.Progressbar(top, mode="determinate")
        self.pb.pack(fill="x", pady=(2, 0))
        self.status = tk.StringVar(value="Ready. Pick a source and click Scan.")
        ttk.Label(top, textvariable=self.status).pack(anchor="w")

        # --- Bottom: table + preview ---
        bottom = ttk.Panedwindow(root, orient="horizontal")
        bottom.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        left = ttk.Frame(bottom)
        cols = ("chk", "type", "size", "date")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("chk", text="✓")
        self.tree.heading("type", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.heading("date", text="Date (from content)")
        self.tree.column("chk", width=34, anchor="center", stretch=False)
        self.tree.column("type", width=70, anchor="center", stretch=False)
        self.tree.column("size", width=90, anchor="e", stretch=False)
        self.tree.column("date", width=150, anchor="center")
        vs = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        bottom.add(left, weight=3)

        right = ttk.LabelFrame(bottom, text="Preview (sandbox — .exe is never run)")
        self.preview = tk.Label(right, text="Select a file from the list.",
                                anchor="center", justify="center", bg="#1b1b1b", fg="#ccc")
        self.preview.pack(fill="both", expand=True, padx=6, pady=6)
        self.info = tk.Text(right, height=7, wrap="word", state="disabled",
                            bg="#111", fg="#ddd")
        self.info.pack(fill="x", padx=6, pady=(0, 6))
        bottom.add(right, weight=2)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if platform.system() != "Windows" and hasattr(os, "geteuid") and os.geteuid() != 0:
            self._set_status("On Linux run with sudo, otherwise disks are not readable.")

        self.load_sources()
        self._state()
        self.root.after(120, self._drain)

    # ---------- helpers ----------
    def _state(self):
        disk = self.src_mode.get() == "disk"
        self.cmb.configure(state="readonly" if disk else "disabled")
        self.file_entry.configure(state="normal" if not disk else "disabled")
        self.file_btn.configure(state="normal" if not disk else "disabled")

    def _set_status(self, s):
        self.status.set(s)

    def load_sources(self):
        self.sources = core.list_sources()
        self.cmb.configure(values=[s[0] for s in self.sources])
        if self.sources:
            self.cmb.current(0)

    def pick_file(self):
        p = filedialog.askopenfilename(title="Select an image file")
        if p:
            self.file_var.set(p)

    def selected_types(self):
        exts = set()
        for key, v in self.cat_vars.items():
            if v.get():
                exts.update(meta.CATEGORIES.get(key, []))
        allt = core.BASE_TYPES + core.EXTRA_TYPES
        return [t for t in allt if t.ext in exts]

    def _parse_date(self, entry, end=False):
        s = entry.get().strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
            try:
                d = datetime.datetime.strptime(s, fmt)
                if end:
                    d = d.replace(hour=23, minute=59, second=59)
                return d
            except ValueError:
                continue
        raise ValueError("Bad date format: %s (use YYYY-MM-DD)" % s)

    # ---------- start ----------
    def start(self):
        if self.src_mode.get() == "disk":
            i = self.cmb.current()
            if i < 0 or not self.sources:
                messagebox.showwarning("Recovery", "Select a disk or USB.")
                return
            source = self.sources[i][1]
        else:
            source = self.file_var.get().strip()
            if not source:
                messagebox.showwarning("Recovery", "Select an image file.")
                return

        types = self.selected_types()
        if not types:
            messagebox.showwarning("Recovery", "Tick at least one type.")
            return
        try:
            self.d_from = self._parse_date(self.date_from)
            self.d_to = self._parse_date(self.date_to, end=True)
        except ValueError as e:
            messagebox.showerror("Recovery", str(e))
            return

        # clean up a previous run
        self._cleanup_quarantine()
        self.quarantine = tempfile.mkdtemp(prefix="RecoverySandbox_")
        for it in self.tree.get_children():
            self.tree.delete(it)
        self.rows.clear()
        self.checked.clear()
        self._set_preview(None, None)

        self.stop_event.clear()
        self.scan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.restore_btn.configure(state="disabled")
        self.pb.configure(value=0)
        self._set_status("Scanning…")

        self.worker = threading.Thread(target=self._run, args=(source, types), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self._set_status("Stopping…")

    def _run(self, source, types):
        def prog(pos, total, rec):
            self.q.put(("prog", (pos, total, rec)))

        def per_file(path, ft):
            d = meta.extract_date(path, ft.ext)
            self.q.put(("file", {"path": path, "ext": ft.ext,
                                 "size": os.path.getsize(path), "date": d}))

        try:
            core.run_carver(source, self.quarantine, types,
                            quiet=True, progress_cb=prog, per_file_cb=per_file,
                            should_stop=self.stop_event.is_set, max_files=5000)
            self.q.put(("done", None))
        except PermissionError:
            self.q.put(("error", "Insufficient privileges. Windows: run as Administrator. "
                                 "Linux: use sudo."))
        except FileNotFoundError:
            self.q.put(("error", "Source not found."))
        except Exception as e:
            self.q.put(("error", "Error: %s" % e))

    # ---------- receive from worker ----------
    def _passes_date(self, d):
        if self.d_from or self.d_to:
            if d is None:
                return self.incl_undated.get()
            if self.d_from and d < self.d_from:
                return False
            if self.d_to and d > self.d_to:
                return False
        return True

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "prog":
                    pos, total, rec = payload
                    if total:
                        self.pb.configure(maximum=total, value=pos)
                        self._set_status("Scanning: %s / %s   found: %d"
                                         % (core.human(pos), core.human(total), rec))
                    else:
                        self._set_status("Scanning: %s   found: %d"
                                         % (core.human(pos), rec))
                elif kind == "file":
                    self._add_row(payload)
                elif kind == "done":
                    self._finish()
                elif kind == "error":
                    messagebox.showerror("Recovery", payload)
                    self._reset()
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _add_row(self, r):
        if not self._passes_date(r["date"]):
            try:
                os.remove(r["path"])     # outside the filter -> drop from sandbox
            except OSError:
                pass
            return
        ds = r["date"].strftime("%Y-%m-%d %H:%M") if r["date"] else "—"
        iid = self.tree.insert("", "end",
                               values=("☐", r["ext"].upper(), core.human(r["size"]), ds))
        self.rows[iid] = r

    def _finish(self):
        self._reset()
        n = len(self.rows)
        self._set_status("Done. Files found and shown: %d" % n)
        if n:
            self.restore_btn.configure(state="normal")
        try:
            self.pb.configure(value=self.pb["maximum"])
        except Exception:
            pass

    def _reset(self):
        self.scan_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    # ---------- table / preview ----------
    def on_tree_click(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) == "#1":   # checkbox column
            iid = self.tree.identify_row(event.y)
            if iid:
                self._toggle(iid)

    def _toggle(self, iid):
        if iid in self.checked:
            self.checked.discard(iid)
            mark = "☐"
        else:
            self.checked.add(iid)
            mark = "☑"
        vals = list(self.tree.item(iid, "values"))
        vals[0] = mark
        self.tree.item(iid, values=vals)

    def check_all(self, on):
        for iid in self.tree.get_children():
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "☑" if on else "☐"
            self.tree.item(iid, values=vals)
            if on:
                self.checked.add(iid)
            else:
                self.checked.discard(iid)

    def on_select(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.rows.get(sel[0])
        if r:
            self._set_preview(r["path"], r["ext"], r)

    def _set_preview(self, path, ext, r=None):
        self._preview_img = None
        if not path:
            self.preview.configure(image="", text="Select a file from the list.")
            self._set_info("")
            return
        # safe preview — nothing is executed
        if ext == "exe":
            self.preview.configure(image="", text="⚙  Executable (.exe)\n\n"
                                                  "NOT executed for safety.\n"
                                                  "Only its metadata is shown below.")
        elif ext in ("png", "gif"):
            try:
                img = tk.PhotoImage(file=path)
                while img.width() > 360 or img.height() > 360:
                    img = img.subsample(2, 2)
                self._preview_img = img
                self.preview.configure(image=img, text="")
            except Exception:
                self.preview.configure(image="", text="(preview not available)")
        elif ext == "jpg":
            if HAVE_PIL:
                try:
                    im = Image.open(path)
                    im.thumbnail((360, 360))
                    self._preview_img = ImageTk.PhotoImage(im)
                    self.preview.configure(image=self._preview_img, text="")
                except Exception:
                    self.preview.configure(image="", text="(JPEG preview not available)")
            else:
                dim = jpeg_dimensions(path)
                self.preview.configure(
                    image="",
                    text="🖼  JPEG photo%s\n\nIn-app JPEG preview needs Pillow\n"
                         "(pip install pillow). The file can still be restored and opened."
                         % (("  %d×%d" % dim) if dim else ""))
        else:
            self.preview.configure(image="", text="(%s — no preview, can be restored)"
                                                  % ext.upper())
        # info panel + hex sample
        lines = []
        if r:
            lines.append("Type: %s" % ext.upper())
            lines.append("Size: %s (%d B)" % (core.human(r["size"]), r["size"]))
            lines.append("Date from content: %s"
                         % (r["date"].strftime("%Y-%m-%d %H:%M:%S") if r["date"] else "unknown"))
        try:
            with open(path, "rb") as f:
                head = f.read(32)
            lines.append("First bytes: " + " ".join("%02X" % b for b in head))
        except OSError:
            pass
        self._set_info("\n".join(lines))

    def _set_info(self, s):
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("end", s)
        self.info.configure(state="disabled")

    # ---------- restore ----------
    def restore(self):
        if not self.checked:
            messagebox.showinfo("Recovery", "Tick some files first (the ✓ column).")
            return
        dest = filedialog.askdirectory(title="Where to restore the selected files")
        if not dest:
            return
        n = 0
        for iid in list(self.checked):
            r = self.rows.get(iid)
            if not r or not os.path.exists(r["path"]):
                continue
            ds = r["date"].strftime("%Y%m%d_%H%M%S") if r["date"] else "nodate"
            name = "recovered_%03d_%s.%s" % (n + 1, ds, r["ext"])
            try:
                shutil.copy2(r["path"], os.path.join(dest, name))
                n += 1
            except OSError:
                pass
        messagebox.showinfo("Recovery",
                            "Restored %d files to:\n%s\n\n"
                            "Unticked files remain unrecovered." % (n, dest))
        try:
            if platform.system() == "Windows":
                os.startfile(dest)
        except Exception:
            pass

    # ---------- close ----------
    def _cleanup_quarantine(self):
        if self.quarantine and os.path.isdir(self.quarantine):
            shutil.rmtree(self.quarantine, ignore_errors=True)
        self.quarantine = None

    def on_close(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
        self._cleanup_quarantine()
        self.root.destroy()


def main():
    ensure_admin_windows()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
