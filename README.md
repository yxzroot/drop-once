# DropOnce

Send a file or text from your **Windows, macOS, or Linux computer** to a phone, or receive one file from your phone. The phone needs only its browser; no account or cloud upload is needed.

Download the archive for your computer from the [latest release](https://github.com/yxzroot/drop-once/releases/latest): `DropOnce-Windows.zip`, `DropOnce-macOS.zip`, or `DropOnce-Linux.tar.gz`. Extract it first, then open `DropOnce.exe` (Windows), `DropOnce.app` (macOS), or `DropOnce` (Linux) inside the extracted folder.

## Wi-Fi transfer

1. Put both devices on the **same Wi-Fi or LAN** (a personal hotspot works too).
2. Open DropOnce. Choose **File** or **Text** to send from the computer, or **Receive** to accept one file into a folder you select.
3. Choose **Same Wi-Fi / LAN**, select the computer's address on that network, and create the link. Scan the QR code on your phone. To receive on your computer, select the file in the phone's browser and tap **Send file**; wait for **Sent!** on the phone and **Received!** on the computer.

## iPhone over a USB cable

An ordinary USB cable does **not** give DropOnce access to iPhone storage. To use the cable, enable **Personal Hotspot** on the iPhone, connect it by USB, and tap **Trust** if prompted. On Windows, you may need Apple's iPhone USB network drivers (installed with Apple Devices/iTunes); on Linux, USB tethering depends on your distribution's iPhone tether/network support. Wait for a new network adapter on your computer, select **iPhone USB Personal Hotspot** in DropOnce, click refresh, and choose the iPhone USB network address. The adapter hint is only a guess; verify the selected connection. Scan the QR code with the iPhone camera (or enter the link in Safari) and transfer as above. If the address doesn't appear, reconnect the cable, check the hotspot/driver, and refresh. Wi-Fi remains an alternative.

This uses the iPhone's browser and local network connection over the USB tether, **not** direct access to the iPhone's Files app. Personal Hotspot availability may depend on your carrier or device settings. Keep the phone connected and awake during a transfer.

The link works for **one completed file download, one text view, or one file upload** and expires after 10 minutes. Closing DropOnce or clicking **Stop sharing** ends the transfer. The computer must remain on and connected. Files move directly between devices without a cloud server. Files are limited to 2 GiB; text to 64 KiB. Received files never overwrite existing files: a numbered name is used instead.

**Security:** The QR link contains a random token, and DropOnce listens only on the network address you select. Transfers use **unencrypted local HTTP** in this first version, so use a trusted home network or your own hotspot—not public Wi-Fi. Anyone who has the link and can reach that network can claim the transfer, including uploading a file into the selected folder in Receive mode. A firewall may ask you to permit incoming connections. Some guest Wi-Fi networks isolate devices and won't work.

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
