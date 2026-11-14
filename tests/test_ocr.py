"""测试 Windows OCR 中文识别。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw, ImageFont

from engine.ocr import ocr_image


def main():
    os.makedirs("data", exist_ok=True)
    img = Image.new("RGB", (700, 180), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
    except Exception:
        font = ImageFont.load_default()
    d.text((20, 50), "姓名：王凯  年龄：28岁  学历：本科  工作经历：5年", font=font, fill="black")
    path = os.path.join("data", "test_ocr.png")
    img.save(path)
    text = ocr_image(os.path.abspath(path))
    print("OCR 识别结果：", repr(text))
    ok = "王凯" in text and "本科" in text and "工作经历" in text
    print("识别成功" if ok else "识别失败（可能缺中文语言包）")
    os.remove(path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
