import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest import mock

from core import enumerator as enum_mod
from core.enumerator import SubdomainEnumerator


class _CountingResolver:
    """Stands in for aiodns.DNSResolver and counts how many were built."""

    instances = 0

    def __init__(self, *a, **k):
        type(self).instances += 1

    async def query(self, name, qtype):
        return [SimpleNamespace(host="10.0.0.1")]


def _fake_aiodns():
    _CountingResolver.instances = 0
    return SimpleNamespace(DNSResolver=_CountingResolver)


class TestResolverReuse(unittest.TestCase):
    """One resolver per enumerator, not one per name.

    Each aiodns.DNSResolver opens its own c-ares channel and sockets. The
    leak was in _resolve_single, which _enumerate_domain calls once per
    CT-discovered subdomain the brute-force missed: 50 such names built 50
    resolvers. (The brute-force loop already shared one.)
    """

    def setUp(self):
        self.enum = SubdomainEnumerator({"example.com"}, logging.getLogger("t"))

    def test_repeated_single_resolution_builds_one_resolver(self):
        with mock.patch.object(enum_mod, "aiodns", _fake_aiodns()):
            async def run():
                for host in [f"h{i}.example.com" for i in range(5)]:
                    await self.enum._resolve_single(host)
            asyncio.run(run())
        self.assertEqual(_CountingResolver.instances, 1)

    def test_wildcard_detection_shares_the_same_resolver(self):
        with mock.patch.object(enum_mod, "aiodns", _fake_aiodns()):
            async def run():
                await self.enum._detect_wildcard("example.com")
                await self.enum._resolve_single("a.example.com")
            asyncio.run(run())
        self.assertEqual(_CountingResolver.instances, 1)

    def test_resolution_still_returns_the_ip(self):
        """Guards the reuse fix from quietly breaking resolution itself."""
        with mock.patch.object(enum_mod, "aiodns", _fake_aiodns()):
            ip = asyncio.run(self.enum._resolve_single("a.example.com"))
        self.assertEqual(ip, "10.0.0.1")

    def test_close_releases_the_resolver(self):
        """A resolver is bound to the loop that built it. Holding one past
        _close() would hand a dead channel to the next run's event loop."""
        with mock.patch.object(enum_mod, "aiodns", _fake_aiodns()):
            async def run():
                await self.enum._resolve_single("a.example.com")
                await self.enum._close()
            asyncio.run(run())
        self.assertIsNone(self.enum._resolver)

    def test_no_resolver_built_when_aiodns_is_absent(self):
        with mock.patch.object(enum_mod, "aiodns", None):
            ip = asyncio.run(self.enum._resolve_single("a.example.com"))
        self.assertEqual(ip, "")


if __name__ == "__main__":
    unittest.main()
