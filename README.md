# ExploiterX 3.0 - Professional Web Vulnerability Scanner

[![Tests](https://github.com/anishalx/ExploiterX/workflows/Tests/badge.svg)](https://github.com/anishalx/ExploiterX/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

A professional, enterprise-grade web vulnerability scanner for security professionals and developers. Detects XSS, SQL Injection, CSRF, XXE, Path Traversal, Open Redirect, Command Injection, and security misconfigurations with advanced scanning capabilities.

## 🎯 Features

### Vulnerability Detection
- **XSS (Cross-Site Scripting)**: 20+ payload variants including DOM-based, event handlers, and encoding bypasses — detects raw, HTML-encoded, and URL-encoded reflections
- **SQL Injection**: Multiple payload types (boolean-based, UNION-based, error-based) plus **time-based blind detection** using response timing
- **XXE (XML External Entity)**: 10+ XXE payload variants including blind XXE and file read attempts
- **Path Traversal**: Directory traversal detection with Unix, Windows, and encoded payloads
- **Open Redirect**: Redirect payloads with reflection and cross-host validation (no false positives on benign same-host redirects)
- **Command Injection**: Output-pattern detection plus time-based detection for blind command execution
- **CSRF Vulnerabilities**: CSRF token detection and missing token identification
- **Security Headers & Cookies**: Missing header analysis plus weak CSP / X-Frame-Options checks and cookie flag analysis (HttpOnly, Secure, SameSite)

### Advanced Capabilities
- **Command-Line Interface**: Fully scriptable non-interactive scans (`--url`, `--format`, `--output`, `--threads`, and more)
- **Concurrent Scanning**: Thread pool-based parallel scanning (up to 50 threads)
- **Web Crawling**: Iterative breadth-first crawler with depth/page limits and O(1) duplicate detection
- **Form Extraction**: Intelligent HTML form parsing covering `input`, `textarea`, and `select` fields
- **Seed URL Scanning**: The entry page is always scanned, so homepage forms are never missed
- **Authentication Support**: Login handling with masked password input (secure `getpass` on TTYs)
- **Multiple Report Formats**: JSON, CSV, and professional HTML reports with safe escaping
- **Comprehensive Logging**: File and console logging with configurable levels
- **Configuration Management**: YAML-based configuration with CLI overrides
- **Resilient Requests**: Built-in retry handling for transient timeouts and connection issues, plus optional politeness delay
- **Smart Parameter Injection**: Payloads are injected into existing query parameters to reduce malformed test URLs
- **Header Validation Fallback**: Security-header checks automatically fall back from `HEAD` to `GET` when needed

### Professional Features
- **Type Hints**: Full type annotation for better IDE support
- **Error Handling**: Robust exception handling with graceful degradation
- **Input Validation**: URL validation with RFC compliance
- **Thread-Safe**: Proper synchronization for concurrent operations
- **Duplicate Finding Protection**: Deduplicates repeated findings for cleaner reports
- **Report Security**: HTML reports escape payloads (no self-XSS) and CSV reports neutralize spreadsheet formula injection
- **87%+ Test Coverage**: Comprehensive unit and integration tests (250 test cases)

## 📋 Requirements

- Python 3.8 or higher
- Linux, macOS, or Windows

### Dependencies
```
requests>=2.31.0
beautifulsoup4>=4.12.2
lxml>=5.1.0
pyyaml>=6.0.1
jinja2>=3.1.2
colorama>=0.4.6
```

## 🚀 Installation

### Via pip (Recommended)
```bash
pip install ExploiterX
exploiterx
```

### From Source
```bash
git clone https://github.com/anishalx/ExploiterX.git
cd ExploiterX
pip install -r requirements.txt
python exploiterX.py
```

### Development Installation
```bash
git clone https://github.com/anishalx/ExploiterX.git
cd ExploiterX
pip install -e ".[dev]"
pytest tests/ --cov=.
```

## 🐳 Docker Deployment

### Quick Start with Docker Compose
```bash
# Clone and start with vulnerable test environments
git clone https://github.com/anishalx/ExploiterX.git
cd ExploiterX

# Start ExploiterX with DVWA and Juice Shop
docker-compose up -d

# Run a scan against DVWA
docker-compose exec exploiterx python exploiterX.py

# Access shell
docker-compose exec exploiterx bash

# Stop all services
docker-compose down
```

### Standalone Docker Image
```bash
# Build the image
docker build -t exploiterx:latest .

# Run a scan with interactive mode
docker run -it \
  -v $(pwd)/reports:/app/reports \
  exploiterx:latest

# Run against external target
docker run -it \
  -v $(pwd)/reports:/app/reports \
  exploiterx:latest --target http://target.com
```

**📖 For detailed Docker documentation, see [DOCKER.md](./DOCKER.md)**

## 💻 Usage

### Basic Usage
```bash
python exploiterX.py
```

### Non-Interactive Mode (CLI)
```bash
# One-shot scan with JSON + HTML reports
python exploiterX.py --url http://target.com --format json --format html --output reports/scan

# Full options
python exploiterX.py --url http://target.com \
    --ignore /logout /admin \
    --threads 10 \
    --max-depth 3 --max-links 200 \
    --delay 0.5 \
    --auth-url http://target.com/login --auth-username admin \
    --format json --format csv --format html \
    --output reports/scan

# Authenticated scan (password is prompted securely on a TTY)
python exploiterX.py --url http://target.com --auth-url http://target.com/login --auth-username admin
```

### Interactive Mode
The tool will prompt you for:
1. **Target URL**: The URL to scan (e.g., http://example.com)
2. **Ignore Links**: URLs to exclude from scanning (optional)
3. **Authentication**: Login credentials if required (optional, password input is masked)

### Example Session
```
╔════════════════════════════════════════╗
║ 🔍 Professional Web Vulnerability      ║
║    Scanner v2.0                         ║
╚════════════════════════════════════════╝

Enter the target URL: http://vulnerable-app.local
Ignore certain links during scan? (y/n): y
Enter URLs to ignore: /logout, /admin

[*] Initializing scanner...
[*] Starting web crawl...
[+] Discovered: http://vulnerable-app.local/search
[+] Discovered: http://vulnerable-app.local/user
[+] Discovered: http://vulnerable-app.local/api/get_user

[*] Scanning 3 endpoints...
[*] [1/3] Scanning: http://vulnerable-app.local/search?q=test
  [+] Testing form 1
  [***] XSS VULNERABILITY FOUND in URL parameters
[*] [2/3] Scanning: http://vulnerable-app.local/user?id=1
  [***] SQL INJECTION VULNERABILITY FOUND in URL parameters

SCAN SUMMARY
============
Target: http://vulnerable-app.local
Endpoints Discovered: 3
Vulnerabilities Found: 2
```

## 🎬 Demo

### Crawling the Target
<p align="center"><img src="https://github.com/user-attachments/assets/cc7ca93f-2199-4e69-a66c-176795fa17c1" width="50%" height="auto"/></p>

### Crawling if Target Has Login Function
<p align="center"><img src="https://github.com/user-attachments/assets/40ea506b-83dd-4c8f-9566-ba8f9dd150b8" width="50%" height="auto"/></p>

### Video Demo
<p align="center">
  <a href="https://www.youtube.com/watch?v=P-AxI8wZUjk">
    <img src="https://img.youtube.com/vi/P-AxI8wZUjk/maxresdefault.jpg" alt="Watch the video" width="600" height="auto">
  </a>
</p>

## 📊 Report Formats

### JSON Report
```bash
# Automatically saved as: reports/scan_2024-01-15_120530.json
```

Example JSON structure:
```json
{
  "report": {
    "generated_at": "2024-01-15T12:05:30",
    "tool": "ExploiterX",
    "version": "2.0.0"
  },
  "summary": {
    "total_vulnerabilities": 2,
    "severity_distribution": {
      "critical": 1,
      "high": 1,
      "medium": 0
    }
  },
  "vulnerabilities": [
    {
      "type": "XSS",
      "url": "http://example.com/search",
      "severity": "high",
      "payload": "<script>alert('XSS')</script>"
    }
  ]
}
```

### CSV Report
Clean, spreadsheet-friendly format:
```
ID,Type,Severity,URL,Method,Parameter,HTTP Status,Payload,Timestamp
1,XSS,HIGH,http://example.com/search,GET,q,200,<script>alert('XSS')</script>,2024-01-15T12:05:30
```

### HTML Report
Professional interactive report with:
- Executive summary with risk scores
- Severity distribution charts
- Detailed vulnerability listings
- Color-coded severity indicators
- Scan metadata and timeline

## ⚙️ Configuration

### config/config.yaml
Customize scanner behavior:

```yaml
# Scanning Configuration
timeout: 10              # Request timeout (1-300 seconds)
threads: 5              # Concurrent threads (1-50)
retries: 3              # Retry failed requests (0-10)

# Logging
log_level: INFO         # DEBUG, INFO, WARNING, ERROR, CRITICAL
log_file: exploiterx.log

# Crawl / request tuning
time_based_threshold: 3.0   # Min. delay (s) for time-based blind detection
delay_between_requests: 0.5 # Seconds between requests (0 to disable)
max_depth: 10               # Max crawl depth
max_links: 0                # Max links to crawl (0 = unlimited)

# Ignore patterns
ignore_links:
  - /logout
  - /admin
  - /settings/account

# Authentication (optional)
# auth_required: true
# auth_url: http://example.com/login
# auth_username: user@example.com
# auth_password: password123
```

### Command-Line Arguments

| Flag | Description |
| --- | --- |
| `-u, --url` | Target URL (enables non-interactive mode) |
| `--ignore` | URL patterns to ignore |
| `--auth-url`, `--auth-username`, `--auth-password` | Authentication for logged-in scans |
| `-t, --threads` | Concurrent scan threads (1-50) |
| `--timeout` | HTTP request timeout in seconds |
| `--retries` | Retries for transient failures |
| `--max-depth` | Maximum crawl depth |
| `--max-links` | Maximum links to crawl (0 = unlimited) |
| `--delay` | Seconds between requests (rate-limit avoidance) |
| `--time-based-threshold` | Min. delay (s) for time-based blind detection (default 3.0) |
| `-f, --format` | Report format(s): `json`, `csv`, `html`, `console` |
| `-o, --output` | Output file (single format) or directory (multiple formats) |
| `-c, --config` | YAML config file path |
| `--log-level` | Logging verbosity |
| `--no-banner` | Skip the banner |
| `--version` | Show version and exit |

```bash
# Examples
python exploiterX.py --url http://target.com --timeout 20
python exploiterX.py --url http://target.com --config custom_config.yaml
python exploiterX.py --url http://target.com --format json --output scan.json
```

## 🧪 Testing

### Run All Tests
```bash
pytest tests/ -v
```

### Run with Coverage
```bash
pytest tests/ --cov=. --cov-report=html
```

### Run Specific Tests
```bash
pytest tests/test_scanner.py::TestXSSDetection -v
pytest tests/test_models.py -v
pytest tests/test_reporters.py -v
```

### Test Coverage
Current coverage: **87%**
- Scanner: 84%
- Models: 98%
- Reporters: 95%
- Configuration: 82%

## 🏗️ Architecture

```
ExploiterX/
├── exploiterX.py              # Main entry point (interactive + CLI)
├── scanner.py                 # Core scanning engine (sequential + threaded)
├── config/
│   ├── settings.py           # Configuration management
│   ├── logging_config.py     # Logging setup
│   ├── constants.py          # Payloads and constants
│   └── config.yaml           # Default configuration
├── models/
│   ├── vulnerability.py      # Vulnerability data model
│   └── scan_result.py        # Scan result container
├── reporters/
│   ├── base.py              # Base reporter class
│   ├── json_reporter.py     # JSON report generator
│   ├── csv_reporter.py      # CSV report generator
│   └── html_reporter.py     # HTML report generator
├── tests/
│   ├── conftest.py          # Pytest configuration
│   ├── test_scanner.py      # Scanner tests
│   ├── test_models.py       # Model tests
│   ├── test_reporters.py    # Reporter tests
│   └── test_config.py       # Configuration tests
└── requirements.txt
```

## 🔒 Security Features

- **Input Validation**: All user inputs are validated
- **Type Safety**: Full type hints throughout codebase
- **Error Handling**: Comprehensive exception handling
- **Thread Safety**: Proper locking for concurrent operations
- **Timeout Protection**: Configurable request timeouts
- **Secure Defaults**: Safe default configurations

## 🚦 Vulnerability Payloads

### XSS Detection
- Script injection: `<script>alert('XSS')</script>`
- Event handlers: `<img onerror="alert('XSS')">`
- DOM manipulation: `"><script>alert('XSS')</script>`
- Protocol handlers: `<a href="javascript:alert('XSS')">`
- And 16+ more variants

### SQL Injection Detection
- Boolean-based: `' OR '1'='1`
- Time-based: `' OR SLEEP(5)--`
- UNION-based: `' UNION SELECT NULL--`
- Error-based: `' AND extractvalue(...)`
- Stacked queries: `'; DROP TABLE users--`

### CSRF Detection
- Missing CSRF tokens
- Token validation bypass
- Method validation
- Cookie attribute analysis

## 📈 Performance

- **Sequential Scanning**: ~50-100 endpoints/minute
- **Concurrent (5 threads)**: ~150-300 endpoints/minute  
- **Concurrent (10 threads)**: ~250-500 endpoints/minute
- **Memory Usage**: Typical ~50-100MB

Performance depends on:
- Target server response time
- Network latency
- Payload complexity
- Number of forms per page

## 🐛 Known Limitations

### Crawler Limitations
1. **Production Websites**: Production websites often block automated crawlers and render links via JavaScript, which this scanner cannot execute. The crawler may find 0 endpoints on real-world sites.
2. **JavaScript Rendering**: Cannot execute JavaScript - only parses static HTML. Modern websites that render content dynamically won't have their full endpoint list discovered.
3. **Security Controls**: Sites with WAF (Web Application Firewall), bot detection, or rate limiting may block or slow down scanning.

### Payload Testing Limitations
4. **Complex Forms**: Multi-step forms with state management may require manual testing
5. **API Rate Limiting**: Respects server rate limits with timeouts (may miss vulnerabilities on slow-responding sites)
6. **CORS Restrictions**: Cannot test cross-origin endpoints
7. **Advanced Obfuscation**: May miss heavily obfuscated payloads

### Recommended Use Cases
- ✅ **Local vulnerable applications** (DVWA, WebGoat, OWASP Juice Shop)
- ✅ **Authorized penetration testing** on staging/dev environments
- ✅ **Security training** and vulnerability learning
- ❌ **NOT recommended** for production website testing without proper WAF/rate limit handling

## 🔧 Testing with DVWA (Recommended)

### What is DVWA?
**DVWA** (Damn Vulnerable Web Application) is a deliberately vulnerable PHP/MySQL application designed for security professionals to test and improve their vulnerability detection skills.

### Quick Start with Docker

1. **Start DVWA locally**:
```bash
docker run --rm -it -p 80:80 vulnerables/web-dvwa
```

2. **Open in browser** (Windows/macOS):
```
http://localhost/DVWA
```

   **On Linux** (if localhost doesn't work):
   ```bash
   # Find Docker container IP
   docker inspect <container_id> | grep IPAddress
   # Then use: http://<docker_ip>/DVWA
   ```

3. **Login with default credentials**:
   - Username: `admin`
   - Password: `password`

4. **Run ExploiterX** (in another terminal):
```bash
cd ExploiterX
python exploiterX.py
```

5. **Enter DVWA URL when prompted**:
```
Enter the target URL: http://localhost/DVWA
```

### DVWA Setup Details

**Security Level Selection** (in DVWA):
- **Low**: Minimal security (great for learning)
- **Medium**: Some security controls
- **High**: Enhanced security (closer to production)

**Vulnerable Pages to Test**:
- `/DVWA/vulnerabilities/xss_r/` - Reflected XSS
- `/DVWA/vulnerabilities/sqli/` - SQL Injection
- `/DVWA/vulnerabilities/csrf/` - CSRF vulnerabilities
- `/DVWA/vulnerabilities/upload/` - File upload issues

### Alternative Vulnerable Applications

#### OWASP Juice Shop (Modern, Feature-Rich)
```bash
docker run --rm -it -p 3000:3000 bkimminich/juice-shop
# Access at http://localhost:3000
```

#### WebGoat (OWASP Learning Platform)
```bash
docker run --rm -it -p 8080:8080 webgoat/goatandwolf
# Access at http://localhost:8080/WebGoat
```

#### Local Testing Alternatives
```bash
# Test against your own vulnerable application
python exploiterX.py --timeout 20
# Enter: http://localhost:8000 (your local app)
```

### Expected Scanner Output with DVWA

When running against DVWA, you should see:
```
[*] Initializing scanner...
[*] Starting web crawl...
[+] Discovered: http://localhost/DVWA/
[+] Discovered: http://localhost/DVWA/vulnerabilities/
[+] Discovered: http://localhost/DVWA/vulnerabilities/xss_r/
[+] Discovered: http://localhost/DVWA/vulnerabilities/sqli/
...
[*] Scanning 15 endpoints...
[*] [1/15] Scanning: http://localhost/DVWA/vulnerabilities/xss_r/
  [+] Testing form 1
  [***] XSS VULNERABILITY FOUND in URL parameters
[*] [2/15] Scanning: http://localhost/DVWA/vulnerabilities/sqli/
  [***] SQL INJECTION VULNERABILITY FOUND in URL parameters
...
```

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Areas for Contribution
- Additional vulnerability types (XXE, Path Traversal, etc.)
- Performance optimization
- Payload improvements
- Documentation
- Bug fixes
- Test coverage expansion

## 📝 Roadmap

- [x] Add XXE injection detection
- [x] Add Path Traversal detection
- [x] Add Open Redirect detection
- [x] Add Command Injection detection
- [x] Time-based blind SQLi detection
- [x] Cookie flag / weak header analysis
- [ ] Headless browser integration (Playwright/Selenium)
- [ ] API endpoint detection
- [ ] GraphQL query scanning
- [ ] Burp Suite integration
- [ ] OWASP Top 10 mapping
- [ ] REST API for automation
- [ ] Docker containerization

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

**This tool is for authorized security testing only.** Unauthorized access to computer systems is illegal. Always obtain proper authorization before scanning any web application.

The authors assume no liability for misuse or damage caused by this tool.

## 🙏 Acknowledgments

- Original ExploiterX concept
- Open-source security community
- BeautifulSoup and requests maintainers

## 📞 Support

### Getting Help
- **Issues**: Report bugs on [GitHub Issues](https://github.com/anishalx/ExploiterX/issues)
- **Discussions**: Ask questions in [GitHub Discussions](https://github.com/anishalx/ExploiterX/discussions)
- **Documentation**: See [Wiki](https://github.com/anishalx/ExploiterX/wiki)

### Configuration Troubleshooting

**Issue**: Scan hangs on a URL
```bash
# Solution: Increase timeout
python exploiterX.py --timeout 30
```

**Issue**: Too many false positives
```bash
# Solution: Add URLs to ignore list
# Edit config/config.yaml and add patterns to ignore_links
```

**Issue**: Out of memory on large sites
```bash
# Solution: Reduce thread count
# Edit config/config.yaml: threads: 2
```

## 📊 Statistics

- **Lines of Code**: ~4,000
- **Test Cases**: 250
- **Supported Payloads**: 23+ XSS, 21+ SQLi, 10+ XXE, 20+ Path Traversal, 12+ Open Redirect, 19+ Command Injection
- **Python Versions**: 3.8, 3.9, 3.10, 3.11, 3.12, 3.13
- **Operating Systems**: Windows, macOS, Linux

## 🔄 Version History

### v3.0.0 (2026-08-16)
- **NEW**: Command-line interface with non-interactive scanning (`--url`, `--format`, `--output`, `--threads`, etc.)
- **NEW**: Open Redirect detection engine
- **NEW**: Command Injection detection engine (output-pattern + time-based)
- **NEW**: Time-based blind SQL injection detection
- **NEW**: Cookie flag analysis (HttpOnly, Secure, SameSite) and weak CSP / X-Frame-Options checks
- **NEW**: Encoded XSS reflection detection (HTML-escaped payloads now caught)
- **NEW**: Seed URL is always scanned so entry-page forms are never missed
- **NEW**: `--time-based-threshold` CLI flag and `time_based_threshold` config key for tuning blind-detection timing
- **CLEANUP**: Removed the unused `concurrent_scanner.py` module (parallelism now lives in `run_scanner(threads=…)`)
- **IMPROVED**: Crawler rewritten as iterative BFS (no recursion limits, O(1) dedup, `--max-links` cap)
- **IMPROVED**: Form submission handles `textarea` and `select` fields
- **IMPROVED**: Config file now supports max_depth, max_links, delay, output_format, user_agent, SSL/redirect toggles, and auth block
- **FIXED**: HTML reports escaped payloads/URLs (prevented report self-XSS)
- **FIXED**: CSV reports neutralize spreadsheet formula injection
- **FIXED**: Password input masked via getpass on TTYs
- **FIXED**: XXE/Path Traversal false positives when the page reflects the payload
- **FIXED**: Banner rendering on Windows consoles with non-UTF-8 codepages
- **ADDED**: 57 new test cases (250 total, 87% coverage)

### v2.1.0 (2026-05-05)
- **HARDENED**: URL payload injection now targets existing parameters instead of appending malformed test params
- **HARDENED**: Added bounded request retries for transient network failures
- **HARDENED**: Security-header analysis now falls back from `HEAD` to `GET` on unsupported targets
- **FIXED**: Parallel form scanning now correctly passes custom payloads into XSS form tests
- **FIXED**: Concurrent batch stats now reset per run for accurate reporting
- **ADDED**: Regression tests for retry behavior, header fallback, concurrency stats, and payload wiring

### v2.0.1 (2024-01-16)
- **FIXED**: Windows CI/CD lxml build failure (updated to lxml>=5.1.0)
- **IMPROVED**: Crawler error handling with helpful feedback for production sites
- **ADDED**: DVWA testing guide and documentation
- **ADDED**: Known limitations section with recommended use cases
- **ADDED**: Support for Python 3.12, 3.13
- **IMPROVED**: Crawler warnings when no endpoints found

### v2.0.0 (2024-01-15)
- **NEW**: Multiple vulnerability detection (XSS, SQLi, CSRF, Headers)
- **NEW**: Concurrent scanning with thread pool
- **NEW**: Multiple report formats (JSON, CSV, HTML)
- **NEW**: Comprehensive configuration system
- **NEW**: Type hints throughout codebase
- **IMPROVED**: Error handling and logging
- **IMPROVED**: Performance with concurrent operations
- **ADDED**: 150+ test cases (80%+ coverage)
- **ADDED**: Professional HTML reports

### v1.0.0 (2023)
- Initial release
- Basic XSS detection
- Web crawling
- Form extraction

---

**Made with ❤️ for the security community**
