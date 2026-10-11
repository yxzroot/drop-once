import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from pathlib import Path
import tempfile
import time

from PySide6.QtWidgets import QApplication

from desktop import DropOnce, lan_addresses, possible_iphone_tether, qr_image


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_qr_and_local_share_from_gui(self):
        with patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                window.set_mode(1)
                window.text_input.setPlainText("hello phone")
                window.start_share()
                self.assertIsNotNone(window.share)
                self.assertIn("127.0.0.1", window.link.text())
                self.assertFalse(window.code.pixmap().isNull())
                self.assertGreater(qr_image("http://127.0.0.1").width(), 0)
                window.stop_share()
                self.assertIsNone(window.share)
            finally:
                window.close()

    def test_interfaces_ignore_loopback_and_link_local(self):
        import socket
        from collections import namedtuple

        interface = namedtuple("Address", "family address")
        with patch("desktop.psutil.net_if_addrs", return_value={
                "lo": [interface(socket.AF_INET, "127.0.0.1")],
                "wifi": [interface(socket.AF_INET, "192.168.1.20")],
                "other": [interface(socket.AF_INET, "169.254.3.4")]}):
            self.assertEqual(lan_addresses(), [("192.168.1.20", "wifi")])

    def test_iphone_usb_network_hints_are_only_hints(self):
        self.assertTrue(possible_iphone_tether("172.20.10.2", "en7"))
        self.assertTrue(possible_iphone_tether("192.168.0.2", "Apple Mobile Device Ethernet"))
        self.assertFalse(possible_iphone_tether("192.168.0.2", "Wi-Fi"))

    def test_gui_receives_one_file_into_chosen_folder(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                window.destination = Path(temp)
                window.set_mode(2)
                window.start_share()
                self.assertIsNotNone(window.share)
                connection = HTTPConnection("127.0.0.1", window.share.port, timeout=3)
                try:
                    connection.request("PUT", f"/s/{window.share.token}/upload?name=from-iphone.txt",
                                       body=b"from my phone")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                finally:
                    connection.close()
                for _ in range(25):
                    window.update_status()
                    if window.share is None:
                        break
                    time.sleep(.01)
                self.assertEqual((Path(temp) / "from-iphone.txt").read_bytes(), b"from my phone")
                self.assertEqual(window.status.text(), "Received!")
            finally:
                window.close()
