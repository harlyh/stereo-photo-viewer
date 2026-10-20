"""Photo loading, depth-based stereo synthesis, and half-SBS TV composition."""

from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MAX_PHOTO_SIZE = (7680, 4320)
MAX_SOURCE_PIXELS = 300_000_000


def photos_in_folder(path, recursive=False):
    """Natural order, with root-folder photos before photos in subfolders."""
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    def key(item):
        natural = [int(part) if part.isdigit() else part.casefold()
                   for part in re.split(r"(\d+)", item.relative_to(folder).as_posix())]
        return item.parent != folder, natural
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    return sorted((p for p in candidates
                   if p.is_file() and p.suffix.lower() in EXTENSIONS), key=key)


def load_photo(path, max_size=MAX_PHOTO_SIZE):
    """Fit oriented photos within 8K before color conversion and stereo work."""
    # Pillow checks source dimensions before decoding. Raise its header limit
    # locally so high-resolution photos can reach the thumbnail step, while
    # retaining an explicit source-size ceiling and restoring the global limit.
    previous_limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
        try:
            source = Image.open(path)
        except Image.DecompressionBombError as error:
            raise ValueError("Photo exceeds the 300-megapixel source limit") from error
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit
    with source:
        if source.width * source.height > MAX_SOURCE_PIXELS:
            raise ValueError("Photo exceeds the 300-megapixel source limit")
        orientation = source.getexif().get(274, 1)
        target = max_size[::-1] if orientation in (5, 6, 7, 8) else max_size
        # thumbnail uses decoder-level reduced-resolution JPEG loading where
        # supported. Do this before EXIF transpose/copy or RGBA conversion.
        source.thumbnail(target, Image.Resampling.LANCZOS)
        image = ImageOps.exif_transpose(source)
        if "A" in image.getbands() or "transparency" in image.info:
            image = image.convert("RGBA")
            background = Image.new("RGBA", image.size, "black")
            image = Image.alpha_composite(background, image)
        image = image.convert("RGB")
        return np.array(image)


def make_eyes(rgb, depth, strength=0.03):
    """Generate a stereo pair centered at the midpoint of relative depth."""
    height, width = rgb.shape[:2]
    if depth.shape != (height, width):
        raise ValueError("Depth map must match the photo dimensions")
    map_x = np.broadcast_to(np.arange(width, dtype=np.float32), (height, width)).copy()
    map_x += (depth.astype(np.float32) - 0.5) * (strength * width)
    map_y = np.broadcast_to(np.arange(height, dtype=np.float32)[:, None], (height, width)).copy()
    right = cv2.remap(rgb, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(rgb), Image.fromarray(right)


def compose_tv(left, right, size):
    """Fit each eye into a display-shaped canvas, then squeeze and pack SBS.

    Padding each eye separately preserves the stereo centers. On a 16:9 TV,
    narrower photos have pillarboxes and wider photos have letterboxes.
    """
    width, height = size
    width = max(2, width - width % 2)
    height = max(1, height)
    output = Image.new("RGB", (width, height), "black")
    for index, eye in enumerate((left, right)):
        fitted = ImageOps.contain(eye, (width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (width, height), "black")
        canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
        squeezed = canvas.resize((width // 2, height), Image.Resampling.LANCZOS)
        output.paste(squeezed, (index * (width // 2), 0))
    return output


def shift_convergence(packed, convergence=0.5, strength=0.03):
    """Translate existing SBS eye views in opposite directions, without warping.

    Work at display resolution and clip each eye independently, filling exposed
    pixels black so the eye views cannot spill across the center seam.
    """
    eye_width = packed.width // 2
    offset = round((convergence - 0.5) * strength * eye_width / 2)
    if offset == 0:
        return packed
    output = Image.new("RGB", packed.size, "black")
    for index, shift in enumerate((-offset, offset)):
        eye = packed.crop((index * eye_width, 0, (index + 1) * eye_width, packed.height))
        canvas = Image.new("RGB", eye.size, "black")
        canvas.paste(eye, (shift, 0))
        output.paste(canvas, (index * eye_width, 0))
    return output
