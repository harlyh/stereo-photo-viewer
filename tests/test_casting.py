from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from casting import CastService, FrameServer
from stereo import compose_tv


class CastTests(unittest.TestCase):
    def test_server_exposes_only_published_images(self):
        server = FrameServer(bind="127.0.0.1")
        try:
            url = server.publish(Image.new("RGB", (320, 180), "red"), "127.0.0.1")
            with urlopen(url, timeout=2) as response:
                self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                image = Image.open(BytesIO(response.read()))
                self.assertEqual(image.size, (320, 180))
            with urlopen(Request(url, method="HEAD"), timeout=2) as response:
                self.assertEqual(response.read(), b"")
                self.assertGreater(int(response.headers["Content-Length"]), 0)
            root = f"http://127.0.0.1:{server.http.server_port}"
            for path in ("/", "/README.md", "/../main.py"):
                with self.assertRaises(HTTPError) as error:
                    urlopen(root + path, timeout=2)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
        finally:
            server.close()

    def test_cast_discovery_send_update_and_cleanup(self):
        device = Mock()
        device.cast_info = SimpleNamespace(friendly_name="Test TV", host="192.0.2.10", cast_type="cast")
        device.socket_client.host = "192.0.2.10"
        browser = Mock()
        speaker = Mock()
        speaker.cast_info = SimpleNamespace(cast_type="audio")
        real_server = FrameServer
        with patch("pychromecast.get_chromecasts", return_value=([device, speaker], browser)), patch(
                "casting.address_for_device", return_value="127.0.0.1"), patch(
                "casting.FrameServer", side_effect=lambda: real_server(bind="127.0.0.1")):
            service = CastService()
            def event(kind):
                while True:
                    received, payload = service.events.get(timeout=5)
                    if received == "error":
                        self.fail(payload)
                    if received == kind:
                        return payload
            try:
                service.scan()
                self.assertEqual(event("devices"), ["Test TV (192.0.2.10)"])
                service.connect(0)
                self.assertEqual(event("connected"), "Test TV")
                eyes = (Image.new("RGB", (160, 90), "red"), Image.new("RGB", (160, 90), "lime"))
                service.publish(eyes, "photo1.jpg")
                self.assertEqual(event("sent"), "photo1.jpg")
                args, kwargs = device.media_controller.play_media.call_args
                self.assertEqual(args[1], "image/jpeg")
                self.assertEqual(kwargs["metadata"]["metadataType"], 4)
                first_url = args[0]
                with urlopen(first_url, timeout=2) as response:
                    image = Image.open(BytesIO(response.read()))
                    self.assertEqual(image.size, (1920, 1080))
                    self.assertGreater(image.getpixel((480, 540))[0], 240)
                    self.assertGreater(image.getpixel((1440, 540))[1], 240)
                with patch("casting.compose_tv", wraps=compose_tv) as compose:
                    service.publish(eyes, "photo2.jpg", strength=0.08, convergence=1.0)
                    event("sent")
                    self.assertEqual(compose.call_count, 0, "Reuse prepared Cast eye views")
                with urlopen(device.media_controller.play_media.call_args.args[0], timeout=2) as response:
                    image = Image.open(BytesIO(response.read()))
                    self.assertLess(max(image.getpixel((950, 540))), 10)
                self.assertNotEqual(first_url, device.media_controller.play_media.call_args.args[0])
                service.stop()
                event("stopped")
                self.assertIsNone(service.server)
                device.quit_app.assert_called_once()
            finally:
                service.close()
                service.thread.join(timeout=5)
            self.assertFalse(service.thread.is_alive())
            browser.stop_discovery.assert_called_once()
