"""One picker for both photos and folders (Tk's native pickers split them)."""

import os
from pathlib import Path
import re
import tkinter as tk
from tkinter import messagebox, ttk

from stereo import EXTENSIONS


class OpenDialog:
    def __init__(self, parent, initial_folder):
        self.result = None
        self.folder = Path(initial_folder)
        self.entries = {}
        self.window = tk.Toplevel(parent)
        self.window.title("Open photo or folder")
        self.window.geometry("760x500")
        self.window.minsize(520, 350)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.address = tk.StringVar(value=str(self.folder))
        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Button(bar, text="Up", width=5, command=lambda: self.browse(self.folder.parent)).pack(side="left")
        locations = [str(Path.home())]
        if os.name == "nt":
            import ctypes
            # Enumerate mounted drive letters without probing removable or
            # disconnected drives merely to populate the location dropdown.
            drives = ctypes.windll.kernel32.GetLogicalDrives()
            locations += [f"{letter}:\\" for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                          if drives & (1 << index)]
        address = ttk.Combobox(bar, textvariable=self.address, values=locations)
        address.pack(side="left", fill="x", expand=True, padx=6)
        address.bind("<Return>", lambda _event: self.go())
        address.bind("<<ComboboxSelected>>", lambda _event: self.go())
        ttk.Button(bar, text="Go", width=5, command=self.go).pack(side="left")
        ttk.Label(frame, text="Select a photo or folder and click Open. Double-click folders to browse inside.").pack(
            anchor="w", pady=(10, 8))
        listing = ttk.Frame(frame)
        listing.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(listing, columns=("type",), selectmode="browse")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("type", text="Type", anchor="w")
        self.tree.column("#0", width=500)
        self.tree.column("type", width=90, stretch=False)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=scroll.set)
        self.tree.bind("<Double-Button-1>", lambda _event: self.activate())
        self.tree.bind("<Return>", lambda _event: self.activate())
        self.tree.bind("<BackSpace>", lambda _event: self.browse(self.folder.parent))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Label(buttons, text="No selection: Open uses the current folder.").pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(buttons, text="Open", command=self.choose).pack(side="right", padx=6)
        self.window.bind("<Escape>", lambda _event: self.cancel())
        self.browse(self.folder)

    def browse(self, folder):
        try:
            folder = Path(folder).expanduser().resolve()
            def key(path):
                return (not path.is_dir(), [int(part) if part.isdigit() else part.casefold()
                                           for part in re.split(r"(\d+)", path.name)])
            entries = sorted((path for path in folder.iterdir()
                              if path.is_dir() or (path.is_file() and path.suffix.lower() in EXTENSIONS)), key=key)
        except OSError as error:
            messagebox.showerror("Cannot browse folder", str(error), parent=self.window)
            return
        self.folder = folder
        self.address.set(str(folder))
        self.tree.delete(*self.tree.get_children())
        self.entries.clear()
        for path in entries:
            item = self.tree.insert("", "end", text=path.name,
                                    values=("Folder" if path.is_dir() else "Photo",))
            self.entries[item] = path

    def go(self):
        path = Path(self.address.get()).expanduser()
        if not path.is_absolute():
            path = self.folder / path
        if path.is_dir():
            self.browse(path)
        else:
            self.accept(path)

    def selected_path(self):
        selected = self.tree.selection()
        return self.entries[selected[0]] if selected else self.folder

    def activate(self):
        path = self.selected_path()
        if path.is_dir():
            self.browse(path)
        else:
            self.accept(path)

    def choose(self):
        if self.address.get() != str(self.folder):
            path = Path(self.address.get()).expanduser()
            self.accept(path if path.is_absolute() else self.folder / path)
        else:
            self.accept(self.selected_path())

    def accept(self, path):
        if not (path.is_dir() or (path.is_file() and path.suffix.lower() in EXTENSIONS)):
            messagebox.showerror("Cannot open", "Select an existing folder or a supported photo.", parent=self.window)
            return
        self.result = path.resolve()
        self.window.destroy()

    def cancel(self):
        self.window.destroy()

    def show(self):
        self.window.wait_visibility()
        self.window.grab_set()
        self.tree.focus_set()
        self.window.wait_window()
        return self.result
