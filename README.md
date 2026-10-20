# Stereo Photo Viewer

A Python desktop viewer that turns ordinary photos into squeezed side-by-side
stereo for a 3D TV. Uses Depth Anything V2 Small, following the depth-based
stereo approach in [2D3D](https://github.com/JulesMellot/2D3D).

## Run

Requires Python 3.10+ with Tkinter (included with standard Windows Python).

```powershell
python -m pip install -r requirements.txt
python main.py
# Or start with a photo:
python main.py "C:\Photos\photo.jpg"
```

The Small depth model starts loading automatically when the app opens and is
downloaded from Hugging Face if it is not cached yet. Chromecast discovery
also starts automatically. Both run in the background while the window remains
usable; discovery does not connect to a receiver or start casting.
Existing Hugging Face model caches are reused. No API key or FFmpeg is needed.
CUDA and Apple MPS are used when available, otherwise inference runs on CPU.
This viewer uses eager PyTorch inference, so Triton is not required.

## Controls

- **Open / Ctrl+O:** select a JPG, PNG, WebP, BMP, TIFF, or a folder in one
  dialog. Select a folder and click Open to start with its first photo and
  browse recursively, including subfolders. Double-click folders to browse
  inside them; click Open without a selection to use the current folder.
  Photos in the selected folder come first.
- **Left / Right:** previous or next photo in the same folder, in natural filename order.
- **Page Down / Page Up:** jump to the first photo in the next/previous folder
  in the current photo list, including in fullscreen. Empty folders are skipped;
  navigation stops at the first/last folder instead of wrapping around.
- **3D strength slider / Up / Down:** adjust strength from 0.000 to 0.080
  (default 0.030). Arrow keys change it by 0.0025, including in fullscreen.
  The current photo updates 500 ms after the last adjustment. Its depth map
  is reused; the selected strength also applies to subsequent photos.
- **Convergence slider / Ctrl+Up / Ctrl+Down:** choose the depth that aligns
  between the eyes. Keyboard shortcuts adjust by 0.025, including in fullscreen.
  **0.000** aligns the far background; **1.000** aligns the nearest foreground.
  The default is **0.500**, in the middle. This is a relative depth position,
  not a distance measured in meters. Changes immediately shift the already
  prepared eye views in opposite directions. There is no debounce, depth
  estimation, stereo regeneration, or reprocessing of the next preload.
  Exposed edges are filled black and each eye is clipped independently.
  Chromecast sends the same shifted image, subject to network/receiver latency.
- **F11 / double-click the photo:** toggle fullscreen.
- **Escape:** leave fullscreen.

When opening a folder, Left/Right also traverse the discovered subfolders.

Move the window to the TV display, enter fullscreen, and manually select
**SBS 3D** on the TV. Fullscreen hides all controls and the cursor, leaving
only the stereo image. This app displays pixels directly; it cannot signal
MP4/MKV metadata or automatically change your TV's HDMI 3D mode.

## Chromecast

1. Put the PC and Chromecast on the same local network.
2. Click **Find Chromecasts**, select your receiver, and click **Cast**.
3. Open a photo or folder and enable **SBS 3D** manually on your TV.

The viewer sends a 1920x1080 half-SBS JPEG with per-eye padding, independent
of the PC window's size or fullscreen state. Navigation and strength changes
automatically update the cast image. **Stop casting** ends the receiver session.
The PC viewer must remain running. Casting uses the default media receiver;
the receiver may resize images or briefly show loading controls between photos.
Actual display behavior depends on the Chromecast/TV model.

Windows may ask to allow Python through the firewall: allow access on your
private/home network so the Chromecast can fetch the generated image. Guest
Wi-Fi/client isolation or a VPN can prevent discovery or image loading. No
router port forwarding is needed. A temporary local HTTP server serves only
generated JPEGs under random URLs; it never exposes your source photo folders.
The server stops when casting ends or the viewer closes. Casting support is
installed by `python -m pip install -r requirements.txt`.

## Image processing

Camera EXIF orientation is applied. Photos larger than 8K are downscaled to fit
7680x4320 before depth estimation and stereo conversion, preserving proportions;
smaller photos are not enlarged. JPEGs use reduced-resolution decoding where
available. Source photos up to 300 megapixels are supported. The
source becomes the left eye; a depth-based horizontal warp generates the right.
Each eye is then centered in a display-shaped black canvas and squeezed to
half width. On a fullscreen 16:9 TV, narrow images are pillarboxed separately
for each eye; ultrawide images are letterboxed. Photos are never cropped.

Conversion runs on a background thread. While you view a photo, the next photo
is loaded, depth-estimated, and converted in the background at the selected
strength. Pending foreground requests take priority over preloading; rapid
navigation discards obsolete pending requests. Preloading never changes the
displayed photo. Failed preloads are reported only if you open that photo.
Recent results are cached in memory (up to six photos with a 256 MiB target;
the current and next photo are retained even if they exceed that target).
Original photos are
never changed. Like 2D3D, the simple warp can stretch pixels at depth boundaries.

## Checks

```powershell
python -m unittest discover -s tests -v
```

Tests use synthetic images and depth maps; no model download or GPU is needed.

The Depth Anything V2 Small model is distributed under Apache-2.0.
