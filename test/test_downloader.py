import contextlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, ClassVar

from parsehub.errors import DownloadError
from parsehub.utils.downloader import download


class RangeTestHandler(BaseHTTPRequestHandler):
    """可配置的 Range/版本头测试服务端。

    通过 ``mutate_on_first_part`` 或 ``mutate_after_parts`` 模拟下载途中远端文件被替换：
    - ``mutate_on_first_part``: 收到第一个真正的分片请求时，先把内容替换为 ``mutate_to`` 再响应；
    - ``mutate_after_parts``: 完成指定数量的分片响应后再替换内容。
    """

    content: ClassVar[bytes] = b""
    support_range: ClassVar[bool] = True
    fail_all: ClassVar[bool] = False
    fail_single_get: ClassVar[bool] = False
    use_etag: ClassVar[bool] = True
    use_last_modified: ClassVar[bool] = True
    mutate_on_first_part: ClassVar[bool] = False
    mutate_after_parts: ClassVar[int | None] = None
    mutate_to: ClassVar[bytes | None] = None
    requests: ClassVar[list[tuple[str, str | None]]] = []
    lock: ClassVar[threading.Lock] = threading.Lock()
    state: ClassVar[dict[str, Any]] = {}

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_HEAD(self) -> None:
        self.__class__.requests.append(("HEAD", self.headers.get("Range")))
        if self.fail_all:
            self.send_response(500)
            self.end_headers()
            return

        content, gen = self._snapshot()
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Accept-Ranges", "bytes" if self.support_range else "none")
        self._send_version_headers(gen)
        self.end_headers()

    def do_GET(self) -> None:
        range_header = self.headers.get("Range")
        self.__class__.requests.append(("GET", range_header))
        if self.fail_all:
            self.send_response(500)
            self.end_headers()
            return

        is_part_request = bool(range_header) and range_header != "bytes=0-0"
        if is_part_request and self.mutate_on_first_part:
            self._mutate_once()

        if range_header and self.support_range:
            self._handle_range(range_header, is_part_request)
            return

        if self.fail_single_get:
            self.send_response(500)
            self.end_headers()
            return

        content, gen = self._snapshot()
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Accept-Ranges", "none")
        self._send_version_headers(gen)
        self.end_headers()
        self.wfile.write(content)

    def _handle_range(self, range_header: str, is_part_request: bool) -> None:
        start, end = self._parse_range(range_header)
        content, gen = self._snapshot()
        if start >= len(content):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{len(content)}")
            self._send_version_headers(gen)
            self.end_headers()
            return

        end = min(end, len(content) - 1)
        body = content[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(content)}")
        self.send_header("Accept-Ranges", "bytes")
        self._send_version_headers(gen)
        self.end_headers()
        self.wfile.write(body)

        if is_part_request:
            self._maybe_mutate_after_part()

    def _snapshot(self) -> tuple[bytes, int]:
        with self.lock:
            return self.state["content"], self.state["gen"]

    def _mutate_once(self) -> None:
        cls = self.__class__
        with cls.lock:
            if cls.state["mutated"] or cls.mutate_to is None:
                return
            cls.state["content"] = cls.mutate_to
            cls.state["gen"] += 1
            cls.state["mutated"] = True

    def _maybe_mutate_after_part(self) -> None:
        cls = self.__class__
        if cls.mutate_after_parts is None:
            return
        with cls.lock:
            if cls.state["mutated"]:
                return
            cls.state["parts_served"] += 1
            if cls.state["parts_served"] >= cls.mutate_after_parts:
                if cls.mutate_to is not None:
                    cls.state["content"] = cls.mutate_to
                    cls.state["gen"] += 1
                cls.state["mutated"] = True

    def _send_version_headers(self, gen: int) -> None:
        if self.use_etag:
            self.send_header("ETag", f'"v{gen}"')
        if self.use_last_modified:
            self.send_header("Last-Modified", f"Wed, 01 Jan 2025 00:00:{gen % 60:02d} GMT")

    @staticmethod
    def _parse_range(header: str) -> tuple[int, int]:
        prefix = "bytes="
        if not header.startswith(prefix):
            return 0, 0
        start_text, end_text = header.removeprefix(prefix).split("-", 1)
        return int(start_text), int(end_text)


@contextlib.contextmanager
def range_server(
    *,
    content: bytes,
    support_range: bool = True,
    fail_all: bool = False,
    fail_single_get: bool = False,
    use_etag: bool = True,
    use_last_modified: bool = True,
    mutate_on_first_part: bytes | None = None,
    mutate_after_parts: int | None = None,
    mutate_to: bytes | None = None,
):
    class Handler(RangeTestHandler):
        pass

    Handler.content = content
    Handler.support_range = support_range
    Handler.fail_all = fail_all
    Handler.fail_single_get = fail_single_get
    Handler.use_etag = use_etag
    Handler.use_last_modified = use_last_modified
    Handler.mutate_on_first_part = mutate_on_first_part is not None
    Handler.mutate_after_parts = mutate_after_parts
    Handler.mutate_to = mutate_on_first_part if mutate_on_first_part is not None else mutate_to
    Handler.requests = []
    Handler.lock = threading.Lock()
    Handler.state = {"content": content, "gen": 0, "mutated": False, "parts_served": 0}

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/file.bin", Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class DownloaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_download_uses_range_parts_when_server_supports_range(self):
        content = bytes(range(251)) * 20
        progresses: list[tuple[int, int]] = []

        async def progress(current: int, total: int) -> None:
            progresses.append((current, total))

        with TemporaryDirectory() as tmp, range_server(content=content, support_range=True) as (url, handler):
            target = Path(tmp) / "video.bin"

            path = await download(
                url,
                target,
                progress=progress,
                connections=4,
                min_split_size=512,
                chunk_size=128,
            )

            self.assertEqual(Path(path), target)
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(progresses[-1], (len(content), len(content)))
            range_gets = [range_header for method, range_header in handler.requests if method == "GET" and range_header]
            self.assertGreaterEqual(len(range_gets), 2)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_download_falls_back_to_single_request_when_range_is_ignored(self):
        content = b"fallback-body" * 100

        with TemporaryDirectory() as tmp, range_server(content=content, support_range=False) as (url, handler):
            target = Path(tmp) / "image.bin"

            await download(url, target, connections=4, min_split_size=10, chunk_size=32)

            self.assertEqual(target.read_bytes(), content)
            self.assertIn(("GET", "bytes=0-0"), handler.requests)
            self.assertIn(("GET", None), handler.requests)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_download_keeps_existing_file_when_request_fails(self):
        with TemporaryDirectory() as tmp, range_server(content=b"new", support_range=True, fail_all=True) as (url, _):
            target = Path(tmp) / "video.bin"
            target.write_bytes(b"old")

            with self.assertRaises(DownloadError) as caught:
                await download(url, target, connections=4, max_retries=0)

            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))
            # 普通网络失败不应被误标为“远端文件已变化”
            self.assertNotIn("远端文件已变化", caught.exception.message)

    async def test_connections_one_uses_single_request(self):
        content = b"single" * 200

        with TemporaryDirectory() as tmp, range_server(content=content, support_range=True) as (url, handler):
            target = Path(tmp) / "single.bin"

            await download(url, target, connections=1, min_split_size=10)

            self.assertEqual(target.read_bytes(), content)
            range_gets = [range_header for method, range_header in handler.requests if method == "GET" and range_header]
            self.assertEqual(range_gets, [])

    async def test_falls_back_to_single_when_content_length_changes_midway(self):
        old_content = b"A" * 4096
        new_content = b"B" * 6144

        with TemporaryDirectory() as tmp, range_server(
            content=old_content, mutate_on_first_part=new_content
        ) as (url, handler):
            target = Path(tmp) / "video.bin"

            await download(url, target, connections=4, min_split_size=512, chunk_size=128)

            self.assertEqual(target.read_bytes(), new_content)
            # 分片被丢弃后必须整体回退为一次完整的非 Range GET
            self.assertIn(("GET", None), handler.requests)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_falls_back_to_single_when_etag_changes_midway(self):
        old_content = b"A" * 4096
        new_content = b"B" * 4096
        progresses: list[tuple[int, int]] = []

        async def progress(current: int, total: int) -> None:
            progresses.append((current, total))

        with TemporaryDirectory() as tmp, range_server(
            content=old_content, mutate_on_first_part=new_content
        ) as (url, handler):
            target = Path(tmp) / "video.bin"

            await download(url, target, progress=progress, connections=4, min_split_size=512, chunk_size=128)

            self.assertEqual(target.read_bytes(), new_content)
            self.assertIn(("GET", None), handler.requests)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))
            self.assert_progress_monotonic(progresses)
            self.assertEqual(progresses[-1], (len(new_content), len(new_content)))

    async def test_detects_change_by_last_modified_when_etag_absent(self):
        old_content = b"A" * 4096
        new_content = b"B" * 4096

        with TemporaryDirectory() as tmp, range_server(
            content=old_content, use_etag=False, mutate_on_first_part=new_content
        ) as (url, handler):
            target = Path(tmp) / "video.bin"

            await download(url, target, connections=4, min_split_size=512, chunk_size=128)

            self.assertEqual(target.read_bytes(), new_content)
            self.assertIn(("GET", None), handler.requests)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_merge_stage_recheck_detects_change_after_all_parts(self):
        old_content = b"A" * 4096
        new_content = b"B" * 5120

        with TemporaryDirectory() as tmp, range_server(
            content=old_content, mutate_after_parts=4, mutate_to=new_content
        ) as (url, handler):
            target = Path(tmp) / "video.bin"

            await download(url, target, connections=4, min_split_size=512, chunk_size=128)

            self.assertEqual(target.read_bytes(), new_content)
            self.assertIn(("GET", None), handler.requests)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))

    async def test_change_detected_then_fallback_failure_keeps_old_file_and_reports_change(self):
        old_content = b"old-target"
        remote_content = b"A" * 4096
        changed_content = b"B" * 6144

        with TemporaryDirectory() as tmp, range_server(
            content=remote_content,
            mutate_on_first_part=changed_content,
            fail_single_get=True,
        ) as (url, handler):
            target = Path(tmp) / "video.bin"
            target.write_bytes(old_content)

            with self.assertRaises(DownloadError) as caught:
                await download(url, target, connections=4, min_split_size=512, chunk_size=128, max_retries=0)

            self.assertEqual(target.read_bytes(), old_content)
            self.assertFalse(list(Path(tmp).glob(".*.parsehub-tmp")))
            self.assertIn("远端文件已变化", caught.exception.message)
            self.assertIn(("GET", None), handler.requests)

    @staticmethod
    def assert_progress_monotonic(progresses: list[tuple[int, int]]) -> None:
        currents = [current for current, _ in progresses]
        assert currents == sorted(set(currents)), f"进度回调出现倒退或重复累计: {progresses}"


if __name__ == "__main__":
    unittest.main()
