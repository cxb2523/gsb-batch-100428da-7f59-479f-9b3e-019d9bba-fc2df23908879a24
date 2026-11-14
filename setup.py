"""Build hook: keep local settings files out of every wheel and sdist.

All metadata lives in pyproject.toml. This file only filters the modules that
setuptools would otherwise pick up from the package folders.
"""

import fnmatch
import os

from setuptools import setup
from setuptools.command.build_py import build_py

# File names that must never be packaged, even if someone drops one into the
# package folder (a local proxy login once leaked that way).
BLOCKED = ("config.py", "config_*.py", "*_config.py", "settings_local.py", "secrets*.py", ".env", ".env.*", "*.env")


def is_blocked(path: str) -> bool:
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, pattern) for pattern in BLOCKED)


class SafeBuildPy(build_py):
    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        return [m for m in modules if not is_blocked(m[2])]


setup(cmdclass={"build_py": SafeBuildPy})
