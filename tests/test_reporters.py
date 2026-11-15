"""Unit tests for report generators."""

import pytest
import json
import os
from tempfile import NamedTemporaryFile
from models.scan_result import ScanResult
from models.vulnerability import Vulnerability, VulnerabilityType, Severity
from reporters.json_reporter import JSONReporter
from reporters.csv_reporter import CSVReporter
from reporters.html_reporter import HTMLReporter


@pytest.fixture
def sample_scan_result():
    """Create sample scan result for testing."""
    result = ScanResult("http://example.com")
    result.endpoints_discovered = 10
    result.endpoints_scanned = 10
    
    result.add_vulnerability(Vulnerability(
        VulnerabilityType.XSS,
        "http://example.com/search",
        "<script>alert('XSS')</script>",
        Severity.HIGH
    ))
    
    result.add_vulnerability(Vulnerability(
        VulnerabilityType.SQL_INJECTION,
        "http://example.com/user",
        "' OR '1'='1",
        Severity.CRITICAL
    ))
    
    result.finish()
    return result


class TestJSONReporter:
    """Test JSON report generation."""
    
    def test_json_report_generation(self, sample_scan_result):
        """Test JSON report generation."""
        reporter = JSONReporter(sample_scan_result)
        report = reporter.generate()
        
        assert isinstance(report, str)
        
        # Parse JSON
        data = json.loads(report)
        
        assert 'report' in data
        assert 'scan' in data
        assert 'summary' in data
        assert 'vulnerabilities' in data
    
    def test_json_report_contains_vulnerabilities(self, sample_scan_result):
        """Test JSON report contains vulnerability data."""
        reporter = JSONReporter(sample_scan_result)
        report = reporter.generate()
        
        data = json.loads(report)
        
        assert len(data['vulnerabilities']) == 2
        assert data['summary']['total_vulnerabilities'] == 2
    
    def test_json_report_save(self, sample_scan_result):
        """Test JSON report saving."""
        with NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            output_file = f.name
        
        try:
            reporter = JSONReporter(sample_scan_result, output_file)
            saved_path = reporter.save()
            
            assert os.path.exists(saved_path)
            
            # Verify content
            with open(saved_path, 'r') as f:
                data = json.load(f)
                assert 'vulnerabilities' in data
        finally:
            if os.path.exists(output_file):
                os.remove(output_file)
    
    def test_json_pretty_print(self, sample_scan_result):
        """Test JSON pretty printing."""
        reporter = JSONReporter(sample_scan_result, pretty=True)
        report = reporter.generate()
        
        assert '\n' in report  # Pretty printed has newlines


class TestCSVReporter:
    """Test CSV report generation."""
    
    def test_csv_report_generation(self, sample_scan_result):
        """Test CSV report generation."""
        reporter = CSVReporter(sample_scan_result)
        report = reporter.generate()
        
        assert isinstance(report, str)
        assert "ID,Type,Severity" in report
        assert "XSS" in report
    
    def test_csv_report_contains_data(self, sample_scan_result):
        """Test CSV report contains vulnerability data."""
        reporter = CSVReporter(sample_scan_result)
        report = reporter.generate()
        
        lines = report.split('\n')
        
        # Should have header + vulnerabilities
        assert any('XSS' in line for line in lines)
        assert any('CRITICAL' in line or 'HIGH' in line for line in lines)
    
    def test_csv_report_save(self, sample_scan_result):
        """Test CSV report saving."""
        with NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            output_file = f.name
        
        try:
            reporter = CSVReporter(sample_scan_result, output_file)
            saved_path = reporter.save()
            
            assert os.path.exists(saved_path)
            
            # Verify content
            with open(saved_path, 'r') as f:
                content = f.read()
                assert 'XSS' in content
        finally:
            if os.path.exists(output_file):
                os.remove(output_file)


class TestReporterSecurity:
    """Security hardening tests for reporters."""
    
    def test_html_report_escapes_payloads(self, sample_scan_result):
        """HTML reports must escape attacker-controlled payloads (no self-XSS)."""
        result = ScanResult("http://example.com")
        result.add_vulnerability(Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com/search",
            "<script>alert(1)</script>",
            Severity.HIGH,
            parameter='"onmouseover="alert(1)'
        ))
        result.finish()
        
        reporter = HTMLReporter(result)
        report = reporter.generate()
        
        # The raw payload must NOT appear as executable HTML
        assert "<script>alert(1)</script>" not in report
        # The escaped payload must be present
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
        # Quote characters in attribute values must be escaped
        assert 'onmouseover="alert' not in report
    
    def test_csv_report_neutralizes_formula_injection(self, sample_scan_result):
        """CSV cells starting with =, +, - or @ must be neutralized."""
        result = ScanResult("http://example.com")
        result.add_vulnerability(Vulnerability(
            VulnerabilityType.SQL_INJECTION,
            "http://example.com/user",
            "=HYPERLINK(\"http://evil.com\")",
            Severity.CRITICAL
        ))
        result.add_vulnerability(Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com/search",
            "-2+3",
            Severity.HIGH,
            parameter="@SUM(A1:A9)"
        ))
        result.finish()
        
        reporter = CSVReporter(result)
        report = reporter.generate()
        
        assert "'=HYPERLINK" in report
        assert "'-2+3" in report
        assert "'@SUM" in report
        # Raw formulas must not appear unguarded at line starts of cells
        assert "\n=HYPERLINK" not in report
    
    def test_csv_report_keeps_normal_cells_unchanged(self, sample_scan_result):
        """Ordinary values must not be prefixed."""
        reporter = CSVReporter(sample_scan_result)
        report = reporter.generate()
        
        assert "http://example.com/search" in report
        assert "<script>alert('XSS')</script>" in report


class TestHTMLReporter:
    """Test HTML report generation."""
    
    def test_html_report_generation(self, sample_scan_result):
        """Test HTML report generation."""
        reporter = HTMLReporter(sample_scan_result)
        report = reporter.generate()
        
        assert isinstance(report, str)
        assert '<html' in report.lower()
        assert '</html>' in report.lower()
    
    def test_html_report_contains_title(self, sample_scan_result):
        """Test HTML report contains title."""
        reporter = HTMLReporter(sample_scan_result)
        report = reporter.generate()
        
        assert 'ExploiterX' in report
        assert 'Vulnerability Report' in report
    
    def test_html_report_contains_vulnerabilities(self, sample_scan_result):
        """Test HTML report contains vulnerability data."""
        reporter = HTMLReporter(sample_scan_result)
        report = reporter.generate()
        
        assert 'XSS' in report
        assert 'SQL_INJECTION' in report or 'SQL Injection' in report
        assert 'http://example.com' in report
    
    def test_html_report_save(self, sample_scan_result):
        """Test HTML report saving."""
        with NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            output_file = f.name
        
        try:
            reporter = HTMLReporter(sample_scan_result, output_file)
            saved_path = reporter.save()
            
            assert os.path.exists(saved_path)
            
            # Verify content
            with open(saved_path, 'r', encoding='utf-8') as f:
                content = f.read()
                assert 'ExploiterX' in content
        finally:
            if os.path.exists(output_file):
                os.remove(output_file)
    
    def test_html_severity_colors(self, sample_scan_result):
        """Test HTML report severity coloring."""
        reporter = HTMLReporter(sample_scan_result)
        report = reporter.generate()
        
        # Check color codes are present
        assert '#dc3545' in report  # Critical
        assert '#fd7e14' in report or '#ffc107' in report  # High/Medium
