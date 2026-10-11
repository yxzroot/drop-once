"""Single-use, local-network transfers without a cloud service."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
import os
from pathlib import Path
import secrets
import ssl
import stat
import sys
from threading import BoundedSemaphore, Lock, Thread
from time import monotonic
from urllib.parse import parse_qs, quote, urlsplit

from tls_identity import tls_context


MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
EXPIRY_SECONDS = 10 * 60
UPLOAD_READ_TIMEOUT = 30
CONNECTION_TIMEOUT = 10
MAX_CONNECTIONS = 16


def _page(content):
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>DropOnce</title><style>"
            "*{box-sizing:border-box}body{margin:0;background:#0e1320;color:#eff5ff;"
            "font:16px system-ui,-apple-system,sans-serif;display:grid;place-items:center;min-height:100vh;padding:20px}"
            "main{width:min(100%,560px);background:#192337;border:1px solid #34465f;"
            "border-radius:24px;padding:32px;box-shadow:0 24px 80px #05091177}"
            "small{color:#84e6cf;font-weight:700;letter-spacing:.12em}h1{font-size:28px;overflow-wrap:anywhere}"
            "p{color:#bac8da;line-height:1.5}pre{white-space:pre-wrap;overflow-wrap:anywhere;"
            "font:inherit;background:#0e1320;padding:20px;border-radius:12px;max-height:60vh;overflow:auto}"
            "a,button{display:inline-block;background:#84e6cf;color:#0e1320;padding:14px 22px;"
            "border:0;border-radius:12px;text-decoration:none;font:700 16px system-ui;cursor:pointer;margin-top:10px}"
            "input[type=file]{display:block;width:100%;padding:18px;border:1px dashed #84e6cf;"
            "border-radius:12px;color:#eff5ff;background:#0e1320}"
            "</style></head><body><main><small>DROPONCE · LOCAL TRANSFER</small>"
            f"{content}</main></body></html>").encode("utf-8")


class TransferHTTPServer(ThreadingHTTPServer):
    """Bound idle sockets and worker threads on the selected local interface."""

    daemon_threads = True
    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self.slots = BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(CONNECTION_TIMEOUT)
        return connection, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLError, TimeoutError, BrokenPipeError,
                                          ConnectionResetError)):
            return  # Invalid TLS connections and idle timeouts are expected on a LAN.
        super().handle_error(request, client_address)


class ShareServer:
    """One file download or one text view; expires even if no device connects."""

    def __init__(self, address, *, file=None, text=None, lifetime=EXPIRY_SECONDS):
        if (file is None) == (text is None):
            raise ValueError("Share either one file or one text message")
        if not (1 <= lifetime <= 3600):
            raise ValueError("Invalid share lifetime")
        self.address = address
        self.file = None
        self.text = None
        self.size = 0
        self.file_identity = None
        if file is not None:
            path = Path(file)
            if path.is_symlink() or not path.is_file():
                raise ValueError("Choose a regular file, not a folder or symlink")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_SIZE:
                raise ValueError("File must be smaller than 2 GiB")
            self.file = path
            self.size = info.st_size
            self.file_identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        else:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Enter some text first")
            self.text = text
            self.size = len(text.encode("utf-8"))
            if self.size > MAX_TEXT_BYTES:
                raise ValueError("Text must be smaller than 64 KiB")
        self._start(address, lifetime)

    def _start(self, address, lifetime):
        self.token = secrets.token_urlsafe(32)
        self.deadline = monotonic() + lifetime
        self.lock = Lock()
        self.served = False
        self.in_progress = False
        self.error = None
        self.stopped = False
        context, self.ca_certificate = tls_context(address)
        self.httpd = TransferHTTPServer((address, 0), self._handler())
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True,
                                                do_handshake_on_connect=False)
        self.port = self.httpd.server_port
        self.thread = Thread(target=self.httpd.serve_forever, name="drop-once-server", daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"https://{self.address}:{self.port}/s/{self.token}"

    @property
    def remaining(self):
        return max(0, int(self.deadline - monotonic() + .999))

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _claim(self):
        with self.lock:
            if self.served or self.in_progress or self.stopped or not self.remaining:
                return False
            self.in_progress = True
            return True

    def _finish(self, successful):
        with self.lock:
            self.in_progress = False
            if successful:
                self.served = True

    def _handler(self):
        share = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Do not log one-time tokens or filenames.

            def reply(self, code, body, mime="text/html; charset=utf-8", headers=None):
                self.send_response(code)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                                 "base-uri 'none'; frame-ancestors 'none'")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return False
                return True

            def do_GET(self):
                location = urlsplit(self.path).path
                expected = f"/s/{share.token}"
                if location not in (expected, expected + "/download"):
                    self.reply(404, _page("<h1>Not found</h1>"))
                    return
                if share.served or share.stopped or not share.remaining:
                    self.reply(410, _page("<h1>This link has expired.</h1>"))
                    return
                if share.text is not None:
                    if location != expected or not share._claim():
                        self.reply(410, _page("<h1>This link is no longer available.</h1>"))
                        return
                    body = _page("<h1>Shared text</h1><p>Select and copy the text below.</p>"
                                 f"<pre>{escape(share.text)}</pre><p>This was a one-time view.</p>")
                    share._finish(self.reply(200, body))
                    return
                if location == expected:
                    filename = escape(share.file.name)
                    body = _page(f"<h1>{filename}</h1><p>{share.size:,} bytes · from a device on your network"
                                 "</p><p>This download works once and expires after 10 minutes.</p>"
                                 f"<a href='{expected}/download'>Download file</a>")
                    self.reply(200, body)
                    return
                if not share._claim():
                    self.reply(410, _page("<h1>This link is no longer available.</h1>"))
                    return
                headers_sent = False
                try:
                    if share.file.is_symlink():
                        raise ValueError("Shared file changed")
                    with share.file.open("rb") as source:
                        info = os.fstat(source.fileno())
                        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                        if not stat.S_ISREG(info.st_mode) or identity != share.file_identity:
                            raise ValueError("Shared file changed")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" +
                                         quote(share.file.name, safe=""))
                        self.send_header("Content-Length", str(share.size))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        headers_sent = True
                        remaining = share.size
                        while remaining:
                            if share.stopped:
                                raise ConnectionAbortedError("Sharing stopped")
                            block = source.read(min(64 * 1024, remaining))
                            if not block:
                                raise OSError("File changed during download")
                            self.wfile.write(block)
                            remaining -= len(block)
                    share._finish(True)
                except (OSError, ValueError, BrokenPipeError, ConnectionResetError) as exc:
                    share.error = str(exc)
                    share._finish(False)
                    # If headers were already sent, closing the connection is safer
                    # than sending a second response containing part of the file.
                    if headers_sent:
                        self.close_connection = True
                    else:
                        try:
                            self.reply(409, _page("<h1>File unavailable</h1><p>The file changed or moved. "
                                                  "Ask the sender to share it again.</p>"))
                        except (BrokenPipeError, ConnectionResetError):
                            pass

        return Handler


class ReceiveServer(ShareServer):
    """One browser upload to a computer-selected folder. Never overwrite a file."""

    def __init__(self, address, directory, lifetime=EXPIRY_SECONDS):
        folder = Path(directory)
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Choose an existing, real destination folder")
        if not (1 <= lifetime <= 3600):
            raise ValueError("Invalid share lifetime")
        self.address = address
        self.directory = folder
        self.received = None
        self._start(address, lifetime)

    def _handler(self):
        share = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body, *, mime="text/html; charset=utf-8", script_nonce=None):
                self.send_response(code)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                csp = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
                if script_nonce:
                    csp += f"; script-src 'nonce-{script_nonce}'; connect-src 'self'"
                self.send_header("Content-Security-Policy", csp)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    return False
                return True

            def do_GET(self):
                if urlsplit(self.path).path != f"/s/{share.token}":
                    self.reply(404, _page("<h1>Not found</h1>"))
                    return
                if share.served or share.stopped or not share.remaining:
                    self.reply(410, _page("<h1>This link has expired.</h1>"))
                    return
                nonce = secrets.token_urlsafe(16)
                body = _page("<h1>Send a file to this computer</h1>"
                             "<p>Choose one file. It is sent directly over this local connection; "
                             "keep this page open until the transfer finishes.</p>"
                             "<input type='file' id='file' aria-label='File to send'>"
                             "<button id='send' type='button'>Send file</button>"
                             "<p id='status'>Maximum file size: 2 GiB.</p>"
                             f"<script nonce='{nonce}'>"
                             "document.getElementById('send').onclick=async()=>{"
                             "const file=document.getElementById('file').files[0];"
                             "const status=document.getElementById('status');"
                             "if(!file){status.textContent='Choose a file first.';return;}"
                             "const button=document.getElementById('send');button.disabled=true;"
                             "status.textContent='Sending… Please keep this page open.';"
                             "try{const response=await fetch(location.pathname+'/upload?name='+"
                             "encodeURIComponent(file.name),{method:'PUT',body:file});"
                             "status.textContent=response.ok?'Sent! You can close this page now.':"
                             "'Could not send. The link may have expired or the file is too large.';"
                             "if(!response.ok)button.disabled=false;}"
                             "catch(_){status.textContent='Connection lost. Ask for a new link.';button.disabled=false;}"
                             "};</script>")
                self.reply(200, body, script_nonce=nonce)

            def do_PUT(self):
                request = urlsplit(self.path)
                if request.path != f"/s/{share.token}/upload":
                    self.reply(404, _page("<h1>Not found</h1>"))
                    return
                if share.stopped or share.served or not share.remaining:
                    self.reply(410, _page("<h1>This link has expired.</h1>"))
                    return
                length = self.headers.get("Content-Length", "")
                if (self.headers.get("Transfer-Encoding") is not None
                        or len(self.headers.get_all("Content-Length", [])) != 1
                        or not length.isascii() or not length.isdecimal() or len(length) > 10):
                    self.reply(411, _page("<h1>Upload length required.</h1>"))
                    return
                size = int(length)
                if size > MAX_FILE_SIZE:
                    self.reply(413, _page("<h1>File is too large.</h1>"))
                    return
                names = parse_qs(request.query, keep_blank_values=True).get("name", [])
                name = names[0] if len(names) == 1 else ""
                if (not name or len(name) > 240 or name in (".", "..") or Path(name).name != name
                        or any(c in name for c in '\\<>:"|?*') or name.endswith((" ", "."))
                        or any(ord(c) < 32 for c in name)
                        or name.split(".")[0].upper() in
                        {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                         *(f"LPT{i}" for i in range(1, 10))}):
                    self.reply(400, _page("<h1>Invalid filename.</h1>"))
                    return
                if not share._claim():
                    self.reply(410, _page("<h1>This link is already in use.</h1>"))
                    return
                destination = None
                try:
                    # Exclusive creation protects existing files, including symlinks.
                    for number in range(1001):
                        filename = name if number == 0 else f"{Path(name).stem}-{number}{Path(name).suffix}"
                        candidate = share.directory / filename
                        try:
                            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                            if hasattr(os, "O_NOFOLLOW"):
                                flags |= os.O_NOFOLLOW
                            fd = os.open(candidate, flags, 0o600)
                        except FileExistsError:
                            continue
                        output = os.fdopen(fd, "wb")
                        destination = candidate
                        break
                    else:
                        raise OSError("No available filename in destination folder")
                    self.connection.settimeout(UPLOAD_READ_TIMEOUT)
                    with output:
                        remaining = size
                        while remaining:
                            if share.stopped or not share.remaining:
                                raise ConnectionAbortedError("Sharing stopped or expired")
                            block = self.rfile.read(min(64 * 1024, remaining))
                            if not block:
                                raise ConnectionError("Upload ended early")
                            output.write(block)
                            remaining -= len(block)
                    self.reply(200, b"Received", mime="text/plain; charset=utf-8")
                    with share.lock:
                        share.received = destination
                    share._finish(True)
                except (OSError, ValueError, ConnectionError) as exc:
                    if destination:
                        try:
                            destination.unlink(missing_ok=True)
                        except OSError:
                            pass
                    share.error = str(exc)
                    share._finish(False)
                    try:
                        self.reply(409, _page("<h1>Could not receive the file.</h1>"))
                    except OSError:
                        pass

        return Handler
