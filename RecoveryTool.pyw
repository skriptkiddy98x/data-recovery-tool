#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RecoveryTool — simple desktop app (double-click). Built on recovery_core.py.

On Windows it requests administrator rights (UAC prompt) so it can read a raw
disk. On Linux run it with:  sudo python3 RecoveryTool.pyw
"""

import os
import sys
import threading
import queue
import platform

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import recovery_core as core


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


class App:
    def __init__(self, root):
        self.root = root
        self.worker = None
        self.stop_event = threading.Event()
        self.q = queue.Queue()
        self.sources = []

        root.title("Deleted File Recovery")
        root.geometry("680x600")
        root.minsize(560, 520)
        pad = {"padx": 10, "pady": 6}

        # --- Source ---
        src = ttk.LabelFrame(root, text="1) Where to recover from (disk / USB / file)")
        src.pack(fill="x", **pad)
        self.src_mode = tk.StringVar(value="disk")
        ttk.Radiobutton(src, text="Disk or USB:", variable=self.src_mode,
                        value="disk", command=self._refresh_state).grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        self.cmb = ttk.Combobox(src, state="readonly", width=55)
        self.cmb.grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(src, text="Refresh list", command=self.load_sources).grid(
            row=0, column=2, padx=6)
        ttk.Radiobutton(src, text="File (image):", variable=self.src_mode,
                        value="file", command=self._refresh_state).grid(
            row=1, column=0, sticky="w", padx=8, pady=4)
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(src, textvariable=self.file_var, width=55)
        self.file_entry.grid(row=1, column=1, sticky="we", padx=4)
        self.file_btn = ttk.Button(src, text="Browse…", command=self.pick_file)
        self.file_btn.grid(row=1, column=2, padx=6)
        src.columnconfigure(1, weight=1)

        # --- Output ---
        out = ttk.LabelFrame(root, text="2) Where to save recovered files (another drive!)")
        out.pack(fill="x", **pad)
        self.out_var = tk.StringVar(value=self._default_out())
        ttk.Entry(out, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, padx=8, pady=6)
        ttk.Button(out, text="Browse…", command=self.pick_out).pack(side="left", padx=6)

        # --- Types ---
        typ = ttk.LabelFrame(root, text="3) What to look for")
        typ.pack(fill="x", **pad)
        self.all_var = tk.BooleanVar(value=False)
        ttk.Label(typ, text="Base: JPEG, PNG, GIF, PDF, ZIP (Word/Excel)").pack(
            anchor="w", padx=8, pady=(6, 0))
        ttk.Checkbutton(typ, text="Also enable extended (MP4, MP3, archives, SQLite, EXE, …)",
                        variable=self.all_var).pack(anchor="w", padx=8, pady=4)

        # --- Buttons ---
        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="▶  Start recovery", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■  Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.open_btn = ttk.Button(btns, text="📂  Open folder", command=self.open_out)
        self.open_btn.pack(side="left")

        self.pb = ttk.Progressbar(root, mode="determinate")
        self.pb.pack(fill="x", padx=10, pady=(4, 0))
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(root, textvariable=self.status).pack(anchor="w", padx=12)

        self.log = tk.Text(root, height=12, wrap="word", state="disabled",
                           bg="#111", fg="#ddd")
        self.log.pack(fill="both", expand=True, padx=10, pady=8)

        if platform.system() != "Windows" and hasattr(os, "geteuid") and os.geteuid() != 0:
            self._append("Note: on Linux run with 'sudo', otherwise disks cannot be "
                         "read (image files work without sudo).\n")

        self.load_sources()
        self._refresh_state()
        self.root.after(120, self._drain_queue)

    def _default_out(self):
        return os.path.join(os.path.expanduser("~"), "recovered_data")

    def _refresh_state(self):
        disk = self.src_mode.get() == "disk"
        self.cmb.configure(state="readonly" if disk else "disabled")
        self.file_entry.configure(state="normal" if not disk else "disabled")
        self.file_btn.configure(state="normal" if not disk else "disabled")

    def load_sources(self):
        self.sources = core.list_sources()
        self.cmb.configure(values=[s[0] for s in self.sources])
        if self.sources:
            self.cmb.current(0)

    def pick_file(self):
        p = filedialog.askopenfilename(title="Select an image file")
        if p:
            self.file_var.set(p)

    def pick_out(self):
        p = filedialog.askdirectory(title="Select a folder for the results")
        if p:
            self.out_var.set(p)

    def open_out(self):
        d = self.out_var.get()
        if not os.path.isdir(d):
            messagebox.showinfo("Recovery", "The folder does not exist yet.")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(d)
            elif platform.system() == "Darwin":
                os.system('open "%s"' % d)
            else:
                os.system('xdg-open "%s"' % d)
        except Exception as e:
            messagebox.showerror("Recovery", str(e))

    def _append(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg)
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        if self.src_mode.get() == "disk":
            idx = self.cmb.current()
            if idx < 0 or not self.sources:
                messagebox.showwarning("Recovery", "Select a disk or USB.")
                return
            source = self.sources[idx][1]
        else:
            source = self.file_var.get().strip()
            if not source:
                messagebox.showwarning("Recovery", "Select an image file.")
                return

        out_dir = self.out_var.get().strip()
        if not out_dir:
            messagebox.showwarning("Recovery", "Enter a folder for the results.")
            return

        if source.startswith("\\\\.\\") and source.endswith(":"):
            letter = source[-2]
            if os.path.abspath(out_dir)[:1].upper() == letter.upper():
                if not messagebox.askyesno(
                        "Warning",
                        "The output is on the same partition you are recovering from.\n"
                        "This may overwrite the data being recovered. Continue?"):
                    return

        types = list(core.BASE_TYPES)
        if self.all_var.get():
            types += core.EXTRA_TYPES

        self.stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.pb.configure(value=0)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status.set("Working…")

        self.worker = threading.Thread(
            target=self._run, args=(source, out_dir, types), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.status.set("Stopping…")

    def _run(self, source, out_dir, types):
        def prog(pos, total, recovered):
            self.q.put(("prog", (pos, total, recovered)))

        def log(msg):
            self.q.put(("log", msg + "\n"))

        try:
            res = core.run_carver(source, out_dir, types,
                                  progress_cb=prog, log_cb=log,
                                  should_stop=self.stop_event.is_set)
            self.q.put(("done", res))
        except PermissionError:
            self.q.put(("error",
                        "Insufficient privileges to read the source.\n"
                        "Windows: run as Administrator.\nLinux: use sudo."))
        except FileNotFoundError:
            self.q.put(("error", "Source not found. Check your selection."))
        except Exception as e:
            self.q.put(("error", "Error: %s" % e))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                elif kind == "prog":
                    pos, total, rec = payload
                    if total:
                        self.pb.configure(mode="determinate", maximum=total, value=pos)
                        self.status.set("Processed: %s / %s   recovered: %d"
                                        % (core.human(pos), core.human(total), rec))
                    else:
                        self.pb.configure(mode="indeterminate")
                        self.pb.step(5)
                        self.status.set("Processed: %s   recovered: %d"
                                        % (core.human(pos), rec))
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._append("\n" + payload + "\n")
                    messagebox.showerror("Recovery", payload)
                    self._reset_buttons()
        except queue.Empty:
            pass
        self.root.after(120, self._drain_queue)

    def _finish(self, res):
        self.pb.configure(mode="determinate")
        try:
            self.pb.configure(value=self.pb["maximum"])
        except Exception:
            pass
        rec = res.get("recovered", 0) if isinstance(res, dict) else 0
        self.status.set("Done. Recovered files: %d" % rec)
        self._reset_buttons()
        messagebox.showinfo(
            "Recovery",
            "Done!\nRecovered files: %d\n\nFind them in:\n%s"
            % (rec, res.get("out_dir", self.out_var.get()) if isinstance(res, dict)
               else self.out_var.get()))

    def _reset_buttons(self):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")


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
