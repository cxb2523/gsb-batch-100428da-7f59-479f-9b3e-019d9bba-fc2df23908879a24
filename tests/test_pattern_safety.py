import random
import re
import time
import unittest

from core.analyzer import JSAnalyzer
from core.patterns import patterns


def svg_icon_js(segments=800):
    """A JS module exporting an inline SVG icon - ubiquitous in real bundles.

    The path data is a long run of digits, dots and hyphens, which slips past
    the base64 noise stripper and reaches the raw pattern set.
    """
    random.seed(7)
    path = "".join(
        f"M{random.randint(0,99)}.{random.randint(0,99)}"
        f"-{random.randint(0,99)}.{random.randint(0,99)}"
        for _ in range(segments)
    )
    return f'export const icon = \'<svg viewBox="0 0 24 24"><path d="{path}"/></svg>\';'


class TestNoCatastrophicBacktracking(unittest.TestCase):
    def test_inline_svg_icon_analyses_quickly(self):
        a = JSAnalyzer(output_dir="/tmp/jsrip_safety", beautify=False)
        content = svg_icon_js()
        t0 = time.time()
        a._find_secrets(content, "icon.js")
        elapsed = time.time() - t0
        self.assertLess(
            elapsed, 2.0,
            f"pattern scan took {elapsed:.1f}s on a {len(content)}-char inline SVG "
            f"- unbounded leading quantifiers are backtracking")

    def test_no_pattern_has_an_unbounded_leading_class(self):
        """`[...]+ ` at the start scans quadratically over long tokens."""
        bad = re.compile(r'^\[[^\]]+\][+*](?![?+])')
        offenders = sorted(n for n, p in patterns.items()
                           if isinstance(p, str) and bad.match(p))
        self.assertEqual(offenders, [], f"unbounded leading quantifier in: {offenders}")


class TestPatternCorrectness(unittest.TestCase):
    def test_aws_host_patterns_escape_their_dots(self):
        for name in ("aws_rds", "aws_elb", "aws_elasticcache",
                     "aws_ec2_internal", "aws_api_gateway"):
            with self.subTest(pattern=name):
                self.assertNotRegex(
                    "hostXrdsYamazonawsZcom".replace("rds", name.split("_")[-1]),
                    patterns[name],
                    f"{name} has unescaped dots - matches any character")

    def test_github_token_pattern_requires_literal_github_com(self):
        rx = patterns["github_access_token_2"]
        self.assertRegex("user:tokenvalue@github.com", rx)
        # trailing `*` on the `m` made "github.co" + m* match
        self.assertNotRegex("user:tokenvalue@github.co", rx)
        self.assertNotRegex("user:tokenvalue@githubXcom", rx)


if __name__ == "__main__":
    unittest.main()
