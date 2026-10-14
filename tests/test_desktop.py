import unittest
import hashlib
from unittest.mock import patch
from http.client import HTTPSConnection
from pathlib import Path
import ssl
import subprocess
import tempfile
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from PySide6.QtCore import QAbstractAnimation, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from desktop import (APP_VERSION, DropOnce, configure_qt_platform, lan_addresses, masked_address, possible_android_tether,
                     possible_iphone_tether, mullvad_blocks_lan, qr_image)


def setUpModule():
    global _tls_directory, _tls_patch, _vpn_patch
    _tls_directory = tempfile.TemporaryDirectory()
    _tls_patch = patch("tls_identity.identity_folder", return_value=Path(_tls_directory.name))
    _tls_patch.start()
    _vpn_patch = patch("desktop.mullvad_blocks_lan", return_value=False)
    _vpn_patch.start()


def tearDownModule():
    _vpn_patch.stop()
    _tls_patch.stop()
    _tls_directory.cleanup()


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_hyprland_prefers_xwayland_without_overriding_a_requested_backend(self):
        environment = {"HYPRLAND_INSTANCE_SIGNATURE": "session", "DISPLAY": ":0",
                       "WAYLAND_DISPLAY": "wayland-1"}
        configure_qt_platform(environment, "linux")
        self.assertEqual(environment["QT_QPA_PLATFORM"], "xcb")
        environment["QT_QPA_PLATFORM"] = "wayland"
        configure_qt_platform(environment, "linux")
        self.assertEqual(environment["QT_QPA_PLATFORM"], "wayland")
        for key, platform in (("DISPLAY", "linux"), ("HYPRLAND_INSTANCE_SIGNATURE", "linux"),
                              ("WAYLAND_DISPLAY", "linux"), (None, "darwin")):
            other = {name: value for name, value in environment.items() if name != "QT_QPA_PLATFORM"}
            if key:
                other.pop(key)
            configure_qt_platform(other, platform)
            self.assertNotIn("QT_QPA_PLATFORM", other)

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

    def test_mullvad_diagnosis_requires_connected_vpn_and_blocked_lan(self):
        with patch("desktop.shutil.which", return_value="/usr/bin/mullvad"), \
                patch("desktop.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 0, "Connected\n", ""),
                    subprocess.CompletedProcess([], 0, "Local network sharing setting: block\n", "")
                ]):
            self.assertTrue(mullvad_blocks_lan())
        with patch("desktop.shutil.which", return_value="/usr/bin/mullvad"), \
                patch("desktop.subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, "Disconnected\n", "")) as run:
            self.assertFalse(mullvad_blocks_lan())
            run.assert_called_once()

    def test_blocked_lan_warns_instead_of_creating_unreachable_link(self):
        with patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                window.set_mode(1)
                window.text_input.setPlainText("test")
                with patch("desktop.mullvad_blocks_lan", return_value=True), \
                        patch("desktop.QMessageBox.warning") as warning:
                    window.start_share()
                self.assertIsNone(window.share)
                self.assertIn("Mullvad", warning.call_args.args[1])
                with patch("desktop.mullvad_blocks_lan", return_value=True), \
                        patch("desktop.QMessageBox.information") as info:
                    window.troubleshoot_connection()
                self.assertIn("Firefox", info.call_args.args[2])
            finally:
                window.close()

    def test_iphone_usb_network_hints_are_only_hints(self):
        self.assertTrue(possible_iphone_tether("172.20.10.2", "en7"))
        self.assertTrue(possible_iphone_tether("192.168.0.2", "Apple Mobile Device Ethernet"))
        self.assertFalse(possible_iphone_tether("192.168.0.2", "Wi-Fi"))
        self.assertTrue(possible_android_tether("192.168.42.1", "Remote NDIS Compatible Device"))
        self.assertTrue(possible_android_tether("192.168.42.1", "usb0"))
        self.assertFalse(possible_android_tether("192.168.1.2", "Wi-Fi"))
        self.assertEqual(masked_address("192.168.1.20"), "192.•••.•••.20")

    def test_android_usb_selection_and_receive_mode(self):
        with patch("desktop.lan_addresses", return_value=[("192.168.1.5", "Wi-Fi"),
                                                         ("192.168.42.2", "usb0")]):
            window = DropOnce()
            try:
                window.connection_mode.setCurrentIndex(window.connection_mode.findData("android-usb"))
                self.assertEqual(window.network.currentData(), "192.168.42.2")
                self.assertIn("Android", window.connection_hint.text())
                self.assertNotIn("192.168.42.2", window.network.currentText())
                window.set_mode(2)
                self.assertIn("phone", window.inputs.widget(2).findChildren(QLabel)[0].text())
            finally:
                window.close()

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
            forest = DropOnce()
            try:
                forest.theme_picker.setCurrentIndex(forest.theme_picker.findData("forest"))
                for color in ("#101c19", "#1c2d28", "#385a4e", "#9ce0b0", "#edf9f1", "#9dbbaa"):
                    self.assertIn(color, forest.styleSheet())
                self.assertIn("#9ce0b0", forest.file_mode.styleSheet())
                self.assertNotIn("$accent", forest.styleSheet())
            finally:
                forest.close()
            restored_forest = DropOnce()
            try:
                self.assertEqual(restored_forest.theme, "forest")
                self.assertEqual(restored_forest.theme_picker.currentData(), "forest")
            finally:
                restored_forest.close()

    def test_new_themes_are_listed_and_render_complete_palettes(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("desktop.QSettings", side_effect=lambda *args: QSettings(
                    str(Path(temp) / "theme.ini"), QSettings.Format.IniFormat)), \
                patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                expected = {
                    "lavender": ("#171326", "#241d3b", "#c5a5ff"),
                    "sunset": ("#241311", "#34201c", "#ffb36b"),
                    "midnight": ("#080b14", "#111827", "#73b7ff"),
                }
                for theme, colors in expected.items():
                    self.assertNotEqual(window.theme_picker.findData(theme), -1)
                    window.theme_picker.setCurrentIndex(window.theme_picker.findData(theme))
                    self.assertEqual(window.theme, theme)
                    self.assertEqual(window.theme_picker.currentData(), theme)
                    for color in colors:
                        self.assertIn(color, window.styleSheet())
                    self.assertNotIn("$accent", window.styleSheet())
            finally:
                window.close()

    def test_version_and_one_shot_fade(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("desktop.QSettings", side_effect=lambda *args: QSettings(
                    str(Path(temp) / "theme.ini"), QSettings.Format.IniFormat)), \
                patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                self.assertIn(APP_VERSION, window.windowTitle())
                window.show()
                window.set_mode(1)
                self.assertEqual(window.transition.state(), QAbstractAnimation.State.Running)
                self.assertLess(window.fade_effect.opacity(), 1)
                QTest.qWait(750)
                self.assertEqual(window.transition.state(), QAbstractAnimation.State.Stopped)
                self.assertEqual(window.fade_effect.opacity(), 1)
                window.set_mode(1)
                self.assertEqual(window.transition.state(), QAbstractAnimation.State.Stopped)
                window.theme_picker.setCurrentIndex(window.theme_picker.findData("forest"))
                self.assertEqual(window.transition.state(), QAbstractAnimation.State.Running)
            finally:
                window.close()

    def test_certificate_can_be_exported_before_a_link(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("desktop.lan_addresses", return_value=[("127.0.0.1", "test")]):
            window = DropOnce()
            try:
                destination = Path(temp) / "DropOnce-CA.cer"
                with patch("desktop.QFileDialog.getSaveFileName", return_value=(str(destination), "")), \
                        patch("desktop.QMessageBox.information") as notice:
                    window.export_certificate()
                cert = x509.load_der_x509_certificate(destination.read_bytes())
                self.assertTrue(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
                self.assertIn(cert.fingerprint(hashes.SHA256()).hex().upper()[:32],
                              notice.call_args.args[2])
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
                self.assertIn(hashlib.sha256(b"from my phone").hexdigest(),
                              window.message.text().replace("\n", ""))
                self.assertEqual(window.copy.text(), "Copy SHA-256")
                window.copy_link()
                self.assertEqual(QApplication.clipboard().text(), hashlib.sha256(b"from my phone").hexdigest())
            finally:
                window.close()
