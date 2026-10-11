import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from desktop import DropOnce, lan_addresses, qr_image


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
