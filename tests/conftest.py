import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shortvideocrawl"))


@pytest.fixture(autouse=True)
def capture_spider_logs(caplog):
    caplog.set_level(logging.INFO)
    return caplog
