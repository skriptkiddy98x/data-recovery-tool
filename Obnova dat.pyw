#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Obnova dat — aplikacia s oknom (dvojklik). Nadstavba nad obnova_dat.py.

Na Windows si sama vyziada administratorske prava (okno UAC), aby vedela
citat surovy disk. Na Linuxe spusti cez:  sudo python3 "Obnova dat.pyw"
"""

import os
import sys
import threading
import queue
import platform

# zarucime, ze najdeme obnova_dat.py vedla tohto suboru
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import obnova_dat as core


# --- na Windows sa v pripade potreby spustime znova s pravami administratora ---
def ensure_admin_windows():
    if platform.system() != "Windows":
        return
    if os.environ.get("OBNOVA_ELEVATED") == "1":
        return
    try:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
    except Exception:
        return
    # skusime sa spustit znova so zvysenymi pravami (vyskoci UAC)
    try:
        import ctypes
        os.environ["OBNOVA_ELEVATED"] = "1"
        script = os.path.abspath(__file__)
        params = '"%s"' % script
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1)
        if rc > 32:          # uspesne spustene elevovane -> tuto instanciu zavrieme
            sys.exit(0)
    except Exception:
        pass  # ak zlyha, pokracujeme bez prav (bude fungovat aspon na image subory)


class App:
    def __init__(self, root):
        self.root = root
        self.worker = None
        self.stop_event = threading.Event()
        self.q = queue.Queue()
        self.sources = []  # (popis, cesta)

        root.title("Obnova vymazaných dát")
        root.geometry("680x600")
        root.minsize(560, 520)

        pad = {"padx": 10, "pady": 6}

        # --- Zdroj ---
        src = ttk.LabelFrame(root, text="1) Odkiaľ obnoviť (disk / USB / súbor)")
        src.pack(fill="x", **pad)

        self.src_mode = tk.StringVar(value="disk")
        ttk.Radiobutton(src, text="Disk alebo USB:", variable=self.src_mode,
                        value="disk", command=self._refresh_state).grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        self.cmb = ttk.Combobox(src, state="readonly", width=55)
        self.cmb.grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(src, text="Obnoviť zoznam", command=self.load_sources).grid(
            row=0, column=2, padx=6)

        ttk.Radiobutton(src, text="Súbor (image):", variable=self.src_mode,
                        value="file", command=self._refresh_state).grid(
            row=1, column=0, sticky="w", padx=8, pady=4)
        self.file_var = tk.StringVar()
        self.file_entry = ttk.Entry(src, textvariable=self.file_var, width=55)
        self.file_entry.grid(row=1, column=1, sticky="we", padx=4)
        self.file_btn = ttk.Button(src, text="Vybrať…", command=self.pick_file)
        self.file_btn.grid(row=1, column=2, padx=6)
        src.columnconfigure(1, weight=1)

        # --- Kam ulozit ---
        out = ttk.LabelFrame(root, text="2) Kam uložiť obnovené súbory (iný disk!)")
        out.pack(fill="x", **pad)
        self.out_var = tk.StringVar(value=self._default_out())
        ttk.Entry(out, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, padx=8, pady=6)
        ttk.Button(out, text="Vybrať…", command=self.pick_out).pack(
            side="left", padx=6)

        # --- Typy ---
        typ = ttk.LabelFrame(root, text="3) Čo hľadať")
        typ.pack(fill="x", **pad)
        self.all_var = tk.BooleanVar(value=False)
        ttk.Label(typ, text="Základ: JPEG, PNG, GIF, PDF, ZIP (Word/Excel)").pack(
            anchor="w", padx=8, pady=(6, 0))
        ttk.Checkbutton(typ, text="Zapnúť aj rozšírené (MP4, MP3, archívy, SQLite, …)",
                        variable=self.all_var).pack(anchor="w", padx=8, pady=4)

        # --- Tlacidla ---
        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="▶  Spustiť obnovu", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■  Zastaviť", command=self.stop,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.open_btn = ttk.Button(btns, text="📂  Otvoriť priečinok",
                                   command=self.open_out)
        self.open_btn.pack(side="left")

        # --- Priebeh ---
        self.pb = ttk.Progressbar(root, mode="determinate")
        self.pb.pack(fill="x", padx=10, pady=(4, 0))
        self.status = tk.StringVar(value="Pripravené.")
        ttk.Label(root, textvariable=self.status).pack(anchor="w", padx=12)

        self.log = tk.Text(root, height=12, wrap="word", state="disabled",
                           bg="#111", fg="#ddd")
        self.log.pack(fill="both", expand=True, padx=10, pady=8)

        if platform.system() != "Windows" and os.geteuid() != 0:
            self._append("Pozn.: na Linuxe spusti cez 'sudo', inak sa disky "
                         "nedajú čítať (image súbory fungujú aj bez sudo).\n")

        self.load_sources()
        self._refresh_state()
        self.root.after(120, self._drain_queue)

    # --- pomocne ---
    def _default_out(self):
        home = os.path.expanduser("~")
        return os.path.join(home, "obnovene_data")

    def _refresh_state(self):
        disk = self.src_mode.get() == "disk"
        self.cmb.configure(state="readonly" if disk else "disabled")
        self.file_entry.configure(state="normal" if not disk else "disabled")
        self.file_btn.configure(state="normal" if not disk else "disabled")

    def load_sources(self):
        self.sources = core.list_sources()
        labels = [s[0] for s in self.sources]
        self.cmb.configure(values=labels)
        if labels:
            self.cmb.current(0)

    def pick_file(self):
        p = filedialog.askopenfilename(title="Vyber image súbor")
        if p:
            self.file_var.set(p)

    def pick_out(self):
        p = filedialog.askdirectory(title="Vyber priečinok pre výsledky")
        if p:
            self.out_var.set(p)

    def open_out(self):
        d = self.out_var.get()
        if not os.path.isdir(d):
            messagebox.showinfo("Obnova dát", "Priečinok ešte neexistuje.")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(d)
            elif platform.system() == "Darwin":
                os.system('open "%s"' % d)
            else:
                os.system('xdg-open "%s"' % d)
        except Exception as e:
            messagebox.showerror("Obnova dát", str(e))

    def _append(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg)
        self.log.see("end")
        self.log.configure(state="disabled")

    # --- spustenie / zastavenie ---
    def start(self):
        if self.src_mode.get() == "disk":
            idx = self.cmb.current()
            if idx < 0 or not self.sources:
                messagebox.showwarning("Obnova dát", "Vyber disk alebo USB.")
                return
            source = self.sources[idx][1]
        else:
            source = self.file_var.get().strip()
            if not source:
                messagebox.showwarning("Obnova dát", "Vyber image súbor.")
                return

        out_dir = self.out_var.get().strip()
        if not out_dir:
            messagebox.showwarning("Obnova dát", "Zadaj priečinok pre výsledky.")
            return

        # ochrana: vystup nesmie byt na citanom oddiele (Windows pismeno)
        if source.startswith("\\\\.\\") and source.endswith(":"):
            letter = source[-2]
            if os.path.abspath(out_dir)[:1].upper() == letter.upper():
                if not messagebox.askyesno(
                        "Pozor",
                        "Výstup je na tom istom oddiele, z ktorého obnovuješ.\n"
                        "Môže to prepísať práve mazané dáta. Pokračovať?"):
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
        self.status.set("Pracujem…")

        self.worker = threading.Thread(
            target=self._run, args=(source, out_dir, types), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.status.set("Zastavujem…")

    def _run(self, source, out_dir, types):
        def prog(pos, total, recovered):
            self.q.put(("prog", (pos, total, recovered)))

        def log(msg):
            self.q.put(("log", msg + "\n"))

        try:
            res = core.run_carver(
                source, out_dir, types,
                progress_cb=prog, log_cb=log,
                should_stop=self.stop_event.is_set)
            self.q.put(("done", res))
        except PermissionError:
            self.q.put(("error",
                        "Nedostatočné práva na čítanie zdroja.\n"
                        "Windows: spusti ako Administrátor.\nLinux: spusti cez sudo."))
        except FileNotFoundError:
            self.q.put(("error", "Zdroj sa nenašiel. Skontroluj výber."))
        except Exception as e:
            self.q.put(("error", "Chyba: %s" % e))

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
                        self.status.set("Spracované: %s / %s   obnovené: %d"
                                        % (core.human(pos), core.human(total), rec))
                    else:
                        self.pb.configure(mode="indeterminate")
                        self.pb.step(5)
                        self.status.set("Spracované: %s   obnovené: %d"
                                        % (core.human(pos), rec))
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._append("\n" + payload + "\n")
                    messagebox.showerror("Obnova dát", payload)
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
        self.status.set("Hotovo. Obnovených súborov: %d" % rec)
        self._reset_buttons()
        messagebox.showinfo(
            "Obnova dát",
            "Hotovo!\nObnovených súborov: %d\n\nNájdeš ich v:\n%s"
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
