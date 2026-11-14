"""Run the command line: ``python -m gmaps_extractor AREA QUERY``."""

import sys

from .cli import main

sys.exit(main())
