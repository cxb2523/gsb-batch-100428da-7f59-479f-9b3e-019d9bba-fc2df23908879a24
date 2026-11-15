"""
JS Analyzer for jsrip
- Beautifies JS (optional)
- Dedupes files by content before scanning (to save time & reduce dup findings)
- Finds secrets with curated regex patterns + entropy/hash/base64 context filters
- Extracts endpoints and filters to in-scope by default (can include external with a flag)
- Smart analysis: variable assignments, config objects, env leaks, DOM storage
- Three-tier confidence with Shannon entropy scoring
- Outputs structures compatible with jsrip's reporter
"""

import os
import re
import math
import hashlib
from urllib.parse import urlparse
from pathlib import Path
from collections import Counter

try:
    import jsbeautifier
except Exception:
    jsbeautifier = None

from .patterns import patterns, pattern_contexts


# Common placeholder / dummy values that should be filtered out
PLACEHOLDER_PATTERNS = re.compile(
    r'^('
    r'YOUR_?API_?KEY.*|CHANGE_?ME.*|TODO.*|FIXME.*|REPLACE_?ME.*|'
    r'INSERT_?.*_?HERE|PUT_?YOUR_?.*|ENTER_?YOUR_?.*|'
    r'xxx+|XXX+|yyy+|zzz+|aaa+|bbb+|000+|111+|123+|'
    r'test_?key.*|example_?key.*|sample_?key.*|demo_?key.*|fake_?key.*|'
    r'dummy.*|placeholder.*|'
    r'sk_test_.*|pk_test_.*|'
    r'<[A-Z_]+>|'  # <API_KEY> style placeholders
    r'\{\{.*\}\}|'  # {{TEMPLATE}} style
    r'\$\{.*\}|'    # ${VARIABLE} style
    r'process\.env\..*|'
    r'undefined|null|true|false|'
    r'function|return|default|module|exports'
    r')$',
    re.IGNORECASE
)

# Known safe / non-secret strings
FALSE_POSITIVE_VALUES = {
    'application/json', 'application/xml', 'text/html', 'text/plain',
    'text/javascript', 'application/javascript', 'multipart/form-data',
    'application/x-www-form-urlencoded', 'charset=utf-8',
    'content-type', 'authorization', 'accept', 'user-agent',
    'no-cache', 'no-store', 'must-revalidate', 'max-age=0',
    'utf-8', 'ascii', 'latin1', 'iso-8859-1',
    'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD',
    'localhost', 'undefined', 'null', 'true', 'false',
    'none', 'auto', 'inherit', 'initial', 'unset',
}

# CSS color hex patterns (6 or 8 digit)
CSS_HEX_RE = re.compile(r'^#?[0-9a-fA-F]{3,8}$')

# Version strings like 1.2.3, v2.0.0
VERSION_RE = re.compile(r'^v?\d+\.\d+\.\d+([-.]\w+)?$')

# Minified variable names (single letter or very short nonsense)
MINIFIED_VAR_RE = re.compile(r'^[a-zA-Z_$][a-zA-Z0-9_$]{0,3}$')

# camelCase/PascalCase JS identifiers (function names, class names)
# Matches things like "componentWillUnmount", "getDirectories", "ClassElementKeywords"
CAMELCASE_IDENT_RE = re.compile(
    r'^[a-zA-Z_$][a-zA-Z0-9_$]*$'
)
# Detect if it's a typical JS identifier by checking for camelCase transitions
def _looks_like_js_identifier(value):
    """Check if value looks like a JS function/variable name, not a secret."""
    if not CAMELCASE_IDENT_RE.match(value):
        return False
    # Must have at least one uppercase transition (camelCase) or be all lowercase letters
    has_upper_transition = bool(re.search(r'[a-z][A-Z]', value))
    has_underscore = '_' in value
    # Pure alphabetic camelCase with no digits/special chars = likely JS name
    if has_upper_transition and value.isalpha():
        return True
    # PascalCase (starts with upper, has transitions)
    if value[0].isupper() and has_upper_transition and value.isalpha():
        return True
    # Common JS naming: all lowercase with no special chars
    if value.isalpha() and value.islower() and len(value) >= 8:
        return True
    return False

# URL path patterns (not secrets)
URL_PATH_RE = re.compile(r'^/[a-zA-Z0-9/_.-]+$')

# Common JS library identifiers
JS_LIBRARY_NAMES = {
    'react', 'angular', 'vue', 'jquery', 'lodash', 'moment', 'axios',
    'webpack', 'babel', 'eslint', 'prettier', 'typescript', 'node_modules',
}

# Category mappings for secret types
CATEGORY_MAP = {
    'cloud': ['aws', 'gcp', 'google_cloud', 'azure', 'alibaba', 'digitalocean',
              'heroku', 'cloudflare', 'vercel', 'netlify', 'railway', 'fly_io',
              'render', 'supabase', 'planetscale', 'neon', 'turso', 'upstash'],
    'api_key': ['api_key', 'apikey', 'api-key', 'openai', 'anthropic', 'pinecone',
                'algolia', 'sendgrid', 'twilio', 'stripe', 'paypal', 'square',
                'google_maps', 'google_ai', 'cohere', 'huggingface', 'replicate',
                'sentry', 'datadog', 'newrelic', 'linear', 'notion', 'clerk',
                'resend', 'postmark', 'doppler'],
    'token': ['token', 'oauth', 'bearer', 'jwt', 'session', 'auth0',
              'slack_token', 'discord', 'github_token', 'gitlab_token',
              'bitbucket', 'firebase'],
    'credential': ['password', 'passwd', 'pwd', 'secret', 'credential',
                   'connection_string', 'database', 'mongodb', 'postgres',
                   'mysql', 'redis', 'basic_auth'],
    'crypto': ['private_key', 'asymmetric', 'rsa', 'dsa', 'ecdsa', 'openssh',
               'pgp', 'ethereum', 'solana', 'mnemonic', 'wallet', 'infura',
               'alchemy'],
    'config_leak': ['env_leak', 'config_object', 'dom_storage', 'hardcoded',
                    'assignment', 'firebase_url', 'webhook'],
}


def _calculate_entropy(value):
    """Calculate Shannon entropy of a string."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    entropy = -sum(
        (count / length) * math.log2(count / length)
        for count in counts.values()
        if count > 0
    )
    return round(entropy, 2)


def _categorize_secret(secret_type):
    """Assign a category based on the secret type name."""
    lower = secret_type.lower()
    for category, keywords in CATEGORY_MAP.items():
        if any(kw in lower for kw in keywords):
            return category
    return 'generic'


class JSAnalyzer:
    # Patterns to skip (reduce false positives)
    BASE64_IMAGE_RE = re.compile(
        r'data:image\/[^,]+;base64,[A-Za-z0-9+/=\s]{100,}',
        re.IGNORECASE
    )
    LARGE_BASE64_RE = re.compile(
        r'[A-Za-z0-9+/]{400,}={0,2}'
    )

    # Smart extraction patterns
    VAR_ASSIGNMENT_RE = re.compile(
        r'(?:export\s+)?(?:const|let|var|this\.)\s*'
        r'([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=\s*'
        r'[\'"]([^\'"]{8,256})[\'"]',
    )
    OBJ_PROPERTY_RE = re.compile(
        r'([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:\s*'
        r'[\'"]([^\'"]{8,256})[\'"]',
    )
    ENV_LEAK_RE = re.compile(
        r'process\.env\.([A-Z_][A-Z0-9_]*)',
    )
    DOM_STORAGE_RE = re.compile(
        r'(?:localStorage|sessionStorage)\.'
        r'(?:setItem|getItem)\s*\(\s*'
        r'[\'"]([^\'"]+)[\'"]\s*'
        r'(?:,\s*[\'"]([^\'"]{8,256})[\'"])?',
    )
    FETCH_URL_RE = re.compile(
        r'(?:fetch|axios\.\w+|\.ajax|\$\.(?:get|post|ajax)|XMLHttpRequest)\s*\(\s*'
        r'[\'"]([^\'"]{8,500})[\'"]',
        re.IGNORECASE
    )
    AUTH_HEADER_RE = re.compile(
        r'[\'"](?:Authorization|X-API-Key|X-Auth-Token|X-Access-Token)[\'"]'
        r'\s*:\s*[\'"]([^\'"]{8,256})[\'"]',
        re.IGNORECASE
    )

    # Sensitive variable names that indicate secrets when assigned string values
    SENSITIVE_VAR_NAMES = re.compile(
        r'(?:api[_-]?key|api[_-]?secret|auth[_-]?token|access[_-]?token|'
        r'secret[_-]?key|private[_-]?key|client[_-]?secret|'
        r'password|passwd|pwd|token|credentials?|'
        r'aws[_-]?(?:key|secret|token)|stripe[_-]?(?:key|secret)|'
        r'database[_-]?(?:url|password|uri)|'
        r'encryption[_-]?key|signing[_-]?(?:key|secret)|'
        r'webhook[_-]?(?:secret|url)|'
        r'smtp[_-]?(?:password|pass)|'
        r'app[_-]?secret|session[_-]?secret|jwt[_-]?secret)',
        re.IGNORECASE
    )

    def __init__(
        self,
        output_dir,
        beautify=True,
        verbose=False,
        include_external_endpoints=False,
    ):
        self.output_dir = Path(output_dir)
        self.js_dir = self.output_dir / "javascript"
        self.js_dir.mkdir(parents=True, exist_ok=True)

        self.beautify = beautify and (jsbeautifier is not None)
        self.verbose = verbose
        self.include_external_endpoints = include_external_endpoints

    def analyze_files(self, js_files_dict):
        """
        Analyze JS files for secrets and endpoints.
        Returns dict: {'secrets': [...], 'endpoints': [...]}
        """
        results = {'secrets': [], 'endpoints': []}

        file_paths = self._get_file_paths(js_files_dict)
        unique_files = self._deduplicate_by_content(file_paths)

        found_secrets = set()
        found_endpoints = set()

        for filepath in unique_files:
            content = self._read_file(filepath)
            if not content:
                continue

            if self.beautify:
                content = self._beautify_js(content, filepath)

            cleaned_content = self._remove_base64_noise(content)

            # Pattern-based secret detection
            file_secrets = self._find_secrets(cleaned_content, filepath)
            for secret in file_secrets:
                key = (secret['type'], secret['value'])
                if key not in found_secrets:
                    found_secrets.add(key)
                    results['secrets'].append(secret)

            # Smart analysis: variable assignments with sensitive names
            assignment_secrets = self._find_sensitive_assignments(cleaned_content, filepath)
            for secret in assignment_secrets:
                key = (secret['type'], secret['value'])
                if key not in found_secrets:
                    found_secrets.add(key)
                    results['secrets'].append(secret)

            # Smart analysis: config objects
            config_secrets = self._find_config_objects(cleaned_content, filepath)
            for secret in config_secrets:
                key = (secret['type'], secret['value'])
                if key not in found_secrets:
                    found_secrets.add(key)
                    results['secrets'].append(secret)

            # Smart analysis: environment variable leaks
            env_leaks = self._find_env_leaks(cleaned_content, filepath)
            for secret in env_leaks:
                key = (secret['type'], secret['value'])
                if key not in found_secrets:
                    found_secrets.add(key)
                    results['secrets'].append(secret)

            # Smart analysis: DOM storage secrets
            dom_secrets = self._find_dom_storage(cleaned_content, filepath)
            for secret in dom_secrets:
                key = (secret['type'], secret['value'])
                if key not in found_secrets:
                    found_secrets.add(key)
                    results['secrets'].append(secret)

            # Endpoint extraction (standard + fetch/XHR)
            file_endpoints = self._find_endpoints(content, filepath)
            for endpoint in file_endpoints:
                if endpoint not in found_endpoints:
                    found_endpoints.add(endpoint)
                    results['endpoints'].append({
                        'url': endpoint,
                        'file': os.path.basename(filepath)
                    })

        return results

    # ===== Helper Methods =====

    def _get_file_paths(self, js_files_dict):
        file_paths = []
        if isinstance(js_files_dict, dict):
            items = js_files_dict.values()
        else:
            items = js_files_dict or []

        for item in items:
            if isinstance(item, dict):
                path = item.get('filepath') or item.get('path')
            else:
                path = str(item)
            if path and os.path.exists(path):
                file_paths.append(path)
        return file_paths

    def _deduplicate_by_content(self, paths):
        seen_hashes = set()
        unique = []
        for path in paths:
            try:
                with open(path, 'rb') as f:
                    content_hash = hashlib.sha256(f.read()).hexdigest()
                if content_hash not in seen_hashes:
                    seen_hashes.add(content_hash)
                    unique.append(path)
            except Exception:
                continue
        return unique

    def _read_file(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception:
            return None

    def _beautify_js(self, content, filepath):
        try:
            beautified = jsbeautifier.beautify(content)
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(beautified)
            except Exception:
                pass
            return beautified
        except Exception:
            return content

    def _remove_base64_noise(self, content):
        content = self.BASE64_IMAGE_RE.sub('', content)
        content = self.LARGE_BASE64_RE.sub('', content)
        return content

    # ===== False Positive Filtering =====

    def _is_false_positive(self, value, secret_type, context=''):
        """Multi-layer false positive detection."""
        stripped = value.strip().strip('\'"')

        # Too short or too long
        if len(stripped) < 8 or len(stripped) > 512:
            return True

        # Placeholder / dummy values
        if PLACEHOLDER_PATTERNS.match(stripped):
            return True

        # Known safe strings
        if stripped.lower() in FALSE_POSITIVE_VALUES:
            return True

        # CSS hex colors
        if CSS_HEX_RE.match(stripped):
            return True

        # Version strings
        if VERSION_RE.match(stripped):
            return True

        # Minified variable names
        if MINIFIED_VAR_RE.match(stripped):
            return True

        # camelCase / PascalCase JS identifiers (function names, class names)
        # These match broad patterns like "host", "key", "etsy" with nearby text
        if _looks_like_js_identifier(stripped):
            return True

        # URL paths that are not actual secrets (e.g. /changepassword, /resetpassword)
        if URL_PATH_RE.match(stripped):
            return True

        # Pure numeric (not a key)
        if stripped.isdigit():
            return True

        # Repetitive characters (low entropy junk like "aaaaaaaaaa")
        if len(set(stripped)) <= 2 and len(stripped) >= 8:
            return True

        # File paths that aren't connection strings
        if stripped.startswith(('/usr/', '/var/', '/etc/', '/home/', '/tmp/',
                                'C:\\', './node_modules/', '../')):
            return True

        # data: URIs
        if stripped.startswith('data:'):
            return True

        # In a data URI context
        if 'data:image' in context.lower() or 'data:font' in context.lower():
            return True

        # Common JS expressions that match patterns
        if any(kw in stripped.lower() for kw in
               ['function(', 'return ', 'module.', 'require(', 'import ',
                '.prototype', '.constructor', 'Object.', 'Array.',
                '.toString', '.valueOf']):
            return True

        # URL-like patterns that are actually documentation
        if any(domain in stripped.lower() for domain in
               ['github.com/', 'stackoverflow.com', 'npmjs.com',
                'docs.google.com', 'developer.mozilla.org']):
            if 'token' not in secret_type.lower() and 'key' not in secret_type.lower():
                return True

        # Comment context detection
        ctx_lower = context.lower()
        if any(marker in ctx_lower for marker in
               ['// example', '// test', '// sample', '// demo',
                '/* example', '/* test', '* @param', '* @returns',
                'todo:', 'fixme:', 'hack:', 'note:']):
            return True

        return False

    def _looks_like_hash(self, value):
        """Check if value looks like a content hash (MD5, SHA1, SHA256)."""
        if re.fullmatch(r'[A-Fa-f0-9]{32}', value):
            return True
        if re.fullmatch(r'[A-Fa-f0-9]{40}', value):
            return True
        if re.fullmatch(r'[A-Fa-f0-9]{64}', value):
            return True
        return False

    # ===== Confidence Assessment =====

    def _assess_confidence(self, secret_type, value, entropy):
        """Assess finding confidence with length-aware entropy thresholds."""
        lower_type = secret_type.lower()

        # High-confidence pattern types (known prefixes like AKIA, sk_live_, etc.)
        high_keywords = [
            'aws_api_key', 'stripe_', 'github_token', 'github_pat',
            'slack_token', 'slack_bot', 'private_key', 'google_api',
            'firebase', 'twilio', 'sendgrid', 'mailgun', 'heroku',
            'shopify', 'square_', 'paypal', 'braintree', 'npm_token',
            'pypi_token', 'rubygems', 'nuget', 'docker_', 'gitlab_',
            'bitbucket_', 'jwt_token', 'clerk_secret', 'azure_',
            'okta_', 'cognito_', 'url_basic_auth', 'webhook_secret',
        ]

        if any(kw in lower_type for kw in high_keywords):
            if entropy >= 3.0:
                return 'high'
            elif entropy >= 2.0:
                return 'medium'

        # Length-aware entropy thresholds
        vlen = len(value)
        if vlen < 16:
            high_t, med_t, low_t = 3.8, 3.3, 2.3
        elif vlen > 128:
            high_t, med_t, low_t = 3.2, 2.7, 1.8
        else:
            high_t, med_t, low_t = 3.5, 3.0, 2.0

        if entropy >= high_t:
            return 'high'
        if entropy >= med_t:
            return 'medium'
        if entropy >= low_t:
            return 'low'
        return 'info'

    # ===== Pattern-Based Secret Detection =====

    def _find_secrets(self, content, filepath):
        findings = []
        filename = os.path.basename(filepath)

        for secret_type, pattern in patterns.items():
            proximity_src = pattern_contexts.get(secret_type)
            proximity_re = re.compile(proximity_src) if proximity_src else None
            try:
                regex = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
            except Exception:
                continue

            for match in regex.finditer(content):
                # Get the captured group if available, otherwise full match.
                # `match.lastindex` can be >=1 while group(1) is None — happens
                # when a later alternation branch captures and group 1 didn't.
                raw = match.group(1) if match.lastindex else None
                if raw is None:
                    raw = match.group(0)
                secret_value = (raw or "").strip().strip('\'"')
                if not secret_value:
                    continue

                # Per-match proximity gate for context-sensitive patterns.
                # Left-only window: in `name = "value"` the identifier always
                # precedes the value, so text after the match can only supply
                # false context - e.g. a following `iv = ...` relabelling a key.
                if proximity_re is not None:
                    win_start = max(0, match.start() - 80)
                    if not proximity_re.search(content[win_start:match.start()]):
                        continue

                # Get context around the match
                start = max(0, match.start() - 150)
                end = min(len(content), match.end() + 150)
                context = content[start:end].strip()

                # Multi-layer false positive check
                if self._is_false_positive(secret_value, secret_type, context):
                    continue

                # Skip obvious hashes unless looking for hash patterns or the
                # pattern is context-gated (proximity gate already proves
                # key/IV naming is adjacent).
                if self._looks_like_hash(secret_value):
                    if ('hash' not in secret_type.lower()
                            and 'md5' not in secret_type.lower()
                            and secret_type not in pattern_contexts):
                        continue

                entropy = _calculate_entropy(secret_value)
                confidence = self._assess_confidence(secret_type, secret_value, entropy)
                category = _categorize_secret(secret_type)

                findings.append({
                    'type': secret_type,
                    'value': secret_value,
                    'file': filename,
                    'context': context[:300],
                    'confidence': confidence,
                    'entropy': entropy,
                    'category': category,
                    'source': 'pattern_match',
                })

        return findings

    # ===== Smart Analysis: Variable Assignments =====

    def _find_sensitive_assignments(self, content, filepath):
        """Detect const/let/var assignments to sensitive variable names."""
        findings = []
        filename = os.path.basename(filepath)

        for match in self.VAR_ASSIGNMENT_RE.finditer(content):
            var_name = match.group(1)
            var_value = match.group(2)

            if not self.SENSITIVE_VAR_NAMES.search(var_name):
                continue

            start = max(0, match.start() - 100)
            end = min(len(content), match.end() + 100)
            context = content[start:end].strip()

            if self._is_false_positive(var_value, f'assignment_{var_name}', context):
                continue

            entropy = _calculate_entropy(var_value)
            if entropy < 2.0:
                continue

            confidence = 'high' if entropy >= 3.5 else 'medium' if entropy >= 2.5 else 'low'

            findings.append({
                'type': f'sensitive_assignment:{var_name}',
                'value': var_value,
                'file': filename,
                'context': context[:300],
                'confidence': confidence,
                'entropy': entropy,
                'category': 'config_leak',
                'source': 'assignment',
            })

        return findings

    # ===== Smart Analysis: Config Objects =====

    def _find_config_objects(self, content, filepath):
        """Detect sensitive keys in object literals."""
        findings = []
        filename = os.path.basename(filepath)

        for match in self.OBJ_PROPERTY_RE.finditer(content):
            prop_name = match.group(1)
            prop_value = match.group(2)

            if not self.SENSITIVE_VAR_NAMES.search(prop_name):
                continue

            start = max(0, match.start() - 100)
            end = min(len(content), match.end() + 100)
            context = content[start:end].strip()

            if self._is_false_positive(prop_value, f'config_{prop_name}', context):
                continue

            entropy = _calculate_entropy(prop_value)
            if entropy < 2.5:
                continue

            confidence = 'high' if entropy >= 3.5 else 'medium'

            findings.append({
                'type': f'config_object:{prop_name}',
                'value': prop_value,
                'file': filename,
                'context': context[:300],
                'confidence': confidence,
                'entropy': entropy,
                'category': 'config_leak',
                'source': 'config_object',
            })

        return findings

    # ===== Smart Analysis: Environment Variable Leaks =====

    def _find_env_leaks(self, content, filepath):
        """Detect process.env references that indicate expected secrets."""
        findings = []
        filename = os.path.basename(filepath)

        for match in self.ENV_LEAK_RE.finditer(content):
            env_var = match.group(1)

            # Only flag if it's a sensitive env var name
            sensitive_env_patterns = [
                'KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CREDENTIAL',
                'AUTH', 'PRIVATE', 'API', 'DATABASE', 'DB_',
                'STRIPE', 'AWS', 'REDIS', 'MONGO', 'SMTP',
            ]
            if not any(pat in env_var.upper() for pat in sensitive_env_patterns):
                continue

            start = max(0, match.start() - 100)
            end = min(len(content), match.end() + 100)
            context = content[start:end].strip()

            # Check if the env var has a fallback value hardcoded
            fallback_match = re.search(
                r'process\.env\.' + re.escape(env_var) +
                r'\s*\|\|\s*[\'"]([^\'"]{8,256})[\'"]',
                content[max(0, match.start() - 20):match.end() + 300]
            )

            if fallback_match:
                fallback_value = fallback_match.group(1)
                if not self._is_false_positive(fallback_value, f'env_fallback_{env_var}', context):
                    entropy = _calculate_entropy(fallback_value)
                    findings.append({
                        'type': f'env_fallback:{env_var}',
                        'value': fallback_value,
                        'file': filename,
                        'context': context[:300],
                        'confidence': 'high' if entropy >= 3.5 else 'medium',
                        'entropy': entropy,
                        'category': 'config_leak',
                        'source': 'env_leak',
                    })
            else:
                # Just the env reference itself (informational)
                findings.append({
                    'type': f'env_reference:{env_var}',
                    'value': f'process.env.{env_var}',
                    'file': filename,
                    'context': context[:300],
                    'confidence': 'info',
                    'entropy': 0.0,
                    'category': 'config_leak',
                    'source': 'env_leak',
                })

        return findings

    # ===== Smart Analysis: DOM Storage =====

    def _find_dom_storage(self, content, filepath):
        """Detect localStorage/sessionStorage usage with sensitive keys."""
        findings = []
        filename = os.path.basename(filepath)

        for match in self.DOM_STORAGE_RE.finditer(content):
            storage_key = match.group(1)
            storage_value = match.group(2) if match.lastindex >= 2 and match.group(2) else None

            sensitive_storage_keys = [
                'token', 'key', 'secret', 'auth', 'session', 'jwt',
                'password', 'credential', 'api', 'access',
            ]
            if not any(pat in storage_key.lower() for pat in sensitive_storage_keys):
                continue

            start = max(0, match.start() - 100)
            end = min(len(content), match.end() + 100)
            context = content[start:end].strip()

            if storage_value and not self._is_false_positive(storage_value, f'dom_storage_{storage_key}', context):
                entropy = _calculate_entropy(storage_value)
                findings.append({
                    'type': f'dom_storage_value:{storage_key}',
                    'value': storage_value,
                    'file': filename,
                    'context': context[:300],
                    'confidence': 'medium' if entropy >= 3.0 else 'low',
                    'entropy': entropy,
                    'category': 'config_leak',
                    'source': 'dom_storage',
                })
            else:
                findings.append({
                    'type': f'dom_storage_key:{storage_key}',
                    'value': storage_key,
                    'file': filename,
                    'context': context[:300],
                    'confidence': 'info',
                    'entropy': 0.0,
                    'category': 'config_leak',
                    'source': 'dom_storage',
                })

        return findings

    # ===== Endpoint Detection =====

    def _find_endpoints(self, content, filepath):
        """Find API endpoints, URLs, and fetch/XHR targets."""
        endpoints = set()

        # Standard endpoint patterns
        standard_patterns = [
            r'["\']/(api|v\d+|rest|graphql|endpoints?)/[a-zA-Z0-9/_\-\.]+["\']',
            r'https?://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}(?:/[^\s\'"<>]*)?',
            r'["\'][a-zA-Z0-9/_-]+\?[a-zA-Z0-9=&_-]+["\']',
            # WebSocket URLs
            r'wss?://[a-zA-Z0-9.\-:]+(?:/[^\s\'"<>]*)?',
            # Internal/private IP endpoints
            r'https?://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})(?::\d+)?(?:/[^\s\'"<>]*)?',
            # gRPC-web / tRPC endpoints
            r'["\'](?:https?://[^\s\'"]*)?/(?:grpc|trpc|twirp)/[a-zA-Z0-9/_\-\.]+["\']',
            # Server-Sent Events
            r'new\s+EventSource\s*\(\s*["\']([^\'"]+)["\']',
            # Common API paths not starting with /api
            r'["\']/(?:auth|oauth|login|signup|register|token|session|webhook|callback|graphql|query|mutation)/[a-zA-Z0-9/_\-\.]*["\']',
        ]

        for pattern in standard_patterns:
            try:
                for match in re.finditer(pattern, content, re.IGNORECASE):
                    endpoint = match.group().strip('\'"')
                    if self._is_valid_endpoint(endpoint):
                        endpoints.add(endpoint)
            except Exception:
                continue

        # Fetch/XHR/Axios URL extraction
        for match in self.FETCH_URL_RE.finditer(content):
            url = match.group(1).strip()
            if self._is_valid_endpoint(url):
                endpoints.add(url)

        return list(endpoints)

    def _is_valid_endpoint(self, endpoint):
        if len(endpoint) < 8 or len(endpoint) > 500:
            return False

        lower = endpoint.lower()

        # Skip documentation/library URLs
        skip_terms = [
            'license', 'github.com/', 'stackoverflow.com',
            'opensource.org', 'w3.org', 'schema.org',
            'creativecommons.org', 'mozilla.org', 'npmjs.com',
            'unpkg.com', 'cdnjs.cloudflare.com', 'cdn.jsdelivr.net',
            'fonts.googleapis.com', 'fonts.gstatic.com',
        ]
        if any(term in lower for term in skip_terms):
            return False

        # Skip example/placeholder URLs
        if any(lower.startswith(x) for x in [
            'http://example.com', 'https://example.com',
            '/path/to/', '/example/',
        ]):
            return False

        # Only filter bare localhost/example with no meaningful path
        if re.match(r'^https?://(localhost|127\.0\.0\.1|example\.com|test\.com)/?$', lower):
            return False

        # Skip file extensions that aren't API endpoints
        skip_extensions = [
            '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
            '.woff', '.woff2', '.ttf', '.eot', '.mp3', '.mp4',
            '.webp', '.pdf', '.zip', '.tar', '.gz',
        ]
        if any(lower.endswith(ext) for ext in skip_extensions):
            return False

        return True
