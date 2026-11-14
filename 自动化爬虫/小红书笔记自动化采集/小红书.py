import os
from loguru import logger
from DrissionPage import ChromiumPage

from DataRecorder import Recorder

# 每个关键词的采集目标条数
MAX_COUNT = 20
# 每个关键词允许的失败次数上限，达到后提前停止该关键词
FAIL_LIMIT = 5
# 每采集成功多少条滚动一次页面
SCROLL_INTERVAL = 5
# 监听接口响应的等待秒数
LISTEN_TIMEOUT = 3
# 连续多少轮没有新卡片时认为页面已加载不出更多内容
MAX_NO_NEW_ROUNDS = 3

# 失败原因类别
REASON_CLICK = '点击失败'
REASON_TIMEOUT = '监听超时'
REASON_FIELD = '字段缺失'

# 卡片与关闭按钮的xpath
CARDS_XPATH = 'xpath://*[@id="global"]/div[2]/div[2]/div/div/div[3]/div[1]/section'
CARD_IMG_XPATH = 'xpath:./div/a[2]/img'
CLOSE_BTN_XPATH = 'xpath:/html/body/div[5]/div[2]/div'


def create_xlsx(keyword):
    filename = f'{keyword.strip()}.xlsx'
    # 如果文件已存在，先删除它
    if os.path.exists(filename):
        os.remove(filename)
        logger.info(f'已清除旧文件: {filename}')
    # 创建新的recorder实例
    recorder = Recorder(filename)
    recorder.set.show_msg(False) # 移除多余打印信息
    return recorder

# 对返回的数据进行处理，用以获得希望返回的字符
def find_want(data, target_key):
    # 处理数据为字典的递归遍历
    if isinstance(data, dict):
        for key, val in data.items():
            if key == target_key:
                return val
            # 递归遍历子元素
            ret = find_want(val, target_key)
            if ret:
                return ret
    elif isinstance(data, list):
        for i in data:
            ret = find_want(i, target_key)
            if ret:
                return ret
    else:
        return None


def close_card(page):
    """关闭已打开的卡片。关闭失败只记警告，不向外抛出，避免中断整轮采集。"""
    try:
        close_btn = page.ele(CLOSE_BTN_XPATH, timeout=1)
        if close_btn:
            close_btn.click()
            page.wait.load_start()
    except Exception as e:
        logger.warning(f'关闭卡片失败（忽略，继续采集下一个位置）: {e}')


def collect_card(page, card, recorder):
    """采集单个卡片位置。

    返回 (是否成功, 失败原因类别)。
    只要卡片被点开过，无论成功失败都会在返回前尝试关闭卡片。
    """
    # 1. 点击卡片（用js点击，默认的模拟点击可能因图层遮蔽点不到）
    try:
        card.ele(CARD_IMG_XPATH).click(by_js=True)
    except Exception as e:
        logger.warning(f'点击卡片异常: {e}')
        return False, REASON_CLICK

    try:
        # 2. 等待接口响应，超时未等到视为该位置失败
        try:
            res = page.listen.wait(count=1, timeout=LISTEN_TIMEOUT, fit_count=True)
        except Exception as e:
            logger.warning(f'监听接口异常: {e}')
            res = None
        if not res:
            return False, REASON_TIMEOUT

        # 3. 数据提取与校验：昵称与标题至少要有其一
        data = res.response.body
        nickname = find_want(data, 'nickname')
        title = find_want(data, 'title')
        if not nickname and not title:
            return False, REASON_FIELD

        # 基于recorder将采集数据写入excel
        map_ = {
            '博主昵称': nickname,
            '标题': title,
            '详情': find_want(data, 'desc'),
            '评论数': find_want(data, 'comment_count'),
            '点赞数': find_want(data, 'liked_count'),
        }
        recorder.add_data(map_)
        recorder.record()
        return True, None
    finally:
        # 无论成功失败，关闭已打开的卡片，不影响下一个位置
        close_card(page)


# dp自动化抓取
def handler(page, keyword):
    recorder = create_xlsx(keyword)
    # 监听数据接口 -- 尽量写在最前面
    page.listen.start('web/v1/feed')  # 形如这种格式的会被抓取
    page.get(f'https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_profile_page')
    page.wait.load_start()

    seen = set()          # 已见过的 data-index（成功/失败/跳过都去重）
    success_count = 0
    fail_count = 0
    skip_count = 0
    failures = []         # [(data-index, 失败原因类别)]，用于汇总核对
    stopped_early = False # 失败达到上限提前停止标记
    cursor = 0            # 卡片列表游标，每个位置只处理一次
    no_new_rounds = 0     # 连续没有新卡片的轮数

    logger.info(f'开始爬取{keyword}项，目标{MAX_COUNT}条...\n')
    while success_count < MAX_COUNT and not stopped_early:
        cards = page.eles(CARDS_XPATH)
        if cursor < len(cards):
            no_new_rounds = 0
            for card in cards[cursor:]:
                cursor += 1
                if success_count >= MAX_COUNT or stopped_early:
                    break

                # 同一关键词内按 data-index 去重：重复位置不写入、不计成功，计为跳过
                index = card.attr('data-index')
                if index in seen:
                    skip_count += 1
                    logger.info(f'位置 data-index={index} 重复，跳过')
                    continue
                seen.add(index)

                logger.info(f'正在爬取第{success_count + 1}条数据 (data-index={index})...')
                try:
                    ok, reason = collect_card(page, card, recorder)
                except Exception as e:
                    # 兜底：单个位置的任何意外都不允许中断整轮采集
                    ok, reason = False, f'{REASON_CLICK}(意外异常: {e})'
                    close_card(page)

                if ok:
                    success_count += 1
                    # 每采集一定数量滚动一次
                    if success_count % SCROLL_INTERVAL == 0:
                        logger.info('页面正进行滚动以加载更多内容...')
                        page.scroll.down(1000)
                        page.wait.load_start()
                else:
                    fail_count += 1
                    failures.append((index, reason))
                    logger.warning(f'位置 data-index={index} 采集失败: {reason}')
                    if fail_count >= FAIL_LIMIT:
                        stopped_early = True
                        logger.warning(f'失败次数达到上限({FAIL_LIMIT})，提前停止该关键词采集')
                        break
        else:
            # 当前列表已处理完，滚动加载更多；连续多轮无新卡片则结束该关键词
            no_new_rounds += 1
            if no_new_rounds >= MAX_NO_NEW_ROUNDS:
                logger.warning(f'连续{MAX_NO_NEW_ROUNDS}轮未发现新卡片，停止该关键词采集')
                break
            logger.info('页面正进行滚动以加载更多内容...')
            page.scroll.down(1000)
            page.wait.load_start()

    # 采集结束，输出可核对的汇总
    summary = {
        'keyword': keyword,
        'success': success_count,
        'failed': fail_count,
        'skipped': skip_count,
        'failures': failures,
        'stopped_early': stopped_early,
    }
    logger.info(f'[{keyword}] 采集汇总: 成功 {success_count} 条, 失败 {fail_count} 条, 跳过 {skip_count} 条')
    if failures:
        logger.info(f'[{keyword}] 失败明细:')
        for index, reason in failures:
            logger.info(f'  位置 data-index={index}: {reason}')
    if stopped_early:
        logger.info(f'[{keyword}] 失败次数达到上限({FAIL_LIMIT})，已提前停止，实际成功 {success_count}/{MAX_COUNT} 条')
    elif success_count >= MAX_COUNT:
        logger.info(f'[{keyword}] 已采满 {success_count} 条，采集完成!')
    else:
        logger.info(f'[{keyword}] 未采满，实际成功 {success_count}/{MAX_COUNT} 条')
    return summary

# 主执行
def main():
    with open('关键字.txt', 'r', encoding='utf8') as f:
        keywords = f.readlines()
    # 浏览器驱动对象
    page = ChromiumPage()
    page.get('https://www.xiaohongshu.com/explore')

    # 等待操作(登录)
    input('请扫码登录,登录成功后按回车继续\n如已登录请直接按回车')

    for keyword in keywords:
        handler(page, keyword.strip())


if __name__ == '__main__':
    main()
