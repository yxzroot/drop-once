import unittest
from unittest.mock import patch
from http.client import HTTPSConnection
from pathlib import Path
import ssl
import tempfile
import time

from cryptography import x509
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from desktop import DropOnce, lan_addresses, masked_address, possible_iphone_tether, qr_image


def setUpModule():
    global _tls_directory, _tls_patch
    _tls_directory = tempfile.TemporaryDirectory()
    _tls_patch = patch("tls_identity.identity_folder", return_value=Path(_tls_directory.name))
    _tls_patch.start()


def tearDownModule():
    _tls_patch.stop()
    _tls_directory.cleanup()


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
                self.assertNotIn("127.0.0.1", window.link.text())
                self.assertNotIn(window.share.token, window.link.text())
                self.assertIn("127.•••.•••.1", window.network.currentText())
                self.assertTrue(window.code.pixmap().isNull())
                window.copy_link()
                self.assertEqual(QApplication.clipboard().text(), window.share.url)
                window.toggle_qr()
                self.assertFalse(window.code.pixmap().isNull())
                self.assertTrue(window.qr_timer.isActive())
                window.toggle_qr()
                self.assertTrue(window.code.pixmap().isNull())
                self.assertFalse(window.qr_timer.isActive())
                self.assertTrue(window.link.text().startswith("https://"))
                self.assertGreater(qr_image("https://127.0.0.1").width(), 0)
                window.stop_share()
                self.assertIsNone(window.share)
                self.assertEqual(QApplication.clipboard().text(), "")
            finally:
                window.close()

    def test_network_loss_cancels_link_and_does_not_reveal_address(self):
        with patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                window.set_mode(1)
                window.text_input.setPlainText("private message")
                window.start_share()
                self.assertNotIn("127.0.0.1", window.link.text())
                with patch("desktop.lan_addresses", return_value=[]):
                    window.refresh_networks()
                self.assertIsNone(window.share)
                self.assertFalse(window.reveal.isEnabled())
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
        self.assertEqual(masked_address("192.168.1.20"), "192.•••.•••.20")

    def test_theme_selection_persists_and_changes_palette(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("desktop.QSettings", side_effect=lambda *args: QSettings(
                    str(Path(temp) / "theme.ini"), QSettings.Format.IniFormat)), \
                patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                window.theme_picker.setCurrentIndex(window.theme_picker.findData("light"))
                self.assertIn("#edf2f8", window.styleSheet())
            finally:
                window.close()
            restored = DropOnce()
            try:
                self.assertEqual(restored.theme, "light")
                restored.theme_picker.setCurrentIndex(restored.theme_picker.findData("ocean"))
                self.assertIn("#061b2e", restored.styleSheet())
                self.assertNotIn("$accent", restored.styleSheet())
            finally:
                restored.close()

    def test_certificate_can_be_exported_before_a_link(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                destination = Path(temp) / "DropOnce-CA.cer"
                with patch("desktop.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
                        patch("desktop.QMessageBox.information"):
                    window.export_certificate()
                cert = x509.load_der_x509_certificate(destination.read_bytes())
                self.assertTrue(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
                self.assertIsNone(window.share)
            finally:
                window.close()

    def test_gui_receives_one_file_into_chosen_folder(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                window.destination = Path(temp)
                window.set_mode(2)
                window.start_share()
                self.assertIsNotNone(window.share)
                context = ssl.create_default_context(cafile=str(window.share.ca_certificate))
                connection = HTTPSConnection("127.0.0.1", window.share.port, timeout=3,
                                             context=context)
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
