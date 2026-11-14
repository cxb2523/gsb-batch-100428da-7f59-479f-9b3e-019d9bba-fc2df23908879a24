# wxbot/detector.py
"""消息检测器 — 红点 / 气泡 / OCR / 客户判定

所有参数通过 config dict 配置，提供合理默认值。
"""

import hashlib
import logging
import time
import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import numpy as np
from paddleocr import PaddleOCR
from PIL import Image, ImageEnhance

logger = logging.getLogger(__name__)


# ═══ 数据结构 ══════════════════════════════

@dataclass
class Bubble:
    """气泡信息"""
    top: int
    bottom: int
    is_blue: bool
    image: Optional[Image.Image] = None
    nick_image: Optional[Image.Image] = None
    text: str = ""


@dataclass
class ChatState:
    """聊天状态"""
    has_red_dot: bool = False
    red_dot_positions: List[int] = field(default_factory=list)
    customer_name: str = ""
    is_customer: bool = False
    unreplied_messages: List[str] = field(default_factory=list)
    last_reply_is_ours: bool = False
    bubbles: List[Bubble] = field(default_factory=list)
    has_blue: bool = False


# ═══ 检测器 ═══════════════════════════════

class MessageDetector:
    """消息检测器 — 所有参数由 config 驱动"""

    def __init__(self, config: dict = None):
        self._cfg = config or {}

        self._ocr: Optional[PaddleOCR] = None
        self._seen_col2: set = set()
        self._tried_y: dict = {}
        self._last_reply = 0.0
        self._seen_messages: dict = {}  # OCR文本hash → (timestamp, original_text)
        self._cooldown = self._cfg.get("reply_cooldown_seconds", 10)
        self._msg_dedup_sec = self._cfg.get("message_dedup_seconds", 300)

        # --- OCR ---
        ocr = self._cfg.get("ocr", {})
        self._ocr_lang = ocr.get("lang", "ch")
        self._ocr_min_h = ocr.get("min_image_height", 50)
        self._ocr_enhance = ocr.get("enhance_contrast", False)
        self._ocr_contrast = ocr.get("contrast_factor", 2.0)
        self._cust_suffixes = ocr.get("customer_name_suffixes", ["@微信", ")微信"])
        self._cust_pattern = ocr.get("customer_name_pattern", r'@[一-鿿]{2,}')
        self._ts_filter = ocr.get("timestamp_filter_pattern", r'^[\d:;.\-\s]+$')
        self._stop_keywords = ocr.get("stop_reply_keywords", [])
        self._sys_keywords = ocr.get(
            "non_customer_keywords",
            ["行业资讯", "企业微信日报", "系统通知", "工作通知",
             "日程提醒", "待办提醒", "会议邀请", "打卡提醒",
             "审批通知", "汇报通知", "公告通知",
             "此群为", "群公告", "群成员", "全员群", "群聊",
             "加入了群", "退出了群", "邀请你加入"],
        )

        # --- OCR 置信度 ---
        occ = self._cfg.get("ocr_confidence", {})
        self._conf_default = occ.get("default", 0.4)
        self._conf_name = occ.get("name_detection", 0.05)
        self._conf_bubble = occ.get("bubble_text", 0.15)

        # --- 去重与冷却 ---
        dedup = self._cfg.get("dedup", {})
        self._hash_cache_size = dedup.get("col2_hash_cache_size", 50)
        self._click_cooldown = dedup.get("click_cooldown_seconds", 60)

        # --- 各检测模块参数 ---
        det = self._cfg.get("detection", {})

        # 红点
        rd = det.get("red_dot", {})
        self._rd_scan_w_ratio = rd.get("scan_width_ratio", 0.05)
        self._rd_scan_w_min = rd.get("scan_width_min_px", 20)
        self._rd_r_min = rd.get("r_min", 90)
        self._rd_r_max = rd.get("r_max", 220)
        self._rd_gb_max = rd.get("gb_max", 130)
        self._rd_min_px = rd.get("min_pixels", 2)
        self._rd_excess_g = rd.get("red_excess_g", 20)
        self._rd_excess_b = rd.get("red_excess_b", 20)
        self._rd_cluster_gap = rd.get("cluster_gap", 5)
        self._rd_cluster_min = rd.get("cluster_min_points", 2)

        # 蓝泡
        bb = det.get("blue_bubble", {})
        self._bb_excess_r = bb.get("b_excess_r", 15)
        self._bb_min_rgb = bb.get("min_rgb", 200)
        self._bb_row_ratio = bb.get("row_threshold_ratio", 0.15)
        self._bb_right_ratio = bb.get("right_edge_ratio", 0.85)
        self._bb_max_h_ratio = bb.get("max_height_ratio", 0.15)
        self._bb_top_limit = bb.get("scan_top_limit_ratio", 0.05)
        self._bb_bottom_start = bb.get("scan_bottom_start_ratio", 0.15)

        # 灰泡
        gb = det.get("gray_bubble", {})
        self._gb_max_diff = gb.get("max_channel_diff", 10)
        self._gb_r_range = tuple(gb.get("r_range", [230, 254]))
        self._gb_g_range = tuple(gb.get("g_range", [230, 254]))
        self._gb_b_range = tuple(gb.get("b_range", [230, 254]))
        self._gb_row_ratio = gb.get("row_threshold_ratio", 0.15)
        self._gb_left_ratio = gb.get("left_edge_ratio", 0.15)
        self._gb_max_h_ratio = gb.get("max_height_ratio", 0.80)
        self._gb_min_h_px = gb.get("min_height_px", 20)
        self._gb_nick_offset = gb.get("nickname_top_offset", 15)

        # 绿badge
        grn = det.get("green_badge", {})
        self._grn_min_g = grn.get("min_green", 100)
        self._grn_excess = grn.get("green_excess_ratio", 1.3)
        self._grn_min_px = grn.get("min_green_pixels", 10)

        # 名字区域
        nr = det.get("name_region", {})
        self._nr_rgb_thr = nr.get("text_rgb_threshold", 100)
        self._nr_min_dark = nr.get("min_dark_pixels", 3)
        self._nr_width_chg = nr.get("width_change_ratio", 0.5)
        self._nr_min_w_ratio = nr.get("min_width_ratio", 0.15)
        self._nr_min_h_px = nr.get("min_height_px", 5)
        self._nr_pad_l = nr.get("crop_padding_left", 20)
        self._nr_pad_r = nr.get("crop_padding_right", 20)
        self._nr_pad_t = nr.get("crop_padding_top", 1)
        self._nr_pad_b = nr.get("crop_padding_bottom", 2)

    # ═══ OCR ════════════════════════════════

    @property
    def ocr(self) -> PaddleOCR:
        if self._ocr is None:
            logger.info("Loading PaddleOCR...")
            self._ocr = PaddleOCR(lang=self._ocr_lang)
            logger.info("PaddleOCR ready")
        return self._ocr

    # PaddleOCR 误读的 WeChat UI 产物
    _DATE_ARTIFACT_RE = re.compile(
        r'^(周[一二三四五六日]|星期[一二三四五六日]|期[一二三四五六日])\s*'
    )
    # 语音转文字、系统提示等 UI 文字被 OCR 误读
    _UI_ARTIFACTS = [
        "√转换完成", "✓转换完成", "转换完成",
        "√ 转换完成", "✓ 转换完成",
    ]

    @staticmethod
    def _strip_ocr_artifacts(text: str) -> str:
        """Delegate to wxbot.detector.ocr_engine."""
        from wxbot.detector.ocr_engine import strip_ocr_artifacts
        return strip_ocr_artifacts(text)

    def extract_text(self, img: Image.Image, min_conf: float = None,
                     enhance: bool = None) -> str:
        """Delegate to wxbot.detector.ocr_engine."""
        from wxbot.detector.ocr_engine import extract_text
        ocr_cfg = {
            "lang": self._ocr_lang,
            "min_image_height": self._ocr_min_h,
            "enhance_contrast": self._ocr_enhance,
            "contrast_factor": self._ocr_contrast,
            "timestamp_filter_pattern": self._ts_filter,
            "default_confidence": min_conf if min_conf is not None else self._conf_default,
        }
        return extract_text(img, config=ocr_cfg)

    # ═══ 红点检测 ═══════════════════════════

    def detect_red_dots(self, img: Image.Image) -> List[int]:
        """Delegate to wxbot.detector.red_dot for standalone red-dot detection."""
        from wxbot.detector.red_dot import detect_red_dots_legacy
        return detect_red_dots_legacy(img, config=self._cfg.get("detection", {}).get("red_dot"))

    # ═══ 图像去重 ═══════════════════════════

    def col2_seen(self, img: Image.Image) -> bool:
        h = hashlib.md5(img.tobytes()).hexdigest()
        if h in self._seen_col2:
            return True
        self._seen_col2.add(h)
        if len(self._seen_col2) > self._hash_cache_size:
            self._seen_col2.clear()
        return False

    def is_clickable(self, y: int, timeout: int = None) -> bool:
        if timeout is None:
            timeout = self._click_cooldown
        now = time.time()
        if y in self._tried_y and now - self._tried_y[y] < timeout:
            return False
        return True

    def mark_clicked(self, y: int):
        self._tried_y[y] = time.time()

    # ═══ 像素判定 ═══════════════════════════

    def _is_gray_pixel(self, r: int, g: int, b: int) -> bool:
        if max(r, g, b) - min(r, g, b) > self._gb_max_diff:
            return False
        return (self._gb_r_range[0] <= r <= self._gb_r_range[1]
                and self._gb_g_range[0] <= g <= self._gb_g_range[1]
                and self._gb_b_range[0] <= b <= self._gb_b_range[1])

    def _is_blue_pixel(self, r: int, g: int, b: int) -> bool:
        from wxbot.detector.bubble import is_blue_pixel
        bb = self._cfg.get("detection", {}).get("blue_bubble", {})
        return is_blue_pixel(r, g, b, config=bb)

    def mask_bubble(self, bubble: Image.Image) -> Image.Image:
        from wxbot.detector.bubble import mask_bubble
        gb = self._cfg.get("detection", {}).get("gray_bubble", {})
        return mask_bubble(bubble, config=gb)

    # ═══ 气泡扫描 ═══════════════════════════

    def extract_unreplied_bubbles(self, chat_img: Image.Image
                                  ) -> Tuple[Optional[List[Tuple[Image.Image, Image.Image]]], bool]:
        pixels = chat_img.load()
        w, h = chat_img.size
        if w < 20 or h < 20:
            return None, False

        gray_thr = w * self._gb_row_ratio
        blue_thr = w * self._bb_row_ratio  # 蓝泡独立阈值，低于灰泡（文字行蓝色像素少）
        right_edge = int(w * self._bb_right_ratio)
        left_edge = int(w * self._gb_left_ratio)
        max_bubble_h = int(h * self._bb_max_h_ratio)
        bubbles = []

        def _is_gray_row(y: int) -> bool:
            gray_xs = [x for x in range(w) if self._is_gray_pixel(*pixels[x, y][:3])]
            if len(gray_xs) < gray_thr:
                return False
            return min(gray_xs) < left_edge

        # 文字行检测: 左半区有深色像素 = 可能是语音转文字等非灰泡消息
        text_min_pixels = 3
        left_limit = int(w * 0.4)

        def _is_text_row(y: int) -> bool:
            count = 0
            for x in range(left_limit):
                r, g, b = pixels[x, y][:3]
                if r < 150 and g < 150 and b < 150:
                    count += 1
                    if count >= text_min_pixels:
                        return True
            return False

        # 蓝泡最小高度阈值：低于此值的蓝条是语音消息UI元素
        BLUE_MIN_HEIGHT = 15

        def _is_any_blue_row(y: int) -> bool:
            """当前行是否有足够蓝色像素（不检查文字），使用蓝泡独立阈值"""
            blue_xs = [x for x in range(w) if self._is_blue_pixel(*pixels[x, y][:3])]
            return len(blue_xs) >= blue_thr

        y = h - 1
        while y > int(h * self._bb_bottom_start):
            # 检测蓝泡：连续蓝色行（含或不合文字），判为客服回复
            if _is_any_blue_row(y):
                bot = y
                while y > int(h * self._bb_top_limit) and _is_any_blue_row(y):
                    y -= 1
                top = y + 1
                block_h = bot - top
                if block_h >= BLUE_MIN_HEIGHT:
                    # 真正的蓝泡 → 标记为蓝色气泡，阻断未回复收集
                    if block_h <= max_bubble_h:
                        bubbles.append((top, bot, True))
                # 薄蓝条（<BLUE_MIN_HEIGHT）→ 忽略，继续扫描
                continue

            # 检测灰泡（但不能吞掉蓝泡行 — 背景像素可能伪装成灰泡）
            if _is_gray_row(y) and not _is_any_blue_row(y):
                bot = y
                while y > int(h * self._bb_top_limit) and _is_gray_row(y) and not _is_any_blue_row(y):
                    y -= 1
                top = y + 1
                if bot - top <= h * self._gb_max_h_ratio:
                    bubbles.append((top, bot, False))
                continue

            # 检测文字行（语音转文字等非灰泡消息）
            if _is_text_row(y) and not _is_any_blue_row(y):
                bot = y
                while y > int(h * self._bb_top_limit) and _is_text_row(y) and not _is_any_blue_row(y):
                    y -= 1
                top = y + 1
                if bot - top <= h * 0.5:
                    bubbles.append((top, bot, False))
                continue

            y -= 1

        # 合并相邻非蓝泡碎片（灰泡被文字行切碎后合并恢复）
        MERGE_GAP = 8
        i = len(bubbles) - 1
        while i > 0:
            top_i, bot_i, blue_i = bubbles[i]
            top_j, bot_j, blue_j = bubbles[i - 1]
            if not blue_i and not blue_j and (top_j - bot_i) <= MERGE_GAP:
                # 合并：上气泡的 top + 下气泡的 bot
                bubbles[i - 1] = (top_i, bot_j, False)
                bubbles.pop(i)
            i -= 1

        has_blue = any(is_blue for _, _, is_blue in bubbles)
        logger.debug(f"bubbles={len(bubbles)} hasBlue={has_blue} "
                     f"blue_count={sum(1 for _,_,b in bubbles if b)}")

        # 找最底部蓝泡的位置（客服最新回复）
        newest_blue_top = None
        for top, bot, is_blue in bubbles:
            if is_blue and (newest_blue_top is None or top > newest_blue_top):
                newest_blue_top = top

        # 收集未回复气泡：只取底部蓝泡之下的内容
        unreplied = []
        for top, bot, is_blue in bubbles:
            if is_blue:
                # 薄蓝条(<15px)跳过（语音UI），厚蓝泡停止
                if bot - top >= BLUE_MIN_HEIGHT:
                    break
                continue
            if bot - top < 5:
                continue
            # 安全检查：只收集 y 低于最新蓝泡的气泡
            if newest_blue_top is not None and top < newest_blue_top:
                continue
            nick_top = max(0, top - self._gb_nick_offset)
            bubble = chat_img.crop((0, top, w, bot + 1))
            bubble_nick = chat_img.crop((0, nick_top, w, bot + 1))
            unreplied.append((bubble, bubble_nick))

        return (unreplied if unreplied else None), has_blue

    def extract_bottom_bubble(self, chat_img: Image.Image
                              ) -> Tuple[Optional[Image.Image], bool, Optional[Image.Image]]:
        unreplied, has_blue = self.extract_unreplied_bubbles(chat_img)
        if unreplied:
            bubble, bubble_nick = unreplied[-1]
            return bubble, False, bubble_nick
        if has_blue:
            return None, True, None
        return None, False, None

    # ═══ 名字检测 ═══════════════════════════

    def detect_name_region(self, row_img: Image.Image) -> Optional[Image.Image]:
        """Delegate to wxbot.detector.name_region."""
        from wxbot.detector.name_region import detect_name_region
        return detect_name_region(row_img, config=self._cfg.get("detection", {}).get("name_region"))

    # ═══ 绿badge ═══════════════════════════

    def has_green_badge(self, img: Image.Image) -> bool:
        """Delegate to wxbot.detector.name_region."""
        from wxbot.detector.name_region import has_green_badge
        return has_green_badge(img, config=self._cfg.get("detection", {}).get("green_badge"))

    # ═══ 客户判定 ═══════════════════════════

    def is_customer_name(self, name_img: Image.Image = None,
                         ocr_text: str = "") -> bool:
        """对话名判定: OCR 文字含 @微信 = 客户"""
        if not ocr_text:
            return False
        for suffix in self._cust_suffixes:
            if suffix in ocr_text:
                return True
        if re.search(self._cust_pattern, ocr_text):
            return True
        return False

    _NAME_SYS_MARKERS = [
        "[语音]", "[图片]", "[视频]", "[文件]", "[链接]", "[位置]",
        "[红包]", "[名片]", "[小程序]", "[聊天记录]", "[动画表情]",
        "[通话]", "[位置共享]",
    ]

    def clean_name_text(self, name_text: str) -> str:
        """清理OCR名字中的系统标记（[语音]、[图片]等），并截取客户名片段"""
        if not name_text:
            return ""
        # 1. 去掉系统消息标记
        for marker in self._NAME_SYS_MARKERS:
            name_text = name_text.replace(marker, "")
        name_text = name_text.strip()
        if not name_text:
            return ""
        # 2. 截取到客户后缀（@微信等）结束，去除后面的消息预览文字
        for suffix in self._cust_suffixes:
            idx = name_text.find(suffix)
            if idx >= 0:
                end = idx + len(suffix)
                name_text = name_text[:end]
                break
        return name_text.strip()

    def is_customer(self, bubble_with_nick: Image.Image,
                    has_blue: bool = False, text: str = "") -> bool:
        """客户判定三层"""
        if has_blue:
            return True

        if bubble_with_nick is not None:
            if self.has_green_badge(bubble_with_nick):
                return True
            nick_text = self.extract_text(bubble_with_nick)
            if nick_text:
                for suffix in self._cust_suffixes:
                    if suffix in nick_text:
                        return True
                if re.search(self._cust_pattern, nick_text):
                    return True
            else:
                # 昵称区域存在但OCR失败，不判定为客户
                return False

        if text:
            for kw in self._sys_keywords:
                if kw in text:
                    return False
            return True

        return False

    # ═══ 冷却 ═══════════════════════════════

    def is_in_cooldown(self) -> bool:
        return time.time() - self._last_reply < self._cooldown

    def mark_replied(self):
        self._last_reply = time.time()

    def is_message_seen(self, customer_name: str, text: str) -> bool:
        """检查OCR文本是否近期已处理过（防止重复回复）

        两层检查：
        1. 精确哈希匹配 (customer_name:text)
        2. 模糊子串匹配 — 同一客户下，兜底路径OCR可能只产生红点路径文本的子集
        """
        if not text or len(text) < 3:
            return False
        dedup_key = f"{customer_name}:{text}"
        h = hashlib.md5(dedup_key.encode()).hexdigest()
        now = time.time()
        # 精确匹配
        if h in self._seen_messages and now - self._seen_messages[h][0] < self._msg_dedup_sec:
            return True
        # 模糊匹配：同一客户下，兜底路径可能产生红点路径的子串
        for seen_h, (ts, seen_key) in list(self._seen_messages.items()):
            if now - ts > self._msg_dedup_sec:
                continue
            # 只对同一客户的记录做模糊匹配
            if not seen_key.startswith(f"{customer_name}:"):
                continue
            seen_text = seen_key[len(customer_name) + 1:]
            if len(text) >= 4 and len(seen_text) >= 4:
                if text in seen_text or seen_text in text:
                    # OCR accumulates visible bubbles: "A" seen, then
                    # "B A" appears — that's a NEW message, not a dup.
                    # Only dedup when lengths are comparable (ratio >= 0.6).
                    ratio = min(len(text), len(seen_text)) / max(len(text), len(seen_text))
                    if ratio >= 0.6:
                        logger.info(f"消息模糊去重: [{text[:40]}] ⊆ [{seen_text[:40]}]")
                        return True
        return False

    def mark_message_seen(self, customer_name: str, text: str):
        """标记OCR文本已处理"""
        if text:
            dedup_key = f"{customer_name}:{text}"
            h = hashlib.md5(dedup_key.encode()).hexdigest()
            self._seen_messages[h] = (time.time(), dedup_key)

    def mark_text_sent(self, text: str):
        """记录刚发送的回复文本，用于防止将自己的回复识别为客户消息"""
        if not text:
            return
        # 取前60字符做特征（太短不可靠，太长浪费）
        key = text[:60].strip()
        if len(key) < 1:
            return
        if not hasattr(self, '_sent_texts'):
            self._sent_texts: dict = {}
        self._sent_texts[key] = time.time()

    def contains_own_reply(self, ocr_text: str) -> bool:
        """检查OCR结果是否包含自己刚发送的回复文本"""
        if not ocr_text or not hasattr(self, '_sent_texts'):
            return False
        now = time.time()
        # 清理过期 (>600s)
        self._sent_texts = {k: v for k, v in self._sent_texts.items()
                            if now - v < 600}
        for sent_text, ts in self._sent_texts.items():
            # 短回复(<4字)跳过子串匹配：太短容易误判
            # 如"好"会匹配到客户说的"好的"、"您好"
            if len(sent_text) < 4:
                continue
            if sent_text in ocr_text:
                logger.info(f"OCR包含自己发送的回复，跳过: {sent_text[:40]}...")
                return True
        return False

    def has_stop_keyword(self, text: str) -> bool:
        """检查文本是否包含停止回复关键词"""
        for kw in self._stop_keywords:
            if kw in text:
                return True
        return False

    def clear_seen(self):
        """清除已见集合"""
        self._seen_col2.clear()
        # 清理过期消息哈希（v[0] = timestamp in (ts, text) tuple）
        now = time.time()
        self._seen_messages = {k: v for k, v in self._seen_messages.items()
                               if now - v[0] < self._msg_dedup_sec}
