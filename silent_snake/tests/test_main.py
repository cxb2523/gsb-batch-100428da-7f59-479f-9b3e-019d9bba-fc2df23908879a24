import unittest
import main


class TestMainScript(unittest.TestCase):
    def test_check_url(self):
        # Test with URL missing scheme
        self.assertEqual(main.check_url("example.com/"), "https://example.com")
        # Test with URL having http scheme
        self.assertEqual(main.check_url("http://example.com"), "http://example.com")
        # Test with URL having https scheme
        self.assertEqual(main.check_url("https://example.com"), "https://example.com")

    def test_get_domain(self):
        # Test with URL having http scheme
        self.assertEqual(
            main.get_domain("http://example.com/path"), "http://example.com"
        )
        # Test with URL having https scheme
        self.assertEqual(
            main.get_domain("https://example.com/path"), "https://example.com"
        )

    def test_shortify(self):
        # Test with string longer than 25 characters
        long_str = "This is a very long string that exceeds twenty-five characters."
        self.assertEqual(main.shortify(long_str), "This is a very long strin...")
        # Test with string shorter than 25 characters
        short_str = "Short string"
        self.assertEqual(main.shortify(short_str), "Short string...")

    def test_is_page(self):
        # Test with URL having pagination query parameter
        self.assertTrue(main.is_page("https://example.com/products?page=2"))
        # Test with URL having pagination fragment
        self.assertTrue(main.is_page("https://example.com/products#page=3"))
        # Test with URL without pagination
        self.assertFalse(main.is_page("https://example.com/products"))


class TestScrapeOutcomes(unittest.TestCase):
    """
    scrape() must always report an outcome: (page_data, links, None) on
    success or (None, [], reason) on failure, and keep collecting media.
    """

    @classmethod
    def setUpClass(cls):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        html = (
            b"<html><head><title>T</title>"
            b'<meta name="description" content="d"></head>'
            b'<body><a href="/other">o</a><img src="/m.png"></body></html>'
        )

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/img":
                    body, ctype = b"\x89PNG fake", "image/png"
                else:
                    body, ctype = html, "text/html"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def url(self, path=""):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_success(self):
        media = []
        page_data, links, error = main.scrape(
            self.url(), f"http://127.0.0.1:{self.port}", media
        )
        self.assertIsNone(error)
        self.assertEqual(page_data["Title"], "T")
        self.assertIn(self.url("/other"), links)
        self.assertIn(self.url("/m.png"), media)  # media still collected

    def test_non_html(self):
        page_data, links, error = main.scrape(
            self.url("/img"), f"http://127.0.0.1:{self.port}", []
        )
        self.assertIsNone(page_data)
        self.assertEqual(links, [])
        self.assertEqual(error, main.REASON_NON_HTML)

    def test_connection_refused(self):
        # port 1 is reserved and refuses connections
        page_data, links, error = main.scrape(
            "http://127.0.0.1:1", "http://127.0.0.1:1", []
        )
        self.assertIsNone(page_data)
        self.assertEqual(links, [])
        self.assertEqual(error, main.REASON_CONNECTION)
