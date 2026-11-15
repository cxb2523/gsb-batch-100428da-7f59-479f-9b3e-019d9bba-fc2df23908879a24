"""Test-only bootstrap.

The project depends on the third-party ``kafka-python`` package (imported as
``kafka``) and on ``pika``. Some CI/offline environments do not have those
packages installed and cannot reach a package index. To keep the Kafka retry
tests runnable with *local mocks only* (no real Kafka service, no extra
dependency), tiny in-repo stubs of the handful of ``kafka``/``pika`` symbols
imported by the project are placed under ``tests/_vendor_stubs`` and put on
``sys.path`` here, but only for whichever of the real packages is not
importable.

This file never affects production code: it lives inside the test package and
yields immediately whenever the genuine dependency is available.
"""
import importlib.util
import os
import sys

if (
    importlib.util.find_spec("kafka") is None
    or importlib.util.find_spec("pika") is None
):
    _stubs = os.path.join(os.path.dirname(__file__), "_vendor_stubs")
    if _stubs not in sys.path:
        sys.path.insert(0, _stubs)
