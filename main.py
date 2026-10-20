"""Desktop half-SBS photo viewer for manually enabled SBS 3D TV playback."""

import argparse
from collections import OrderedDict
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import ImageTk

from stereo import EXTENSIONS, compose_tv, load_photo, make_eyes, photos_in_folder, shift_convergence
from casting import CastService
from open_dialog import OpenDialog


class App:
    def __init__(self, root):
        self.root = root
        root.title("Stereo Photo Viewer")
        root.geometry("1100x720")
        root.minsize(640, 400)
        self.photos = []
        self.index = 0
        self.eyes = None
        self.eye_strength = 0.03
        self.display_eyes = None
        self.display_size = None
        self.display_base = None
        self.photo = None
        self.fullscreen = False
        self.generation = 0
        self.folder_generation = 0
        self.strength = tk.DoubleVar(value=0.03)
        self.convergence = tk.DoubleVar(value=0.5)
        self.strength_job = None
        self.cache = OrderedDict()
        self.jobs = queue.Queue(maxsize=1)
        self.prefetch_lock = threading.Lock()
        self.prefetch_pending = None
        self.prefetch_active = None
        self.results = queue.Queue()
        self.resize_job = None
        self.closed = False
        self.caster = None
        self.casting = False

        self.toolbar = ttk.Frame(root, padding=8)
        self.toolbar.pack(side="top", fill="x")
        actions = ttk.Frame(self.toolbar)
        actions.pack(fill="x")
        ttk.Button(actions, text="Open…", command=self.open_source).pack(side="left")
        self.previous = ttk.Button(actions, text="← Previous", command=lambda: self.navigate(-1))
        self.previous.pack(side="left", padx=(12, 4))
        self.next = ttk.Button(actions, text="Next →", command=lambda: self.navigate(1))
        self.next.pack(side="left")
        ttk.Button(actions, text="Fullscreen (F11)", command=self.toggle_fullscreen).pack(side="right")
        strength_bar = ttk.Frame(self.toolbar)
        strength_bar.pack(fill="x", pady=(8, 0))
        strength_bar.columnconfigure(1, weight=1)
        strength_bar.columnconfigure(4, weight=1)
        ttk.Label(strength_bar, text="Strength (↑/↓):").grid(row=0, column=0, sticky="w")
        self.strength_scale = ttk.Scale(
            strength_bar, from_=0.0, to=0.08, variable=self.strength,
            command=self.change_strength, length=160)
        self.strength_scale.grid(row=0, column=1, sticky="ew", padx=8)
        self.strength_label = ttk.Label(strength_bar, text="0.0300", width=6)
        self.strength_label.grid(row=0, column=2, sticky="w")
        ttk.Label(strength_bar, text="Convergence (Ctrl+↑/↓):").grid(
            row=0, column=3, sticky="w", padx=(12, 0))
        self.convergence_scale = ttk.Scale(
            strength_bar, from_=0.0, to=1.0, variable=self.convergence,
            command=self.change_convergence, length=160)
        self.convergence_scale.grid(row=0, column=4, sticky="ew", padx=8)
        self.convergence_label = ttk.Label(strength_bar, text="0.500", width=5)
        self.convergence_label.grid(row=0, column=5, sticky="w")
        self.convergence_scale.bind("<Up>", lambda _event: self.adjust_strength(1))
        self.convergence_scale.bind("<Down>", lambda _event: self.adjust_strength(-1))
        self.convergence_scale.bind("<Left>", lambda _event: self.navigate(-1) or "break")
        self.convergence_scale.bind("<Right>", lambda _event: self.navigate(1) or "break")
        self.strength_scale.bind("<Up>", lambda _event: self.adjust_strength(1))
        self.strength_scale.bind("<Down>", lambda _event: self.adjust_strength(-1))
        self.strength_scale.bind("<Left>", lambda _event: self.navigate(-1) or "break")
        self.strength_scale.bind("<Right>", lambda _event: self.navigate(1) or "break")
        for scale in (self.strength_scale, self.convergence_scale):
            scale.bind("<Control-Up>", lambda _event: self.adjust_convergence(1))
            scale.bind("<Control-Down>", lambda _event: self.adjust_convergence(-1))

        cast_bar = ttk.Frame(self.toolbar)
        cast_bar.pack(fill="x", pady=(8, 0))
        self.scan_button = ttk.Button(cast_bar, text="Find Chromecasts", command=self.scan_casts)
        self.scan_button.pack(side="left")
        self.cast_choice = ttk.Combobox(cast_bar, state="readonly", width=30)
        self.cast_choice.pack(side="left", padx=6)
        self.cast_button = ttk.Button(cast_bar, text="Cast", command=self.start_cast, state="disabled")
        self.cast_button.pack(side="left")
        self.stop_cast_button = ttk.Button(cast_bar, text="Stop casting", command=self.stop_cast, state="disabled")
        self.stop_cast_button.pack(side="left", padx=6)
        self.cast_status = ttk.Label(self.toolbar, text="Chromecast: not connected")
        self.cast_status.pack(anchor="w", pady=(4, 0))
        self.model_status = ttk.Label(self.toolbar, text="Depth model: waiting to load…")
        self.model_status.pack(anchor="w")

        self.status = ttk.Label(root, padding=(8, 6), anchor="w")
        self.status.pack(side="bottom", fill="x")
        self.canvas = tk.Canvas(root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.schedule_render)
        self.canvas.bind("<Double-Button-1>", lambda _event: self.toggle_fullscreen())
        root.bind("<Control-o>", lambda _event: self.open_source())
        root.bind("<Left>", lambda _event: self.navigate(-1))
        root.bind("<Right>", lambda _event: self.navigate(1))
        for widget in (root, self.strength_scale, self.convergence_scale, self.cast_choice):
            widget.bind("<Prior>", lambda _event: self.navigate_folder(-1))
            widget.bind("<Next>", lambda _event: self.navigate_folder(1))
        root.bind("<Up>", lambda _event: self.adjust_strength(1))
        root.bind("<Down>", lambda _event: self.adjust_strength(-1))
        root.bind("<Control-Up>", lambda _event: self.adjust_convergence(1))
        root.bind("<Control-Down>", lambda _event: self.adjust_convergence(-1))
        root.bind("<F11>", lambda _event: self.toggle_fullscreen())
        root.bind("<Escape>", lambda _event: self.set_fullscreen(False))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.status.config(text="Open a photo • ←/→ browse its folder • F11 fullscreen • Enable SBS 3D on your TV")
        self.update_navigation()
        threading.Thread(target=self.worker, daemon=True, name="depth-worker").start()
        self.poll_job = root.after(75, self.poll_results)
        self.startup_job = root.after(0, self.startup)

    def startup(self):
        self.startup_job = None
        if self.closed:
            return
        # If a photo was opened immediately, that job already loads the model.
        try:
            self.jobs.put_nowait("warmup")
        except queue.Full:
            pass
        self.scan_casts()

    def open_source(self, path=None):
        if path is None:
            initial = self.photos[self.index].parent if self.photos else Path.home()
            path = OpenDialog(self.root, initial).show()
        if path:
            if Path(path).is_dir():
                self.open_folder(path)
            else:
                self.open_photo(path)

    def open_photo(self, path=None):
        if path is None:
            self.open_source()
            return
        if not path:
            return
        try:
            selected = Path(path).resolve()
            if not selected.is_file() or selected.suffix.lower() not in EXTENSIONS:
                raise ValueError("Select a JPG, PNG, WebP, BMP, or TIFF photo")
            photos = photos_in_folder(selected)
            index = photos.index(selected)
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot open photo", str(error), parent=self.root)
            return
        self.folder_generation += 1
        self.photos, self.index = photos, index
        self.request_photo()
        self.canvas.focus_set()

    def open_folder(self, path=None):
        if path is None:
            self.open_source()
            return
        if not path:
            return
        folder = Path(path).resolve()
        self.folder_generation += 1
        self.generation += 1
        with self.prefetch_lock:
            self.prefetch_pending = None
        token = self.folder_generation
        self.status.config(text=f"Finding photos in {folder} and its subfolders…")
        def scan():
            try:
                if not folder.is_dir():
                    raise ValueError("Select an existing folder")
                photos = photos_in_folder(folder, recursive=True)
                self.results.put((token, "folder_ready", photos))
            except (OSError, ValueError) as error:
                self.results.put((token, "folder_error", str(error)))
        threading.Thread(target=scan, daemon=True, name="folder-scan").start()

    def change_strength(self, value):
        strength = round(max(0.0, min(0.08, float(value))), 4)
        self.strength.set(strength)
        self.strength_label.config(text=f"{strength:.4f}")
        self.schedule_stereo_update()

    def change_convergence(self, value):
        convergence = round(max(0.0, min(1.0, float(value))), 3)
        self.convergence.set(convergence)
        self.convergence_label.config(text=f"{convergence:.3f}")
        # Convergence only translates the existing views, immediately. Do not
        # touch the conversion queue, pending strength timer, or preload.
        self.render()
        if self.eyes is not None:
            self.update_photo_status()
        self.publish_cast()

    def schedule_stereo_update(self):
        with self.prefetch_lock:
            self.prefetch_pending = None
        if self.strength_job is not None:
            self.root.after_cancel(self.strength_job)
        self.strength_job = self.root.after(500, self.apply_strength)

    def adjust_strength(self, steps):
        self.change_strength(self.strength.get() + steps * 0.0025)
        return "break"

    def adjust_convergence(self, steps):
        self.change_convergence(self.convergence.get() + steps * 0.025)
        return "break"

    def apply_strength(self):
        self.strength_job = None
        if self.photos:
            self.request_photo(keep_current=True)

    def navigate(self, step):
        if not self.photos:
            return
        target = self.index + step
        if 0 <= target < len(self.photos):
            self.index = target
            self.request_photo()

    def update_navigation(self):
        self.previous.config(state="normal" if self.photos and self.index > 0 else "disabled")
        self.next.config(state="normal" if self.photos and self.index < len(self.photos) - 1 else "disabled")

    def navigate_folder(self, step):
        if not self.photos:
            return "break"
        # Use distinct full folder paths, even if nested folders have matching
        # names or their photos are interleaved in natural filename order.
        starts = {}
        for index, path in enumerate(self.photos):
            starts.setdefault(path.parent, index)
        folders = list(starts)
        current = folders.index(self.photos[self.index].parent)
        target = current + step
        if 0 <= target < len(folders):
            self.index = starts[folders[target]]
            self.request_photo()
        return "break"

    def request_photo(self, keep_current=False):
        if self.caster is not None:
            self.caster.clear_pending()
        if self.strength_job is not None:
            self.root.after_cancel(self.strength_job)
            self.strength_job = None
        self.generation += 1
        with self.prefetch_lock:
            self.prefetch_pending = None
        path = self.photos[self.index]
        self.update_navigation()
        self.root.title(f"{path.name} — Stereo Photo Viewer")
        if not keep_current:
            self.eyes = None
            self.render()
        # Keep only the newest pending request when arrow keys are pressed quickly.
        try:
            self.jobs.get_nowait()
        except queue.Empty:
            pass
        cached = self.cache.get(path)
        strength = self.strength.get()
        if cached is not None and cached["strength"] == strength:
            self.eyes = cached["eyes"]
            self.eye_strength = cached["strength"]
            self.cache.move_to_end(path)
            self.show_ready()
            return
        self.status.config(text=f"{self.index + 1}/{len(self.photos)} • {path.name} • Converting to 3D…")
        self.jobs.put_nowait((self.generation, path, strength, cached))

    def worker(self):
        estimator = None
        def load_estimator():
            nonlocal estimator
            if estimator is None:
                self.results.put((0, "model_loading", None))
                from depth import DepthEstimator
                estimator = DepthEstimator()
                self.results.put((0, "model_ready", getattr(estimator, "device", "")))
            return estimator
        last_path, last_entry = None, None
        while True:
            prefetch = False
            try:
                job = self.jobs.get(timeout=0.05)
            except queue.Empty:
                with self.prefetch_lock:
                    job = self.prefetch_pending
                    self.prefetch_pending = None
                    if job is not None:
                        self.prefetch_active = (job[1], job[2])
                if job is None:
                    continue
                prefetch = True
            if job is None:
                return
            if job == "warmup":
                try:
                    load_estimator()
                except Exception as error:
                    self.results.put((0, "model_error", str(error)))
                continue
            generation, path, strength, cached = job
            try:
                if prefetch and (not self.jobs.empty() or generation != self.generation):
                    continue
                # Navigation can request a photo while its preload is finishing.
                # Reuse that result instead of estimating its depth a second time.
                if cached is None and path == last_path:
                    cached = last_entry
                if cached is None:
                    rgb = load_photo(path)
                    if estimator is None:
                        if not prefetch:
                            self.results.put((generation, "loading", None))
                        load_estimator()
                    depth = estimator.predict(rgb)
                else:
                    rgb, depth = cached["rgb"], cached["depth"]
                if cached is not None and cached["strength"] == strength:
                    eyes = cached["eyes"]
                else:
                    eyes = make_eyes(rgb, depth, strength=strength)
                entry = {"rgb": rgb, "depth": depth, "eyes": eyes, "strength": strength}
                last_path, last_entry = path, entry
                self.results.put((generation, "prefetched" if prefetch else "ready", (path, entry)))
            except Exception as error:
                if not prefetch:
                    self.results.put((generation, "error", str(error)))
            finally:
                if prefetch:
                    with self.prefetch_lock:
                        self.prefetch_active = None

    def prefetch_next(self):
        if self.closed or self.strength_job is not None or self.index + 1 >= len(self.photos):
            return
        path = self.photos[self.index + 1]
        strength = self.strength.get()
        cached = self.cache.get(path)
        if cached is not None and cached["strength"] == strength:
            return
        with self.prefetch_lock:
            if self.prefetch_active != (path, strength):
                self.prefetch_pending = (self.generation, path, strength, cached)

    def trim_cache(self):
        protected = set(self.photos[self.index:self.index + 2])
        while len(self.cache) > 6 or sum(
                item["rgb"].nbytes + item["depth"].nbytes + sum(
                    eye.width * eye.height * 3 for eye in item["eyes"])
                for item in self.cache.values()) > 256 * 1024 * 1024:
            victim = next((path for path in self.cache if path not in protected), None)
            if victim is None:
                break
            del self.cache[victim]

    def poll_results(self):
        if self.closed:
            return
        self.poll_cast_events()
        while True:
            try:
                generation, kind, payload = self.results.get_nowait()
            except queue.Empty:
                break
            if kind.startswith("model_"):
                text = {"model_loading": "Loading Depth Anything V2 Small… First use may download weights.",
                        "model_ready": f"Depth model: ready{' (' + payload + ')' if payload else ''}",
                        "model_error": f"Depth model could not load: {payload}"}[kind]
                self.model_status.config(text=text)
                continue
            if kind.startswith("folder_"):
                if generation != self.folder_generation:
                    continue
                if kind == "folder_error" or not payload:
                    self.status.config(text="Folder could not be opened" if kind == "folder_error"
                                       else "No supported photos found in this folder or its subfolders")
                    self.set_fullscreen(False)
                    messagebox.showinfo("Open folder", payload if kind == "folder_error" else
                                        "No supported photos found in this folder or its subfolders.",
                                        parent=self.root)
                else:
                    self.photos, self.index = payload, 0
                    self.request_photo()
                    self.canvas.focus_set()
                continue
            if kind in ("ready", "prefetched"):
                path, entry = payload
                self.cache[path] = entry
                self.cache.move_to_end(path)
                self.trim_cache()
            if kind == "prefetched":
                continue
            if generation != self.generation:
                continue
            if kind == "loading":
                self.status.config(text="Loading Depth Anything V2 Small… First use may download model weights.")
            elif kind == "ready":
                self.eyes = payload[1]["eyes"]
                self.eye_strength = payload[1]["strength"]
                self.show_ready()
            elif kind == "error":
                self.status.config(text=f"Could not convert {self.photos[self.index].name}")
                # Return to windowed mode so error dialogs remain readable in 2D.
                self.set_fullscreen(False)
                messagebox.showerror("Conversion failed", payload, parent=self.root)
        self.poll_job = self.root.after(75, self.poll_results)

    def update_photo_status(self):
        self.status.config(text=f"{self.index + 1}/{len(self.photos)} • {self.photos[self.index].name}"
                           f" • Half-SBS 3D • Strength {self.strength.get():.4f}"
                           f" • Convergence {self.convergence.get():.3f}")

    def show_ready(self):
        self.update_photo_status()
        self.render()
        self.prefetch_next()
        self.publish_cast()

    def publish_cast(self):
        if self.casting and self.eyes is not None:
            self.caster.publish(self.eyes, self.photos[self.index].name,
                                strength=self.eye_strength, convergence=self.convergence.get())

    def scan_casts(self):
        if self.caster is None:
            self.caster = CastService()
        self.scan_button.config(state="disabled")
        self.cast_button.config(state="disabled")
        self.cast_status.config(text="Searching for Chromecasts on your local network…")
        self.caster.scan()

    def start_cast(self):
        index = self.cast_choice.current()
        if self.caster is None or index < 0:
            return
        self.cast_button.config(state="disabled")
        self.scan_button.config(state="disabled")
        self.cast_status.config(text="Connecting to Chromecast…")
        self.caster.connect(index)

    def stop_cast(self):
        self.casting = False
        if self.caster is not None:
            self.stop_cast_button.config(state="disabled")
            self.cast_status.config(text="Stopping casting…")
            self.caster.stop()

    def poll_cast_events(self):
        if self.caster is None:
            return
        while True:
            try:
                kind, payload = self.caster.events.get_nowait()
            except queue.Empty:
                break
            if kind == "devices":
                self.cast_choice.config(values=payload, state="readonly")
                if payload:
                    self.cast_choice.current(0)
                else:
                    self.cast_choice.set("")
                self.scan_button.config(state="normal")
                self.cast_button.config(state="normal" if payload else "disabled")
                self.cast_status.config(text=f"Found {len(payload)} display(s). Select one and click Cast."
                                        if payload else "No Chromecasts found. Check Wi-Fi and Windows firewall.")
            elif kind == "connected":
                self.casting = True
                self.cast_choice.config(state="disabled")
                self.stop_cast_button.config(state="normal")
                self.cast_status.config(text=f"Connected to {payload} • Enable SBS 3D on the TV")
                if self.eyes is not None:
                    self.publish_cast()
            elif kind == "sent":
                if self.casting:
                    self.cast_status.config(text=f"Sent: {payload} • Enable SBS 3D on the TV")
            elif kind == "stopped":
                self.casting = False
                self.cast_choice.config(state="readonly")
                self.scan_button.config(state="normal")
                self.cast_button.config(state="normal" if self.cast_choice.current() >= 0 else "disabled")
                self.cast_status.config(text="Chromecast: disconnected")
            elif kind == "error":
                self.cast_status.config(text=f"Chromecast: {payload}")
                if not self.casting:
                    self.scan_button.config(state="normal")
                    self.cast_button.config(state="normal" if self.cast_choice.current() >= 0 else "disabled")

    def schedule_render(self, _event=None):
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(80, self.render)

    def render(self):
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        self.resize_job = None
        self.canvas.delete("all")
        if self.eyes is None:
            self.display_eyes = self.display_base = None
            return
        size = (self.canvas.winfo_width(), self.canvas.winfo_height())
        if min(size) < 2:
            return
        if self.eyes is not self.display_eyes or size != self.display_size:
            self.display_base = compose_tv(*self.eyes, size)
            self.display_eyes, self.display_size = self.eyes, size
        image = shift_convergence(self.display_base, self.convergence.get(), self.eye_strength)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

    def toggle_fullscreen(self):
        self.set_fullscreen(not self.fullscreen)

    def set_fullscreen(self, enabled):
        if enabled == self.fullscreen:
            return
        self.fullscreen = enabled
        self.root.attributes("-fullscreen", enabled)
        if enabled:
            self.toolbar.pack_forget()
            self.status.pack_forget()
        else:
            self.toolbar.pack(side="top", fill="x", before=self.canvas)
            self.status.pack(side="bottom", fill="x", before=self.canvas)
        self.canvas.config(cursor="none" if enabled else "")
        self.canvas.focus_set()
        self.schedule_render()

    def close(self):
        self.closed = True
        if self.caster is not None:
            self.caster.close()
        with self.prefetch_lock:
            self.prefetch_pending = None
        for job in (self.strength_job, self.resize_job, self.poll_job, self.startup_job):
            if job is not None:
                self.root.after_cancel(job)
        try:
            self.jobs.get_nowait()
        except queue.Empty:
            pass
        self.jobs.put_nowait(None)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="View 2D photos as half-SBS 3D on a TV")
    parser.add_argument("photo", nargs="?", help="Optional photo or folder to open")
    args = parser.parse_args()
    root = tk.Tk()
    app = App(root)
    if args.photo:
        root.after(100, lambda: app.open_source(args.photo))
    root.mainloop()


if __name__ == "__main__":
    main()
