"""
Subdomain enumeration for jsrip.
Passive (crt.sh) + active (DNS brute-force) discovery with HTTP alive-checking.
"""

import asyncio
import json
import random
import string
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

try:
    import aiohttp
except Exception:
    aiohttp = None

try:
    import aiodns
except Exception:
    aiodns = None


@dataclass
class SubdomainResult:
    subdomain: str
    ip: str
    status_code: int
    alive: bool
    source: str  # "crt.sh" | "certspotter" | "bruteforce" | "both"
    # How the host fared in the pipeline. Kept separate from `alive` because
    # "resolved to a wildcard IP" and "did not resolve" both end up alive=False
    # yet mean very different things to whoever reads subdomains.json.
    classification: str = "dead"  # "alive" | "dead" | "wildcard" | "unresolved"
    filter_reason: str = ""       # why it was not counted as an alive asset

    ALIVE = "alive"
    DEAD = "dead"
    WILDCARD = "wildcard"
    UNRESOLVED = "unresolved"

    def to_dict(self):
        return asdict(self)


def recon_summary(results: list) -> dict:
    """Per-domain view of an enumeration, shared by every JSON output.

    `wildcard_filtered` and `unresolved` are explicit counts rather than
    being folded into `alive`/`dead`: on a wildcard domain the reader has to
    be able to tell "no HTTP" apart from "only answered because of the
    wildcard", which is the whole point of keeping filtered records.
    """
    return {
        "total": len(results),
        "alive": sum(1 for r in results if r.alive),
        "dead": sum(
            1 for r in results if r.classification == SubdomainResult.DEAD),
        "wildcard_filtered": sum(
            1 for r in results if r.classification == SubdomainResult.WILDCARD),
        "unresolved": sum(
            1 for r in results if r.classification == SubdomainResult.UNRESOLVED),
        "results": [r.to_dict() for r in results],
    }


class SubdomainEnumerator:
    """
    Public API:
        results = await SubdomainEnumerator(domains, logger).enumerate()
    Returns dict: {domain: [SubdomainResult, ...]}
    """

    BUILTIN_WORDLIST = Path(__file__).parent.parent / "wordlists" / "subdomains.txt"

    def __init__(self, domains: set, logger, wordlist_path: str = None,
                 dns_concurrency: int = 50, http_concurrency: int = 20):
        self.domains = domains
        self.logger = logger
        self.wordlist_path = Path(wordlist_path) if wordlist_path else self.BUILTIN_WORDLIST
        self.dns_concurrency = dns_concurrency
        self.http_concurrency = http_concurrency
        self._resolver = None
        self._aio_session = None

    def _load_wordlist(self) -> list[str]:
        """Load subdomain wordlist from file."""
        try:
            with open(self.wordlist_path, "r", encoding="utf-8") as f:
                words = [
                    line.strip().lower()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            return words
        except FileNotFoundError:
            self.logger.error(f"Wordlist not found: {self.wordlist_path}")
            return []

    async def _get_aio_session(self):
        if self._aio_session is None and aiohttp:
            timeout = aiohttp.ClientTimeout(total=10)
            self._aio_session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "jsrip/1.0"},
                connector=aiohttp.TCPConnector(ssl=False),
            )
        return self._aio_session

    def _get_resolver(self):
        """One resolver per enumerator run.

        Each DNSResolver opens its own c-ares channel and sockets. The
        brute-force already shared one across the wordlist, but _resolve_single
        built a fresh one per call - and _enumerate_domain calls it once for
        every CT-discovered subdomain the brute-force missed, so a domain with
        a large certificate history opened that many channels. The resolver is
        bound to the running loop, so _close() releases it.
        """
        if self._resolver is None and aiodns:
            self._resolver = aiodns.DNSResolver()
        return self._resolver

    async def _close(self):
        if self._aio_session:
            await self._aio_session.close()
            self._aio_session = None
        self._resolver = None

    async def enumerate(self) -> dict:
        """Run full enumeration for all domains."""
        results = {}
        try:
            for domain in sorted(self.domains):
                self.logger.info(f"Enumerating subdomains for: {domain}")
                domain_results = await self._enumerate_domain(domain)
                results[domain] = domain_results
        finally:
            await self._close()
        return results

    async def _detect_wildcard(self, domain: str) -> set:
        """Detect wildcard DNS by resolving random garbage subdomains.
        Returns set of wildcard IPs, or empty set if no wildcard.

        Every A record seen for a garbage name is collected, not just the
        first: a wildcard backed by several addresses can hand a different
        one back per lookup, and comparing against only one would let
        invented hosts slip through the filter.
        """
        if not aiodns:
            return set()

        wildcard_ips = set()
        resolver = self._get_resolver()

        # Try 3 random subdomains - if they all resolve to the same IP, it's a wildcard
        for _ in range(3):
            garbage = ''.join(random.choices(string.ascii_lowercase, k=12))
            fqdn = f"{garbage}.{domain}"
            try:
                resp = await asyncio.wait_for(
                    resolver.query(fqdn, "A"), timeout=3
                )
                if resp:
                    wildcard_ips.update(r.host for r in resp)
            except Exception:
                # If even one random sub fails to resolve, no wildcard
                return set()

        return wildcard_ips

    async def _enumerate_domain(self, domain: str) -> list[SubdomainResult]:
        """Enumerate a single domain: crt.sh + brute-force + alive check."""
        # Phase 0: Wildcard detection
        wildcard_ips = await self._detect_wildcard(domain)
        if wildcard_ips:
            self.logger.info(f"  WILDCARD DNS detected for {domain} (IPs: {', '.join(wildcard_ips)}) - filtering results")

        # Phase 1: Passive - certificate transparency (crt.sh, then certspotter)
        # Always runs: a wildcard only makes active brute-force worthless, it
        # says nothing about names that were actually certified.
        crtsh_subs, passive_source = await self._query_passive_ct(domain)
        self.logger.info(f"  {passive_source}: {len(crtsh_subs)} subdomains")

        # Phase 2: Active - DNS brute-force (skip if wildcard)
        bruteforce_subs = {}
        if wildcard_ips:
            self.logger.info(f"  bruteforce: SKIPPED (wildcard DNS)")
        else:
            bruteforce_subs = await self._bruteforce_dns(domain)
            self.logger.info(f"  bruteforce: {len(bruteforce_subs)} resolved")

        # Merge and track sources
        merged = {}
        for sub in crtsh_subs:
            merged[sub] = passive_source
        for sub, ip in bruteforce_subs.items():
            if sub in merged:
                merged[sub] = "both"
            else:
                merged[sub] = "bruteforce"

        if not merged:
            if wildcard_ips:
                self.logger.info(
                    f"  wildcard-filtered: 0 host(s) ignored for {domain} "
                    f"(resolved to wildcard IPs)")
            return []

        # Phase 3: DNS resolve any CT-only subs that need IPs.
        # A wildcard DNS makes every invented name resolve, so an IP from the
        # wildcard set is evidence of the wildcard rather than of a real host:
        # such names are recorded for auditability but never probed or counted
        # as assets. Names that fail to resolve are likewise kept (CT found
        # them, so they are worth reporting as such) instead of vanishing.
        resolved = {}
        wildcard_filtered = []
        unresolved = []
        for sub, source in merged.items():
            if sub in bruteforce_subs:
                ips = [bruteforce_subs[sub]]
            else:
                ips = await self._resolve_ips(sub)

            if not ips:
                # Without a wildcard this is the historical behaviour: a CT
                # name that no longer resolves is simply dropped. With a wildcard
                # the domain's result keeps the record so "unresolved" stays
                # distinguishable from "wildcard", which both fail alive checks.
                if wildcard_ips:
                    unresolved.append(SubdomainResult(
                        sub, "", 0, False, source,
                        SubdomainResult.UNRESOLVED,
                        "DNS resolution failed",
                    ))
            elif any(ip in wildcard_ips for ip in ips):
                # Any wildcard answer taints the host: a real multi-homed name
                # that also lands on the wildcard address can't be told apart
                # from an invented one, so it isn't an asset we can trust.
                matched = next(ip for ip in ips if ip in wildcard_ips)
                wildcard_filtered.append(SubdomainResult(
                    sub, matched, 0, False, source,
                    SubdomainResult.WILDCARD,
                    f"resolved to wildcard IP {matched}",
                ))
            else:
                resolved[sub] = (ips[0], source)

        self.logger.info(f"  resolved: {len(resolved)} total unique subdomains")
        if wildcard_ips:
            self.logger.info(
                f"  wildcard-filtered: {len(wildcard_filtered)} host(s) "
                f"ignored for {domain} (resolved to wildcard IPs)")

        # Phase 4: HTTP alive check (wildcard/unresolved hosts excluded)
        results = await self._check_alive(resolved)
        results.extend(wildcard_filtered)
        results.extend(unresolved)
        alive_count = sum(1 for r in results if r.alive)
        self.logger.info(f"  alive: {alive_count}/{len(results)}")

        return sorted(results, key=lambda r: (not r.alive, r.subdomain))

    @staticmethod
    def _normalise_ct_names(raw_names, domain: str) -> set:
        """Filter raw certificate names down to in-scope hostnames.

        Shared by every CT source so crt.sh and certspotter agree exactly:
        splits multi-line SAN blobs, strips a leading wildcard, lowercases,
        and keeps only the domain itself or something under it.
        """
        out = set()
        for raw in raw_names:
            for line in (raw or "").strip().lower().split("\n"):
                line = line.strip()
                if line.startswith("*."):
                    line = line[2:]
                if not line or " " in line or "*" in line:
                    continue
                if line == domain or line.endswith(f".{domain}"):
                    out.add(line)
        return out

    async def _query_crtsh(self, domain: str):
        """Query crt.sh Certificate Transparency logs.

        Returns a list of subdomains, or None if the source itself failed —
        the caller uses None to decide whether a fallback source is warranted.
        """
        session = await self._get_aio_session()
        if not session:
            return None

        url = f"https://crt.sh/?q=%25.{domain}&output=json"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(
                        f"crt.sh unavailable for {domain} (HTTP {resp.status})")
                    return None
                data = await resp.json(content_type=None)
        except Exception as e:
            self.logger.warning(f"crt.sh query failed for {domain}: {e!r}")
            return None

        names = [(entry.get("name_value") or "") for entry in (data or [])]
        return list(self._normalise_ct_names(names, domain))

    async def _query_certspotter(self, domain: str):
        """Query Cert Spotter CT logs. Fallback for when crt.sh is unreachable.

        Returns a list of subdomains, or None if the source failed. The free
        tier is rate-limited (HTTP 429) when used without an API key.
        """
        session = await self._get_aio_session()
        if not session:
            return None

        url = (f"https://api.certspotter.com/v1/issuances?domain={domain}"
               f"&include_subdomains=true&expand=dns_names")
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    detail = " (rate limited - no API key)" if resp.status == 429 else ""
                    self.logger.warning(
                        f"certspotter unavailable for {domain} "
                        f"(HTTP {resp.status}){detail}")
                    return None
                data = await resp.json(content_type=None)
        except Exception as e:
            self.logger.warning(f"certspotter query failed for {domain}: {e!r}")
            return None

        names = []
        for entry in (data or []):
            names.extend(entry.get("dns_names") or [])
        return list(self._normalise_ct_names(names, domain))

    async def _query_passive_ct(self, domain: str):
        """Passive discovery across CT sources. Returns (subdomains, source).

        crt.sh first; certspotter only when crt.sh actually failed. A crt.sh
        result of zero is a real answer, not a failure, so it does not spend
        the certspotter rate limit.
        """
        subs = await self._query_crtsh(domain)
        if subs is not None:
            return subs, "crt.sh"

        self.logger.info("  falling back to certspotter for passive discovery")
        subs = await self._query_certspotter(domain)
        if subs is not None:
            return subs, "certspotter"

        self.logger.warning(
            f"  all CT sources failed for {domain} - brute-force only")
        return [], "crt.sh"

    async def _bruteforce_dns(self, domain: str) -> dict:
        """DNS brute-force using wordlist. Returns {subdomain: ip}."""
        words = self._load_wordlist()
        if not words:
            return {}

        if not aiodns:
            self.logger.warning("aiodns not installed - skipping DNS brute-force")
            return {}

        resolver = self._get_resolver()
        results = {}
        sem = asyncio.Semaphore(self.dns_concurrency)

        async def resolve_one(word):
            fqdn = f"{word}.{domain}"
            async with sem:
                try:
                    resp = await asyncio.wait_for(
                        resolver.query(fqdn, "A"),
                        timeout=3
                    )
                    if resp:
                        ip = resp[0].host
                        results[fqdn] = ip
                except Exception:
                    pass

        tasks = [asyncio.create_task(resolve_one(w)) for w in words]
        await asyncio.gather(*tasks)
        return results

    async def _resolve_single(self, subdomain: str) -> str:
        """Resolve a single subdomain to an IP."""
        ips = await self._resolve_ips(subdomain)
        return ips[0] if ips else ""

    async def _resolve_ips(self, subdomain: str) -> list:
        """Resolve every A record for a subdomain (empty list on failure).

        The full set matters under wildcard DNS: round-robin wildcards hand
        back different addresses per lookup, so judging a host by only its
        first answer would let wildcard-backed names through the filter.
        """
        if not aiodns:
            return []
        resolver = self._get_resolver()
        try:
            resp = await asyncio.wait_for(
                resolver.query(subdomain, "A"),
                timeout=3
            )
            return [r.host for r in (resp or [])]
        except Exception:
            return []

    async def _check_alive(self, resolved: dict) -> list[SubdomainResult]:
        """HTTP probe resolved subdomains for liveness."""
        session = await self._get_aio_session()
        results = []

        if not session:
            return [
                SubdomainResult(sub, ip, 0, False, source,
                                SubdomainResult.DEAD,
                                "HTTP probe unavailable")
                for sub, (ip, source) in resolved.items()
            ]

        sem = asyncio.Semaphore(self.http_concurrency)

        async def probe(sub, ip, source):
            async with sem:
                for scheme in ("https", "http"):
                    try:
                        async with session.get(
                            f"{scheme}://{sub}",
                            timeout=aiohttp.ClientTimeout(total=10),
                            allow_redirects=True,
                            max_redirects=3,
                        ) as resp:
                            return SubdomainResult(
                                sub, ip, resp.status, True, source,
                                SubdomainResult.ALIVE)
                    except Exception:
                        continue
                return SubdomainResult(sub, ip, 0, False, source,
                                       SubdomainResult.DEAD)

        tasks = [
            asyncio.create_task(probe(sub, ip, source))
            for sub, (ip, source) in resolved.items()
        ]
        results = await asyncio.gather(*tasks)
        return list(results)
