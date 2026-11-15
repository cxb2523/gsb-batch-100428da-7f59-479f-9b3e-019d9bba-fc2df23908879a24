import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestReadmeDocumentsOutputs(unittest.TestCase):
    def test_readme_lists_every_file_the_reporter_writes(self):
        """The output tree is the first thing a new user reads; a file that
        appears on disk but not in the tree looks like stray output."""
        source = (REPO / "utils" / "reporter.py").read_text()
        written = set(re.findall(r"reports_dir / '([^']+)'", source))
        readme = (REPO / "README.md").read_text()
        self.assertTrue(written, "no report filenames found - regex drifted")
        for name in sorted(written):
            with self.subTest(file=name):
                self.assertIn(name, readme)

if __name__ == "__main__":
    unittest.main()
