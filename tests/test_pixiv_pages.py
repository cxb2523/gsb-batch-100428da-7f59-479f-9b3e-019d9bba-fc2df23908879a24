"""pixiv_pages.py 的本地偽造響應測試，不需真實連線 Pixiv。

以 -I 隔離模式執行：
    python -I tests/test_pixiv_pages.py
"""

import json
import os
import sys
import tempfile
import unittest

# -I 模式下不會把腳本目錄外的路徑加入 sys.path，手動加入 repo 根目錄。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pixiv_pages as pp  # noqa: E402


class FakeNetworkError(Exception):
    """模擬 requests 可能拋出的連線層異常。"""


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content

    def json(self):
        if self._json_data is None:
            raise ValueError("fake response has no JSON body")
        return self._json_data


class FakeClient:
    """以路由表模擬 Pixiv 與圖床的所有回應，可安排 HTTP 錯誤與異常。"""

    SEARCH_PREFIX = "https://www.pixiv.net/ajax/search/illustrations/"

    def __init__(self):
        self.search_routes = {}   # page_num -> response
        self.work_routes = {}     # illust_id -> response
        self.image_routes = {}    # url -> response
        self.search_calls = []
        self.work_calls = []
        self.image_calls = []

    # ---- 路由安排 ----
    def set_search(self, page_num, illust_ids):
        self.search_routes[page_num] = FakeResponse(json_data={
            "body": {"illust": {"data": [{"id": str(i)} for i in illust_ids]}}
        })

    def set_search_empty(self, page_num):
        self.search_routes[page_num] = FakeResponse(json_data={
            "body": {"illust": {"data": []}}
        })

    def set_search_http_error(self, page_num, code):
        self.search_routes[page_num] = FakeResponse(status_code=code)

    def set_search_exception(self, page_num, exc):
        self.search_routes[page_num] = exc

    def set_work(self, illust_id, image_urls):
        self.work_routes[str(illust_id)] = FakeResponse(json_data={
            "body": [{"urls": {"original": u}} for u in image_urls]
        })

    def set_work_http_error(self, illust_id, code):
        self.work_routes[str(illust_id)] = FakeResponse(status_code=code)

    def set_work_exception(self, illust_id, exc):
        self.work_routes[str(illust_id)] = exc

    def set_image(self, url, content=None):
        self.image_routes[url] = FakeResponse(
            content=content if content is not None else b"img-" + url.encode())

    def set_image_http_error(self, url, code):
        self.image_routes[url] = FakeResponse(status_code=code)

    def set_image_exception(self, url, exc):
        self.image_routes[url] = exc

    # ---- 與 RequestsClient 相同的介面 ----
    def get(self, url, params=None, headers=None):
        if url.startswith(self.SEARCH_PREFIX):
            page_num = params["p"]
            self.search_calls.append(page_num)
            route = self.search_routes[page_num]
        elif "/ajax/illust/" in url:
            illust_id = url.split("/ajax/illust/", 1)[1].split("/", 1)[0]
            self.work_calls.append(illust_id)
            route = self.work_routes[illust_id]
        else:
            self.image_calls.append(url)
            route = self.image_routes[url]

        if isinstance(route, BaseException):
            raise route
        return route


def image_url(illust_id, page_index, ext="jpg"):
    return (f"https://i.pximg.net/img-original/img/2024/01/01/00/00/00/"
            f"{illust_id}_p{page_index}.{ext}")


def image_filename(url):
    return url.split("/")[-1]


class PixivPagesTests(unittest.TestCase):

    def setUp(self):
        self.download_folder = tempfile.mkdtemp(prefix="pixiv-test-")
        self.progress_path = os.path.join(
            self.download_folder, pp.RECORD_FILENAME)
        self.client = FakeClient()

    def read_progress(self):
        with open(self.progress_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def build_mixed_scenario(self):
        """第 1 頁：成功／圖片 404／作品分頁異常；第 2 頁：空；第 3 頁：HTTP 500。"""
        c = self.client
        # 作品 1001：兩張圖都成功
        c.set_search(1, [1001, 1002, 1003])
        urls_1001 = [image_url(1001, 0), image_url(1001, 1, "png")]
        c.set_work(1001, urls_1001)
        for u in urls_1001:
            c.set_image(u)

        # 作品 1002：第一張成功，第二張 HTTP 404
        urls_1002 = [image_url(1002, 0), image_url(1002, 1)]
        c.set_work(1002, urls_1002)
        c.set_image(urls_1002[0])
        c.set_image_http_error(urls_1002[1], 404)

        # 作品 1003：分頁請求直接拋異常
        c.set_work_exception(1003, FakeNetworkError("connection reset"))

        c.set_search_empty(2)
        c.set_search_http_error(3, 500)
        return urls_1001, urls_1002

    def test_full_run_records_three_outcomes_and_continues(self):
        urls_1001, urls_1002 = self.build_mixed_scenario()

        result = pp.run_download(
            "鏡流", 1, 3, self.download_folder,
            client=self.client, progress_path=self.progress_path)

        # 彙總數字
        self.assertEqual(result["summary"][pp.STATUS_SUCCESS], 1)
        self.assertEqual(result["summary"][pp.STATUS_FAILED], 2)
        self.assertEqual(result["summary"][pp.STATUS_SKIPPED], 0)

        # 失敗原因分別是 HTTP 狀態碼與異常類型
        reasons = {f["id"]: f["reason"] for f in result["failures"]}
        self.assertEqual(reasons["1002"], "HTTP 404")
        self.assertTrue(reasons["1003"].startswith("FakeNetworkError:"))
        self.assertEqual(
            result["failures"][0]["url"],
            "https://www.pixiv.net/artworks/1002")

        # 空頁不計錯誤；搜尋失敗頁有記錄且整批未中斷
        self.assertEqual(result["empty_pages"], [2])
        self.assertEqual(result["failed_pages"], [(3, "HTTP 500")])

        # 成功作品的圖片確實落盤，失敗作品的成功圖片也保留
        self.assertTrue(os.path.exists(
            os.path.join(self.download_folder, image_filename(urls_1001[0]))))
        self.assertTrue(os.path.exists(
            os.path.join(self.download_folder, image_filename(urls_1001[1]))))
        self.assertTrue(os.path.exists(
            os.path.join(self.download_folder, image_filename(urls_1002[0]))))
        self.assertFalse(os.path.exists(
            os.path.join(self.download_folder, image_filename(urls_1002[1]))))

        # 記錄檔內容正確
        progress = self.read_progress()
        self.assertEqual(progress["pages"]["1"]["status"], pp.PAGE_DONE)
        self.assertEqual(progress["pages"]["1"]["illust_ids"],
                         ["1001", "1002", "1003"])
        self.assertEqual(progress["pages"]["2"]["status"], pp.PAGE_NO_RESULTS)
        self.assertEqual(progress["pages"]["3"]["status"], pp.PAGE_FAILED)
        self.assertEqual(progress["works"]["1001"]["status"], pp.STATUS_SUCCESS)
        self.assertEqual(progress["works"]["1002"]["status"], pp.STATUS_FAILED)
        self.assertEqual(progress["works"]["1002"]["failed_images"][0]["reason"],
                         "HTTP 404")
        self.assertEqual(progress["works"]["1003"]["status"], pp.STATUS_FAILED)
        self.assertIn("FakeNetworkError",
                      progress["works"]["1003"]["reason"])

    def test_resume_skips_done_and_retries_only_unfinished(self):
        self.build_mixed_scenario()
        pp.run_download("鏡流", 1, 3, self.download_folder,
                        client=self.client, progress_path=self.progress_path)

        # 第二次執行：修復所有失敗來源，另讓第 3 頁出現新作品
        c = self.client
        fixed_url = image_url(1002, 1)
        c.set_image(fixed_url)
        url_1003 = image_url(1003, 0)
        c.set_work(1003, [url_1003])
        c.set_image(url_1003)
        c.set_search(3, [1004])
        url_1004 = image_url(1004, 0)
        c.set_work(1004, [url_1004])
        c.set_image(url_1004)

        c.search_calls.clear()
        c.work_calls.clear()
        c.image_calls.clear()

        result = pp.run_download(
            "鏡流", 1, 3, self.download_folder,
            client=c, progress_path=self.progress_path)

        # 1001 跳過；1002、1003 補跑成功；1004 新下載
        self.assertEqual(result["summary"][pp.STATUS_SKIPPED], 1)
        self.assertEqual(result["summary"][pp.STATUS_SUCCESS], 3)
        self.assertEqual(result["summary"][pp.STATUS_FAILED], 0)
        self.assertTrue(result["resumed"])
        self.assertEqual(result["failures"], [])

        # 第 1、2 頁沿用快取，不應再發搜尋請求；只有失敗過的第 3 頁重試
        self.assertEqual(c.search_calls, [3])
        # 1001 不應再請求分頁
        self.assertNotIn("1001", c.work_calls)
        self.assertEqual(set(c.work_calls), {"1002", "1003", "1004"})
        # 已存在的圖片不重抓
        self.assertNotIn(image_url(1001, 0), c.image_calls)
        self.assertNotIn(image_url(1002, 0), c.image_calls)
        self.assertIn(fixed_url, c.image_calls)

        # 全部圖片到齊
        for url in (image_url(1001, 0), image_url(1001, 1, "png"),
                    image_url(1002, 0), fixed_url, url_1003, url_1004):
            self.assertTrue(
                os.path.exists(
                    os.path.join(self.download_folder, image_filename(url))),
                f"缺少 {url}")

        progress = self.read_progress()
        self.assertEqual(progress["works"]["1001"]["last_outcome"],
                         pp.STATUS_SKIPPED)
        self.assertEqual(progress["works"]["1002"]["status"],
                         pp.STATUS_SUCCESS)
        self.assertEqual(progress["works"]["1003"]["status"],
                         pp.STATUS_SUCCESS)
        self.assertEqual(progress["pages"]["3"]["status"], pp.PAGE_DONE)

    def test_missing_record_starts_fresh(self):
        self.client.set_search_empty(5)
        result = pp.run_download(
            "cat", 5, 5, self.download_folder,
            client=self.client, progress_path=self.progress_path)

        self.assertFalse(result["resumed"])
        self.assertTrue(os.path.exists(self.progress_path))
        self.assertEqual(result["empty_pages"], [5])
        self.assertEqual(result["summary"],
                         {pp.STATUS_SUCCESS: 0, pp.STATUS_FAILED: 0,
                          pp.STATUS_SKIPPED: 0})

    def test_corrupt_record_is_backed_up_and_restarts(self):
        with open(self.progress_path, "w", encoding="utf-8") as f:
            f.write("{this is not valid json")

        self.client.set_search(1, [1001])
        urls = [image_url(1001, 0)]
        self.client.set_work(1001, urls)
        self.client.set_image(urls[0])

        result = pp.run_download(
            "cat", 1, 1, self.download_folder,
            client=self.client, progress_path=self.progress_path)

        self.assertFalse(result["resumed"])
        self.assertIsNotNone(result["backup_path"])
        self.assertTrue(os.path.exists(result["backup_path"]))
        self.assertEqual(result["summary"][pp.STATUS_SUCCESS], 1)
        # 原記錄檔已被合法內容取代
        progress = self.read_progress()
        self.assertEqual(progress["works"]["1001"]["status"],
                         pp.STATUS_SUCCESS)

    def test_structurally_invalid_record_restarts(self):
        with open(self.progress_path, "w", encoding="utf-8") as f:
            json.dump({"foo": "bar"}, f)

        self.client.set_search(1, [1001])
        self.client.set_work(1001, [image_url(1001, 0)])
        self.client.set_image(image_url(1001, 0))

        result = pp.run_download(
            "cat", 1, 1, self.download_folder,
            client=self.client, progress_path=self.progress_path)

        self.assertFalse(result["resumed"])
        self.assertTrue(os.path.exists(result["backup_path"]))
        self.assertEqual(result["summary"][pp.STATUS_SUCCESS], 1)

    def test_different_word_restarts_with_backup(self):
        self.client.set_search(1, [1001])
        self.client.set_work(1001, [image_url(1001, 0)])
        self.client.set_image(image_url(1001, 0))

        pp.run_download("cat", 1, 1, self.download_folder,
                        client=self.client, progress_path=self.progress_path)

        # 同資料夾換標籤，視為新任務，舊記錄備份後重來
        self.client.set_search(1, [2002])
        self.client.set_work(2002, [image_url(2002, 0)])
        self.client.set_image(image_url(2002, 0))

        result = pp.run_download(
            "dog", 1, 1, self.download_folder,
            client=self.client, progress_path=self.progress_path)

        self.assertFalse(result["resumed"])
        self.assertTrue(os.path.exists(result["backup_path"]))
        progress = self.read_progress()
        self.assertEqual(progress["word"], "dog")
        self.assertIn("2002", progress["works"])
        self.assertNotIn("1001", progress["works"])

    def test_image_exception_fails_one_work_but_batch_continues(self):
        c = self.client
        c.set_search(1, [2001, 2002])
        bad_url = image_url(2001, 0)
        good_url = image_url(2002, 0)
        c.set_work(2001, [bad_url])
        c.set_image_exception(bad_url, FakeNetworkError("timeout"))
        c.set_work(2002, [good_url])
        c.set_image(good_url)

        result = pp.run_download(
            "tag", 1, 1, self.download_folder,
            client=c, progress_path=self.progress_path)

        self.assertEqual(result["summary"][pp.STATUS_FAILED], 1)
        self.assertEqual(result["summary"][pp.STATUS_SUCCESS], 1)
        reason = result["failures"][0]["reason"]
        self.assertTrue(reason.startswith("FakeNetworkError:"))
        self.assertTrue(os.path.exists(
            os.path.join(self.download_folder, image_filename(good_url))))

    def test_search_exception_does_not_stop_later_pages(self):
        c = self.client
        c.set_search_exception(1, FakeNetworkError("dns failure"))
        c.set_search(2, [3001])
        c.set_work(3001, [image_url(3001, 0)])
        c.set_image(image_url(3001, 0))

        result = pp.run_download(
            "tag", 1, 2, self.download_folder,
            client=c, progress_path=self.progress_path)

        self.assertEqual(result["failed_pages"][0][0], 1)
        self.assertIn("FakeNetworkError", result["failed_pages"][0][1])
        self.assertEqual(result["summary"][pp.STATUS_SUCCESS], 1)
        # 失敗頁在記錄中保留 failed 狀態，下次仍會重試
        self.assertEqual(self.read_progress()["pages"]["1"]["status"],
                         pp.PAGE_FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
