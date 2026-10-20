"""Local generated-image serving and asynchronous Chromecast control."""

from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import queue
import secrets
import socket
import threading

from stereo import compose_tv, shift_convergence


class FrameServer:
    """Serve only a few generated JPEGs under unguessable, immutable URLs."""
    def __init__(self, bind="0.0.0.0"):
        self.frames = OrderedDict()
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.reply(False)

            def do_HEAD(self):
                self.reply(True)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Range")
                self.end_headers()

            def reply(self, head):
                with owner.lock:
                    data = owner.frames.get(self.path)
                if data is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if not head:
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        self.http = ThreadingHTTPServer((bind, 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def publish(self, image, host):
        data = BytesIO()
        image.save(data, "JPEG", quality=95, subsampling=0)
        path = f"/{secrets.token_urlsafe(24)}.jpg"
        with self.lock:
            self.frames[path] = data.getvalue()
            while len(self.frames) > 4:
                self.frames.popitem(last=False)
        return f"http://{host}:{self.http.server_port}{path}"

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        with self.lock:
            self.frames.clear()


def address_for_device(device_host):
    """Select the PC's interface routed toward the receiver, including multi-NIC PCs."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect((device_host, 8009))
        address = connection.getsockname()[0]
    if address.startswith("127.") or address == "0.0.0.0":
        raise RuntimeError("Could not determine a LAN address reachable by the Chromecast")
    return address


class CastService:
    """Serialize discovery/control and coalesce image updates outside Tk's thread."""
    def __init__(self):
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.lock = threading.Lock()
        self.pending = None
        self.closed = threading.Event()
        self.devices = []
        self.browser = None
        self.cast = None
        self.media_started = False
        self.server = None
        self.host = None
        self.thread = threading.Thread(target=self.run, daemon=True, name="chromecast")
        self.thread.start()

    def scan(self):
        self.commands.put(("scan", None))

    def connect(self, index):
        self.commands.put(("connect", index))

    def stop(self):
        with self.lock:
            self.pending = None
        self.commands.put(("stop", None))

    def publish(self, eyes, title, strength=0.03, convergence=0.5):
        with self.lock:
            self.pending = (eyes, title, strength, convergence)

    def clear_pending(self):
        with self.lock:
            self.pending = None

    def close(self):
        self.closed.set()
        self.commands.put(("close", None))

    def release_devices(self):
        for device in self.devices:
            try:
                device.disconnect(timeout=1)
            except Exception:
                pass
        self.devices = []
        if self.browser is not None:
            self.browser.stop_discovery()
            self.browser = None

    def stop_session(self):
        receiver, self.cast = self.cast, None
        try:
            if receiver is not None and self.media_started:
                receiver.quit_app(timeout=5)
        finally:
            self.media_started = False
            if self.server is not None:
                self.server.close()
                self.server = None
            self.host = None

    def run(self):
        prepared_eyes, prepared_frame = None, None
        try:
            while not self.closed.is_set():
                try:
                    action, argument = self.commands.get(timeout=0.1)
                except queue.Empty:
                    with self.lock:
                        frame, self.pending = self.pending, None
                    if frame is None or self.cast is None:
                        continue
                    try:
                        eyes, title, strength, convergence = frame
                        if eyes is not prepared_eyes:
                            prepared_frame = compose_tv(*eyes, (1920, 1080))
                            prepared_eyes = eyes
                        image = shift_convergence(prepared_frame, convergence, strength)
                        url = self.server.publish(image, self.host)
                        self.cast.media_controller.play_media(
                            url, "image/jpeg", stream_type="BUFFERED",
                            metadata={"metadataType": 4, "title": title,
                                      "width": 1920, "height": 1080})
                        self.media_started = True
                        self.events.put(("sent", title))
                    except Exception as error:
                        self.events.put(("error", f"Could not send photo: {error}"))
                    continue
                try:
                    if action == "close":
                        break
                    if action == "scan":
                        if self.cast is not None:
                            raise RuntimeError("Stop casting before rescanning")
                        try:
                            import pychromecast
                        except ImportError as error:
                            raise RuntimeError("Install casting support: python -m pip install -r requirements.txt") from error
                        self.release_devices()
                        devices, self.browser = pychromecast.get_chromecasts(tries=1, timeout=5)
                        self.devices = []
                        for device in devices:
                            if device.cast_info.cast_type not in ("audio", "group"):
                                self.devices.append(device)
                            else:
                                try:
                                    device.disconnect(timeout=1)
                                except Exception:
                                    pass
                        self.devices.sort(key=lambda d: (d.cast_info.friendly_name or "").casefold())
                        self.events.put(("devices", [
                            f"{d.cast_info.friendly_name} ({d.cast_info.host})" for d in self.devices]))
                    elif action == "connect":
                        receiver = self.devices[argument]
                        receiver.wait(timeout=10)
                        host = address_for_device(receiver.socket_client.host)
                        self.server = FrameServer()
                        self.host, self.cast = host, receiver
                        owner = self
                        class MediaEvents:
                            def new_media_status(self, status):
                                if (owner.cast is receiver and status.player_state == "IDLE"
                                        and status.idle_reason == "ERROR"):
                                    owner.events.put(("error", "Receiver could not load the image. Check the PC firewall and network access."))

                            def load_media_failed(self, _item_id, error_code):
                                if owner.cast is receiver:
                                    owner.events.put(("error", f"Receiver rejected the image (code {error_code}). Check the PC firewall and image support."))
                        receiver.media_controller.register_status_listener(MediaEvents())
                        self.events.put(("connected", receiver.cast_info.friendly_name))
                    elif action == "stop":
                        self.stop_session()
                        prepared_eyes, prepared_frame = None, None
                        self.events.put(("stopped", None))
                except Exception as error:
                    self.events.put(("error", str(error)))
        finally:
            try:
                self.stop_session()
            except Exception:
                pass
            self.release_devices()
