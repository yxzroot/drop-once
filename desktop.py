#!/usr/bin/env python3
"""DropOnce — share one file or text message with another device on your LAN."""

import ipaddress
from pathlib import Path
import socket
import sys

import psutil
import qrcode
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                              QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                              QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from transfer import ShareServer


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


STYLE = """
QWidget { background: #0d1420; color: #ecf4ff; font: 13px 'Inter', 'Noto Sans', sans-serif; }
QFrame#card { background: #172335; border: 1px solid #31435b; border-radius: 20px; }
QFrame#card QLabel { background: transparent; }
QFrame#drop { background: #101b2b; border: 2px dashed #47617c; border-radius: 16px; }
QFrame#drop:hover { border-color: #81e6d1; }
QFrame#drop QLabel { background: transparent; }
QLabel#eyebrow { color: #81e6d1; font-size: 11px; font-weight: 700; }
QLabel#headline { font-size: 31px; font-weight: 800; }
QLabel#heading { font-size: 19px; font-weight: 750; }
QLabel#muted { color: #b1c0d2; }
QLabel#qr { background: transparent; border-radius: 12px; }
QPushButton, QComboBox, QLineEdit, QPlainTextEdit {
  color: #ecf4ff; background: #101b2b; border: 1px solid #405571; border-radius: 11px;
  padding: 10px 13px; min-height: 22px;
}
QPushButton { font-weight: 700; }
QPushButton:hover { border-color: #81e6d1; background: #24364b; }
QPushButton:disabled { color: #8292a6; border-color: #31435b; }
QPushButton#primary { background: #81e6d1; color: #0a1a22; border-color: #81e6d1; }
QPushButton#primary:hover { background: #b4ffed; }
QPushButton#switch:checked { background: #81e6d1; color: #0a1a22; border-color: #81e6d1; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #81e6d1; }
QComboBox QAbstractItemView { background: #172335; color: #ecf4ff; selection-background-color: #405571; }
"""


class FileDrop(QFrame):
    selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(155)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(3)
        art = QLabel("⇣")
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setStyleSheet("color:#81e6d1;font-size:29px")
        layout.addWidget(art)
        title = QLabel("Drop one file here")
        title.setObjectName("heading")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("or browse your computer · up to 2 GiB")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
    def __init__(self):
        super().__init__()
        self.share = None
        self.file = None
        self.setWindowTitle("DropOnce · One link. One transfer.")
        self.resize(1060, 700)
        self.setMinimumSize(710, 660)
        self.setStyleSheet(STYLE)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(18)
        header = QHBoxLayout()
        intro = QVBoxLayout()
        intro.setSpacing(3)
        eyebrow = QLabel("LOCAL FILE SHARING  /  NO ACCOUNT")
        eyebrow.setObjectName("eyebrow")
        intro.addWidget(eyebrow)
        logo = QLabel("DropOnce.")
        logo.setObjectName("headline")
        intro.addWidget(logo)
        header.addLayout(intro, 1)
        badge = QLabel("●  SAME WI-FI · NO CLOUD")
        badge.setObjectName("eyebrow")
        header.addWidget(badge)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)
        left = QFrame()
        left.setObjectName("card")
        controls = QVBoxLayout(left)
        controls.setContentsMargins(20, 16, 20, 16)
        controls.setSpacing(9)
        heading = QLabel("What are we sending?")
        heading.setObjectName("heading")
        controls.addWidget(heading)
        caption = QLabel("Pick a file or paste text. Anyone with the link on this network can open it.")
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        controls.addWidget(caption)
        switch = QHBoxLayout()
        self.file_mode = QPushButton("↗  File")
        self.text_mode = QPushButton("≡  Text")
        for button in (self.file_mode, self.text_mode):
            button.setObjectName("switch")
            button.setCheckable(True)
            switch.addWidget(button)
        self.file_mode.clicked.connect(self.show_file)
        self.text_mode.clicked.connect(self.show_text)
        controls.addLayout(switch)
        self.inputs = QStackedWidget()
        file_page = QWidget()
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
        controls.addWidget(self.inputs, 1)
        self.network = QComboBox()
        self.network.setAccessibleName("Network address to share from")
        controls.addWidget(QLabel("YOUR NETWORK ADDRESS"))
        network_row = QHBoxLayout()
        network_row.addWidget(self.network, 1)
        rescan = QPushButton("↻")
        rescan.setToolTip("Refresh network addresses")
        rescan.clicked.connect(self.refresh_networks)
        network_row.addWidget(rescan)
        controls.addLayout(network_row)
        self.start_button = QPushButton("Create one-time link  →")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_share)
        controls.addWidget(self.start_button)
        body.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("card")
        right.setMinimumWidth(295)
        output = QVBoxLayout(right)
        output.setContentsMargins(21, 21, 21, 21)
        output.setSpacing(12)
        self.status = QLabel("Ready when you are")
        self.status.setObjectName("heading")
        output.addWidget(self.status)
        self.message = QLabel("Choose something to share, then scan the code on another device.")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        output.addWidget(self.message)
        output.addStretch()
        self.code = QLabel("◇")
        self.code.setObjectName("qr")
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code.setStyleSheet("color:#81e6d1;font-size:82px")
        self.code.setFixedSize(274, 274)
        output.addWidget(self.code, alignment=Qt.AlignmentFlag.AlignCenter)
        output.addStretch()
        self.link = QLineEdit()
        self.link.setReadOnly(True)
        self.link.setPlaceholderText("A one-time link will appear here")
        self.link.setAccessibleName("One-time transfer link")
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
        foot = QLabel("PRIVATE LINK  ·  EXPIRES IN 10 MINUTES  ·  ONE DOWNLOAD OR ONE TEXT VIEW  ·  USE A TRUSTED NETWORK")
        foot.setObjectName("eyebrow")
        foot.setWordWrap(True)
        outer.addWidget(foot)

        self.ticker = QTimer(self)
        self.ticker.setInterval(250)
        self.ticker.timeout.connect(self.update_status)
        self.ticker.start()
        self.set_mode(0)
        self.refresh_networks()

    def set_mode(self, index):
        self.inputs.setCurrentIndex(index)
        self.file_mode.setChecked(index == 0)
        self.text_mode.setChecked(index == 1)

    def show_file(self):
        self.set_mode(0)

    def show_text(self):
        self.set_mode(1)

    def copy_link(self):
        QApplication.clipboard().setText(self.link.text())

    def select_file(self, filename):
        self.file = Path(filename)
        self.file_label.setText(self.file.name)
        self.file_label.setToolTip(str(self.file))
        self.set_mode(0)

    def refresh_networks(self):
        previous = self.network.currentData()
        self.network.clear()
        for address, interface in lan_addresses():
            self.network.addItem(f"{address}  ·  {interface}", address)
        if previous:
            index = self.network.findData(previous)
            if index >= 0:
                self.network.setCurrentIndex(index)
        self.start_button.setEnabled(self.network.count() > 0)
        if not self.network.count():
            self.message.setText("No private Wi-Fi or LAN address found. Connect to a network and refresh.")

    def start_share(self):
        address = self.network.currentData()
        if not address:
            return
        try:
            if self.inputs.currentIndex() == 0:
                if self.file is None:
                    raise ValueError("Choose a file first")
                new_share = ShareServer(address, file=self.file)
            else:
                new_share = ShareServer(address, text=self.text_input.toPlainText())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Couldn't start sharing", str(exc))
            return
        if self.share:
            self.share.stop()
        self.share = new_share
        self.code.setStyleSheet("")
        self.code.setPixmap(qr_image(new_share.url))
        self.link.setText(new_share.url)
        self.link.setCursorPosition(0)
        self.link.setToolTip(new_share.url)
        self.copy.setEnabled(True)
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
        self.message.setText(f"Scan with a device on the same Wi-Fi. Expires in {seconds // 60}:{seconds % 60:02d}."
                             + (f"\n{self.share.error}" if self.share.error else ""))

    def stop_share(self, checked=False, *, completed=False, expired=False):
        if self.share:
            self.share.stop()
            self.share = None
        self.link.clear()
        self.copy.setEnabled(False)
        self.stop.setEnabled(False)
        self.code.clear()
        self.code.setPixmap(QPixmap())
        self.code.setText("◇")
        self.code.setStyleSheet("color:#81e6d1;font-size:82px")
        self.status.setText("Sent!" if completed else "Link expired" if expired else "Sharing stopped")
        self.message.setText("Ready for another transfer." if completed else
                             "Create a new link whenever you're ready.")

    def closeEvent(self, event):
        if self.share:
            self.share.stop()
            self.share = None
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DropOnce()
    if "--self-check" in sys.argv:
        assert window.network is not None and window.inputs.count() == 2
        assert not qr_image("http://127.0.0.1:1234/s/example").isNull()
        window.close()
        raise SystemExit(0)
    window.show()
    raise SystemExit(app.exec())
