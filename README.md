# DropOnce

Send one file or text message from your **Mac or Windows PC** to a phone or another computer. Scan a QR code on the receiving device; no account or receiver app is needed. Linux works too.

Download the archive for your computer from the [latest release](https://github.com/yxzroot/drop-once/releases/latest): `DropOnce-Windows.zip`, `DropOnce-macOS.zip`, or `DropOnce-Linux.tar.gz`. Extract it first, then open `DropOnce.exe` (Windows), `DropOnce.app` (macOS), or `DropOnce` (Linux) inside the extracted folder.

## How it works

1. Put both devices on the **same Wi-Fi or LAN** (a personal hotspot works too).
2. Open DropOnce, choose a file or paste text, and select the network address that belongs to that Wi-Fi.
3. Click **Create one-time link** and scan the QR code with the other device. For a file, tap **Download file** in the browser. For text, select and copy the message.

The link works for **one completed file download or one text view** and expires after 10 minutes. Closing DropOnce or clicking **Stop sharing** ends the share. The sender must remain on and connected; files are streamed directly from the computer without uploading to a cloud server. Files are limited to 2 GiB; text to 64 KiB.

**Security:** The QR link contains a random token, and DropOnce listens only on the network address you select. Transfers use **unencrypted local HTTP** in this first version, so use a trusted home network or your own hotspot—not public Wi-Fi. Anyone who has the link and can reach that network can claim the transfer. A firewall may ask you to permit incoming connections. Some guest Wi-Fi networks isolate devices and won't work.

## Run from source

Install Python 3.12 or newer, then:

```bash
python -m pip install -r requirements.txt
python desktop.py
```

## Build desktop apps

```bash
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean drop-once.spec
```

The app appears in `dist/DropOnce` on Windows and Linux, or `dist/DropOnce.app` on macOS. GitHub Actions builds and tests all three platforms; a `v*` tag publishes their archives to a release. Windows SmartScreen or macOS Gatekeeper may warn about unsigned builds.
