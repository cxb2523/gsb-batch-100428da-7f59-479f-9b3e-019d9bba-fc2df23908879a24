"""依標籤翻頁批次下載 Pixiv 插畫。

特性：
- 每個作品在每次執行都有明確結局：success（完成）／failed（失敗）／skipped（跳過）。
- 失敗會記錄原因（HTTP 狀態碼或異常類型），單頁、單作品、單張圖片失敗都不中断整批。
- 某頁搜尋結果為空不算錯誤，記錄該頁無結果後繼續後續頁。
- 進度持久化在下載目錄的記錄檔中，重跑時已完成的作品不再重複下載，只補失敗或未開始的部分。
- 記錄檔缺失或內容損壞時，備份後從頭開始，不會崩潰。
- 網路請求透過 client 物件注入，測試時可傳入本地偽造的 client，不需真實連線。
"""

import json
import os
import shutil
import tempfile
import time
from datetime import datetime

import requests

RECORD_FILENAME = "pixiv_pages_progress.json"
RECORD_VERSION = 1

# 作品在單次執行中的結局
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# 搜尋頁的狀態
PAGE_DONE = "done"
PAGE_NO_RESULTS = "no_results"
PAGE_FAILED = "failed"

DEFAULT_HEADERS = {
    'user-agent': 'user-agent',
    'cookie': 'cookie',
}

SEARCH_URL_TEMPLATE = 'https://www.pixiv.net/ajax/search/illustrations/{word}'
PAGES_URL_TEMPLATE = (
    'https://www.pixiv.net/ajax/illust/{illust_id}/pages'
    '?lang=zh_tw&version=f32089e9d176912e655d9eda2c1b816e46a82d4b'
)
ARTWORK_URL_TEMPLATE = 'https://www.pixiv.net/artworks/{illust_id}'


class DownloadError(Exception):
    """包裝請求或解析失敗，訊息即為寫入記錄的失敗原因。"""


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _failure_reason(exc):
    """把任意異常轉成「異常類型：訊息」格式的原因字串。"""
    return f"{type(exc).__name__}: {exc}"


class RequestsClient:
    """正式環境使用的請求客戶端，介面只需實作 get()。"""

    def __init__(self, headers=None, timeout=30):
        self.headers = dict(headers or DEFAULT_HEADERS)
        self.timeout = timeout

    def get(self, url, params=None, headers=None):
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)
        return requests.get(url, params=params, headers=merged_headers,
                            timeout=self.timeout)


class ProgressStore:
    """下載目錄下的 JSON 進度記錄檔，負責載入、持久化與狀態查詢。"""

    def __init__(self, path):
        self.path = path
        self.data = self._blank()
        self.resumed = False
        self.backup_path = None  # 記錄檔損壞/不相容時的備份路徑

    @staticmethod
    def _blank():
        return {
            "version": RECORD_VERSION,
            "word": None,
            "pages": {},
            "works": {},
        }

    def load(self, word):
        """載入記錄檔。

        檔案缺失、JSON 損壞、結構不符或標籤與本次不同時，備份舊檔並從空白開始，
        絕不拋異常。回傳 True 表示成功接續既有進度。
        """
        if not os.path.exists(self.path):
            self.data = self._blank()
            return False

        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("頂層結構不是 JSON 物件")
            if not isinstance(data.get("pages"), dict):
                raise ValueError("pages 區段缺失或格式錯誤")
            if not isinstance(data.get("works"), dict):
                raise ValueError("works 區段缺失或格式錯誤")
            recorded_word = data.get("word")
            if recorded_word is not None and recorded_word != word:
                raise ValueError(
                    f"記錄檔標籤為 {recorded_word!r}，與本次標籤 {word!r} 不同")
        except (json.JSONDecodeError, ValueError, OSError, UnicodeDecodeError) as exc:
            self._quarantine(reason=exc)
            self.data = self._blank()
            return False

        data.setdefault("version", RECORD_VERSION)
        self.data = data
        self.resumed = True
        return True

    def _quarantine(self, reason):
        """把無法使用的記錄檔複製備份，避免之後被覆蓋。"""
        backup = f"{self.path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copy2(self.path, backup)
            self.backup_path = backup
        except OSError:
            self.backup_path = None
        print(f"進度記錄無法使用（{_failure_reason(reason)}），已備份至 "
              f"{self.backup_path or '（備份失敗）'}，將從頭開始。")

    def save(self):
        """原子寫入，避免程式中斷留下半截 JSON。"""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".progress-", suffix=".tmp",
                                        dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get_page(self, page_num):
        return self.data["pages"].get(str(page_num))

    def set_page(self, page_num, status, illust_ids=None, reason=None):
        self.data["pages"][str(page_num)] = {
            "status": status,
            "reason": reason,
            "illust_ids": list(illust_ids or []),
            "updated_at": _now(),
        }

    def get_work(self, illust_id):
        return self.data["works"].get(str(illust_id))

    def upsert_work(self, illust_id, url, status, last_outcome,
                    reason=None, failed_images=None, page_num=None):
        previous = self.get_work(illust_id) or {}
        self.data["works"][str(illust_id)] = {
            "url": url,
            "page": page_num if page_num is not None else previous.get("page"),
            # status 是下載狀態，只會是 success/failed；
            # last_outcome 是該作品在最近一次執行的結局（含 skipped）。
            "status": status,
            "last_outcome": last_outcome,
            "reason": reason,
            "failed_images": list(failed_images or []),
            "updated_at": _now(),
        }


def _get_json(client, url, params=None, headers=None):
    """發送 GET 並解析 JSON，把所有失敗正規化成 DownloadError。"""
    try:
        response = client.get(url, params=params, headers=headers)
    except Exception as exc:  # 連線異常等
        raise DownloadError(_failure_reason(exc)) from exc

    status_code = getattr(response, "status_code", None)
    if status_code != 200:
        raise DownloadError(f"HTTP {status_code}")

    try:
        return response.json()
    except Exception as exc:  # JSON 解析失敗
        raise DownloadError(f"回應不是有效 JSON（{_failure_reason(exc)}）") from exc


def fetch_search_page(client, word, page_num):
    """取得某一搜尋頁的作品列表，回傳 illust dict 列表；空列表代表該頁無結果。"""
    params = {
        'word': word,
        'order': 'date_d',
        'mode': 'all',
        'p': page_num,
        's_mode': 's_tag_full',
        'type': 'illust_and_ugoira',
        'lang': 'zh_tw',
    }
    url = SEARCH_URL_TEMPLATE.format(word=word)
    data = _get_json(client, url, params=params)
    try:
        return data["body"]["illust"]["data"]
    except (KeyError, TypeError) as exc:
        raise DownloadError(f"搜尋回應結構不符（{_failure_reason(exc)}）") from exc


def fetch_work_pages(client, illust_id):
    """取得單一作品的分頁資料（每頁含一張原圖網址）。"""
    referer = ARTWORK_URL_TEMPLATE.format(illust_id=illust_id)
    url = PAGES_URL_TEMPLATE.format(illust_id=illust_id)
    data = _get_json(client, url, headers={"referer": referer})
    try:
        return data["body"]
    except (KeyError, TypeError) as exc:
        raise DownloadError(f"作品分頁回應結構不符（{_failure_reason(exc)}）") from exc


def download_image(client, url, dest_path, referer):
    """下載單張圖片並原子寫入 dest_path，失敗時拋 DownloadError。"""
    try:
        response = client.get(url, headers={"referer": referer})
    except Exception as exc:
        raise DownloadError(_failure_reason(exc)) from exc

    if getattr(response, "status_code", None) != 200:
        raise DownloadError(f"HTTP {getattr(response, 'status_code', None)}")

    directory = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".image-", suffix=".tmp",
                                    dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(response.content)
        os.replace(tmp_path, dest_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def process_work(client, store, illust_id, download_folder, page_num, log=print):
    """處理單一作品，回傳 (結局, 失敗原因或 None)，任何失敗都不向外拋。"""
    illust_id = str(illust_id)
    referer = ARTWORK_URL_TEMPLATE.format(illust_id=illust_id)

    existing = store.get_work(illust_id)
    if existing and existing.get("status") == STATUS_SUCCESS:
        # 先前已完整下載，本次跳過，不重複請求。
        store.upsert_work(illust_id, referer, STATUS_SUCCESS, STATUS_SKIPPED,
                          page_num=page_num)
        store.save()
        log(f"作品 {illust_id} 已完成，跳過。")
        return STATUS_SKIPPED, None

    try:
        pages = fetch_work_pages(client, illust_id)
    except DownloadError as exc:
        reason = str(exc)
        store.upsert_work(illust_id, referer, STATUS_FAILED, STATUS_FAILED,
                          reason=reason, failed_images=[], page_num=page_num)
        store.save()
        log(f"作品 {illust_id} 取得分頁失敗：{reason}")
        return STATUS_FAILED, reason
    except Exception as exc:  # 防禦性兜底，保證單一作品不擊垮整批
        reason = _failure_reason(exc)
        store.upsert_work(illust_id, referer, STATUS_FAILED, STATUS_FAILED,
                          reason=reason, failed_images=[], page_num=page_num)
        store.save()
        log(f"作品 {illust_id} 發生未預期錯誤：{reason}")
        return STATUS_FAILED, reason

    failed_images = []
    for index, page in enumerate(pages):
        try:
            download_url = page["urls"]["original"]
        except (KeyError, TypeError) as exc:
            reason = f"第 {index + 1} 張圖片網址結構不符（{_failure_reason(exc)}）"
            failed_images.append({"index": index, "url": None, "reason": reason})
            log(f"作品 {illust_id} {reason}，繼續其餘圖片。")
            continue

        filename = download_url.split("/")[-1]
        dest_path = os.path.join(download_folder, filename)

        # 記錄顯示未完成但檔案已存在（例如上次寫完才中斷），直接視同已完成。
        if os.path.exists(dest_path):
            log(f"{filename} 已存在，跳過重抓。")
            continue

        try:
            download_image(client, download_url, dest_path, referer)
            log(f"{filename} 下載成功。")
        except DownloadError as exc:
            reason = str(exc)
            failed_images.append({
                "index": index,
                "url": download_url,
                "reason": reason,
            })
            log(f"作品 {illust_id} 圖片下載失敗：{reason}，繼續其餘圖片。")
            continue
        except Exception as exc:  # 防禦性兜底
            reason = _failure_reason(exc)
            failed_images.append({
                "index": index,
                "url": download_url,
                "reason": reason,
            })
            log(f"作品 {illust_id} 圖片發生未預期錯誤：{reason}，繼續其餘圖片。")
            continue

    if failed_images:
        reason = failed_images[0]["reason"]
        store.upsert_work(illust_id, referer, STATUS_FAILED, STATUS_FAILED,
                          reason=reason, failed_images=failed_images,
                          page_num=page_num)
        store.save()
        return STATUS_FAILED, reason

    store.upsert_work(illust_id, referer, STATUS_SUCCESS, STATUS_SUCCESS,
                      reason=None, failed_images=[], page_num=page_num)
    store.save()
    return STATUS_SUCCESS, None


def print_summary(summary, failures, empty_pages, failed_pages):
    print("=" * 50)
    print("下載結果彙總")
    print(f"成功：{summary[STATUS_SUCCESS]} 個作品")
    print(f"失敗：{summary[STATUS_FAILED]} 個作品")
    print(f"跳過（先前已完成）：{summary[STATUS_SKIPPED]} 個作品")
    if empty_pages:
        print(f"無結果的頁面：{', '.join(str(p) for p in empty_pages)}")
    if failed_pages:
        print("搜尋失敗的頁面（下次執行會重試）：")
        for page_num, reason in failed_pages:
            print(f"  - 第 {page_num} 頁：{reason}")
    if failures:
        print("失敗作品明細：")
        for item in failures:
            print(f"  - {item['url']} 原因：{item['reason']}")
    else:
        print("沒有失敗的作品。")
    print("=" * 50)


def run_download(word, start_page, end_page, download_folder,
                 client=None, progress_path=None, log=print):
    """執行翻頁下載主流程，回傳本次執行的彙總資訊 dict。"""
    os.makedirs(download_folder, exist_ok=True)
    client = client or RequestsClient()
    progress_path = progress_path or os.path.join(download_folder, RECORD_FILENAME)

    store = ProgressStore(progress_path)
    resumed = store.load(word)
    if store.data.get("word") is None:
        store.data["word"] = word
    store.save()  # 先落地一份，確保記錄檔存在

    if resumed:
        log(f"已載入進度記錄 {progress_path}，只處理失敗或未開始的作品。")
    else:
        log(f"從頭開始，進度記錄將寫入 {progress_path}。")

    summary = {STATUS_SUCCESS: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0}
    failures = []
    empty_pages = []
    failed_pages = []

    for page_num in range(start_page, end_page + 1):
        page_rec = store.get_page(page_num)

        # 已成功搜尋過（含無結果）的頁面直接复用快取的作品 id，不再發搜尋請求。
        if page_rec and page_rec.get("status") in (PAGE_DONE, PAGE_NO_RESULTS):
            if page_rec.get("status") == PAGE_NO_RESULTS:
                log(f"第 {page_num} 頁記錄為無結果，略過搜尋。")
                empty_pages.append(page_num)
            illust_ids = list(page_rec.get("illust_ids", []))
        else:
            try:
                items = fetch_search_page(client, word, page_num)
            except DownloadError as exc:
                reason = str(exc)
                store.set_page(page_num, PAGE_FAILED, reason=reason)
                store.save()
                failed_pages.append((page_num, reason))
                log(f"第 {page_num} 頁搜尋失敗：{reason}，繼續後續頁面。")
                continue
            except Exception as exc:  # 防禦性兜底
                reason = _failure_reason(exc)
                store.set_page(page_num, PAGE_FAILED, reason=reason)
                store.save()
                failed_pages.append((page_num, reason))
                log(f"第 {page_num} 頁搜尋發生未預期錯誤：{reason}，繼續後續頁面。")
                continue

            if not items:
                # 空結果不是錯誤，記錄後繼續。
                store.set_page(page_num, PAGE_NO_RESULTS, illust_ids=[])
                store.save()
                empty_pages.append(page_num)
                log(f"第 {page_num} 頁無搜尋結果，繼續後續頁面。")
                continue

            illust_ids = []
            for item in items:
                try:
                    illust_ids.append(str(item["id"]))
                except (KeyError, TypeError) as exc:
                    log(f"第 {page_num} 頁有作品資料缺 id（{_failure_reason(exc)}），略過該筆。")
            store.set_page(page_num, PAGE_DONE, illust_ids=illust_ids)
            store.save()

        for index, illust_id in enumerate(illust_ids):
            referer = ARTWORK_URL_TEMPLATE.format(illust_id=illust_id)
            log(f"第 {page_num} 頁_{index + 1}：{referer}")
            status, reason = process_work(
                client, store, illust_id, download_folder, page_num, log=log)
            summary[status] += 1
            if status == STATUS_FAILED:
                failures.append({"id": illust_id, "url": referer, "reason": reason})

    print_summary(summary, failures, empty_pages, failed_pages)
    return {
        "summary": summary,
        "failures": failures,
        "empty_pages": empty_pages,
        "failed_pages": failed_pages,
        "resumed": resumed,
        "progress_path": progress_path,
        "backup_path": store.backup_path,
    }


def main():
    word = input("請輸入名稱(標籤)：")
    start_page = int(input("請輸入起始頁數："))
    end_page = int(input("請輸入結束頁數："))
    custom_path = input("是否要自訂儲存路徑？(輸入 '是' 或 '否')：")

    if custom_path == "是":
        download_folder = input("請輸入自訂儲存路徑：")
    else:
        download_folder = input("請輸入自訂資料夾名稱：")
        desktop_path = os.path.expanduser('~/desktop')
        download_folder = os.path.join(desktop_path, download_folder)

    run_download(word, start_page, end_page, download_folder)


if __name__ == "__main__":
    main()
