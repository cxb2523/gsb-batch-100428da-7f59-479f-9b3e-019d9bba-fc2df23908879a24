import unittest

from core.url_parser import normalize_douyin_url


class UrlParserTest(unittest.TestCase):
    def test_live_url_normalizes_room_id(self):
        target = normalize_douyin_url("https://live.douyin.com/123456789?foo=bar")

        self.assertEqual(target.normalized_url, "https://live.douyin.com/123456789")
        self.assertEqual(target.live_room_id, "123456789")
        self.assertIsNone(target.sec_uid)
        self.assertEqual(target.douyin_id, "room_123456789")

    def test_user_url_normalizes_sec_uid(self):
        target = normalize_douyin_url("https://www.douyin.com/user/MS4wLjABAAAAabc")

        self.assertEqual(target.normalized_url, "https://www.douyin.com/user/MS4wLjABAAAAabc")
        self.assertEqual(target.sec_uid, "MS4wLjABAAAAabc")
        self.assertIsNone(target.live_room_id)
        self.assertEqual(target.douyin_id, "user_MS4wLjABAAAAabc")

    def test_rejects_non_douyin_url(self):
        with self.assertRaises(ValueError):
            normalize_douyin_url("https://example.com/live/123")


if __name__ == "__main__":
    unittest.main()
