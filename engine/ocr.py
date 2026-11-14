"""Windows 自带 OCR：识别图片中的中文文字（无需安装额外软件）。"""
import asyncio
import re


def ocr_image(path):
    """识别单张图片中的文字，返回文本。失败返回空字符串。"""
    try:
        return asyncio.run(_ocr_async(str(path)))
    except Exception:
        return ""


def ocr_images(paths):
    """识别多张图片，按顺序拼接文本。"""
    parts = []
    for p in paths:
        t = ocr_image(p)
        if t:
            parts.append(t)
    return "\n".join(parts)


async def _ocr_async(path):
    from winsdk.windows.storage import StorageFile
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.globalization import Language

    file = await StorageFile.get_file_from_path_async(path)
    stream = await file.open_async(0)  # FileAccessMode.READ
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    engine = OcrEngine.try_create_from_language(Language("zh-Hans-CN"))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""
    result = await engine.recognize_async(bitmap)
    return clean_ocr_text(result.text or "")


def clean_ocr_text(text):
    """去掉 Windows OCR 在中文字符之间插入的空格，避免拆散「姓名」「工作经历」等标签。"""
    t = re.sub(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])", "", text)
    # 中文标点附近的空格
    t = re.sub(r"(?<=[\u4e00-\u9fa5，。：；、（）])\s+", "", t)
    t = re.sub(r"\s+(?=[\u4e00-\u9fa5，。：；、（）])", "", t)
    return t
