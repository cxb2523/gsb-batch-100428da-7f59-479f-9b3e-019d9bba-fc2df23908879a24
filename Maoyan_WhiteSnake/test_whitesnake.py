# -*- coding:utf-8 -*-
"""
WhiteSnake.py 改造验收测试。

使用方式(本地临时 MySQL 实例):
    python -I test_whitesnake.py --host 127.0.0.1 --port 3316 --user root --password ""

测试全部使用伪造的 JSON 响应(FakeSession)与临时数据库(whitesnake_test_*),
不访问猫眼线上接口; 伪造数据仅用于流程验收, 不代表线上真实数据。
"""

import argparse
import json
import os
import sys
import unittest

import pymysql

# 保证无论从哪个目录启动都能导入同目录的 WhiteSnake
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import WhiteSnake as ws  # noqa: E402


# --------------------------------------------------------------------- #
# 伪造的请求层: 不发起任何真实网络请求
# --------------------------------------------------------------------- #
class FakeResponse(object):
    def __init__(self, status_code=200, text=''):
        self.status_code = status_code
        self.text = text


class FakeSession(object):
    """按预定计划逐页返回伪造响应, 并记录实际请求的 offset。"""

    def __init__(self, plan):
        # plan: [(status_code, text), ...] 按请求顺序消费
        self.plan = list(plan)
        self.requested_offsets = []
        self.calls = 0

    def get(self, url, headers=None, **kwargs):
        self.calls += 1
        # 从 URL 中解析 offset, 校验分页节奏
        offset = int(url.rsplit('offset=', 1)[1])
        self.requested_offsets.append(offset)
        if not self.plan:
            raise AssertionError('FakeSession 响应计划已耗尽, 但又收到请求: ' + url)
        return FakeResponse(*self.plan.pop(0))


def make_comment(cid, content=None, with_gender=True):
    comment = {
        'id': cid,
        'nickName': 'user{}'.format(cid),
        'userId': 900000000 + cid,
        'userLevel': 2,
        'cityName': '测试城市',
        'score': 4.5,
        'startTime': '2019-01-20 12:00:00',
        'filmView': False,
        'supportComment': True,
        'supportLike': False,
        'sureViewed': 1,
        'avatarurl': 'https://example.com/{}.png'.format(cid),
        'content': content if content is not None else '伪造短评内容{}'.format(cid),
    }
    if with_gender:
        comment['gender'] = 1
    return comment


def json_text(comments):
    return json.dumps({'cmts': comments, 'total': 999}, ensure_ascii=False)


def make_conn(db_config, dbname):
    config = dict(db_config)
    config['database'] = dbname
    config['charset'] = 'utf8mb4'
    config['use_unicode'] = False
    return pymysql.connect(**config)


class WhiteSnakeAcceptanceTest(unittest.TestCase):

    db_config = None  # 由 main 注入

    def setUp(self):
        # 每个用例一个全新临时库, 用例间互不影响
        self.dbname = 'ws_test_{}'.format(self._testMethodName.lower())
        admin = pymysql.connect(**self.db_config)
        cur = admin.cursor()
        cur.execute('DROP DATABASE IF EXISTS `{}`'.format(self.dbname))
        cur.execute('CREATE DATABASE `{}` DEFAULT CHARSET utf8mb4'.format(self.dbname))
        cur.close()
        admin.close()
        self.conn = make_conn(self.db_config, self.dbname)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        admin = pymysql.connect(**self.db_config)
        cur = admin.cursor()
        cur.execute('DROP DATABASE IF EXISTS `{}`'.format(self.dbname))
        cur.close()
        admin.close()

    def _spider(self, session, table='short_comments'):
        return ws.WhiteSnakeSpider(
            conn=self.conn,
            session=session,
            table=table,
            sleeper=lambda seconds: None,   # 去掉随机休眠, 保持反爬入口可替换
            verbose=False)

    def _assert_summary_shape(self, stats):
        """汇总必须包含可核对的全部字段。"""
        for key in ('pages_attempted', 'pages_succeeded', 'inserted',
                    'duplicates_skipped', 'stop_reason', 'stop_reason_text',
                    'stop_offset', 'detail'):
            self.assertIn(key, stats)
        self.assertEqual(
            stats['stop_reason_text'],
            ws.STOP_REASON_TEXT[stats['stop_reason']])

    def _table_row_count(self, table):
        cur = self.conn.cursor()
        cur.execute('SELECT COUNT(*) FROM `{}`'.format(table))
        return cur.fetchone()[0]

    def _table_ids(self, table):
        cur = self.conn.cursor()
        cur.execute('SELECT `id` FROM `{}` ORDER BY `id`'.format(table))
        return [r[0].decode('utf-8') if isinstance(r[0], bytes) else str(r[0])
                for r in cur.fetchall()]

    def _create_legacy_table(self):
        """模拟 whitesnake.sql 中的旧表: 14 列齐全, 但 id 无唯一约束。"""
        cur = self.conn.cursor()
        cur.execute(
            "CREATE TABLE `short_comments` ("
            "`id` VARCHAR(12) NOT NULL,"
            "`nickName` VARCHAR(30),"
            "`userId` VARCHAR(12),"
            "`userLevel` INT(3),"
            "`cityName` VARCHAR(10),"
            "`gender` tinyint(1),"
            "`score` FLOAT(2,1),"
            "`startTime` VARCHAR(30),"
            "`filmView` BOOLEAN,"
            "`supportComment` BOOLEAN,"
            "`supportLike` BOOLEAN,"
            "`sureViewed` INT(2),"
            "`avatarurl` VARCHAR(200),"
            "`content` TEXT"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")

    def _has_unique_index(self, table):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT INDEX_NAME, NON_UNIQUE FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME='id'",
            (table,))
        rows = cur.fetchall()
        return any(int(r[1]) == 0 for r in rows)

    # -------------------------------------------------------------- #
    # 结局一: 正常翻到空页结束 + 重跑去重
    # -------------------------------------------------------------- #
    def test_01_empty_page_and_idempotent_rerun(self):
        page1 = [make_comment(1001), make_comment(1002, with_gender=False)]
        page2 = [make_comment(1003), make_comment(1001)]  # 1001 与上一页重复
        page3 = []  # 空页结束
        plan = [
            (200, json_text(page1)),
            (200, json_text(page2)),
            (200, json_text(page3)),
        ]
        session = FakeSession(plan)
        spider = self._spider(session)
        spider.create_database()
        stats = spider.get_shorts_comments()

        self._assert_summary_shape(stats)
        self.assertEqual(stats['stop_reason'], ws.STOP_EMPTY_PAGE)
        self.assertEqual(stats['stop_reason_text'], '正常翻到空页结束')
        self.assertEqual(stats['pages_attempted'], 3)
        self.assertEqual(stats['pages_succeeded'], 3)
        self.assertEqual(stats['inserted'], 3)            # 1001/1002/1003
        self.assertEqual(stats['duplicates_skipped'], 1)  # 第二页的 1001
        self.assertEqual(stats['stop_offset'], 3)
        self.assertEqual(session.requested_offsets, [1, 2, 3])
        self.assertEqual(self._table_row_count('short_comments'), 3)
        self.assertEqual(self._table_ids('short_comments'),
                         ['1001', '1002', '1003'])
        self.assertTrue(self._has_unique_index('short_comments'))

        # 同一 offset 重跑: 接口回吐全部旧数据, 应全部幂等跳过, 行数不变
        rerun_session = FakeSession([
            (200, json_text(page1)),
            (200, json_text(page2)),
            (200, json_text(page3)),
        ])
        spider2 = self._spider(rerun_session)
        stats2 = spider2.get_shorts_comments()
        self.assertEqual(stats2['stop_reason'], ws.STOP_EMPTY_PAGE)
        self.assertEqual(stats2['inserted'], 0)
        self.assertEqual(stats2['duplicates_skipped'], 4)  # 三页共回吐 4 条
        self.assertEqual(self._table_row_count('short_comments'), 3)

    # -------------------------------------------------------------- #
    # 结局二: 接口返回非 200
    # -------------------------------------------------------------- #
    def test_02_http_error(self):
        session = FakeSession([
            (200, json_text([make_comment(2001)])),
            (503, '<html>Service Unavailable</html>'),
        ])
        spider = self._spider(session)
        spider.create_database()
        stats = spider.get_shorts_comments()

        self._assert_summary_shape(stats)
        self.assertEqual(stats['stop_reason'], ws.STOP_HTTP_ERROR)
        self.assertEqual(stats['pages_attempted'], 2)
        self.assertEqual(stats['pages_succeeded'], 1)
        self.assertEqual(stats['inserted'], 1)
        self.assertEqual(stats['stop_offset'], 2)
        self.assertIn('503', stats['detail'])
        # 第一页数据已入库, 停止位置可核对
        self.assertEqual(self._table_row_count('short_comments'), 1)

    def test_02b_request_exception_treated_as_http_error(self):
        class RaisingSession(FakeSession):
            def get(self, url, headers=None, **kwargs):
                self.calls += 1
                raise ws.requests.exceptions.ConnectionError('mock connection reset')

        spider = self._spider(RaisingSession([]))
        spider.create_database()
        stats = spider.get_shorts_comments()
        self.assertEqual(stats['stop_reason'], ws.STOP_HTTP_ERROR)
        self.assertEqual(stats['pages_attempted'], 1)
        self.assertEqual(stats['pages_succeeded'], 0)
        self.assertEqual(stats['stop_offset'], 1)
        self.assertIn('mock connection reset', stats['detail'])

    # -------------------------------------------------------------- #
    # 结局三: JSON 解析失败 / 字段缺失
    # -------------------------------------------------------------- #
    def test_03_invalid_json(self):
        session = FakeSession([
            (200, json_text([make_comment(3001)])),
            (200, 'this-is-not-json{'),
        ])
        spider = self._spider(session)
        spider.create_database()
        stats = spider.get_shorts_comments()

        self._assert_summary_shape(stats)
        self.assertEqual(stats['stop_reason'], ws.STOP_PARSE_ERROR)
        self.assertEqual(stats['stop_reason_text'], 'JSON解析失败或字段缺失')
        self.assertEqual(stats['pages_attempted'], 2)
        self.assertEqual(stats['pages_succeeded'], 1)
        self.assertEqual(stats['inserted'], 1)
        self.assertEqual(stats['stop_offset'], 2)
        self.assertIn('JSON', stats['detail'])
        self.assertEqual(self._table_row_count('short_comments'), 1)

    def test_03b_missing_cmts_field(self):
        session = FakeSession([
            (200, json_text([make_comment(3002)])),
            (200, json.dumps({'total': 10, 'hcmts': []})),  # 缺 cmts
        ])
        spider = self._spider(session)
        spider.create_database()
        stats = spider.get_shorts_comments()
        self.assertEqual(stats['stop_reason'], ws.STOP_PARSE_ERROR)
        self.assertEqual(stats['stop_offset'], 2)
        self.assertIn('cmts', stats['detail'])

    def test_03c_cmts_wrong_type(self):
        session = FakeSession([(200, json.dumps({'cmts': {'id': 1}}))])
        spider = self._spider(session)
        spider.create_database()
        stats = spider.get_shorts_comments()
        self.assertEqual(stats['stop_reason'], ws.STOP_PARSE_ERROR)
        self.assertEqual(stats['pages_attempted'], 1)
        self.assertEqual(stats['pages_succeeded'], 0)

    # -------------------------------------------------------------- #
    # 单条重复 / 缺 id 不影响整批
    # -------------------------------------------------------------- #
    def test_04_batch_with_duplicate_and_missing_id(self):
        # 先入库 4001
        session = FakeSession([
            (200, json_text([make_comment(4001), make_comment(4002)])),
            (200, json_text([])),
        ])
        spider = self._spider(session)
        spider.create_database()
        spider.get_shorts_comments()

        # 第二批: 4001 库中已存在, 4003 本批内重复两次, 一条缺 id, 4004 正常
        batch = [
            make_comment(4001),                       # 库中重复
            make_comment(4003),
            make_comment(4003),                       # 批内重复
            dict(make_comment(4005), id=None),        # 缺 id 的无效行
            make_comment(4004),
        ]
        session2 = FakeSession([
            (200, json_text(batch)),
            (200, json_text([])),
        ])
        spider2 = self._spider(session2)
        stats = spider2.get_shorts_comments()

        self.assertEqual(stats['stop_reason'], ws.STOP_EMPTY_PAGE)
        self.assertEqual(stats['inserted'], 2)             # 4003, 4004
        self.assertEqual(stats['duplicates_skipped'], 2)   # 4001 + 批内 4003
        self.assertEqual(stats['invalid_rows'], 1)         # 缺 id 行
        self.assertEqual(self._table_row_count('short_comments'), 4)
        self.assertEqual(self._table_ids('short_comments'),
                         ['4001', '4002', '4003', '4004'])

    # -------------------------------------------------------------- #
    # 旧表迁移: 无唯一约束 + 已有重复行, 补约束且不丢行
    # -------------------------------------------------------------- #
    def test_05_migrate_legacy_table_with_duplicates(self):
        cur = self.conn.cursor()
        # 模拟 whitesnake.sql 中的旧表结构(无唯一约束)
        self._create_legacy_table()
        # 直接写旧格式数据, 含两组重复 id(共 5 行, 3 个不同 id)
        legacy_rows = [
            ('5001', '甲', None, None, None, -1, None, None, None, None, None, None, None, '旧行1'),
            ('5001', '甲', None, None, None, -1, None, None, None, None, None, None, None, '旧行1重复'),
            ('5002', '乙', None, None, None, 1, None, None, None, None, None, None, None, '旧行2'),
            ('5003', '丙', None, None, None, 2, None, None, None, None, None, None, None, '旧行3'),
            ('5003', '丙', None, None, None, 2, None, None, None, None, None, None, None, '旧行3重复'),
        ]
        cur.executemany(
            "INSERT INTO `short_comments` VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            legacy_rows)
        self.conn.commit()
        self.assertFalse(self._has_unique_index('short_comments'))

        spider = self._spider(FakeSession([]))
        info = spider.ensure_unique_constraint()

        self.assertTrue(info['migrated'])
        self.assertEqual(info['mode'], 'rebuild')
        self.assertEqual(info['duplicate_id_count'], 2)
        self.assertTrue(self._has_unique_index('short_comments'))
        # 主表每个 id 一行
        self.assertEqual(self._table_row_count('short_comments'), 3)
        self.assertEqual(self._table_ids('short_comments'),
                         ['5001', '5002', '5003'])
        # 归档表保留全部 5 行, 一行不丢
        archive = info['archive_table']
        self.assertEqual(self._table_row_count(archive), 5)

        # 再次调用迁移应幂等: 已存在唯一索引, 不再改动
        info2 = spider.ensure_unique_constraint()
        self.assertFalse(info2['migrated'])

    def test_06_migrate_legacy_table_without_duplicates(self):
        cur = self.conn.cursor()
        # 旧表列与线上一致(14 列), 仅缺唯一约束
        self._create_legacy_table()
        cur.execute(
            "INSERT INTO `short_comments` (`id`,`nickName`,`content`) "
            "VALUES ('6001','旧用户甲','a'),('6002','旧用户乙','b')")
        self.conn.commit()
        self.assertFalse(self._has_unique_index('short_comments'))

        spider = self._spider(FakeSession([]))
        info = spider.ensure_unique_constraint()
        self.assertTrue(info['migrated'])
        self.assertEqual(info['mode'], 'alter')
        self.assertTrue(self._has_unique_index('short_comments'))
        self.assertEqual(self._table_row_count('short_comments'), 2)

        # 迁移后插入重复 id 被 IGNORE, 不报错也不新增
        session = FakeSession([
            (200, json_text([make_comment(6001), make_comment(6003)])),
            (200, json_text([])),
        ])
        spider2 = self._spider(session)
        stats = spider2.get_shorts_comments()
        self.assertEqual(stats['inserted'], 1)
        self.assertEqual(stats['duplicates_skipped'], 1)
        self.assertEqual(self._table_row_count('short_comments'), 3)

    # -------------------------------------------------------------- #
    # 反爬节奏: 每个成功翻页后随机休眠 10~20 秒, 且只在成功页后休眠
    # -------------------------------------------------------------- #
    def test_07_sleep_rhythm_preserved(self):
        sleep_calls = []
        randint_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        def fake_randint(a, b):
            randint_calls.append((a, b))
            return 13  # 固定返回, 不真的等待

        session = FakeSession([
            (200, json_text([make_comment(7101)])),
            (200, json_text([make_comment(7102)])),
            (503, 'err'),  # 失败页之后不再休眠
        ])
        spider = ws.WhiteSnakeSpider(
            conn=self.conn, session=session,
            sleeper=fake_sleep, randint_func=fake_randint, verbose=False)
        spider.create_database()
        spider.get_shorts_comments()

        # 两次成功页各休眠一次, 随机区间保持 10~20
        self.assertEqual(randint_calls, [(10, 20), (10, 20)])
        self.assertEqual(sleep_calls, [13, 13])

    # -------------------------------------------------------------- #
    # 字段映射保持不变: gender 缺失仍写 -1
    # -------------------------------------------------------------- #
    def test_08_field_mapping_unchanged(self):
        session = FakeSession([
            (200, json_text([make_comment(7001, with_gender=False)])),
            (200, json_text([])),
        ])
        spider = self._spider(session)
        spider.create_database()
        stats = spider.get_shorts_comments()
        self.assertEqual(stats['inserted'], 1)
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, nickName, gender, score, cityName, content "
            "FROM `short_comments` WHERE id='7001'")
        row = cur.fetchone()
        row_id = row[0].decode('utf-8') if isinstance(row[0], bytes) else str(row[0])
        self.assertEqual(row_id, '7001')
        self.assertEqual(row[1], b'user7001')
        self.assertEqual(row[2], -1)                 # gender 缺失 -> -1, 与旧逻辑一致
        self.assertAlmostEqual(float(row[3]), 4.5)
        self.assertEqual(row[4], '测试城市'.encode('utf-8'))
        self.assertEqual(row[5], '伪造短评内容7001'.encode('utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=3316)
    parser.add_argument('--user', default='root')
    parser.add_argument('--password', default='')
    args = parser.parse_args()

    WhiteSnakeAcceptanceTest.db_config = {
        'host': args.host,
        'port': args.port,
        'user': args.user,
        'password': args.password,
        'charset': 'utf8mb4',
    }
    suite = unittest.TestLoader().loadTestsFromTestCase(WhiteSnakeAcceptanceTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
