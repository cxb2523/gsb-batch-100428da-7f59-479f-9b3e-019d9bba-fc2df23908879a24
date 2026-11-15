"""CSV report generator for ExploiterX."""

import csv
from typing import Optional
from io import StringIO
from .base import BaseReporter
from models.scan_result import ScanResult


class CSVReporter(BaseReporter):
    """Generate CSV format vulnerability reports."""
    
    def __init__(
        self,
        scan_result: ScanResult,
        output_file: Optional[str] = None
    ) -> None:
        """
        Initialize CSV reporter.
        
        Args:
            scan_result: ScanResult object
            output_file: Optional output file path
        """
        super().__init__(scan_result, output_file)
    
    def generate(self) -> str:
        """
        Generate CSV report.
        
        Returns:
            CSV formatted string
        """
        output = StringIO()
        
        # Write summary section as comments
        summary = self.get_summary_stats()
        output.write("# ExploiterX Vulnerability Report (CSV Format)\n")
        output.write(f"# Target: {summary['target']}\n")
        output.write(f"# Endpoints Discovered: {summary['endpoints_discovered']}\n")
        output.write(f"# Endpoints Scanned: {summary['endpoints_scanned']}\n")
        output.write(f"# Total Vulnerabilities: {summary['total_vulnerabilities']}\n")
        output.write(f"# Scan Duration: {summary['scan_duration']}\n")
        output.write("#\n")
        
        # Write vulnerability details
        output.write("ID,Type,Severity,URL,Method,Parameter,HTTP Status,Payload,Timestamp\n")
        
        writer = csv.writer(output)
        
        for idx, vuln in enumerate(self.scan_result.vulnerabilities, 1):
            writer.writerow([
                idx,
                vuln.vuln_type,
                vuln.severity.upper(),
                self._safe_csv(vuln.url),
                vuln.method,
                self._safe_csv(vuln.parameter or "N/A"),
                vuln.response_code or "N/A",
                self._safe_csv(vuln.payload[:50] + "..." if len(vuln.payload) > 50 else vuln.payload),
                vuln.timestamp.isoformat(),
            ])
        
        return output.getvalue()
    
    @staticmethod
    def _safe_csv(value: str) -> str:
        """
        Neutralize spreadsheet formula injection.

        Cells beginning with =, +, -, @ or tab/CR are interpreted as formulas
        by Excel/Sheets. Prefixing with a single quote makes them inert text.
        """
        if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + value
        return value
