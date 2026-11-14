"""The wheel and the sdist must never carry a local settings file or secrets.

An earlier release shipped a local ``config.py`` with a real proxy login. These
tests plant such files in a copy of the project, build it, and check the
archives hold only allow-listed files.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLANTED = [
    "gmaps_extractor/config.py",
    "gmaps_extractor/geo/config.py",
    "gmaps_extractor/config_local.py",
    "gmaps_extractor/.env",
    "gmaps_extractor/prod.env",
    ".env",
    "config.py",
]
PLANTED_TEXT = "PROXY_URL = 'http://planted-user:planted-pass@planted-host:1'\n"

ALLOWED_PACKAGE_FILES = re.compile(r"^gmaps_extractor/(__init__|__main__|cli|client|collector|exceptions|output|urls)\.py$"
                                   r"|^gmaps_extractor/py\.typed$"
                                   r"|^gmaps_extractor/geo/(__init__|grid|nominatim)\.py$"
                                   r"|^gmaps_extractor/parsers/(__init__|business|hours|response)\.py$")


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("gmaps_setup", ROOT / "setup.py")
    module = importlib.util.module_from_spec(spec)
    # setup.py calls setup() at import; stub it out for this check.
    import setuptools

    real_setup = setuptools.setup
    setuptools.setup = lambda **kwargs: None
    try:
        spec.loader.exec_module(module)
    finally:
        setuptools.setup = real_setup
    return module


def test_setup_filter_blocks_settings_files():
    setup_module = _load_setup_module()
    for name in ("config.py", "config_local.py", "proxy_config.py", "secrets.py", ".env", ".env.local", "prod.env"):
        assert setup_module.is_blocked(f"gmaps_extractor/{name}"), name
    for name in ("client.py", "collector.py", "urls.py", "__init__.py"):
        assert not setup_module.is_blocked(f"gmaps_extractor/{name}"), name


def test_package_rules_are_explicit():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["gmaps_extractor", "gmaps_extractor.geo", "gmaps_extractor.parsers"]' in text
    assert "include-package-data = false" in text
    assert 'gmaps_extractor = ["py.typed"]' in text
    assert "config" not in text.split("[tool.setuptools.package-data]")[1].split("[")[0]
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "global-exclude config.py" in manifest
    assert "global-exclude .env" in manifest


def test_no_settings_file_in_the_source_tree():
    for pattern in ("config.py", "config_*.py", ".env", "*.env"):
        assert not list(ROOT.joinpath("gmaps_extractor").rglob(pattern)), pattern


def _can_build() -> bool:
    if importlib.util.find_spec("build") is None:
        return False
    try:
        import setuptools

        return int(setuptools.__version__.split(".")[0]) >= 77
    except Exception:
        return False


@pytest.mark.packaging
@pytest.mark.skipif(not _can_build(), reason="needs the 'build' package and setuptools>=77")
def test_built_archives_hold_only_library_files(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(
        ROOT,
        project,
        ignore=shutil.ignore_patterns(".git", "dist", "build", "*.egg-info", "__pycache__", ".pytest_cache", "site"),
    )
    for rel in PLANTED:
        (project / rel).write_text(PLANTED_TEXT, encoding="utf-8")

    out = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--no-isolation", "--outdir", str(out), str(project)],
        check=True,
        capture_output=True,
    )
    (wheel,) = out.glob("*.whl")
    (sdist,) = out.glob("*.tar.gz")

    with zipfile.ZipFile(wheel) as zf:
        wheel_names = zf.namelist()
        wheel_text = b"".join(zf.read(n) for n in wheel_names)
    with tarfile.open(sdist) as tf:
        sdist_members = [m for m in tf.getmembers() if m.isfile()]
        sdist_names = [m.name.split("/", 1)[1] for m in sdist_members]
        sdist_text = b"".join(tf.extractfile(m).read() for m in sdist_members)

    for names in (wheel_names, sdist_names):
        assert not [n for n in names if n.endswith("config.py") or n.endswith(".env") or "/.env" in n], names
    assert b"planted-pass" not in wheel_text
    assert b"planted-pass" not in sdist_text

    package_files = [n for n in wheel_names if not n.startswith("gmaps_extractor-3.0.0.dist-info/")]
    assert package_files and all(ALLOWED_PACKAGE_FILES.match(n) for n in package_files), package_files
    dist_info = {n.split("/", 1)[1] for n in wheel_names if n.startswith("gmaps_extractor-3.0.0.dist-info/")}
    assert dist_info <= {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt", "licenses/LICENSE"}

    allowed_sdist_extra = {
        "PKG-INFO",
        "LICENSE",
        "README.md",
        "CHANGELOG.md",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "MANIFEST.in",
    }
    for name in sdist_names:
        if name.startswith("gmaps_extractor.egg-info/"):
            continue
        assert name in allowed_sdist_extra or ALLOWED_PACKAGE_FILES.match(name), name
