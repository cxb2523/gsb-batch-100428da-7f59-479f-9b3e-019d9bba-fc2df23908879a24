"""Tests for the Solana branch of the engine on fake data (no network): chain by address, SOLANA_ENABLED flag,
pack window per chain, unread entries, token entirely in the curve."""
import os, time, unittest
from contextlib import ExitStack
from unittest import mock

import fakes
from fakes import ch, engine, market
from chains import solana as sol
import detect as d

SOL_TOKEN = sol.b58encode(bytes([7]) * 32)
SOL_CURVE = sol.b58encode(bytes([8]) * 32)
SOL_WALLETS = [sol.b58encode(bytes([20 + i]) * 32) for i in range(12)]
EVM_WALLETS = ["0x" + f"{20 + i:02x}" * 20 for i in range(12)]
LAUNCH_SLOT, LAUNCH_TS = 1000, int(time.time()) - 3600   # fresh launch: an hour ago
SUPPLY = 10 ** 15
PACK = 3                                   # first 3 wallets — a pack in adjacent slots


def enabled(on=True):
    return mock.patch.dict(os.environ, {"SOLANA_ENABLED": "true" if on else "false"})


def balances(wallets):
    return {w: SUPPLY // 100 * (30 - i) for i, w in enumerate(wallets)}


def patched_chain(mod, token, wallets, entries, history, bal=None):
    """Chain adapter mod is patched: base set directly, no transfers, network forbidden."""
    stack = ExitStack()
    engine._HIST.clear()
    curve = SOL_CURVE if mod is sol else fakes.CURVE
    bal = balances(wallets) if bal is None else bal
    base = {"supply": SUPPLY, "circulating": sum(bal.values()), "holders_total": len(bal), "balances": bal}
    p = lambda name, **kw: stack.enter_context(mock.patch.object(mod, name, **kw))
    p("_post", side_effect=fakes._no_network)
    p("get_launch", side_effect=lambda t: {"block": LAUNCH_SLOT, "curve": curve, "deployer": "deployer",
                                           "tx": "launch"} if t == token else None)
    p("token_facts", side_effect=lambda t, l: {"supply": SUPPLY, "transfers": [], "excluded": {curve},
                                               "market": {curve}, "reserve": SUPPLY - base["circulating"],
                                               "base": base, "skipped": []})
    p("classify_entries", side_effect=lambda t, trs, ws: {w: entries[w] for w in ws})
    p("block_timestamps", side_effect=lambda blocks: {b: LAUNCH_TS + (b - LAUNCH_SLOT) for b in blocks})
    p("wallet_distinct_tokens", side_effect=history)
    p("is_contract", side_effect=lambda addrs: {x: False for x in addrs})
    p("token_meta", return_value={"name": "Synthetic", "symbol": "SYN"})
    stack.enter_context(mock.patch.object(market, "fetch_market", return_value={}))
    return stack


def buy(slot, eth, tx):
    return {"kind": "buy", "tx": tx, "block": slot, "via": "pool", "eth_in": eth, "unread": False}


def pack_entries(wallets, eth):
    """The first PACK wallets bought in slots +100, +101, +102 with equal amounts; the rest — far apart."""
    return {w: buy(LAUNCH_SLOT + (100 + i if i < PACK else 300 + 10 * i), eth, f"tx{i}") for i, w in enumerate(wallets)}


def pack_history(wallets):
    return lambda w, b, t, window=None, cap=4: 0 if wallets.index(w) < PACK else cap


def sig_page(n, tag="page"):
    """A getSignaturesForAddress page of n signatures (newest → oldest); n == SIG_PAGE is a full page."""
    return [{"signature": f"sig{tag}-{i}", "slot": LAUNCH_SLOT + 1000, "err": None} for i in range(n)]


class FakeRpc:
    """Local fake of the Solana RPC for the token account history: no network at all.
    history — {token account: [signatures newest → oldest] or None (the RPC call fails)}.
    A page is SIG_PAGE signatures: exactly SIG_PAGE means "there may be more" (the adapter asks for the
    next page, `before` = the last signature of this one); fewer means the history is over."""

    def __init__(self, history):
        self.history, self.calls = history, []

    def get_signatures(self, addr, params):
        page = self.history.get(addr, [])   # an account not in the fake data: an empty history, not a failure
        if page is None:
            raise RuntimeError("getSignaturesForAddress failed")
        if params.get("before"):
            sigs = [s["signature"] for s in page]
            page = page[sigs.index(params["before"]) + 1:]
        return page[:params["limit"]]

    def rpc_batch(self, calls, chunk=50):
        out = []
        for method, params in calls:
            assert method == "getSignaturesForAddress", method
            try:
                out.append(self.get_signatures(params[0], params[1]))
            except RuntimeError:
                out.append(None)
        return out

    def rpc(self, method, params):
        assert method == "getSignaturesForAddress", method
        return self.get_signatures(params[0], params[1])   # a failure propagates, as with the real rpc()

    def patch(self, stack):
        stack.enter_context(mock.patch.dict(sol._ACC_HIST, clear=True))
        stack.enter_context(mock.patch.object(sol, "rpc_batch", side_effect=self.rpc_batch))
        stack.enter_context(mock.patch.object(sol, "rpc", side_effect=self.rpc))
        stack.enter_context(mock.patch.object(sol, "get_transactions", return_value={}))


class TestChainDetection(unittest.TestCase):

    def test_address_to_chain(self):
        with enabled():
            self.assertEqual(engine.chain_of("0x" + "AB" * 20), ("robinhood", "0x" + "ab" * 20))
            self.assertEqual(engine.chain_of(" " + SOL_TOKEN + " "), ("solana", SOL_TOKEN))   # case is preserved
            self.assertEqual(engine.chain_of("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")[0], "solana")
            for bad in ("", None, "hello", "0x123", "0x" + "g" * 40, "ab" * 20, SOL_TOKEN + "1", "0" * 44):
                with self.assertRaises(engine.ScanError) as cm:
                    engine.chain_of(bad)
                self.assertEqual(str(cm.exception), "not a token address", bad)

    def test_solana_disabled(self):
        for value in ("false", "", None):                                            # None — variable not set
            with mock.patch.dict(os.environ, {"SOLANA_ENABLED": value or ""}):
                if value is None:
                    del os.environ["SOLANA_ENABLED"]
                with self.assertRaises(engine.ScanError) as cm:
                    engine.chain_of(SOL_TOKEN)
                self.assertEqual(str(cm.exception), "Solana support is coming soon")
        with enabled(False), patched_chain(sol, SOL_TOKEN, SOL_WALLETS, pack_entries(SOL_WALLETS, 10 ** 8),
                                           pack_history(SOL_WALLETS)):
            events = []
            with self.assertRaises(engine.ScanError):
                engine.scan(SOL_TOKEN, events.append)
            self.assertEqual(events, [])
            self.assertFalse(sol.get_launch.called)
        self.assertEqual(engine.chain_of("0x" + "ab" * 20)[0], "robinhood")            # Robinhood is unaffected by the flag


class TestPackWindow(unittest.TestCase):

    def test_find_packs_window(self):
        sig = {f"w{i}": {"kind": "buy", "virgin": True, "eth_in": 100, "is_deployer": False, "block": 100 + i}
               for i in range(3)}
        self.assertEqual(d.find_packs(sig), [])                                       # same block — no pack
        self.assertEqual(d.find_packs(sig, window=2)[0]["wallets"], ["w0", "w1", "w2"])
        sig["w2"]["block"] = 103                                                      # more than 2 slots from the first
        self.assertEqual(d.find_packs(sig, window=2), [])

    def scan(self, mod, token, wallets, eth):
        with enabled(), patched_chain(mod, token, wallets, pack_entries(wallets, eth), pack_history(wallets)):
            events = []
            return engine.scan(token, events.append), events

    def test_adjacent_slots_pack_on_solana_only(self):
        res, events = self.scan(sol, SOL_TOKEN, SOL_WALLETS, 10 ** 8)
        self.assertEqual(res["chain"], "solana")
        self.assertTrue(all(e["chain"] == "solana" for e in events))
        packs = [o for o in res["operators"] if o["level"] == "pack"]
        self.assertEqual(len(packs), 1)
        self.assertEqual(sorted(packs[0]["wallets"]), sorted(SOL_WALLETS[:PACK]))
        self.assertTrue(any("SOL" in e["detail"] for e in events if e["type"] == "wallet_flag"))

        res, events = self.scan(ch, fakes.TOKEN, EVM_WALLETS, 10 ** 16)
        self.assertEqual(res["chain"], "robinhood")
        self.assertTrue(all(e["chain"] == "robinhood" for e in events))
        self.assertEqual([o for o in res["operators"] if len(o["wallets"]) > 1], [])
        self.assertEqual(res["packs"], [])


class TestSolanaReserve(unittest.TestCase):
    """reserve in token_facts: on the curve — virtual reserve, after migration — the real pool balance."""
    POOL = sol.b58encode(bytes([9]) * 32)

    def facts(self, complete, virtual):
        rows = [{"account": "a1", "owner": SOL_CURVE, "amount": 700, "pda": True, "label": "bonding curve"},
                {"account": "a2", "owner": self.POOL, "amount": 900, "pda": True, "label": "PumpSwap pool"},
                {"account": "a3", "owner": SOL_WALLETS[0], "amount": 50, "pda": False, "label": "wallet"}]
        launch = {"block": LAUNCH_SLOT, "curve": SOL_CURVE, "complete": complete, "virtual_token_reserves": virtual}
        base = {"supply": 1650, "circulating": 50, "holders_total": 1, "balances": {SOL_WALLETS[0]: 50}}
        with ExitStack() as st:
            p = lambda name, **kw: st.enter_context(mock.patch.object(sol, name, **kw))
            p("_post", side_effect=fakes._no_network)
            p("supply_base", return_value=base)
            p("get_token_transfers", return_value=([], []))
            p("top_accounts", return_value=rows)
            st.enter_context(mock.patch.dict(sol._OWNER_PROG, {self.POOL: sol.PUMP_SWAP, SOL_CURVE: sol.PUMP}))
            return sol.token_facts(SOL_TOKEN, launch)

    def test_curve_uses_virtual_reserves(self):
        self.assertEqual(self.facts(False, 1_073_000)["reserve"], 1_073_000)

    def test_no_skipped_accounts(self):
        self.assertEqual(self.facts(False, 1_073_000)["skipped"], [])   # nothing was left unread

    def test_curve_without_virtual_uses_real_balance(self):
        self.assertEqual(self.facts(False, None)["reserve"], 700)

    def test_migrated_uses_real_pool_balance(self):
        self.assertEqual(self.facts(True, 1_073_000)["reserve"], 900)

    def test_parse_curve_layout(self):
        data = (b"\0" * 8 + (1_073_000_000_000_000).to_bytes(8, "little") + b"\0" * 32 + b"\1"
                + sol.b58decode(SOL_WALLETS[1]))
        cs = sol._parse_curve(sol.base64.b64encode(data).decode())
        self.assertEqual(cs, {"virtual_token_reserves": 1_073_000_000_000_000, "complete": True,
                              "creator": SOL_WALLETS[1]})


class TestSolanaScan(unittest.TestCase):

    def test_thin_liquidity_from_header(self):
        """Liquidity from the GeckoTerminal header < $1,000 → soft rule; no header — not applied."""
        entries = {w: buy(LAUNCH_SLOT + 300 + 10 * i, 10 ** 8, f"tx{i}") for i, w in enumerate(SOL_WALLETS)}
        history = lambda w, b, t, window=None, cap=4: cap
        for liq, thin in ((114.0, True), (None, False), (5000.0, False)):
            with enabled(), patched_chain(sol, SOL_TOKEN, SOL_WALLETS, entries, history), \
                    mock.patch.object(market, "fetch_market", return_value={"liquidity_usd": liq}):
                res = engine.scan(SOL_TOKEN)
            gate = [g for g in res["gates"] if "liquidity too thin" in g]
            self.assertEqual(bool(gate), thin, liq)
            if thin:
                self.assertEqual(gate, ["soft: liquidity too thin ($114)"])
                self.assertNotIn(res["band"], ("CLEAN", "OK"))
                self.assertEqual(res["header"]["liquidity_usd"], 114.0)

    def test_unread_entries_not_virgin(self):
        entries = pack_entries(SOL_WALLETS, 10 ** 8)
        lost = SOL_WALLETS[5:8]
        for w in lost:
            entries[w] = {"kind": None, "tx": None, "block": None, "via": None, "eth_in": None, "unread": True}
        history = mock.Mock(side_effect=lambda w, b, t, window=None, cap=4: 0)    # had we read them — they would be virgin
        with enabled(), patched_chain(sol, SOL_TOKEN, SOL_WALLETS, entries, history):
            events = []
            res = engine.scan(SOL_TOKEN, events.append)
        self.assertEqual(sorted(res["unread"]), sorted(lost))
        self.assertFalse({c.args[0] for c in history.call_args_list} & set(lost))   # history was not read
        sig = {h["wallet"]: h["signals"] for h in res["holders"]}
        flags = {e["wallet"]: e for e in events if e["type"] == "wallet_flag"}
        for w in lost:
            self.assertTrue(sig[w]["unread"])
            self.assertFalse(sig[w]["virgin"])
            self.assertFalse(sig[w]["sniper"])
            self.assertIsNone(sig[w]["kind"])
            self.assertIn("unread", flags[w]["flags"])
            self.assertNotIn("virgin", flags[w]["flags"])
            self.assertIn("entry not found", flags[w]["detail"])
        self.assertEqual(sum(1 for o in res["operators"] if set(lost) & set(o["wallets"]) and len(o["wallets"]) > 1), 0)
        self.assertEqual(events[-1]["type"], "done")

    def test_all_in_curve_too_early(self):
        with enabled(), patched_chain(sol, SOL_TOKEN, [], {}, lambda *a, **k: 0, bal={}):
            events = []
            res = engine.scan(SOL_TOKEN, events.append)
        self.assertEqual(res["band"], d.TOO_EARLY)
        self.assertIsNone(res["score"])
        self.assertEqual(res["holders"], [])
        self.assertEqual(res["circulating"], 0)
        self.assertEqual(events[-1]["type"], "done")

    def test_not_pump_token(self):
        other = sol.b58encode(bytes([9]) * 32)
        with enabled(), patched_chain(sol, SOL_TOKEN, SOL_WALLETS, pack_entries(SOL_WALLETS, 10 ** 8),
                                      pack_history(SOL_WALLETS)):
            with self.assertRaises(engine.ScanError) as cm:
                engine.scan(other)
        self.assertEqual(str(cm.exception), "not a pump.fun token")


class TestSkippedAccounts(unittest.TestCase):
    """Token accounts whose history does not fit HOLDER_MAX_PAGES pages of SIG_PAGE signatures are skipped;
    the scan reports them (account, owner, reason) and does not take them for wallets without history.
    Everything runs on faked RPC data: no network."""
    OK, LONG, FAILED = SOL_WALLETS[0], SOL_WALLETS[1], SOL_WALLETS[2]   # regular wallets (not PDAs)

    def top_accounts(self, wallet):
        """Top 20 token accounts: one per wallet, plus the curve (infrastructure: not read)."""
        return [{"account": f"acc-{i}", "owner": wallet, "amount": SUPPLY // 100, "pda": False, "label": "wallet"}
                for i, wallet in enumerate(SOL_WALLETS)] + \
               [{"account": "acc-curve", "owner": SOL_CURVE, "amount": 1, "pda": True, "label": "bonding curve"}]

    def history(self):
        """The first wallet's token account history fits one page; the second is longer than the HOLDER_MAX_PAGES cap
        (two full pages of SIG_PAGE plus more — the adapter stops at the cap, so it is truncated); the third
        account's RPC call fails."""
        return {"acc-0": sig_page(sol.SIG_PAGE - 1, "ok"),
                "acc-1": sig_page(sol.HOLDER_MAX_PAGES * sol.SIG_PAGE + 5, "long"), "acc-2": None,
                "acc-curve": sig_page(3, "curve")}

    def run_transfers(self, fake_pages=None):
        rpc = FakeRpc(fake_pages or self.history())
        with ExitStack() as st:
            rpc.patch(st)
            st.enter_context(mock.patch.object(sol, "top_accounts", side_effect=self.top_accounts))
            st.enter_context(mock.patch.dict(sol._PDA_SEEN, clear=True))
            return sol.get_token_transfers(SOL_TOKEN, LAUNCH_SLOT)

    def test_only_truncated_accounts_are_skipped(self):
        transfers, skipped = self.run_transfers()
        self.assertEqual(transfers, [])                        # no transactions faked: parsing gives nothing
        self.assertEqual([s["account"] for s in skipped], ["acc-1", "acc-2"])
        self.assertEqual([s["owner"] for s in skipped], [self.LONG, self.FAILED])
        self.assertIn(f"page cap reached ({sol.HOLDER_MAX_PAGES} × {sol.SIG_PAGE} signatures", skipped[0]["reason"])
        self.assertIn("unavailable", skipped[1]["reason"])
        self.assertNotIn("acc-0", [s["account"] for s in skipped])   # the page was not full: history read in full
        self.assertNotIn("acc-curve", [s["account"] for s in skipped])   # infrastructure is not read at all

    def test_page_boundary_read_in_full(self):
        """A history of exactly SIG_PAGE signatures: the first page is full, the next one is empty — the whole
        history fits in one page, so the account is read, not skipped."""
        transfers, skipped = self.run_transfers({"acc-0": sig_page(sol.SIG_PAGE, "edge")})
        self.assertEqual(transfers, [])
        self.assertEqual(skipped, [])

    def test_exactly_page_limit_is_truncated(self):
        """With a single-page allowance (HOLDER_MAX_PAGES = 1) a history of exactly SIG_PAGE signatures reaches
        the cap — more may follow, so it is skipped, while a shorter one is read as usual."""
        rpc = FakeRpc({"acc-0": sig_page(sol.SIG_PAGE, "at-limit"), "acc-1": sig_page(sol.SIG_PAGE - 1, "under")})
        with ExitStack() as st:
            rpc.patch(st)
            st.enter_context(mock.patch.object(sol, "top_accounts", side_effect=self.top_accounts))
            with mock.patch.object(sol, "HOLDER_MAX_PAGES", 1):
                transfers, skipped = sol.get_token_transfers(SOL_TOKEN, LAUNCH_SLOT)
        self.assertEqual(transfers, [])
        self.assertEqual([s["account"] for s in skipped], ["acc-0"])    # only the account at the cap
        self.assertIn("truncated", skipped[0]["reason"])
        self.assertIn(f"page cap reached (1 × {sol.SIG_PAGE} signatures", skipped[0]["reason"])

    def test_no_truncation_empty_list(self):
        pages = {"acc-0": sig_page(sol.SIG_PAGE - 1, "ok"), "acc-1": sig_page(sol.SIG_PAGE - 2, "ok2"),
                 "acc-curve": sig_page(3, "curve")}
        transfers, skipped = self.run_transfers(pages)
        self.assertEqual(transfers, [])
        self.assertEqual(skipped, [])

    def test_token_facts_passes_list(self):
        rpc = FakeRpc({**self.history(), "acc-0": sig_page(5, "ok")})
        launch = {"block": LAUNCH_SLOT, "curve": SOL_CURVE, "virtual_token_reserves": SUPPLY}
        rows = self.top_accounts
        base = {"supply": SUPPLY, "circulating": SUPPLY, "holders_total": len(SOL_WALLETS),
                "balances": {w: 1 for w in SOL_WALLETS}}
        with ExitStack() as st:
            rpc.patch(st)
            p = lambda name, **kw: st.enter_context(mock.patch.object(sol, name, **kw))
            p("top_accounts", side_effect=rows)
            p("supply_base", return_value=base)
            facts = sol.token_facts(SOL_TOKEN, launch)
        self.assertEqual([s["owner"] for s in facts["skipped"]], [self.LONG, self.FAILED])
        self.assertEqual(facts["transfers"], [])

    def scan(self, truncate):
        """Engine on a full fake chain; truncate — the LONG wallet's token account history was not read in full.
        The adapter still reports an entry for it (a leg can surface from the counterparty's account): the engine
        itself must treat such a wallet as unread, not as a buy / virgin / link evidence."""
        wallets = SOL_WALLETS
        transfers = [{"frm": SOL_CURVE, "to": w, "amount": SUPPLY // 100, "tx": f"tx{i}",
                      "block": LAUNCH_SLOT + 100 + i, "log_index": 0} for i, w in enumerate(wallets)]
        # the LONG wallet got tokens by transfer from another holder: a distributor link through it
        transfers.append({"frm": wallets[3], "to": self.LONG, "amount": SUPPLY // 1000, "tx": "tx-move",
                          "block": LAUNCH_SLOT + 200, "log_index": 0})
        entries = {w: buy(LAUNCH_SLOT + 100 + i, 10 ** 8, f"tx{i}") for i, w in enumerate(wallets)}
        skipped = []
        if truncate:
            skipped = [{"account": "acc-1", "owner": self.LONG,
                        "reason": sol.SKIP_TOO_LONG.format(pages=sol.HOLDER_MAX_PAGES, limit=sol.SIG_PAGE)}]
        base = {"supply": SUPPLY, "circulating": sum(SUPPLY // 100 for _ in wallets), "holders_total": len(wallets),
                "balances": {w: SUPPLY // 100 for w in wallets}}
        history = mock.Mock(side_effect=lambda w, b, t, window=None, cap=4: 0)   # had it been read — a virgin
        with enabled(), patched_chain(sol, SOL_TOKEN, wallets, entries, pack_history(wallets)):
            with mock.patch.object(sol, "wallet_distinct_tokens", side_effect=history):
                facts = {"supply": SUPPLY, "transfers": transfers, "excluded": {SOL_CURVE}, "market": {SOL_CURVE},
                         "reserve": SUPPLY, "base": base, "skipped": skipped}
                with mock.patch.object(market, "fetch_market", return_value={}), \
                        mock.patch.object(sol, "token_facts", return_value=facts):
                    events = []
                    res = engine.scan(SOL_TOKEN, events.append)
        return res, events, history

    def test_scan_reports_skipped(self):
        res, events, _ = self.scan(truncate=True)
        self.assertEqual([s["owner"] for s in res["skipped"]], [self.LONG])
        self.assertIn("page cap reached (2 ×", res["skipped"][0]["reason"])   # the reason is reported
        flags = {e["wallet"]: e for e in events if e["type"] == "wallet_flag"}
        self.assertIn("truncated", flags[self.LONG]["detail"])
        self.assertIn("unread", flags[self.LONG]["flags"])
        self.assertIn(self.LONG, res["unread"])

    def test_skipped_wallet_not_evidence(self):
        """A skipped account is not a virgin wallet, not a sniper, and does not merge into an operator —
        even when the adapter reports an entry for it: the engine overrides it to unread."""
        res, _, history = self.scan(truncate=True)
        sig = {h["wallet"]: h["signals"] for h in res["holders"]}
        s = sig[self.LONG]
        self.assertTrue(s["unread"])                     # not read, not "no history"
        self.assertFalse(s["virgin"])
        self.assertFalse(s["sniper"])
        self.assertFalse(s["launch_bundle"])
        self.assertIsNone(s["kind"])
        self.assertFalse(s["sold"])
        self.assertNotIn(self.LONG, {c.args[0] for c in history.call_args_list})   # history is not read for it
        self.assertNotIn(self.LONG, [w for p in res["packs"] for w in p["wallets"]])
        self.assertEqual([o for o in res["operators"] if self.LONG in o["wallets"] and len(o["wallets"]) > 1], [])
        self.assertEqual([l for l in res["links"] if self.LONG in (l["a"], l["b"])], [])   # its transfer is no link
        self.assertNotIn("virgin", [p["kind"] for p in (res["rug"] or {}).get("parts", [])])

    def test_all_in_curve_too_early_with_skipped(self):
        """All tokens in the curve: still TOO EARLY; the skipped list does not turn into holders or signals."""
        with enabled(), patched_chain(sol, SOL_TOKEN, [], {}, lambda *a, **k: 0, bal={}):
            facts = {"supply": SUPPLY, "transfers": [], "base": None, "excluded": {SOL_CURVE},
                     "market": {SOL_CURVE}, "reserve": SUPPLY, "skipped": []}
            with mock.patch.object(market, "fetch_market", return_value={}), \
                    mock.patch.object(sol, "token_facts", return_value=facts):
                res = engine.scan(SOL_TOKEN)
        self.assertEqual(res["band"], d.TOO_EARLY)
        self.assertIsNone(res["score"])
        self.assertEqual(res["holders"], [])
        self.assertEqual(res["skipped"], [])

    def test_no_skipped_unchanged(self):
        """No truncated accounts: the skipped list is empty and buys/transfers are parsed as before."""
        res, _, _ = self.scan(truncate=False)
        self.assertEqual(res["skipped"], [])
        self.assertEqual({h["wallet"]: h["signals"]["kind"] for h in res["holders"]},
                         {w: "buy" for w in SOL_WALLETS})
        self.assertEqual(res["unread"], [])
        self.assertEqual(sorted(res["packs"][0]["wallets"]), sorted(SOL_WALLETS[:PACK]))   # as before: the pack holds
        # the transfer between holders is still a proven direct link, as before
        self.assertIn({"a": SOL_WALLETS[3], "b": self.LONG, "kind": "direct", "level": "proven", "via": "tx-move"},
                      res["links"])


class TestSkippedFrontend(unittest.TestCase):
    """Built index.html shows the scan's coverage: the skipped list from the result (raw), nothing for other chains."""

    def test_built_page(self):
        import json, re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "index.html"), encoding="utf-8") as f:
            src = f.read()
        a = re.search(r'<script type="__bundler/template">', src).end()
        t = json.loads(src[a:src.find("</script>", a)])
        self.assertIn("skipVals(){", t)
        self.assertIn("raw.skipped", t)                     # the list comes from the scan result
        self.assertIn("Coverage incomplete:", t)
        self.assertIn("...this.skipVals(),", t)             # in the props of the result card
        self.assertIn('<sc-if value="{{skipOn}}"', t)       # shown only when the list is not empty


if __name__ == "__main__":
    unittest.main()
