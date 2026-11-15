"""Unit tests for data models."""

import pytest
from models.vulnerability import Vulnerability, VulnerabilityType, Severity
from models.scan_result import ScanResult
from datetime import datetime


class TestVulnerability:
    """Test Vulnerability model."""
    
    def test_vulnerability_creation(self):
        """Test vulnerability creation."""
        vuln = Vulnerability(
            vuln_type=VulnerabilityType.XSS,
            url="http://example.com/search",
            payload="<script>alert('XSS')</script>",
            severity=Severity.HIGH
        )
        
        assert vuln.vuln_type == VulnerabilityType.XSS
        assert vuln.severity == Severity.HIGH
        assert vuln.url == "http://example.com/search"
    
    def test_vulnerability_invalid_severity(self):
        """Test vulnerability with invalid severity."""
        with pytest.raises(ValueError):
            Vulnerability(
                vuln_type=VulnerabilityType.XSS,
                url="http://example.com",
                payload="test",
                severity="invalid"
            )
    
    def test_vulnerability_invalid_type(self):
        """Test vulnerability with invalid type."""
        with pytest.raises(ValueError):
            Vulnerability(
                vuln_type="INVALID",
                url="http://example.com",
                payload="test"
            )
    
    def test_vulnerability_severity_score(self):
        """Test vulnerability severity scoring."""
        vuln_critical = Vulnerability(
            VulnerabilityType.SQL_INJECTION,
            "http://example.com",
            "test",
            severity=Severity.CRITICAL
        )
        
        vuln_low = Vulnerability(
            VulnerabilityType.INSECURE_HEADERS,
            "http://example.com",
            "test",
            severity=Severity.LOW
        )
        
        assert vuln_critical.severity_score == 9.0
        assert vuln_low.severity_score == 3.0
    
    def test_vulnerability_to_dict(self):
        """Test vulnerability to_dict conversion."""
        vuln = Vulnerability(
            vuln_type=VulnerabilityType.XSS,
            url="http://example.com",
            payload="<script>alert('XSS')</script>",
            severity=Severity.HIGH,
            method="GET",
            parameter="q",
            response_code=200
        )
        
        vuln_dict = vuln.to_dict()
        
        assert vuln_dict['type'] == VulnerabilityType.XSS
        assert vuln_dict['severity'] == Severity.HIGH
        assert vuln_dict['url'] == "http://example.com"
        assert vuln_dict['method'] == "GET"
    
    def test_vulnerability_to_json(self):
        """Test vulnerability to_json conversion."""
        vuln = Vulnerability(
            vuln_type=VulnerabilityType.XSS,
            url="http://example.com",
            payload="test",
            severity=Severity.MEDIUM
        )
        
        json_str = vuln.to_json()
        assert isinstance(json_str, str)
        assert "XSS" in json_str
    
    def test_vulnerability_is_critical(self):
        """Test is_critical check."""
        vuln_critical = Vulnerability(
            VulnerabilityType.SQL_INJECTION,
            "http://example.com",
            "test",
            severity=Severity.CRITICAL
        )
        
        vuln_low = Vulnerability(
            VulnerabilityType.INSECURE_HEADERS,
            "http://example.com",
            "test",
            severity=Severity.LOW
        )
        
        assert vuln_critical.is_critical() is True
        assert vuln_low.is_critical() is False
    
    def test_vulnerability_get_remediation(self):
        """Test remediation advice."""
        vuln = Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com",
            "test"
        )
        
        remediation = vuln.get_remediation()
        assert isinstance(remediation, str)
        assert len(remediation) > 0
        assert "input validation" in remediation.lower()


class TestScanResult:
    """Test ScanResult model."""
    
    def test_scan_result_creation(self):
        """Test scan result creation."""
        result = ScanResult("http://example.com")
        
        assert result.target_url == "http://example.com"
        assert len(result.vulnerabilities) == 0
    
    def test_add_vulnerability(self):
        """Test adding vulnerability to result."""
        result = ScanResult("http://example.com")
        vuln = Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com",
            "test"
        )
        
        result.add_vulnerability(vuln)
        
        assert len(result.vulnerabilities) == 1
    
    def test_add_invalid_vulnerability(self):
        """Test adding invalid vulnerability."""
        result = ScanResult("http://example.com")
        
        with pytest.raises(TypeError):
            result.add_vulnerability("not a vulnerability")
    
    def test_get_vulnerabilities_by_type(self):
        """Test filtering by type."""
        result = ScanResult("http://example.com")
        
        xss_vuln = Vulnerability(VulnerabilityType.XSS, "http://example.com", "test")
        sqli_vuln = Vulnerability(VulnerabilityType.SQL_INJECTION, "http://example.com", "test")
        
        result.add_vulnerability(xss_vuln)
        result.add_vulnerability(sqli_vuln)
        
        xss_vulns = result.get_vulnerabilities_by_type(VulnerabilityType.XSS)
        assert len(xss_vulns) == 1
    
    def test_get_vulnerabilities_by_severity(self):
        """Test filtering by severity."""
        result = ScanResult("http://example.com")
        
        critical_vuln = Vulnerability(
            VulnerabilityType.SQL_INJECTION,
            "http://example.com",
            "test",
            severity=Severity.CRITICAL
        )
        low_vuln = Vulnerability(
            VulnerabilityType.INSECURE_HEADERS,
            "http://example.com",
            "test",
            severity=Severity.LOW
        )
        
        result.add_vulnerability(critical_vuln)
        result.add_vulnerability(low_vuln)
        
        critical_vulns = result.get_critical_vulnerabilities()
        assert len(critical_vulns) == 1
    
    def test_count_by_type(self):
        """Test counting vulnerabilities by type."""
        result = ScanResult("http://example.com")
        
        result.add_vulnerability(Vulnerability(VulnerabilityType.XSS, "http://example.com", "test"))
        result.add_vulnerability(Vulnerability(VulnerabilityType.XSS, "http://example.com", "test"))
        result.add_vulnerability(Vulnerability(VulnerabilityType.SQL_INJECTION, "http://example.com", "test"))
        
        counts = result.count_by_type()
        
        assert counts[VulnerabilityType.XSS] == 2
        assert counts[VulnerabilityType.SQL_INJECTION] == 1
    
    def test_count_by_severity(self):
        """Test counting vulnerabilities by severity."""
        result = ScanResult("http://example.com")
        
        result.add_vulnerability(Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com",
            "test",
            severity=Severity.HIGH
        ))
        result.add_vulnerability(Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com",
            "test",
            severity=Severity.HIGH
        ))
        
        counts = result.count_by_severity()
        
        assert counts[Severity.HIGH] == 2
    
    def test_get_summary(self):
        """Test getting summary."""
        result = ScanResult("http://example.com")
        result.endpoints_discovered = 10
        result.endpoints_scanned = 10
        
        result.add_vulnerability(Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com",
            "test",
            severity=Severity.HIGH
        ))
        
        summary = result.get_summary()
        
        assert "http://example.com" in summary
        assert "10" in summary
        assert "XSS" in summary
    
    def test_scan_result_finish(self, monkeypatch):
        """Test marking scan complete."""
        # Patch the module-level datetime used by finish() so the wall clock
        # cannot race (datetime.now() has coarse resolution on Windows, which
        # made scan_duration flakily 0.0 in CI).
        import models.scan_result as scan_result_mod

        fake_times = iter([
            datetime(2024, 1, 1, 12, 0, 0),   # finish() end_time
        ])

        class _FakeDatetime:
            @classmethod
            def now(cls):
                return next(fake_times)

        monkeypatch.setattr(scan_result_mod, "datetime", _FakeDatetime)

        result = ScanResult("http://example.com")
        result.start_time = datetime(2024, 1, 1, 11, 59, 57)
        result.finish()

        assert result.end_time is not None
        assert result.scan_duration == 3.0
        assert result.end_time == datetime(2024, 1, 1, 12, 0, 0)
    
    def test_scan_result_to_dict(self):
        """Test scan result to_dict."""
        result = ScanResult("http://example.com")
        result.endpoints_discovered = 5
        result.endpoints_scanned = 5
        result.add_vulnerability(Vulnerability(
            VulnerabilityType.XSS,
            "http://example.com",
            "test"
        ))
        
        result_dict = result.to_dict()
        
        assert result_dict['target_url'] == "http://example.com"
        assert result_dict['total_vulnerabilities'] == 1
        assert 'vulnerabilities' in result_dict
