"""Models package for ExploiterX data structures."""

from .vulnerability import Vulnerability, VulnerabilityType
from .scan_result import ScanResult

__all__ = [
    'Vulnerability',
    'VulnerabilityType',
    'ScanResult'
]
