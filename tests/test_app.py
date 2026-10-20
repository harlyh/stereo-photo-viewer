"""Headless-window smoke test: real Tk widgets and worker, stub depth model."""

import tempfile
import queue
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from main import App
from stereo import make_eyes, compose_tv


class FakeEstimator:
    def predict(self, rgb):
        return np.ones(rgb.shape[:2], dtype=np.float32)


class AppTests(unittest.TestCase):
    def setUp(self):
        cast_patch = patch("main.CastService")
        self.cast_factory = cast_patch.start()
        self.cast_factory.return_value.events = queue.Queue()
        self.addCleanup(cast_patch.stop)

    def test_startup_loads_model_once_and_scans_cast_without_opening_photo(self):
        root = tk.Tk()
        root.withdraw()
        factory = Mock(return_value=FakeEstimator())
        with patch.dict("sys.modules", {"depth": SimpleNamespace(DepthEstimator=factory)}):
            app = App(root)
            try:
                deadline = time.monotonic() + 3
                while "ready" not in str(app.model_status["text"]) and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                factory.assert_called_once()
                self.cast_factory.return_value.scan.assert_called_once()
                self.assertEqual(app.photos, [])
                self.assertIn("ready", str(app.model_status["text"]))
                with tempfile.TemporaryDirectory() as directory:
                    photo = Path(directory) / "photo.jpg"
                    Image.new("RGB", (160, 90), "white").save(photo)
                    app.open_photo(photo)
                    deadline = time.monotonic() + 3
                    while app.eyes is None and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertIsNotNone(app.eyes)
                    factory.assert_called_once()
            finally:
                app.close()

    def test_page_navigation_skips_folders_and_stops_at_boundaries(self):
        app = App.__new__(App)
        root = Path("events")
        app.photos = [root / f"event{event}" / f"photo{photo}.jpg"
                      for event in (1, 2, 3, 4) for photo in (1, 2)]
        app.index = 1
        app.request_photo = Mock()
        for expected in (2, 4, 6):
            self.assertEqual(app.navigate_folder(1), "break")
            self.assertEqual(app.index, expected)
        self.assertEqual(app.request_photo.call_count, 3)
        app.navigate_folder(1)
        self.assertEqual(app.index, 6)
        self.assertEqual(app.request_photo.call_count, 3)
        app.index = 7
        app.navigate_folder(-1)
        self.assertEqual(app.index, 4, "Previous folder starts at its first photo")
        app.index = 0
        app.request_photo.reset_mock()
        app.navigate_folder(-1)
        app.request_photo.assert_not_called()
        app.photos = []
        app.navigate_folder(1)
        app.request_photo.assert_not_called()

    def test_page_navigation_distinguishes_nested_paths(self):
        app = App.__new__(App)
        root = Path("events")
        app.photos = [root / "a" / "day1" / "1.jpg", root / "b" / "day1" / "1.jpg",
                      root / "a" / "day1" / "2.jpg"]
        app.index = 2
        app.request_photo = Mock()
        app.navigate_folder(1)
        self.assertEqual(app.index, 1)
        app.navigate_folder(-1)
        self.assertEqual(app.index, 0)

    def test_recursive_open_and_debounced_strength_without_new_depth(self):
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        def pump(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(
                    "sys.modules", {"depth": SimpleNamespace(DepthEstimator=FakeEstimator)}), patch.object(
                        FakeEstimator, "predict", autospec=True,
                        side_effect=lambda _self, rgb: np.ones(rgb.shape[:2], dtype=np.float32)) as predict:
                folder = Path(directory)
                (folder / "nested").mkdir()
                Image.new("RGB", (160, 90), "white").save(folder / "first.png")
                Image.new("RGB", (160, 90), "white").save(folder / "nested" / "next.png")
                app.open_folder(folder)
                pump(0.4)
                self.assertEqual(len(app.photos), 2)
                self.assertEqual(app.photos[0].name, "first.png")
                self.assertIsNotNone(app.eyes)
                self.assertEqual(predict.call_count, 2, "The next photo should already be processed")
                self.assertEqual(app.convergence.get(), 0.5)
                self.assertIn(folder / "nested" / "next.png", app.cache)
                self.assertEqual(app.index, 0, "Preloading must not advance the displayed photo")
                original_eyes = app.eyes
                with patch("main.make_eyes", wraps=make_eyes) as warp:
                    app.adjust_strength(1)
                    pump(0.3)
                    app.adjust_strength(-1)
                    app.adjust_strength(1)
                    pump(0.3)
                    self.assertAlmostEqual(app.strength.get(), 0.0325)
                    self.assertIs(app.eyes, original_eyes)
                    self.assertEqual(warp.call_count, 0, "Wait 500 ms after the final keypress")
                    pump(0.4)
                    self.assertEqual(warp.call_count, 2, "Update the current photo and its preload")
                    self.assertTrue(all(call.kwargs["strength"] == 0.0325 for call in warp.call_args_list))
                    self.assertEqual(predict.call_count, 2, "Strength changes reuse both depth maps")
                with patch.object(app.canvas, "winfo_width", return_value=320), patch.object(
                        app.canvas, "winfo_height", return_value=180):
                    app.render()
                    prepared = app.display_base
                    generation = app.generation
                    with patch("main.make_eyes", wraps=make_eyes) as warp, patch(
                            "main.compose_tv", wraps=compose_tv) as compose:
                        app.change_convergence(0.2)
                        self.assertEqual(app.convergence.get(), 0.2)
                        self.assertIs(app.display_base, prepared)
                        self.assertIsNone(app.strength_job, "No convergence debounce timer")
                        self.assertEqual(app.generation, generation)
                        self.assertEqual(compose.call_count, 0, "Reuse the display-sized eye views")
                        pump(0.7)
                        self.assertEqual(warp.call_count, 0, "Convergence never regenerates stereo")
                        self.assertEqual(predict.call_count, 2)
                preloaded_eyes = app.cache[folder / "nested" / "next.png"]["eyes"]
                app.navigate(1)
                self.assertIs(app.eyes, preloaded_eyes, "The next photo should display without waiting")
                pump(0.3)
                self.assertEqual(app.photos[app.index].name, "next.png")
                self.assertEqual(app.cache[app.photos[app.index]]["strength"], 0.0325)
                app.adjust_strength(-1000)
                self.assertEqual(app.strength.get(), 0.0)
                app.adjust_strength(1000)
                self.assertEqual(app.strength.get(), 0.08)
        finally:
            app.close()

    def test_open_navigate_and_fullscreen(self):
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(
                    "sys.modules", {"depth": SimpleNamespace(DepthEstimator=FakeEstimator)}):
                paths = [Path(directory) / f"photo{i}.png" for i in (1, 2, 10)]
                for path in paths:
                    Image.new("RGB", (160, 120), "white").save(path)
                app.open_photo(paths[0])
                deadline = time.monotonic() + 5
                while app.eyes is None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.eyes, "Background conversion did not complete")
                self.assertEqual(str(app.previous["state"]), "disabled")
                app.navigate(1)
                app.navigate(1)
                self.assertEqual(app.photos[app.index].name, "photo10.png")
                self.assertEqual(str(app.next["state"]), "disabled")
                app.navigate(-2)
                self.assertIsNotNone(app.eyes, "Returning to a cached image should be instant")
                with patch.object(app.canvas, "winfo_width", return_value=320), patch.object(
                        app.canvas, "winfo_height", return_value=180):
                    app.render()
                self.assertEqual(app.photo.width(), 320)
                self.assertEqual(app.photo.height(), 180)
                app.set_fullscreen(True)
                self.assertTrue(app.fullscreen)
                self.assertEqual(app.toolbar.winfo_manager(), "")
                app.set_fullscreen(False)
                self.assertFalse(app.fullscreen)
                self.assertEqual(app.toolbar.winfo_manager(), "pack")
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
