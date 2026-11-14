"""局域网扫描结果完整性的回归测试（不访问真实网络）。

覆盖 scan_devices 的四种完成状态：
  - not_scanned：本机没有局域网前缀
  - complete   ：扫描完整、无设备
  - complete   ：扫描完整、发现设备
  - partial    ：平台探测失败 / 超时，或端口扫描批次超时
"""

import time
import unittest
from unittest import mock

from ascript_mcp import device as dev


class ScanDevicesResultTest(unittest.TestCase):
    def _run(self, prefixes, is_open, probe, **kwargs):
        kwargs.setdefault("total_timeout_s", 5.0)
        kwargs.setdefault("concurrency", 32)
        with mock.patch.object(dev, "_get_local_prefixes", return_value=list(prefixes)), \
             mock.patch.object(dev, "_is_port_open", side_effect=is_open), \
             mock.patch.object(dev, "_probe_platform", side_effect=probe):
            return dev.scan_devices(**kwargs)

    def test_no_lan_prefix_means_not_scanned(self):
        result = self._run([], lambda ip, port, timeout_s=0.8: False,
                           lambda ip, port, password="", timeout=5.0: "android")
        self.assertEqual(result["devices"], [])
        self.assertEqual(result["status"], "not_scanned")
        self.assertFalse(result["complete"])
        self.assertFalse(result["scan"]["scanned"])

    def test_complete_scan_without_devices(self):
        result = self._run(["10.9.9."], lambda ip, port, timeout_s=0.8: False,
                           lambda ip, port, password="", timeout=5.0: None)
        self.assertEqual(result["devices"], [])
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["complete"])
        self.assertEqual(result["scan"]["scanned_prefixes"], ["10.9.9."])
        self.assertEqual(result["scan"]["platform_probe_incomplete_count"], 0)

    def test_complete_scan_with_devices_keeps_device_fields(self):
        open_ips = {"10.9.9.5", "10.9.9.7"}
        result = self._run(
            ["10.9.9."],
            lambda ip, port, timeout_s=0.8: ip in open_ips,
            lambda ip, port, password="", timeout=5.0:
                "android" if ip.endswith(".5") else "ios",
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["devices"]), 2)
        for device in result["devices"]:
            self.assertEqual(set(device), {"name", "ip", "port", "platform"})
        self.assertEqual(
            {d["ip"]: d["platform"] for d in result["devices"]},
            {"10.9.9.5": "android", "10.9.9.7": "ios"},
        )

    def test_platform_probe_failure_is_partial_and_not_a_device(self):
        open_ips = {"10.9.9.5", "10.9.9.9"}
        result = self._run(
            ["10.9.9."],
            lambda ip, port, timeout_s=0.8: ip in open_ips,
            lambda ip, port, password="", timeout=5.0:
                None if ip.endswith(".9") else "android",
        )
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["complete"])
        self.assertEqual([d["ip"] for d in result["devices"]], ["10.9.9.5"])
        self.assertEqual(result["scan"]["platform_probe_failed_count"], 1)
        self.assertIn(
            {"ip": "10.9.9.9", "port": 9096, "reason": "no_platform"},
            result["scan"]["probe_failures"],
        )

    def test_hanging_platform_probe_respects_total_timeout(self):
        open_ips = {"10.9.9.5", "10.9.9.11"}

        def probe(ip, port, password="", timeout=5.0):
            if ip.endswith(".11"):
                time.sleep(3)
                return "ios"
            return "android"

        started = time.monotonic()
        result = self._run(
            ["10.9.9."],
            lambda ip, port, timeout_s=0.8: ip in open_ips,
            probe,
            total_timeout_s=1.0,
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["scan"]["timed_out"])
        self.assertEqual(result["scan"]["platform_probe_timeout_count"], 1)
        self.assertEqual([d["ip"] for d in result["devices"]], ["10.9.9.5"])

    def test_hanging_port_batch_marks_prefix_unscanned(self):
        def is_open(ip, port, timeout_s=0.8):
            if ip.endswith(".200"):
                time.sleep(3)
            return False

        started = time.monotonic()
        result = self._run(
            ["10.9.9."],
            is_open,
            lambda ip, port, password="", timeout=5.0: "android",
            total_timeout_s=1.0,
            concurrency=32,
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["scan"]["timed_out"])
        self.assertEqual(result["scan"]["unscanned_prefixes"][0]["prefix"], "10.9.9.")
        self.assertLess(
            result["scan"]["unscanned_prefixes"][0]["scanned_ips"],
            result["scan"]["unscanned_prefixes"][0]["total_ips"],
        )

    def test_baseline_constants_unchanged(self):
        self.assertEqual(dev.LAN_SCAN_TOTAL_TIMEOUT_S, 25.0)
        self.assertEqual(dev.LAN_SCAN_CONCURRENCY, 80)
        self.assertEqual(dev.LAN_SCAN_PORT_TIMEOUT_S, 0.8)


if __name__ == "__main__":
    unittest.main()
