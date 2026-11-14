# -*- coding: utf-8 -*-
"""bot.py 检索结局标记的轻量测试。

bot.py 依赖 win32gui / wechatauto，缺依赖时整份测试跳过。
运行：python -m unittest test_bot -v
"""

import unittest

try:
    import bot
except ImportError:  # noqa: F401
    bot = None


@unittest.skipIf(bot is None, "bot.py 依赖（win32gui/wechatauto）不可用，跳过")
class SearchStatusTests(unittest.TestCase):
    def test_status_labels_and_tags_consistent(self):
        # 三种结局都要有可读标签
        self.assertEqual(
            set(bot.SEARCH_STATUS_LABELS), {"ok", "empty", "failed"}
        )
        # 无结果/搜索失败要能在回复里看出来；有来源靠“来源：URL”附注
        self.assertEqual(set(bot.SEARCH_STATUS_TAGS), {"empty", "failed"})
        for key in bot.SEARCH_STATUS_TAGS:
            self.assertIn(key, bot.SEARCH_STATUS_LABELS)
            self.assertTrue(bot.SEARCH_STATUS_TAGS[key].strip())

    def test_finalize_answer_keeps_source_footer(self):
        # finalize_answer 不依赖实例状态，直接以未绑定方式调用
        text = bot.Bot.finalize_answer(
            None, "回答内容", [{"url": "http://example.com/a"}], True
        )
        self.assertIn("http://example.com/a", text)


if __name__ == "__main__":
    unittest.main()
