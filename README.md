# DropOnce

Send a file or text from your **Windows, macOS, or Linux computer** to an Android phone or iPhone, or receive one file from your phone. The phone needs only its browser; no account or cloud upload is needed.

Download the archive for your computer from the [latest release](https://github.com/yxzroot/drop-once/releases/latest): `DropOnce-Windows.zip`, `DropOnce-macOS.zip`, or `DropOnce-Linux.tar.gz`. Extract it first, then open `DropOnce.exe` (Windows), `DropOnce.app` (macOS), or `DropOnce` (Linux) inside the extracted folder.

## Wi-Fi transfer

1. Put both devices on the **same Wi-Fi or LAN** (a personal hotspot works too). Complete the [one-time HTTPS certificate setup](#encrypted-transfer-and-phone-setup) before scanning a link.
2. Open DropOnce. Choose **File** or **Text** to send from the computer, or **Receive** to accept one file into a folder you select.
3. Choose **Same Wi-Fi / LAN**, select the computer's network adapter, and create the link. Click **Show QR code** to scan with your phone, or click **Copy link** to share it. To receive on your computer, select the file in the phone's browser and tap **Send file**; wait for **Sent!** on the phone and **Received!** on the computer.

**Screen privacy:** DropOnce masks parts of the IP address and token in the window, and keeps the QR hidden until you click **Show QR code** (it hides again after 45 seconds). The copied link and the revealed QR **must contain the complete address and token** so the other device can connect. Masking the screen does not hide your local address from the recipient or other devices on your network. The clipboard is cleared when a share stops, but another app may have read it before then.

Choose **System**, **Dark**, **Light**, or **Ocean** in the appearance selector at the top. Your choice is saved for the next launch.

## Android over a USB cable

On Android, connect the phone to the computer with a USB data cable and enable **USB tethering** in the phone's **Hotspot & tethering** settings (names vary by manufacturer). Choose **Android USB tethering** in DropOnce, refresh adapters, and select the new USB network adapter. Then open the QR link in your Android browser and send or receive as on Wi-Fi. Android USB tethering typically works with Windows and Linux; [Google notes that Macs cannot normally USB-tether with Android](https://support.google.com/android/answer/9059108). On macOS, use the same Wi-Fi instead. Your carrier or device may limit tethering. The adapter name is only a hint; verify the connection if multiple networks are present.

This transfers over the local USB **network** created by tethering. A plain USB connection without tethering does not give DropOnce access to all files on an Android phone. Both directions work via the phone's browser; Bluetooth is not used.

## iPhone over a USB cable

An ordinary USB cable does **not** give DropOnce access to iPhone storage. To use the cable, enable **Personal Hotspot** on the iPhone, connect it by USB, and tap **Trust** if prompted. On Windows, you may need Apple's iPhone USB network drivers (installed with Apple Devices/iTunes); on Linux, USB tethering depends on your distribution's iPhone tether/network support. Wait for a new network adapter on your computer, select **iPhone USB Personal Hotspot** in DropOnce, click refresh, and choose the iPhone USB network address. The adapter hint is only a guess; verify the selected connection. Scan the QR code with the iPhone camera (or enter the link in Safari) and transfer as above. If the address doesn't appear, reconnect the cable, check the hotspot/driver, and refresh. Wi-Fi remains an alternative.

This uses the iPhone's browser and local network connection over the USB tether, **not** direct access to the iPhone's Files app. Personal Hotspot availability may depend on your carrier or device settings. Keep the phone connected and awake during a transfer.

## Encrypted transfer and phone setup

**All transfers use HTTPS/TLS** (including the upload page, file data, and text). DropOnce creates a private certificate authority on your computer and uses it to sign certificates for your selected local IP addresses. Public certificate authorities generally cannot issue certificates for private LAN addresses. **The phone must trust your computer's DropOnce certificate before scanning a link.**

1. Select your computer's network address in DropOnce, then click **Export phone trust certificate…** before creating a link. This exports only the public `DropOnce-CA.cer` certificate, **never** its private key.
2. Transfer that `.cer` file to your phone by a method you trust, such as AirDrop, a cable, or another trusted local transfer. Do **not** get it from an untrusted website or a certificate-warning page.
3. On iPhone, open the `.cer` file, install the downloaded profile in **Settings → General → VPN & Device Management**, then explicitly enable it in **Settings → General → About → Certificate Trust Settings**. On Android, open **Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate → CA certificate** (labels vary by manufacturer). Your phone may ask you to confirm or restart its browser. If your browser cannot trust the certificate, the transfer will not work; **do not bypass certificate errors**. On other receiving devices, import the certificate into that device's trusted certificate store following its OS instructions.
4. Now scan the QR code. The HTTPS address should open **without** a certificate warning. If the IP address changes, DropOnce signs a new server certificate using the same trusted CA; the phone normally needs no new setup.

**Protect the CA private key:** it remains in your computer's per-user DropOnce TLS configuration folder. A trusted CA can certify websites beyond DropOnce if its private key is stolen; only install this CA on your own devices and remove it from your phone when you stop using DropOnce. If your computer's DropOnce TLS identity folder is deleted or its CA expires, export and install the new certificate again. Never distribute the private-key files.

The link works for **one completed file download, one text view, or one file upload** and expires after 10 minutes. Closing DropOnce or clicking **Stop sharing** ends the transfer. The computer must remain on and connected. Files move directly between devices without a cloud server. Files are limited to 2 GiB; text to 64 KiB. Received files never overwrite existing files: a numbered name is used instead. New uploads are created with owner-only permissions on macOS and Linux (folder permissions still control access).

**SHA-256 is not encryption.** HTTPS/TLS encrypts and authenticates the connection. DropOnce also calculates a SHA-256 checksum of each completed file transfer and displays it on the computer (and on the phone after an upload). For uploads of at most 32 MiB, supported phone browsers calculate the SHA-256 checksum before sending and the computer rejects a mismatch. Larger files still get a SHA-256 checksum on the computer, but the phone does **not** independently calculate one because Web Crypto requires loading the whole file into memory. A checksum detects accidental changes; it does not authenticate a malicious sender or scan files for malware.

**Security:** The QR link contains a random token, and DropOnce listens only on the network address you select. Transfers are encrypted and authenticated **only after the phone trusts the correct DropOnce CA**. Anyone who has the link and can reach that network can claim the transfer, including uploading a file into the selected folder in Receive mode. Use a trusted network anyway, especially when installing the certificate. A firewall may ask you to permit incoming connections. Some guest Wi-Fi networks isolate devices and won't work.

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
