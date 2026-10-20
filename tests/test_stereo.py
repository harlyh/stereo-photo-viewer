import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from stereo import compose_tv, load_photo, make_eyes, photos_in_folder, shift_convergence


class StereoTests(unittest.TestCase):
    def test_convergence_translates_eyes_without_warp_or_crossing_seam(self):
        packed = Image.new("RGB", (400, 30), "red")
        packed.paste(Image.new("RGB", (200, 30), "lime"), (200, 0))
        original = np.asarray(packed).copy()
        self.assertIs(shift_convergence(packed, 0.5, 0.08), packed)
        near = np.asarray(shift_convergence(packed, 1.0, 0.08))
        # Offset = 0.5 * 0.08 * 200 / 2 = four pixels per eye.
        np.testing.assert_array_equal(near[:, :196], original[:, 4:200])
        self.assertTrue(np.all(near[:, 196:204] == 0))
        np.testing.assert_array_equal(near[:, 204:], original[:, 200:396])
        far = np.asarray(shift_convergence(packed, 0.0, 0.08))
        self.assertTrue(np.all(far[:, :4] == 0))
        self.assertTrue(np.all(far[:, -4:] == 0))
        np.testing.assert_array_equal(far[:, 4:200], original[:, :196])
        np.testing.assert_array_equal(far[:, 200:396], original[:, 204:])
        np.testing.assert_array_equal(np.asarray(packed), original)

    def test_recursive_folder_navigation_order(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            nested = folder / "album" / "nested"
            nested.mkdir(parents=True)
            for path in (folder / "photo10.jpg", folder / "photo2.jpg",
                         nested / "photo1.jpg", folder / "album" / "photo1.png"):
                Image.new("RGB", (10, 10), "white").save(path)
            (folder / "notes.txt").write_text("ignore")
            self.assertEqual([p.name for p in photos_in_folder(folder)],
                             ["photo2.jpg", "photo10.jpg"])
            recursive = photos_in_folder(folder, recursive=True)
            self.assertEqual([p.name for p in recursive[:2]], ["photo2.jpg", "photo10.jpg"])
            self.assertEqual(len(recursive), 4)
            self.assertIn(nested / "photo1.jpg", recursive)

    def test_warp_preserves_left_and_changes_right(self):
        rgb = np.random.default_rng(0).integers(0, 256, (90, 160, 3), dtype=np.uint8)
        depth = np.ones((90, 160), dtype=np.float32)
        left, right = make_eyes(rgb, depth)
        np.testing.assert_array_equal(np.asarray(left), rgb)
        self.assertFalse(np.array_equal(np.asarray(right), rgb))

    def test_independent_pillarboxes_and_eye_order(self):
        left = Image.new("RGB", (120, 90), "red")
        right = Image.new("RGB", (120, 90), "lime")
        packed = np.asarray(compose_tv(left, right, (320, 180)))
        self.assertEqual(packed.shape, (180, 320, 3))
        for eye, color in zip(np.hsplit(packed, 2), ((255, 0, 0), (0, 255, 0))):
            # Exclude the narrow Lanczos transition at the image/bar edges.
            self.assertTrue(np.all(eye[:, :16] == 0))
            self.assertTrue(np.all(eye[:, -16:] == 0))
            self.assertTrue(np.all(eye[:, 24:-24] == color))

    def test_widescreen_fills_each_eye(self):
        image = Image.new("RGB", (320, 180), "white")
        packed = np.asarray(compose_tv(image, image, (320, 180)))
        self.assertTrue(np.all(packed == 255))

    def test_ultrawide_letterbox(self):
        image = Image.new("RGB", (320, 90), "white")
        packed = np.asarray(compose_tv(image, image, (320, 180)))
        self.assertTrue(np.all(packed[:45] == 0))
        self.assertTrue(np.all(packed[-45:] == 0))
        self.assertTrue(np.all(packed[45:-45] == 255))

    def test_orientation_and_natural_folder_order(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            image = Image.new("RGB", (120, 80), "white")
            exif = Image.Exif()
            exif[274] = 6
            image.save(folder / "photo2.jpg", exif=exif)
            image.save(folder / "photo10.jpg")
            (folder / "ignore.txt").write_text("ignored")
            self.assertEqual(load_photo(folder / "photo2.jpg").shape[:2], (120, 80))
            self.assertEqual([p.name for p in photos_in_folder(folder / "photo2.jpg")],
                             ["photo2.jpg", "photo10.jpg"])

    def test_transparency_and_processing_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transparent.png"
            Image.new("RGBA", (2400, 1600), (255, 255, 255, 0)).save(path)
            rgb = load_photo(path, max_size=(1920, 1080))
            self.assertEqual(rgb.shape, (1080, 1620, 3))
            self.assertTrue(np.all(rgb == 0))

    def test_large_header_limit_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.jpg"
            Image.new("RGB", (120, 80), "white").save(path)
            # Simulate a source exceeding Pillow's default threshold without
            # allocating a hundreds-of-megapixels image in the unit tests.
            with patch.object(Image, "MAX_IMAGE_PIXELS", 1000):
                self.assertEqual(load_photo(path).shape[:2], (80, 120))
                self.assertEqual(Image.MAX_IMAGE_PIXELS, 1000)
                with self.assertRaises(OSError):
                    load_photo(Path(directory) / "missing.jpg")
                self.assertEqual(Image.MAX_IMAGE_PIXELS, 1000)
            with patch("stereo.MAX_SOURCE_PIXELS", 5000):
                with self.assertRaisesRegex(ValueError, "source limit"):
                    load_photo(path)

    def test_orientation_before_size_limit_and_no_upscaling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotated.jpg"
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (120, 80), "white").save(path, exif=exif)
            self.assertEqual(load_photo(path, (60, 30)).shape[:2], (30, 20))
            self.assertEqual(load_photo(path).shape[:2], (120, 80))


if __name__ == "__main__":
    unittest.main()
