"""
A tiny offline stand-in for the real `jmcomic` package.

Why it exists
-------------
`tests/test_webui_batch.py` exercises the batch bookkeeping in-process with a stubbed
`jmcore.run_download`. That proves the accounting, but not that the real
`webui/server.py` -> real `jmcore.run_download` path works. `tests/test_webui_batch_e2e.py`
starts the actual server subprocess with this package first on PYTHONPATH, so the real
engine code runs end to end with no network.

Only the surface `jmcore.run_download` touches is implemented (option structure,
download_photo/download_album, DownloadResult/manifest, the exceptions), so a drift in
jmcore's expectations breaks this fixture loudly rather than silently passing.

Deterministic fake ids:
    <digits>       succeeds: writes 3 PNG images and zip/png exports into save_dir
    999999999      raises MissingAlbumPhotoException (invalid/unavailable id)
    123456789      raises PartialDownloadFailedException
    777777777      sleeps 2s, so a cancel can arrive mid-download
"""
import os
import time


class JmcomicException(Exception):
    pass


class MissingAlbumPhotoException(JmcomicException):
    def __init__(self, error_jmid="", *args):
        super().__init__(f"album/photo not found: JM{error_jmid}")
        self.error_jmid = error_jmid


class PartialDownloadFailedException(JmcomicException):
    def __init__(self, downloader=None, *args):
        super().__init__("partial download failure")
        self.downloader = downloader


class JmcomicText:
    @staticmethod
    def parse_to_jm_id(value):
        text = str(value).strip().upper()
        if text.startswith("JM"):
            text = text[2:]
        if not text.isdigit():
            raise ValueError(f"invalid id: {value}")
        return text


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Feature:
    """Feature flags combine with `+`, exactly like jmcomic's real ones."""

    def __init__(self, name=""):
        self.name = name

    def __add__(self, other):
        return Feature(f"{self.name}+{getattr(other, 'name', other)}")

    __radd__ = __add__


Feature.export_zip = Feature("export_zip")
Feature.export_long_img = Feature("export_long_img")


class _Rule:
    base_dir = ""
    rule = "Bd / Aid / Ptitle"


class _Threading:
    image = 4


class _Download:
    threading = _Threading()
    cache = True
    image = _Obj(suffix=".jpg")


class _Option:
    def __init__(self):
        self.dir_rule = _Rule()
        self.download = _Download()
        self.client = _Obj(postman=_Obj(type="requests", meta_data=_Obj(proxies=None)))

    @classmethod
    def default(cls):
        return cls()

    def new_jm_client(self):
        return _Obj()


class JmOption:
    default = staticmethod(_Option.default)


class _Detail:
    def __init__(self, jm_id, name, save_path):
        self.id = jm_id
        self.name = name
        self.save_path = save_path


class _Manifest:
    def __init__(self, images, exports=None):
        self.image_filepath_list = images
        self._exports = exports or {}

    def get_export_filepath_list(self, suffix):
        return list(self._exports.get(str(suffix).lower().lstrip("."), []))


class DownloadResult:
    def __init__(self, jm_id, name, save_path, images, exports=None, duration=0.1):
        self.detail = _Detail(jm_id, name, save_path)
        self.manifest = _Manifest(images, exports)
        self.duration = duration


class BatchResult(list):
    def __init__(self, results, failed=None):
        super().__init__(results)
        self.failed = failed or {}
        self.total = len(results) + len(self.failed)

    @property
    def all_succeeded(self):
        return not self.failed

    @property
    def all_succeeded_bool(self):
        return self.all_succeeded


def _write_images(jm_id, base_dir, count):
    from PIL import Image

    folder = os.path.join(base_dir, f"[JM{jm_id}] fake album [{jm_id}]")
    os.makedirs(folder, exist_ok=True)
    paths = []
    for i in range(1, count + 1):
        path = os.path.join(folder, f"{i:05d}.png")
        if not os.path.isfile(path):
            Image.new("RGB", (64, 96), (30 * i % 255, 90, 160)).save(path)
        paths.append(path)
    return folder, paths


def _export(folder, image_paths, title, exports):
    """Produce zip / long-image exports the way jmcomic's features would."""
    produced = {}
    for suffix in exports:
        suffix = str(suffix).lower().lstrip(".")
        if suffix == "zip":
            import zipfile

            target = os.path.join(folder, f"[JM]{title}.zip")
            with zipfile.ZipFile(target, "w") as zf:
                for path in image_paths:
                    zf.write(path, os.path.basename(path))
            produced.setdefault("zip", []).append(target)
        elif suffix == "png":
            from PIL import Image

            target = os.path.join(folder, f"[JM]{title}.png")
            images = [Image.open(p) for p in image_paths]
            width = max(i.width for i in images)
            height = sum(i.height for i in images)
            canvas = Image.new("RGB", (width, height), "white")
            y = 0
            for image in images:
                canvas.paste(image, (0, y))
                y += image.height
                image.close()
            canvas.save(target)
            produced.setdefault("png", []).append(target)
    return produced


def _download(ids, option, exports):
    base_dir = option.dir_rule.base_dir or os.getcwd()
    results = []
    for jm_id in ids:
        jm_id = str(jm_id)
        if jm_id == "999999999":
            raise MissingAlbumPhotoException(jm_id)
        if jm_id == "123456789":
            raise PartialDownloadFailedException(downloader=None)
        if not jm_id.isdigit():
            raise JmcomicException(f"unexpected id {jm_id}")
        if jm_id == "777777777":
            # Slow "book" so a cancel request can arrive while it is downloading.
            time.sleep(2.0)

        started = time.monotonic()
        folder, images = _write_images(jm_id, base_dir, 3)
        title = f"[JM{jm_id}] fake album"
        produced = _export(folder, images, title, exports)
        results.append(DownloadResult(jm_id, title, folder, images, produced,
                                      duration=round(time.monotonic() - started, 3)))
    return results


def _run(target, option, exports):
    ids = target if isinstance(target, (list, tuple)) else [target]
    results = _download(ids, option, exports)
    if len(results) == 1:
        return results[0]
    return BatchResult(results)


def download_photo(target, option, extra=None):
    return _run(target, option, [k for k in ("zip", "png")])


def download_album(target, option, extra=None):
    return _run(target, option, [k for k in ("zip", "png")])


__version__ = "fake-0.1"
