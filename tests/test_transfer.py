from http.client import HTTPConnection
from pathlib import Path
import tempfile
import time
import unittest

from transfer import ShareServer


def get(share, path):
    connection = HTTPConnection("127.0.0.1", share.port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


class TransferTests(unittest.TestCase):
    def test_file_preview_does_not_use_link_but_download_does(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'demo "pack".zip'
            source.write_bytes(b"zip bytes")
            share = ShareServer("127.0.0.1", file=source)
            try:
                link = f"/s/{share.token}"
                code, preview, _ = get(share, link)
                self.assertEqual(code, 200)
                self.assertIn(b'&quot;pack&quot;', preview)
                self.assertFalse(share.served)
                code, content, headers = get(share, link + "/download")
                self.assertEqual((code, content), (200, b"zip bytes"))
                self.assertEqual(headers["Cache-Control"], "no-store")
                for _ in range(20):
                    if share.served:
                        break
                    time.sleep(.01)
                self.assertTrue(share.served)
                self.assertEqual(get(share, link + "/download")[0], 410)
                self.assertEqual(get(share, "/s/not-the-token")[0], 404)
            finally:
                share.stop()

    def test_text_is_escaped_and_consumed_once(self):
        share = ShareServer("127.0.0.1", text="<script>alert(1)</script> & text")
        try:
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
