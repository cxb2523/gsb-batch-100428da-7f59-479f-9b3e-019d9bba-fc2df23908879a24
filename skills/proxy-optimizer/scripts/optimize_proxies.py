#!/usr/bin/env python3
"""
Target Site Proxy Optimizer

Tests all proxies in a Webshare plan against a target URL, identifies which
proxies/countries/ASNs are blocked, and outputs a structured report that an
AI agent can use to drive replacements via the Webshare MCP.

Plan and proxy data come from the webshare CLI (which reads WEBSHARE_API_KEY);
only the target-site probes talk to the network directly.

Proxies that cannot be probed (for example records without an address, port
or credentials) are reported separately as "unprobed" with a reason. They are
never silently dropped and never counted in the success rate.

Usage:
    python optimize_proxies.py --target https://example.com
    python optimize_proxies.py --target https://example.com --plan-id 12345
    python optimize_proxies.py --target https://example.com --timeout 10 --workers 20
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict


# Fields a direct proxy needs before it can be probed at all.
REQUIRED_PROXY_FIELDS = ("proxy_address", "port", "username", "password")


def cli_json(*args):
    """Run a webshare CLI command with --json and return the parsed output."""
    cli = shutil.which("webshare")
    if not cli:
        print("ERROR: webshare CLI not found on PATH. Install it with "
              "`brew install webshare-proxy/tap/webshare` or from "
              "https://github.com/webshare-proxy/webshare-cli/releases",
              file=sys.stderr)
        sys.exit(1)
    result = subprocess.run([cli, *args, "--json"],
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"ERROR: webshare {' '.join(args)} failed: "
              f"{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def fetch_all_proxies(plan_id):
    """Return every proxy record in the plan.

    Records that can't be probed (residential pools without a per-proxy
    address, incomplete records) are kept here on purpose: they are routed
    into the "unprobed" section of the report rather than dropped.
    """
    return cli_json("proxies", "list", "--plan", str(plan_id),
                    "--mode", "direct")


def get_active_plan():
    plans = cli_json("plans", "list")
    active = [p for p in plans if p.get("status") == "active"]
    for plan in active:
        if plan.get("monthly_price", 0) > 0:
            return plan
    if active:
        return active[0]
    print("ERROR: No active plan found", file=sys.stderr)
    sys.exit(1)


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def validate_proxy(proxy):
    """Check whether a proxy record has everything needed for a probe.

    Returns (normalized_proxy, None) when it can be probed, otherwise
    (None, reason) describing what is missing or invalid.
    """
    if not isinstance(proxy, dict):
        return None, "proxy record is not an object"

    missing = [field for field in REQUIRED_PROXY_FIELDS
               if _is_missing(proxy.get(field))]
    if missing:
        return None, "missing required field(s): " + ", ".join(missing)

    port = proxy.get("port")
    if isinstance(port, bool):
        return None, f"invalid port: {port!r}"
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        return None, f"invalid port: {port!r}"
    if not 1 <= port_number <= 65535:
        return None, f"invalid port: {port!r}"

    normalized = dict(proxy)
    normalized["proxy_address"] = str(proxy["proxy_address"]).strip()
    normalized["port"] = port_number
    normalized["username"] = str(proxy["username"])
    normalized["password"] = str(proxy["password"])
    return normalized, None


def describe_unprobed(proxy, reason):
    """Build the report entry for a proxy that was never tested."""
    record = proxy if isinstance(proxy, dict) else {}
    entry = {
        "proxy_address": record.get("proxy_address"),
        "port": record.get("port"),
        "country_code": record.get("country_code"),
        "reason": reason,
    }
    if "id" in record:
        entry["id"] = record.get("id")
    return entry


def prepare_proxies(proxies):
    """Split records into probeable proxies and unprobed entries.

    Returns (probeable, unprobed), both preserving input order.
    """
    probeable = []
    unprobed = []
    for proxy in proxies:
        normalized, reason = validate_proxy(proxy)
        if reason is not None:
            unprobed.append(describe_unprobed(proxy, reason))
        else:
            probeable.append(normalized)
    return probeable, unprobed


def _result(proxy, success, status_code, latency_ms, error):
    return {
        "proxy_address": proxy.get("proxy_address"),
        "port": proxy.get("port"),
        "country_code": proxy.get("country_code"),
        "city_name": proxy.get("city_name", ""),
        "asn_name": proxy.get("asn_name", ""),
        "asn_number": proxy.get("asn_number"),
        "success": success,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "error": error,
    }


def exception_result(proxy, exc, started=None):
    """Turn an exception raised by a probe task into a failed probe result."""
    latency_ms = 0
    if started is not None:
        latency_ms = round((time.time() - started) * 1000)
    message = f"{type(exc).__name__}: {exc}"
    return _result(proxy, False, None, latency_ms, message)


def test_proxy(proxy, target_url, timeout):
    """Probe one normalized proxy. Network errors are fully contained here."""
    ip = proxy["proxy_address"]
    port = proxy["port"]
    user = proxy["username"]
    pw = proxy["password"]

    proxy_url = f"http://{user}:{pw}@{ip}:{port}"

    handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    ctx = ssl.create_default_context()
    https_handler = urllib.request.HTTPSHandler(context=ctx)
    opener = urllib.request.build_opener(handler, https_handler)

    start = time.time()
    try:
        req = urllib.request.Request(target_url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        resp = opener.open(req, timeout=timeout)
        elapsed = time.time() - start
        status = resp.getcode()
        return _result(proxy, 200 <= status < 400, status,
                       round(elapsed * 1000), None)
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        return _result(proxy, False, e.code, round(elapsed * 1000),
                       f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        elapsed = time.time() - start
        return _result(proxy, False, None, round(elapsed * 1000), str(e))


def safe_test_proxy(proxy, target_url, timeout):
    """Guard every probe task: an exception only fails this one proxy.

    Errors raised outside test_proxy's own network exception handling (e.g.
    malformed data or a bug in the opener construction) are converted into a
    failed result for that proxy instead of propagating and aborting the batch.
    """
    started = time.time()
    try:
        return test_proxy(proxy, target_url, timeout)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        # Exception and SystemExit/GeneratorExit alike: a probe task must
        # never take the rest of the batch down with it.
        return exception_result(proxy, exc, started)


def subnet_for(ip):
    """Map an IPv4 address to its /24 subnet; fall back to the raw address."""
    parts = str(ip).split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return ".".join(parts[:3]) + ".0/24"
    return str(ip) if ip else "UNKNOWN"


def _append_unique(items, ip):
    if ip not in items:
        items.append(ip)


def analyze_results(results):
    """Analyze only actually probed results (unprobed proxies excluded)."""
    by_country = defaultdict(lambda: {"total": 0, "failed": 0, "ips": []})
    by_asn = defaultdict(lambda: {"total": 0, "failed": 0, "name": "", "ips": []})
    by_subnet = defaultdict(lambda: {"total": 0, "failed": 0, "ips": []})
    failed_ips = []
    failed_count = 0

    for r in results:
        ip = r.get("proxy_address") or ""
        cc = str(r.get("country_code") or "UNKNOWN")
        asn = r.get("asn_number")
        asn_name = r.get("asn_name") or ""
        subnet = subnet_for(ip)

        by_country[cc]["total"] += 1
        if asn is not None and str(asn).strip():
            by_asn[asn]["total"] += 1
            by_asn[asn]["name"] = asn_name
        by_subnet[subnet]["total"] += 1

        if not r.get("success"):
            failed_count += 1
            by_country[cc]["failed"] += 1
            _append_unique(by_country[cc]["ips"], ip)
            if asn is not None and str(asn).strip():
                by_asn[asn]["failed"] += 1
                _append_unique(by_asn[asn]["ips"], ip)
            by_subnet[subnet]["failed"] += 1
            _append_unique(by_subnet[subnet]["ips"], ip)
            _append_unique(failed_ips, ip)

    total = len(results)
    success_rate = round((total - failed_count) / total * 100, 1) if total else 0

    problem_countries = {
        cc: {
            "total": d["total"],
            "failed": d["failed"],
            "failure_rate": round(d["failed"] / d["total"] * 100, 1),
            "failed_ips": d["ips"],
        }
        for cc, d in by_country.items()
        if d["failed"] > 0
    }

    problem_asns = {
        str(asn): {
            "name": d["name"],
            "total": d["total"],
            "failed": d["failed"],
            "failure_rate": round(d["failed"] / d["total"] * 100, 1),
            "failed_ips": d["ips"],
        }
        for asn, d in by_asn.items()
        if d["failed"] > 0
    }

    problem_subnets = {
        subnet: {
            "total": d["total"],
            "failed": d["failed"],
            "failure_rate": round(d["failed"] / d["total"] * 100, 1),
            "failed_ips": d["ips"],
        }
        for subnet, d in by_subnet.items()
        if d["failed"] > 0
    }

    problem_countries = dict(sorted(problem_countries.items(), key=lambda x: x[1]["failure_rate"], reverse=True))
    problem_asns = dict(sorted(problem_asns.items(), key=lambda x: x[1]["failure_rate"], reverse=True))
    problem_subnets = dict(sorted(problem_subnets.items(), key=lambda x: x[1]["failure_rate"], reverse=True))

    return {
        "total_proxies": total,
        "failed_proxies": failed_count,
        "success_rate": success_rate,
        "failed_ips": failed_ips,
        "problem_countries": problem_countries,
        "problem_asns": problem_asns,
        "problem_subnets": problem_subnets,
    }


def build_recommendations(analysis, plan):
    recs = []
    replacements_available = plan.get("proxy_replacements_available", 0) or 0

    failed_ips = list(analysis.get("failed_ips", []))
    if not failed_ips:
        return recs

    # Track IPs already scheduled so a group-level recommendation and the
    # fallback individual recommendation never name the same IP twice.
    covered = set()

    def claim_group(ips):
        """Claim a whole group for replacement, or None if it overlaps.

        A group that is already partly covered by a previous recommendation
        is left to the individual fallback: emitting it would either repeat
        IPs or misrepresent it as a full-group replacement.
        """
        if any(ip in covered for ip in ips):
            return None
        covered.update(ips)
        return list(ips)

    for asn_id, data in analysis["problem_asns"].items():
        if data["failure_rate"] == 100 and data["total"] >= 2:
            ips = claim_group(data["failed_ips"])
            if not ips:
                continue
            recs.append({
                "action": "replace_by_asn",
                "reason": f"ASN {data['name']} (AS{asn_id}) has 100% failure rate ({data['total']} proxies)",
                "ips_to_replace": ips,
                "count": len(ips),
            })

    for subnet, data in analysis["problem_subnets"].items():
        if data["failure_rate"] == 100 and data["total"] >= 2:
            ips = claim_group(data["failed_ips"])
            if not ips:
                continue
            recs.append({
                "action": "replace_by_subnet",
                "reason": f"Subnet {subnet} has 100% failure rate ({data['total']} proxies)",
                "ips_to_replace": ips,
                "count": len(ips),
            })

    remaining = [ip for ip in failed_ips if ip not in covered]
    if remaining:
        recs.append({
            "action": "replace_individual",
            "reason": f"{len(remaining)} additional proxy(ies) failed individually",
            "ips_to_replace": remaining,
            "count": len(remaining),
        })

    total_to_replace = len({ip for r in recs for ip in r["ips_to_replace"]})
    return {
        "recommendations": recs,
        "total_ips_to_replace": total_to_replace,
        "replacement_credits_available": replacements_available,
        "can_replace_all": total_to_replace <= replacements_available,
        "mcp_actions": build_mcp_actions(analysis, recs),
    }


def build_mcp_actions(analysis, recs):
    all_ips = []
    for r in recs:
        for ip in r["ips_to_replace"]:
            if ip not in all_ips:
                all_ips.append(ip)

    if not all_ips:
        return []

    working_countries = []
    for cc, data in analysis.get("problem_countries", {}).items():
        if data["failure_rate"] < 50:
            working_countries.append(cc)

    if not working_countries:
        replace_with = [{"type": "any"}]
    else:
        replace_with = [{"type": "country", "country_code": cc} for cc in working_countries[:3]]

    return [
        {
            "tool": "create_proxy_replacement",
            "description": f"Replace {len(all_ips)} failing proxies",
            "params": {
                "to_replace": {
                    "type": "ip_address",
                    "ip_addresses": all_ips,
                },
                "replace_with": replace_with,
                "dry_run": True,
            },
            "note": "Run with dry_run=true first, then confirm with the user before setting dry_run=false",
        }
    ]


def probe_proxies(proxies, target_url, timeout, workers, progress=None):
    """Probe proxies concurrently. Never lets one task abort the batch.

    Returns results in input order, with exactly one entry per submitted
    proxy. Exceptions raised by a task become that proxy's failed result.
    """
    results_by_index = {}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(safe_test_proxy, proxy, target_url, timeout): index
            for index, proxy in enumerate(proxies)
        }
        for future in as_completed(futures):
            index = futures[future]
            proxy = proxies[index]
            try:
                result = future.result()
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                # Belt and braces: even a failure outside safe_test_proxy
                # (including a future that raises on result()) stays local
                # to this proxy.
                result = exception_result(proxy, exc)
            results_by_index[index] = result
            if progress is not None:
                progress(result, len(results_by_index), len(proxies))

    return [results_by_index[index] for index in sorted(results_by_index)]


def main():
    parser = argparse.ArgumentParser(description="Test proxies against a target site and find what to replace")
    parser.add_argument("--target", required=True, help="Target URL to test proxies against")
    parser.add_argument("--plan-id", type=int, help="Webshare plan ID (auto-detected if omitted)")
    parser.add_argument("--timeout", type=int, default=15, help="Request timeout in seconds (default: 15)")
    parser.add_argument("--workers", type=int, default=10, help="Concurrent test workers (default: 10)")
    parser.add_argument("--sample", type=int, help="Test only N random proxies instead of all")
    args = parser.parse_args()

    print(f"Target: {args.target}", file=sys.stderr)

    if args.plan_id:
        plan = cli_json("plans", "show", str(args.plan_id))
    else:
        plan = get_active_plan()
    plan_id = plan["id"]
    print(f"Plan: {plan_id} ({plan.get('proxy_type', '?')}/{plan.get('proxy_subtype', '?')}, {plan.get('proxy_count', '?')} proxies, {plan.get('proxy_replacements_available', 0)} replacement credits)", file=sys.stderr)

    print("Fetching proxy list...", file=sys.stderr)
    proxies = fetch_all_proxies(plan_id)
    print(f"Found {len(proxies)} proxies", file=sys.stderr)

    if args.sample and args.sample < len(proxies):
        import random
        proxies = random.sample(proxies, args.sample)
        print(f"Sampling {args.sample} proxies", file=sys.stderr)

    probeable, unprobed = prepare_proxies(proxies)
    if unprobed:
        print(f"{len(unprobed)} proxies cannot be probed (unprobed):", file=sys.stderr)
        for entry in unprobed:
            label = entry["proxy_address"] or (f"id={entry['id']}" if "id" in entry else "<unknown>")
            print(f"  [unprobed] {label} -> {entry['reason']}", file=sys.stderr)

    print(f"Testing {len(probeable)} proxies against {args.target} ({args.workers} workers, {args.timeout}s timeout)...", file=sys.stderr)

    def report_progress(result, done, total):
        status = "OK" if result["success"] else "FAIL"
        detail = result.get("error") or result.get("status_code", "")
        print(f"  [{done}/{total}] {result['proxy_address']} ({result['country_code']}) -> {status} {detail} ({result['latency_ms']}ms)", file=sys.stderr)

    results = probe_proxies(probeable, args.target, args.timeout,
                            args.workers, progress=report_progress)

    analysis = analyze_results(results)
    recommendations = build_recommendations(analysis, plan)

    output = {
        "target": args.target,
        "plan_id": plan_id,
        "plan_product": f"{plan.get('proxy_type', '?')}/{plan.get('proxy_subtype', '?')}",
        "summary": {
            "total_proxies": len(proxies),
            "total_tested": analysis["total_proxies"],
            "successful": analysis["total_proxies"] - analysis["failed_proxies"],
            "failed": analysis["failed_proxies"],
            "success_rate": analysis["success_rate"],
            "unprobed": len(unprobed),
        },
        "unprobed_proxies": unprobed,
        "problem_countries": analysis["problem_countries"],
        "problem_asns": analysis["problem_asns"],
        "problem_subnets": analysis["problem_subnets"],
        "recommendations": recommendations,
        "raw_results": results,
    }

    print(json.dumps(output, indent=2))

    print("\n--- Summary ---", file=sys.stderr)
    print(f"Success rate: {analysis['success_rate']}% ({analysis['total_proxies'] - analysis['failed_proxies']}/{analysis['total_proxies']} tested)", file=sys.stderr)
    if unprobed:
        print(f"Unprobed proxies: {len(unprobed)} (not counted in success rate; see unprobed_proxies)", file=sys.stderr)
    if analysis["failed_proxies"] > 0:
        print(f"Failed proxies: {analysis['failed_proxies']}", file=sys.stderr)
        if analysis["problem_countries"]:
            countries = ", ".join(f"{cc} ({d['failure_rate']}%)" for cc, d in analysis["problem_countries"].items())
            print(f"Problem countries: {countries}", file=sys.stderr)
        if analysis["problem_asns"]:
            asns = ", ".join(f"{d['name']} ({d['failure_rate']}%)" for d in analysis["problem_asns"].values())
            print(f"Problem ASNs: {asns}", file=sys.stderr)
        if recommendations and recommendations.get("mcp_actions"):
            print(f"\nRecommended MCP action: replace {recommendations['total_ips_to_replace']} proxies ({recommendations['replacement_credits_available']} credits available)", file=sys.stderr)
    elif analysis["total_proxies"] == 0 and unprobed:
        print("No proxies could be probed - see unprobed reasons above.", file=sys.stderr)
    elif analysis["total_proxies"] == 0:
        print("No proxies found to test.", file=sys.stderr)
    else:
        print("All tested proxies working - no action needed.", file=sys.stderr)


if __name__ == "__main__":
    main()
