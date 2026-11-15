import unittest

from core.analyzer import JSAnalyzer


class TestProximityWindow(unittest.TestCase):
    def setUp(self):
        self.a = JSAnalyzer(output_dir="/tmp/jsrip_test_out", beautify=False)

    def _types_for(self, content, value):
        return {f["type"] for f in self.a._find_secrets(content, "t.js")
                if f["value"] == value}

    def test_key_labelled_key_not_iv_when_iv_declared_next_line(self):
        """A neighbouring `iv =` must not relabel an AES key as an IV."""
        content = ('const encryptionKey = "0123456789abcdef0123456789abcdef";\n'
                   'const iv = "fedcba9876543210";')
        types = self._types_for(content, "0123456789abcdef0123456789abcdef")
        self.assertIn("aes_hex_key_contextual", types)
        self.assertNotIn("aes_hex_iv_contextual", types)

    def test_iv_still_detected_on_its_own_declaration(self):
        content = 'const iv = "fedcba9876543210";'
        types = self._types_for(content, "fedcba9876543210")
        self.assertIn("aes_hex_iv_contextual", types)

    def test_bare_hex_with_no_nearby_keyword_is_not_reported(self):
        content = ('const sha = '
                   '"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";')
        types = {f["type"] for f in self.a._find_secrets(content, "t.js")}
        self.assertNotIn("aes_hex_key_contextual", types)
        self.assertNotIn("aes_hex_iv_contextual", types)


if __name__ == "__main__":
    unittest.main()
