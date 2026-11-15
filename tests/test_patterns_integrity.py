import ast
import collections
import re
import unittest

from core.patterns import patterns, pattern_contexts


def _dict_keys(name):
    tree = ast.parse(open("core/patterns.py", encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name and isinstance(node.value, ast.Dict):
                    return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    raise AssertionError(f"{name} dict not found")


class TestPatternsIntegrity(unittest.TestCase):
    def test_no_duplicate_pattern_keys(self):
        """A duplicate key silently shadows the earlier pattern."""
        keys = _dict_keys("patterns")
        dupes = sorted(k for k, c in collections.Counter(keys).items() if c > 1)
        self.assertEqual(dupes, [], f"duplicate keys shadow earlier ones: {dupes}")

    def test_openai_legacy_key_matches(self):
        key = "sk-" + "A" * 20 + "T3BlbkFJ" + "B" * 20
        self.assertRegex(key, patterns["openai_api_key"])

    def test_openai_project_key_matches(self):
        self.assertRegex("sk-proj-" + "A" * 60, patterns["openai_api_key"])

    def test_openai_pattern_rejects_generic_48_chars(self):
        self.assertNotRegex("sk-" + "a" * 48, patterns["openai_api_key"])

    def test_square_both_formats_match(self):
        self.assertRegex("sq0atp-" + "A" * 22, patterns["square_access_token"])
        self.assertRegex("EAAA" + "b" * 50, patterns["square_access_token"])

    def test_anthropic_80_char_key_matches(self):
        self.assertRegex("sk-ant-" + "A" * 80, patterns["anthropic_api_key"])


class TestProximityGates(unittest.TestCase):
    def _gate(self, name):
        return re.compile(pattern_contexts[name])

    def test_key_gate_matches_camelcase(self):
        g = self._gate("aes_hex_key_contextual")
        for sample in ("encryptionKey =", "apiKey =", "secretKey =", "cipherKey ="):
            self.assertTrue(g.search(sample), f"gate should match {sample!r}")

    def test_key_gate_matches_snake_and_bare(self):
        g = self._gate("aes_hex_key_contextual")
        for sample in ("x_encryption_Key =", "AES_KEY =", "key =", "password:"):
            self.assertTrue(g.search(sample), f"gate should match {sample!r}")

    def test_key_gate_rejects_words_merely_containing_key(self):
        self.assertFalse(self._gate("aes_hex_key_contextual").search("monkey ="))

    def test_iv_gate_rejects_div(self):
        self.assertFalse(self._gate("aes_hex_iv_contextual").search("div ="))

    def test_iv_gate_matches_camelcase_iv(self):
        self.assertTrue(self._gate("aes_hex_iv_contextual").search("myIV ="))


if __name__ == "__main__":
    unittest.main()
