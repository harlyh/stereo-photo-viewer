from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from PIL import Image

from main import App
from open_dialog import OpenDialog


class OpenTests(unittest.TestCase):
    def test_picker_accepts_photos_folders_and_current_folder(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory).resolve()
                child = folder / "album"
                child.mkdir()
                photo = folder / "photo.jpg"
                Image.new("RGB", (10, 10), "white").save(photo)
                (folder / "notes.txt").write_text("ignore")
                for choice in (photo, child, None):
                    dialog = OpenDialog(root, folder)
                    dialog.window.withdraw()
                    self.assertNotIn(folder / "notes.txt", dialog.entries.values())
                    if choice is not None:
                        item = next(key for key, path in dialog.entries.items() if path == choice)
                        dialog.tree.selection_set(item)
                    dialog.choose()
                    self.assertEqual(dialog.result, choice or folder)
                dialog = OpenDialog(root, folder)
                dialog.window.withdraw()
                item = next(key for key, path in dialog.entries.items() if path == child)
                dialog.tree.selection_set(item)
                dialog.activate()
                self.assertEqual(dialog.folder, child)
                self.assertIsNone(dialog.result)
                dialog.cancel()
        finally:
            root.destroy()

    def test_unified_open_dispatches_and_cancellation_does_nothing(self):
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(app, "open_photo") as photo_open, patch.object(
                    app, "open_folder") as folder_open:
                folder = Path(directory)
                photo = folder / "photo.jpg"
                Image.new("RGB", (10, 10), "white").save(photo)
                app.open_source(photo)
                photo_open.assert_called_once_with(photo)
                app.open_source(folder)
                folder_open.assert_called_once_with(folder)
                with patch("main.OpenDialog") as picker:
                    picker.return_value.show.return_value = None
                    app.open_source()
                self.assertEqual(photo_open.call_count, 1)
                self.assertEqual(folder_open.call_count, 1)
        finally:
            app.close()
