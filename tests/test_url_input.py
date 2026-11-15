import unittest

from jsrip import normalise_url


class TestNormaliseUrl(unittest.TestCase):
    def test_valid_urls_pass_through_unchanged(self):
        for u in ("https://example.com", "http://example.com/a?b=c",
                  "https://sub.example.co.uk:8443/x"):
            self.assertEqual(normalise_url(u), u)

    def test_scheme_less_host_gets_https(self):
        """The most common CLI typo must not silently scan nothing."""
        self.assertEqual(normalise_url("example.com"), "https://example.com")
        self.assertEqual(normalise_url("example.com/path"), "https://example.com/path")
        self.assertEqual(normalise_url("192.168.1.10:8080"), "https://192.168.1.10:8080")

    def test_surrounding_whitespace_tolerated(self):
        self.assertEqual(normalise_url("  example.com  "), "https://example.com")

    def test_garbage_is_rejected(self):
        for bad in ("not a url", "", "   ", "http://", "https://", "///"):
            with self.subTest(value=bad):
                self.assertIsNone(normalise_url(bad))

    def test_non_http_schemes_rejected(self):
        for bad in ("ftp://example.com", "file:///etc/passwd",
                    "javascript:alert(1)"):
            with self.subTest(value=bad):
                self.assertIsNone(normalise_url(bad))

    def test_hostless_input_rejected(self):
        self.assertIsNone(normalise_url("/just/a/path"))


if __name__ == "__main__":
    unittest.main()
