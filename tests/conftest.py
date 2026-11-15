import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    # Pinterest.__init__ writes a data/ directory; keep the repository clean.
    monkeypatch.chdir(tmp_path)
