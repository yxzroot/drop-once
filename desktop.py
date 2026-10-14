#!/usr/bin/env python3
"""DropOnce — send or receive one file over a local network."""

import ipaddress
from http.client import HTTPSConnection
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile


def configure_qt_platform(environment, platform):
    """Avoid Qt Wayland windows closing unexpectedly on Hyprland when XWayland is available."""
    if (platform.startswith("linux") and environment.get("HYPRLAND_INSTANCE_SIGNATURE")
            and environment.get("DISPLAY") and environment.get("WAYLAND_DISPLAY")):
        environment.setdefault("QT_QPA_PLATFORM", "xcb")


configure_qt_platform(os.environ, sys.platform)

import psutil
import qrcode
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                              QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                              QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from transfer import ReceiveServer, ShareServer
import tls_identity
from tls_identity import tls_context


APP_VERSION = "0.5.1-beta.6"


def lan_addresses():
    """Return reachable private IPv4 interfaces; never advertise localhost."""
    addresses = {}
    for name, entries in psutil.net_if_addrs().items():
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            try:
                address = ipaddress.ip_address(entry.address)
            except ValueError:
                continue
            if address.is_private and not (address.is_loopback or address.is_link_local):
                addresses[str(address)] = name
    preferred = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(("1.1.1.1", 80))  # Route lookup; UDP sends no traffic.
            preferred = route.getsockname()[0]
    except OSError:
        pass
    def priority(item):
        name = item[1].lower()
        virtual = name.startswith(("wg", "tun", "tap", "utun", "tailscale", "docker", "veth", "br-"))
        return virtual, item[0] != preferred, item[1], item[0]

    return sorted(addresses.items(), key=priority)


def possible_iphone_tether(address, interface):
    """Only a hint: hotspot Wi-Fi can use the same IP range as USB tethering."""
    name = interface.lower()
    return (any(part in name for part in ("iphone", "apple mobile", "usb", "rndis", "ncm"))
            or ipaddress.ip_address(address) in ipaddress.ip_network("172.20.10.0/28"))


def possible_android_tether(address, interface):
    """Android USB network names vary; only rank candidates, never assume reachability."""
    name = interface.lower()
    return any(part in name for part in ("android", "rndis", "ndis", "usb", "ncm", "enx", "tether"))


def masked_address(address):
    """Keep enough context to distinguish adapters without displaying a full IP."""
    parts = address.split(".")
    return f"{parts[0]}.•••.•••.{parts[-1]}" if len(parts) == 4 else "•••"


def mullvad_blocks_lan():
    """Diagnose an unreachable LAN without altering any VPN settings."""
    if not shutil.which("mullvad"):
        return False
    try:
        status = subprocess.run(["mullvad", "status"], capture_output=True, text=True,
                                timeout=2, check=False)
        if status.returncode != 0 or not status.stdout.lstrip().startswith("Connected"):
            return False
        result = subprocess.run(["mullvad", "lan", "get"], capture_output=True, text=True,
                                timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "setting: block" in result.stdout.lower()


def qr_image(url, size=260):
    code = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                         box_size=1, border=4)
    code.add_data(url)
    code.make(fit=True)
    grid = code.get_matrix()
    image = QPixmap(size, size)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0b1721"))
    step = size / len(grid)
    for y, row in enumerate(grid):
        for x, filled in enumerate(row):
            if filled:
                painter.drawRect(round(x * step), round(y * step),
                                 round((x + 1) * step) - round(x * step),
                                 round((y + 1) * step) - round(y * step))
    painter.end()
    return image


PALETTES = {
    "dark": dict(bg="#0b111d", card="#151f30", field="#0d1725", text="#f4f8ff",
                 muted="#adbbce", border="#33465c", accent="#85e8d3", accent_text="#09231e",
                 hover="#273c50", badge="#203d3b"),
    "light": dict(bg="#edf2f8", card="#ffffff", field="#f5f8fc", text="#14273a",
                  muted="#526579", border="#c7d5e3", accent="#087d74", accent_text="#ffffff",
                  hover="#e3eef2", badge="#def2ee"),
    "ocean": dict(bg="#061b2e", card="#0b2a40", field="#082238", text="#eefcff",
                  muted="#a4c9d3", border="#286075", accent="#54e0dd", accent_text="#032532",
                  hover="#164158", badge="#15525a"),
    "forest": dict(bg="#101c19", card="#1c2d28", field="#172722", text="#edf9f1",
                   muted="#9dbbaa", border="#385a4e", accent="#9ce0b0", accent_text="#101c19",
                   hover="#304c43", badge="#263e34"),
    "lavender": dict(bg="#171326", card="#241d3b", field="#1c1730", text="#f6f0ff",
                     muted="#c1b5d9", border="#50466f", accent="#c5a5ff", accent_text="#201531",
                     hover="#352c50", badge="#33274f"),
    "sunset": dict(bg="#241311", card="#34201c", field="#2b1714", text="#fff4ed",
                   muted="#e4bcb0", border="#70483d", accent="#ffb36b", accent_text="#321a0d",
                   hover="#4a2c25", badge="#503027"),
    "midnight": dict(bg="#080b14", card="#111827", field="#0c1220", text="#f1f5ff",
                     muted="#a8b3cc", border="#2c3d5b", accent="#73b7ff", accent_text="#071426",
                     hover="#1c2b46", badge="#182946"),
}


STYLE = """
QWidget { background: $bg; color: $text; font: 13px 'Inter', 'Noto Sans', sans-serif; }
QFrame#card { background: $card; border: 1px solid $border; border-radius: 22px; }
QFrame#card QLabel, QWidget#formContents, QWidget#footerContents,
QWidget#filePage, QWidget#receivePage, QStackedWidget#inputStack { background: transparent; }
QFrame#drop { background: $field; border: 2px dashed $border; border-radius: 16px; }
QFrame#drop:hover { border-color: $accent; }
QFrame#drop QLabel { background: transparent; }
QFrame#qrWell { background: $field; border: 1px solid $border; border-radius: 20px; }
QFrame#qrWell QLabel { background: transparent; }
QScrollArea, QScrollArea#formScroll { border: none; background: transparent; }
QScrollArea#formScroll QWidget#qt_scrollarea_viewport { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 5px 0; }
QScrollBar::handle:vertical { background: $border; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#eyebrow, QLabel#section { color: $accent; font-size: 11px; font-weight: 800; }
QLabel#headline { font-size: 33px; font-weight: 800; }
QLabel#heading { font-size: 20px; font-weight: 750; }
QLabel#muted { color: $muted; }
QLabel#pill { color: $accent; background: $badge; padding: 8px 13px; border-radius: 12px; font-weight: 700; }
QLabel#accentGlyph { color: $accent; font-size: 29px; }
QLabel#qr { background: transparent; border-radius: 12px; }
QPushButton, QComboBox, QLineEdit, QPlainTextEdit {
  color: $text; background: $field; border: 1px solid $border; border-radius: 11px;
  padding: 10px 13px; min-height: 22px;
}
QPushButton { font-weight: 700; }
QPushButton:hover { border-color: $accent; background: $hover; }
QPushButton:disabled { color: $muted; border-color: $border; }
QPushButton#primary, QPushButton#switch:checked {
  background: $accent; color: $accent_text; border-color: $accent;
}
QPushButton#primary:hover, QPushButton#switch:checked:hover {
  background: $accent; color: $accent_text; border-color: $accent;
}
QPushButton#secondary { background: $badge; color: $accent; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: $accent; }
QComboBox QAbstractItemView { background: $card; color: $text; selection-background-color: $hover; }
"""


def theme_stylesheet(theme):
    style = STYLE
    for name in sorted(PALETTES[theme], key=len, reverse=True):
        style = style.replace(f"${name}", PALETTES[theme][name])
    return style


class FileDrop(QFrame):
    selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(128)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(3)
        art = QLabel("⇣")
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setObjectName("accentGlyph")
        layout.addWidget(art)
        title = QLabel("Drop one file here")
        title.setObjectName("heading")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("or browse your computer · up to 2 GiB")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        browse = QPushButton("Choose file")
        browse.clicked.connect(self.choose_file)
        layout.addWidget(browse, alignment=Qt.AlignmentFlag.AlignCenter)

    def choose_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Choose a file to share", str(Path.home()))
        if filename:
            self.selected.emit(filename)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_file():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        self.selected.emit(event.mimeData().urls()[0].toLocalFile())
        event.acceptProposedAction()


class DropOnce(QMainWindow):
    def effective_theme(self):
        if self.theme == "system":
            return "light" if QApplication.styleHints().colorScheme() == Qt.ColorScheme.Light else "dark"
        return self.theme

    def apply_theme(self):
        selected = self.effective_theme()
        self.setStyleSheet(theme_stylesheet(selected))
        if hasattr(self, "file_mode"):
            self.refresh_mode_style()

    def refresh_mode_style(self):
        palette = PALETTES[self.effective_theme()]
        for button in (self.file_mode, self.text_mode, self.receive_mode):
            if button.isChecked():
                button.setStyleSheet("QPushButton { background: %s; color: %s; border-color: %s; }"
                                     % (palette["accent"], palette["accent_text"], palette["accent"]))
            else:
                button.setStyleSheet("")

    def change_theme(self):
        self.theme = self.theme_picker.currentData()
        self.settings.setValue("theme", self.theme)
        self.apply_theme()
        self.animate_once()

    def animate_once(self):
        """Fade in a completed UI change once, rather than animating continuously."""
        if not self.isVisible():
            return
        self.transition.stop()
        self.fade_effect.setOpacity(0.55)
        self.transition.start()

    def system_theme_changed(self):
        if self.theme == "system":
            self.apply_theme()

    def __init__(self):
        super().__init__()
        self.share = None
        self.file = None
        self.destination = None
        self.last_digest = None
        self.settings = QSettings("DropOnce", "DropOnce")
        self.theme = str(self.settings.value("theme", "system"))
        if self.theme not in ("system", *PALETTES):
            self.theme = "system"
        self.qr_visible = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"DropOnce {APP_VERSION} · One link. One transfer.")
        self.resize(1080, 730)
        self.setMinimumSize(710, 660)
        self.apply_theme()
        root = QWidget()
        self.setCentralWidget(root)
        self.fade_effect = QGraphicsOpacityEffect(root)
        root.setGraphicsEffect(self.fade_effect)
        self.transition = QPropertyAnimation(self.fade_effect, b"opacity", self)
        self.transition.setDuration(600)
        self.transition.setStartValue(0.55)
        self.transition.setEndValue(1.0)
        self.transition.setEasingCurve(QEasingCurve.Type.OutCubic)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(26, 20, 26, 16)
        outer.setSpacing(14)
        header = QHBoxLayout()
        intro = QVBoxLayout()
        intro.setSpacing(3)
        eyebrow = QLabel(f"LOCAL FILE SHARING  /  NO ACCOUNT  ·  {APP_VERSION}")
        eyebrow.setObjectName("eyebrow")
        intro.addWidget(eyebrow)
        logo = QLabel("DropOnce.")
        logo.setObjectName("headline")
        intro.addWidget(logo)
        subtitle = QLabel("One link. One transfer. Yours to control.")
        subtitle.setObjectName("muted")
        intro.addWidget(subtitle)
        header.addLayout(intro, 1)
        header_actions = QVBoxLayout()
        header_actions.setSpacing(6)
        badge = QLabel("●  ENCRYPTED · NO CLOUD")
        badge.setObjectName("pill")
        header_actions.addWidget(badge, alignment=Qt.AlignmentFlag.AlignRight)
        self.theme_picker = QComboBox()
        self.theme_picker.addItem("System theme", "system")
        self.theme_picker.addItem("Dark", "dark")
        self.theme_picker.addItem("Light", "light")
        self.theme_picker.addItem("Ocean", "ocean")
        self.theme_picker.addItem("Forest", "forest")
        self.theme_picker.addItem("Lavender", "lavender")
        self.theme_picker.addItem("Sunset", "sunset")
        self.theme_picker.addItem("Midnight", "midnight")
        self.theme_picker.setAccessibleName("Appearance theme")
        self.theme_picker.setToolTip("Choose the appearance of DropOnce")
        self.theme_picker.setCurrentIndex(self.theme_picker.findData(self.theme))
        self.theme_picker.currentIndexChanged.connect(self.change_theme)
        header_actions.addWidget(self.theme_picker)
        header.addLayout(header_actions)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)
        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        form = QWidget()
        form.setObjectName("formContents")
        controls = QVBoxLayout(form)
        controls.setContentsMargins(20, 16, 20, 16)
        controls.setSpacing(7)
        heading = QLabel("Make a transfer")
        heading.setObjectName("heading")
        controls.addWidget(heading)
        caption = QLabel("Send something from this computer, or receive a file from your phone.")
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        controls.addWidget(caption)
        switch = QHBoxLayout()
        self.file_mode = QPushButton("File")
        self.text_mode = QPushButton("Text")
        self.receive_mode = QPushButton("Receive")
        for button, label in ((self.file_mode, "Send a file"),
                              (self.text_mode, "Send text"),
                              (self.receive_mode, "Receive a file")):
            button.setObjectName("switch")
            button.setAccessibleName(label)
            button.setCheckable(True)
            switch.addWidget(button)
        self.file_mode.clicked.connect(self.show_file)
        self.text_mode.clicked.connect(self.show_text)
        self.receive_mode.clicked.connect(self.show_receive)
        controls.addLayout(switch)
        self.inputs = QStackedWidget()
        self.inputs.setObjectName("inputStack")
        file_page = QWidget()
        file_page.setObjectName("filePage")
        file_layout = QVBoxLayout(file_page)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.drop = FileDrop()
        self.drop.selected.connect(self.select_file)
        file_layout.addWidget(self.drop)
        self.file_label = QLabel("No file selected")
        self.file_label.setObjectName("muted")
        self.file_label.setWordWrap(True)
        file_layout.addWidget(self.file_label)
        self.inputs.addWidget(file_page)
        self.text_input = QPlainTextEdit()
        self.text_input.setPlaceholderText("Paste a message, link, or note…")
        self.inputs.addWidget(self.text_input)
        receive_page = QWidget()
        receive_page.setObjectName("receivePage")
        receive_layout = QVBoxLayout(receive_page)
        receive_layout.setContentsMargins(3, 10, 3, 10)
        receive_layout.setSpacing(12)
        receive_heading = QLabel("Receive from your phone")
        receive_heading.setObjectName("heading")
        receive_layout.addWidget(receive_heading)
        receive_intro = QLabel("Pick a save folder. Your phone uploads one file through its browser.")
        receive_intro.setObjectName("muted")
        receive_intro.setWordWrap(True)
        receive_layout.addWidget(receive_intro)
        folder_button = QPushButton("Choose destination folder…")
        folder_button.clicked.connect(self.choose_destination)
        receive_layout.addWidget(folder_button)
        self.destination_label = QLabel("No destination folder selected")
        self.destination_label.setObjectName("muted")
        self.destination_label.setWordWrap(True)
        receive_layout.addWidget(self.destination_label)
        receive_layout.addStretch()
        self.inputs.addWidget(receive_page)
        controls.addWidget(self.inputs, 1)
        self.connection_mode = QComboBox()
        self.connection_mode.addItem("Same Wi-Fi / LAN", "wifi")
        self.connection_mode.addItem("iPhone USB Personal Hotspot", "iphone-usb")
        self.connection_mode.addItem("Android USB tethering", "android-usb")
        self.connection_mode.setAccessibleName("How the phone connects")
        self.connection_mode.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        connection_label = QLabel("CONNECTION")
        connection_label.setObjectName("section")
        controls.addWidget(connection_label)
        controls.addWidget(self.connection_mode)
        self.connection_hint = QLabel()
        self.connection_hint.setObjectName("muted")
        self.connection_hint.setWordWrap(True)
        controls.addWidget(self.connection_hint)
        self.network = QComboBox()
        self.network.setAccessibleName("Network address to share from")
        self.network.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.network.setMinimumContentsLength(8)
        self.network.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("formScroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setWidget(form)
        self.form_scroll.viewport().setStyleSheet("background: transparent;")
        left_layout.addWidget(self.form_scroll, 1)
        footer = QWidget()
        footer.setObjectName("footerContents")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(20, 7, 20, 16)
        footer_layout.setSpacing(8)
        self.network_label = QLabel("NETWORK · ADDRESS MASKED")
        self.network_label.setObjectName("section")
        footer_layout.addWidget(self.network_label)
        network_row = QHBoxLayout()
        network_row.addWidget(self.network, 1)
        rescan = QPushButton("↻")
        rescan.setToolTip("Refresh network addresses")
        rescan.setAccessibleName("Refresh network addresses")
        rescan.clicked.connect(self.refresh_networks)
        network_row.addWidget(rescan)
        footer_layout.addLayout(network_row)
        self.certificate_button = QPushButton("Export phone trust certificate…")
        self.certificate_button.setToolTip("Install and trust this certificate on your phone before using HTTPS")
        self.certificate_button.clicked.connect(self.export_certificate)
        cert_row = QHBoxLayout()
        cert_row.addWidget(self.certificate_button, 1)
        help_button = QPushButton("?")
        help_button.setAccessibleName("Troubleshoot phone connection and Firefox certificate")
        help_button.setToolTip("Phone can't load the page? Check network and certificate setup")
        help_button.clicked.connect(self.troubleshoot_connection)
        cert_row.addWidget(help_button)
        footer_layout.addLayout(cert_row)
        self.start_button = QPushButton("Create one-time link  →")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_share)
        footer_layout.addWidget(self.start_button)
        left_layout.addWidget(footer)
        body.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("card")
        right.setMinimumWidth(295)
        output = QVBoxLayout(right)
        output.setContentsMargins(17, 17, 17, 17)
        output.setSpacing(8)
        self.status = QLabel("Ready when you are")
        self.status.setObjectName("heading")
        output.addWidget(self.status)
        self.message = QLabel("Export and trust the local certificate on your phone, then create a link and scan it.")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        output.addWidget(self.message)
        output.addStretch()
        qr_well = QFrame()
        qr_well.setObjectName("qrWell")
        qr_well_layout = QVBoxLayout(qr_well)
        qr_well_layout.setContentsMargins(12, 14, 12, 14)
        self.code = QLabel("QR hidden\nReveal to scan")
        self.code.setObjectName("qr")
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code.setWordWrap(True)
        self.code.setFixedSize(190, 190)
        qr_well_layout.addWidget(self.code, alignment=Qt.AlignmentFlag.AlignCenter)
        output.addWidget(qr_well, alignment=Qt.AlignmentFlag.AlignCenter)
        output.addStretch()
        self.reveal = QPushButton("Show QR code")
        self.reveal.setObjectName("secondary")
        self.reveal.setEnabled(False)
        self.reveal.clicked.connect(self.toggle_qr)
        output.addWidget(self.reveal)
        self.link = QLineEdit()
        self.link.setReadOnly(True)
        self.link.setPlaceholderText("Masked link appears here")
        self.link.setAccessibleName("Masked one-time transfer link")
        self.link.setToolTip("The real address and token are hidden. Use Copy link to share it.")
        output.addWidget(self.link)
        actions = QHBoxLayout()
        self.copy = QPushButton("Copy link")
        self.copy.setEnabled(False)
        self.copy.clicked.connect(self.copy_link)
        actions.addWidget(self.copy)
        self.stop = QPushButton("Stop sharing")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_share)
        actions.addWidget(self.stop)
        output.addLayout(actions)
        body.addWidget(right, 1)
        outer.addLayout(body, 1)
        foot = QLabel("SCREEN MASKED · QR / COPY SHARE REAL ADDRESS · 10 MINUTES · ONE TRANSFER")
        foot.setObjectName("eyebrow")
        foot.setWordWrap(True)
        outer.addWidget(foot)

        self.ticker = QTimer(self)
        self.ticker.setInterval(250)
        self.ticker.timeout.connect(self.update_status)
        self.ticker.start()
        self.qr_timer = QTimer(self)
        self.qr_timer.setSingleShot(True)
        self.qr_timer.timeout.connect(self.hide_qr)
        self.set_mode(0)
        self.connection_mode.currentIndexChanged.connect(self.change_connection)
        self.network.currentIndexChanged.connect(self.change_network)
        QApplication.styleHints().colorSchemeChanged.connect(self.system_theme_changed)
        self.refresh_networks()

    def set_mode(self, index):
        changed = self.inputs.currentIndex() != index
        if self.share and self.inputs.currentIndex() != index:
            self.stop_share()
        self.inputs.setCurrentIndex(index)
        self.file_mode.setChecked(index == 0)
        self.text_mode.setChecked(index == 1)
        self.receive_mode.setChecked(index == 2)
        self.refresh_mode_style()
        self.start_button.setText("Create receive link  →" if index == 2 else "Create one-time link  →")
        if index == 2 and not self.share:
            self.message.setText("Choose a folder, then let your phone send one file here.")
        if changed:
            self.animate_once()

    def show_file(self):
        self.set_mode(0)

    def show_text(self):
        self.set_mode(1)

    def show_receive(self):
        self.set_mode(2)

    def choose_destination(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose where received files are saved",
                                                  str(Path.home() / "Downloads"))
        if folder:
            self.destination = Path(folder)
            self.destination_label.setText(str(self.destination))

    def copy_link(self):
        if self.share and self.share.remaining and not self.share.served:
            QApplication.clipboard().setText(self.share.url)
            self.copy.setText("Copied ✓")
        elif not self.share and self.last_digest:
            QApplication.clipboard().setText(self.last_digest)
            self.copy.setText("Copied SHA ✓")

    def hide_qr(self):
        self.qr_timer.stop()
        self.qr_visible = False
        self.code.setPixmap(QPixmap())
        self.code.setText("QR hidden\nReveal to scan" if self.share else "Your QR will appear here")
        self.reveal.setText("Show QR code")

    def toggle_qr(self):
        if not self.share:
            return
        if self.qr_visible:
            self.hide_qr()
        else:
            self.qr_visible = True
            self.code.setPixmap(qr_image(self.share.url, 180))
            self.reveal.setText("Hide QR code")
            self.qr_timer.start(45_000)
        self.update_status()

    def export_certificate(self):
        address = self.network.currentData()
        if not address:
            QMessageBox.information(self, "No network", "Connect to a network and choose its address first.")
            return
        try:
            _, certificate = tls_context(address)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not create certificate", str(exc))
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export DropOnce phone trust certificate", str(Path.home() / "Downloads" / "DropOnce-CA.cer"),
            "Certificate (*.cer)")
        if destination:
            try:
                cert = x509.load_pem_x509_certificate(certificate.read_bytes())
                Path(destination).write_bytes(cert.public_bytes(serialization.Encoding.DER))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Could not export certificate", str(exc))
                return
            fingerprint = cert.fingerprint(hashes.SHA256()).hex().upper()
            QMessageBox.information(self, "Install on your phone",
                                    "Transfer this certificate to your phone through a trusted method. "
                                    "On iPhone, install the profile and enable full trust in Settings → "
                                    "General → About → Certificate Trust Settings. On Android, use "
                                    "Settings → Security → Install a certificate → CA certificate "
                                    "(names vary by phone). Never bypass a certificate warning. "
                                    "Keep the private key on this computer. See README for details.\n\n"
                                    "Check this certificate's SHA-256 fingerprint on the receiving device:\n"
                                    + fingerprint[:32] + "\n" + fingerprint[32:])

    def troubleshoot_connection(self):
        warning = ("Mullvad is currently blocking local network sharing. In Mullvad, enable "
                   "Local network sharing only if you trust this LAN. Keep the VPN connected.\n\n"
                   if mullvad_blocks_lan() else "")
        QMessageBox.information(
            self, "Phone connection and Firefox help",
            warning + "1. Put the phone and computer on the same trusted Wi-Fi, or enable USB tethering. "
            "Guest Wi-Fi and some VPN/firewall settings block local devices. Keep DropOnce open.\n\n"
            "2. Export the DropOnce CA certificate and transfer it to the phone by a trusted method. "
            "Install and trust it in iPhone Settings → General → About → Certificate Trust Settings "
            "or Android Settings → Security → Install a certificate → CA certificate.\n\n"
            "3. Desktop Firefox: Settings → Privacy & Security → Certificates → View Certificates "
            "→ Authorities → Import; trust the DropOnce CA for websites. Android Firefox may require "
            "enabling trust for user-installed CAs; if unavailable, use a browser that trusts the installed CA. "
            "iPhone Firefox uses iOS certificate trust.\n\n"
            "Never bypass a certificate warning or install a certificate from the warning page. "
            "The HTTPS link should load without a warning after setup.")

    def select_file(self, filename):
        self.file = Path(filename)
        self.file_label.setText(self.file.name)
        self.file_label.setToolTip(str(self.file))
        self.set_mode(0)

    def refresh_networks(self):
        previous = self.network.currentData()
        addresses = lan_addresses()
        if self.share and previous not in (address for address, _ in addresses):
            self.stop_share()
        mode = self.connection_mode.currentData()
        if mode == "iphone-usb":
            addresses.sort(key=lambda item: not possible_iphone_tether(*item))
        elif mode == "android-usb":
            addresses.sort(key=lambda item: not possible_android_tether(*item))
        self.network.blockSignals(True)
        self.network.clear()
        for address, interface in addresses:
            possible_usb = ((mode == "iphone-usb" and possible_iphone_tether(address, interface))
                            or (mode == "android-usb" and possible_android_tether(address, interface)))
            hint = " · possible USB tether" if possible_usb else ""
            self.network.addItem(f"{masked_address(address)}  ·  {interface}{hint}", address)
            self.network.setItemData(self.network.count() - 1, interface, Qt.ItemDataRole.UserRole + 1)
        if previous:
            index = self.network.findData(previous)
            if index >= 0:
                self.network.setCurrentIndex(index)
        self.network.blockSignals(False)
        self.start_button.setEnabled(self.network.count() > 0)
        if not self.network.count():
            self.message.setText("No private network address found. Connect to Wi-Fi or enable "
                                 "USB tethering on your phone, then refresh.")
        self.update_connection_hint()

    def change_connection(self):
        if self.share:
            self.stop_share()
        self.network.blockSignals(True)
        self.network.clear()
        self.network.blockSignals(False)
        self.refresh_networks()

    def change_network(self):
        if self.share:
            self.stop_share()
        self.update_connection_hint()

    def update_connection_hint(self):
        mode = self.connection_mode.currentData()
        self.network_label.setText({"wifi": "WI-FI · ADDRESS MASKED",
                                    "iphone-usb": "IPHONE USB · ADDRESS MASKED",
                                    "android-usb": "ANDROID USB · ADDRESS MASKED"}.get(mode, "NETWORK · ADDRESS MASKED"))
        if mode == "wifi":
            self.connection_hint.setText("Your phone and computer should be on the same Wi-Fi.")
            return
        address = self.network.currentData()
        interface = self.network.currentData(Qt.ItemDataRole.UserRole + 1) if address else ""
        if mode == "iphone-usb":
            likely = possible_iphone_tether(address, interface) if address else False
            hint = "Plug in your iPhone, tap Trust, turn on Personal Hotspot, then refresh. "
        else:
            likely = possible_android_tether(address, interface) if address else False
            hint = "Plug in your Android, enable USB tethering in Hotspot & tethering, then refresh. "
            if sys.platform == "darwin":
                hint += "macOS generally does not support Android USB tethering; use Wi-Fi. "
        self.connection_hint.setText(hint + "Choose the phone's USB adapter, not regular Wi-Fi."
                                     + ("" if likely else " Check the selected adapter."))

    def start_share(self):
        address = self.network.currentData()
        if not address:
            return
        if mullvad_blocks_lan():
            QMessageBox.warning(self, "Local network blocked by Mullvad",
                                "Mullvad currently blocks local network sharing, so your phone cannot "
                                "open a DropOnce link. On a trusted network, enable Local network "
                                "sharing in Mullvad while leaving the VPN connected, then try again. "
                                "You must also install and trust DropOnce's CA certificate on the phone.")
            return
        try:
            if self.inputs.currentIndex() == 0:
                if self.file is None:
                    raise ValueError("Choose a file first")
                new_share = ShareServer(address, file=self.file)
            elif self.inputs.currentIndex() == 1:
                new_share = ShareServer(address, text=self.text_input.toPlainText())
            else:
                if self.destination is None:
                    raise ValueError("Choose a destination folder first")
                new_share = ReceiveServer(address, self.destination)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Couldn't start sharing", str(exc))
            return
        if self.share:
            if QApplication.clipboard().text() == self.share.url:
                QApplication.clipboard().clear()
            self.share.stop()
        self.last_digest = None
        self.share = new_share
        self.hide_qr()
        self.link.setText(f"https://{masked_address(address)}:••••/s/••••••")
        self.link.setCursorPosition(0)
        self.reveal.setEnabled(True)
        self.copy.setEnabled(True)
        self.copy.setText("Copy link")
        self.stop.setEnabled(True)
        self.update_status()

    def update_status(self):
        if not self.share:
            return
        if self.share.served:
            self.stop_share(completed=True)
            return
        if not self.share.remaining:
            self.stop_share(expired=True)
            return
        seconds = self.share.remaining
        self.status.setText("Your link is live")
        if isinstance(self.share, ReceiveServer):
            direction = ("Open the link on your phone and choose one file to upload." if self.qr_visible else
                         "Reveal the QR or copy the link to receive a file.")
        else:
            direction = ("Scan with your phone to get the file or text." if self.qr_visible else
                         "Reveal the QR or copy the link to share it.")
        connection = ("iPhone USB Personal Hotspot" if self.connection_mode.currentData() == "iphone-usb" else
                      "Android USB tethering" if self.connection_mode.currentData() == "android-usb" else
                      "the same Wi-Fi")
        self.message.setText(f"{direction} Use {connection}. Install and trust the exported certificate "
                             f"on your phone first. Expires in {seconds // 60}:{seconds % 60:02d}."
                             + (f"\n{self.share.error}" if self.share.error else ""))

    def stop_share(self, checked=False, *, completed=False, expired=False):
        received = self.share.received if isinstance(self.share, ReceiveServer) else None
        digest = self.share.sha256 if self.share else None
        self.last_digest = digest if completed else None
        if self.share and QApplication.clipboard().text() == self.share.url:
            QApplication.clipboard().clear()
        if self.share:
            self.share.stop()
            self.share = None
        self.link.clear()
        self.copy.setEnabled(bool(self.last_digest))
        self.copy.setText("Copy SHA-256" if self.last_digest else "Copy link")
        self.stop.setEnabled(False)
        self.reveal.setEnabled(False)
        self.hide_qr()
        self.status.setText(("Received!" if received else "Sent!") if completed else
                            "Link expired" if expired else "Sharing stopped")
        result = f"Saved to {received}" if completed and received else "Ready for another transfer."
        if completed and digest:
            result += f"\nSHA-256: {digest[:32]}\n{digest[32:]}"
        self.message.setText(result if completed else "Create a new link whenever you're ready.")

    def closeEvent(self, event):
        self.ticker.stop()
        self.ticker.timeout.disconnect(self.update_status)
        if self.share:
            self.stop_share()
        self.qr_timer.stop()
        super().closeEvent(event)


def packaged_self_check():
    """Exercise the frozen Qt app, certificate generation and an authenticated transfer."""
    with tempfile.TemporaryDirectory() as temp:
        original = tls_identity.identity_folder
        tls_identity.identity_folder = lambda: Path(temp) / "tls"
        share = None
        connection = None
        try:
            share = ShareServer("127.0.0.1", text="DropOnce self-check")
            context = ssl.create_default_context(cafile=str(share.ca_certificate))
            connection = HTTPSConnection("127.0.0.1", share.port, context=context, timeout=10)
            connection.request("GET", f"/s/{share.token}")
            response = connection.getresponse()
            assert response.status == 200 and b"DropOnce self-check" in response.read()
        finally:
            if connection:
                connection.close()
            if share:
                share.stop()
            tls_identity.identity_folder = original
    window = DropOnce()
    try:
        assert window.network is not None and window.inputs.count() == 3
        assert window.connection_mode.count() == 3
        assert not qr_image("https://127.0.0.1:1234/s/example").isNull()
    finally:
        window.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if "--self-check" in sys.argv:
        packaged_self_check()
        raise SystemExit(0)
    window = DropOnce()
    window.show()
    raise SystemExit(app.exec())
