from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
import hashlib
import os
import socket
import stat
import ssl
import tempfile
import time
import unittest
from unittest.mock import patch

from transfer import ReceiveServer, ShareServer


def setUpModule():
    global _tls_directory, _tls_patch
    _tls_directory = tempfile.TemporaryDirectory()
    _tls_patch = patch("tls_identity.identity_folder", return_value=Path(_tls_directory.name))
    _tls_patch.start()


def tearDownModule():
    _tls_patch.stop()
    _tls_directory.cleanup()


def get(share, path):
    connection = connect(share)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def connect(share):
    context = ssl.create_default_context(cafile=str(share.ca_certificate))
    return HTTPSConnection("127.0.0.1", share.port, timeout=3, context=context)


class TransferTests(unittest.TestCase):
    def test_idle_non_tls_socket_cannot_block_other_connections(self):
        with patch("transfer.CONNECTION_TIMEOUT", .2):
            share = ShareServer("127.0.0.1", text="secure")
            idle = socket.create_connection(("127.0.0.1", share.port), timeout=2)
            try:
                self.assertEqual(get(share, f"/s/{share.token}")[0], 200)
            finally:
                idle.close()
                share.stop()

    def test_iphone_browser_can_upload_once_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            existing = folder / "photo.jpg"
            existing.write_bytes(b"keep original")
            receive = ReceiveServer("127.0.0.1", folder)
            try:
                link = f"/s/{receive.token}"
                code, page, headers = get(receive, link)
                self.assertEqual(code, 200)
                self.assertIn(b"Send a file to this computer", page)
                self.assertIn(b"crypto.subtle.digest('SHA-256'", page)
                self.assertIn("script-src", headers["Content-Security-Policy"])
                connection = connect(receive)
                try:
                    connection.request("PUT", link + "/upload?name=photo.jpg", body=b"new photo",
                                       headers={"Content-Type": "application/octet-stream",
                                                "X-Content-SHA256": hashlib.sha256(b"new photo").hexdigest()})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader("X-Content-SHA256"),
                                     hashlib.sha256(b"new photo").hexdigest())
                    response.read()
                finally:
                    connection.close()
                for _ in range(30):
                    if receive.served:
                        break
                    time.sleep(.01)
                self.assertEqual(existing.read_bytes(), b"keep original")
                self.assertEqual((folder / "photo-1.jpg").read_bytes(), b"new photo")
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE((folder / "photo-1.jpg").stat().st_mode), 0o600)
                self.assertEqual(receive.received, folder / "photo-1.jpg")
                self.assertEqual(receive.sha256, hashlib.sha256(b"new photo").hexdigest())
                self.assertEqual(get(receive, link)[0], 410)
            finally:
                receive.stop()

    def test_iphone_upload_rejects_bad_paths_and_excess_size(self):
        with tempfile.TemporaryDirectory() as temp:
            receive = ReceiveServer("127.0.0.1", Path(temp))
            try:
                link = f"/s/{receive.token}/upload"
                for name, expected in (("..%2Fsecret", 400), ("bad%5Cname", 400),
                                       ("CON.txt", 400), ("%2E%2E", 400)):
                    connection = connect(receive)
                    try:
                        connection.request("PUT", link + "?name=" + name, body=b"bad")
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                    finally:
                        connection.close()
                self.assertEqual(list(Path(temp).iterdir()), [])
                self.assertFalse(receive.served)
                connection = connect(receive)
                try:
                    connection.request("PUT", link + "?name=hash.bin", body=b"bad",
                                       headers={"X-Content-SHA256": "not-a-sha256"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    response.read()
                finally:
                    connection.close()
                connection = connect(receive)
                try:
                    connection.putrequest("PUT", link + "?name=duplicate.bin")
                    connection.putheader("Content-Length", "0")
                    connection.putheader("Content-Length", "0")
                    connection.endheaders()
                    response = connection.getresponse()
                    self.assertEqual(response.status, 411)
                    response.read()
                finally:
                    connection.close()
                connection = connect(receive)
                try:
                    connection.request("PUT", link + "?name=huge.bin", body=b"",
                                       headers={"Content-Length": str(2 * 1024**3 + 1)})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 413)
                    response.read()
                finally:
                    connection.close()
            finally:
                receive.stop()

    def test_interrupted_upload_removes_partial_file_and_can_retry(self):
        with tempfile.TemporaryDirectory() as temp, patch("transfer.UPLOAD_READ_TIMEOUT", .2):
            receive = ReceiveServer("127.0.0.1", temp)
            try:
                path = f"/s/{receive.token}/upload?name=retry.txt"
                connection = connect(receive)
                connection.connect()
                connection.putrequest("PUT", path)
                connection.putheader("Content-Length", "5")
                connection.endheaders()
                connection.send(b"ab")
                for _ in range(200):
                    if receive.in_progress:
                        break
                    time.sleep(.01)
                self.assertTrue(receive.in_progress)
                connection.sock.shutdown(socket.SHUT_RDWR)
                connection.close()
                for _ in range(200):
                    if not receive.in_progress and not list(Path(temp).iterdir()):
                        break
                    time.sleep(.01)
                self.assertEqual(list(Path(temp).iterdir()), [])
                connection = connect(receive)
                try:
                    connection.request("PUT", path, body=b"retry")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                finally:
                    connection.close()
                self.assertEqual((Path(temp) / "retry.txt").read_bytes(), b"retry")
            finally:
                receive.stop()

    def test_wrong_sha256_rejects_upload_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            share = ReceiveServer("127.0.0.1", temp)
            try:
                path = f"/s/{share.token}/upload?name=verified.bin"
                connection = connect(share)
                try:
                    connection.request("PUT", path, body=b"actual",
                                       headers={"X-Content-SHA256": "0" * 64})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 409)
                    response.read()
                finally:
                    connection.close()
                self.assertEqual(list(Path(temp).iterdir()), [])
                self.assertIsNone(share.sha256)
                self.assertFalse(share.served)
                connection = connect(share)
                try:
                    connection.request("PUT", path, body=b"actual",
                                       headers={"X-Content-SHA256": hashlib.sha256(b"actual").hexdigest()})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                finally:
                    connection.close()
                for _ in range(30):
                    if share.served:
                        break
                    time.sleep(.01)
                self.assertEqual(share.sha256, hashlib.sha256(b"actual").hexdigest())
            finally:
                share.stop()

    def test_file_preview_does_not_use_link_but_download_does(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "demo & pack.zip"
            source.write_bytes(b"zip bytes")
            share = ShareServer("127.0.0.1", file=source)
            try:
                link = f"/s/{share.token}"
                code, preview, _ = get(share, link)
                self.assertEqual(code, 200)
                self.assertIn(b'demo &amp; pack.zip', preview)
                self.assertFalse(share.served)
                code, content, headers = get(share, link + "/download")
                self.assertEqual((code, content), (200, b"zip bytes"))
                self.assertEqual(headers["Cache-Control"], "no-store")
                for _ in range(20):
                    if share.served:
                        break
                    time.sleep(.01)
                self.assertTrue(share.served)
                self.assertEqual(share.sha256, hashlib.sha256(b"zip bytes").hexdigest())
                self.assertEqual(get(share, link + "/download")[0], 410)
                self.assertEqual(get(share, "/s/not-the-token")[0], 404)
            finally:
                share.stop()

    def test_text_is_escaped_and_consumed_once(self):
        share = ShareServer("127.0.0.1", text="<script>alert(1)</script> & text")
        try:
            self.assertTrue(share.url.startswith("https://"))
            unencrypted = HTTPConnection("127.0.0.1", share.port, timeout=3)
            try:
                with self.assertRaises((OSError, ConnectionError)):
                    unencrypted.request("GET", f"/s/{share.token}")
                    unencrypted.getresponse()
            finally:
                unencrypted.close()
            with self.assertRaises(ssl.SSLCertVerificationError):
                untrusted = HTTPSConnection("127.0.0.1", share.port, timeout=3)
                try:
                    untrusted.request("GET", f"/s/{share.token}")
                finally:
                    untrusted.close()
            link = f"/s/{share.token}"
            code, body, _ = get(share, link)
            self.assertEqual(code, 200)
            self.assertIn(b"&lt;script&gt;", body)
            self.assertNotIn(b"<script>", body)
            self.assertEqual(get(share, link)[0], 410)
        finally:
            share.stop()

    def test_expired_link_and_modified_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.txt"
            path.write_text("first")
            share = ShareServer("127.0.0.1", file=path)
            try:
                path.write_text("different")
                self.assertEqual(get(share, f"/s/{share.token}/download")[0], 409)
                self.assertFalse(share.served)
                share.deadline = time.monotonic() - 1
                self.assertEqual(get(share, f"/s/{share.token}")[0], 410)
            finally:
                share.stop()

    def test_rejects_symlink_empty_text_and_multiple_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "file"
            path.write_text("data")
            link = Path(temp) / "link"
            try:
                link.symlink_to(path)
            except (OSError, NotImplementedError):
                pass  # Creating symlinks requires additional privileges on some Windows hosts.
            else:
                with self.assertRaises(ValueError):
                    ShareServer("127.0.0.1", file=link)
            with self.assertRaises(ValueError):
                ShareServer("127.0.0.1", text=" ")
            with self.assertRaises(ValueError):
                ShareServer("127.0.0.1", file=path, text="something")
