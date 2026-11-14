# -*- coding:utf-8 -*-

import json
import random
import time

import pymysql
import requests

"""
【1】教程文章：https://mp.weixin.qq.com/s/zRJww2SRsexYGhbNJ4HtPw
【2】抓取猫眼 《白蛇：缘起》的短片
【3】反爬虫分析, 抓包教程

改造点:
    1. short_comments.id 增加唯一约束:
       - 新建表时建表语句直接带 UNIQUE KEY;
       - 已存在的旧表通过 ensure_unique_constraint() 迁移补约束,
         迁移前保留全部已有行(含重复行)到归档表, 不丢数据。
    2. 插入幂等: 插入前筛掉库中已存在 / 本批重复的 id, 并使用
       INSERT IGNORE, 单条重复不会导致整批失败。
    3. 分页循环区分三类结局:
       - EMPTY_PAGE   正常翻到空页结束
       - HTTP_ERROR   接口返回非 200 (含网络异常)
       - PARSE_ERROR  JSON 解析失败或缺少 cmts 等字段
       结束时打印可核对的采集汇总。
    4. requests.Session 与 pymysql 连接均可注入, 便于用伪造响应和
       临时 MySQL 库做验收测试。

@Author monkey
@Date 2019-1-20
"""

# Mysql 配置信息
# Mysql 数据库建议使用 5.5 或者 5.5 以上的版本
# 根据你的环境修改
MYSQL_HOST = '127.0.0.1'
MYSQL_PORT = 3306
MYSQL_DBNAME = 'whitesnake'  # 数据库名
MYSQL_USER = 'root'          # 数据库用户
MYSQL_PASSWORD = '123456'    # 数据库密码

# 电影短片的 API 接口地址
MOVIE_URL = 'http://m.maoyan.com/mmdb/comments/movie/1235560.json?_v_=yes&offset={}'

HEADERS = {
    'User-agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.2883.87 Mobile Safari/537.36',
    'Accept-Encoding': 'gzip, deflate',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Host': 'm.maoyan.com',
    'Accept-Language': 'zh-CN,zh;q=0.8',
    'Cache-Control': 'max-age=0',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# 入库字段(顺序与含义保持不变, 不要调整已有列的含义)
COLUMNS = ('id', 'nickName', 'userId', 'userLevel', 'cityName', 'gender',
           'score', 'startTime', 'filmView', 'supportComment', 'supportLike',
           'sureViewed', 'avatarurl', 'content')

# 采集结束原因
STOP_EMPTY_PAGE = 'EMPTY_PAGE'      # 正常翻到空页结束
STOP_HTTP_ERROR = 'HTTP_ERROR'      # 接口返回非 200 / 请求异常
STOP_PARSE_ERROR = 'PARSE_ERROR'    # JSON 解析失败或字段缺失
STOP_DATABASE_ERROR = 'DATABASE_ERROR'  # 入库阶段数据库异常(兜底)

STOP_REASON_TEXT = {
    STOP_EMPTY_PAGE: '正常翻到空页结束',
    STOP_HTTP_ERROR: '接口返回非200(或请求异常)',
    STOP_PARSE_ERROR: 'JSON解析失败或字段缺失',
    STOP_DATABASE_ERROR: '数据库写入异常',
}


class CommentParseError(ValueError):
    """响应体不是合法 JSON, 或缺少 cmts 等必需字段时抛出。"""


class WhiteSnakeSpider(object):

    def __init__(self,
                 conn=None,
                 session=None,
                 mysql_config=None,
                 table='short_comments',
                 start_offset=1,
                 sleeper=time.sleep,
                 randint_func=random.randint,
                 verbose=True):
        """
        :param conn: 已建立的 pymysql 连接; 为 None 时按 mysql_config/模块默认配置自建。
        :param session: requests.Session 或任意带 get(url, headers=...) 方法的对象,
                        测试时可注入返回伪造 JSON 的假 session。
        :param mysql_config: 自建连接时覆盖默认 MYSQL_* 配置的字典。
        :param table: 短评表名。
        :param start_offset: 起始分页 offset。
        :param sleeper: 每页之间的休眠函数, 默认 time.sleep, 测试可注入空函数。
        :param randint_func: 随机休眠秒数函数, 默认 random.randint。
        :param verbose: 是否打印每条短评等过程日志。
        """
        self.__table = table
        self.start_offset = start_offset
        self._sleeper = sleeper
        self._randint = randint_func
        self.verbose = verbose

        # ---- 可替换的数据库连接入口 ----
        if conn is not None:
            self.conn = conn
            self._owns_conn = False
        else:
            config = {
                'host': MYSQL_HOST,
                'port': MYSQL_PORT,
                'db': MYSQL_DBNAME,
                'user': MYSQL_USER,
                'passwd': MYSQL_PASSWORD,
                'charset': 'utf8mb4',
                'use_unicode': False,
            }
            if mysql_config:
                config.update(mysql_config)
            self.conn = pymysql.connect(**config)
            self._owns_conn = True
        self.cursor = self.conn.cursor()

        # ---- 可替换的请求会话入口 ----
        self._injected_session = session

    # ------------------------------------------------------------------ #
    # 建表 / 迁移
    # ------------------------------------------------------------------ #
    def _build_create_table_sql(self, table_name):
        """带 id 唯一约束的建表语句。"""
        return (
            "CREATE TABLE IF NOT EXISTS {} ("
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
            "`content` TEXT,"
            "UNIQUE KEY `uk_{}_id` (`id`)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4".format(
                table_name, table_name)
        )

    def create_database(self):
        """建表(不存在则建, 新建表带 id 唯一约束), 并对旧表补齐唯一约束。"""
        self.cursor.execute(self._build_create_table_sql(self.__table))
        self.conn.commit()
        if self.verbose:
            print('===== 成功创建数据库表 =====')
        self.ensure_unique_constraint()

    def _has_unique_index_on_id(self):
        """判断表上是否已存在作用于 id 列的唯一索引(唯一约束)。"""
        self.cursor.execute(
            "SELECT INDEX_NAME, NON_UNIQUE FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND COLUMN_NAME = 'id'",
            (self.__table,))
        rows = self.cursor.fetchall()
        # NON_UNIQUE = 0 即唯一索引
        return any(int(row[1]) == 0 for row in rows)

    def _duplicate_ids(self):
        """返回表中存在重复的 id 列表。"""
        self.cursor.execute(
            "SELECT `id`, COUNT(*) AS c FROM `{}` "
            "GROUP BY `id` HAVING COUNT(*) > 1".format(self.__table))
        return [row[0] for row in self.cursor.fetchall()]

    def ensure_unique_constraint(self):
        """
        给已存在的旧表补 id 唯一约束。
        - 已有唯一索引: 直接返回;
        - 无重复行: ALTER TABLE 加 UNIQUE KEY;
        - 有重复行: 原表整体改名为归档表(一行不丢), 新建带约束的表并
          INSERT IGNORE 回填每个 id 的一行, 再切换表名。
        返回本次迁移的说明字典, 便于核对。
        """
        if self._has_unique_index_on_id():
            return {'migrated': False, 'reason': 'unique index already exists'}

        dup_ids = self._duplicate_ids()
        if not dup_ids:
            # 无重复数据, 直接加约束, 不动任何行
            self.cursor.execute(
                "ALTER TABLE `{}` ADD UNIQUE KEY `uk_{}_id` (`id`)".format(
                    self.__table, self.__table))
            self.conn.commit()
            return {'migrated': True, 'mode': 'alter', 'duplicate_id_count': 0}

        # 存在重复 id: 原表整体改名为归档表(一行不丢、不复制数据),
        # 再建带唯一约束的新表, 每个 id 回填一行
        archive_table = self._available_archive_name()
        column_list = ', '.join('`{}`'.format(c) for c in COLUMNS)

        self.cursor.execute(
            "RENAME TABLE `{}` TO `{}`".format(self.__table, archive_table))
        self.cursor.execute(self._build_create_table_sql(self.__table))
        # 每个重复 id 只回填一行; 被舍弃的重复副本完整保留在归档表中
        self.cursor.execute(
            "INSERT IGNORE INTO `{}` ({}) SELECT {} FROM `{}`".format(
                self.__table, column_list, column_list, archive_table))
        kept_rows = self.cursor.rowcount
        self.conn.commit()

        self.cursor.execute("SELECT COUNT(*) FROM `{}`".format(archive_table))
        archived_rows = self.cursor.fetchone()[0]

        if self.verbose:
            print('===== 检测到重复 id {} 个, 原表全部 {} 行已归档到 {}, '
                  '新表保留 {} 行并补齐唯一约束 ====='.format(
                      len(dup_ids), archived_rows, archive_table, kept_rows))
        return {
            'migrated': True,
            'mode': 'rebuild',
            'duplicate_id_count': len(dup_ids),
            'archive_table': archive_table,
            'archived_rows': archived_rows,
            'kept_rows': kept_rows,
        }

    def _available_archive_name(self):
        """生成一个当前库中不存在的归档表名。"""
        base = '{}__pre_unique'.format(self.__table)
        name = base
        suffix = 1
        while True:
            self.cursor.execute(
                "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (name,))
            if self.cursor.fetchone() is None:
                return name
            suffix += 1
            name = '{}_{}'.format(base, suffix)

    # ------------------------------------------------------------------ #
    # 解析 / 入库
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_comments(response_text):
        """
        把接口响应文本解析成 cmts 短评列表。
        JSON 非法、根不是对象、缺少 cmts 字段或 cmts 不是列表时抛 CommentParseError。
        """
        try:
            payload = json.loads(response_text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise CommentParseError('响应不是合法 JSON: {}'.format(exc))
        if not isinstance(payload, dict) or 'cmts' not in payload:
            raise CommentParseError("响应 JSON 缺少 'cmts' 字段")
        if not isinstance(payload['cmts'], list):
            raise CommentParseError("响应字段 'cmts' 不是列表")
        return payload['cmts']

    @staticmethod
    def _normalize_id(value):
        """统一 id 的比对形式: use_unicode=False 时库里读回的是 bytes。"""
        if isinstance(value, bytes):
            return value.decode('utf-8')
        return str(value)

    def _build_row(self, comment):
        """单条短评 dict -> 入库 tuple, 字段映射与旧版完全一致。缺 id 返回 None。"""
        if comment.get('id') is None:
            return None
        if comment.get('gender') is None:
            comment['gender'] = -1
        return tuple(comment.get(column) for column in COLUMNS)

    def insert_comments(self, datalist):
        """
        幂等批量插入。
        - 库中已存在的 id、本批内重复的 id 跳过(计入 duplicates_skipped);
        - 缺少 id 的无效行跳过(计入 invalid_rows);
        - 使用 INSERT IGNORE, 单条重复/冲突不会让整批失败。
        :return: (inserted, duplicates_skipped, invalid_rows)
        """
        insert_sql = (
            "INSERT IGNORE INTO {} (id, nickName, userId, userLevel, cityName, "
            "gender, score, startTime, filmView, supportComment, supportLike, "
            "sureViewed, avatarurl, content) "
            "VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        ).format(self.__table)

        invalid_rows = 0
        unique_rows = []
        seen_ids = set()
        duplicates_skipped = 0

        for comment in datalist:
            row = self._build_row(comment)
            if row is None:
                invalid_rows += 1
                continue
            row_id = self._normalize_id(row[0])
            if row_id in seen_ids:
                duplicates_skipped += 1
                continue
            seen_ids.add(row_id)
            unique_rows.append((row_id, row))

        if not unique_rows:
            return 0, duplicates_skipped, invalid_rows

        # 查询库里已经存在的 id
        placeholders = ', '.join(['%s'] * len(unique_rows))
        self.cursor.execute(
            "SELECT `id` FROM `{}` WHERE `id` IN ({})".format(
                self.__table, placeholders),
            [item[0] for item in unique_rows])
        existing_ids = set(self._normalize_id(r[0]) for r in self.cursor.fetchall())

        to_insert = []
        for row_id, row in unique_rows:
            if row_id in existing_ids:
                duplicates_skipped += 1
            else:
                to_insert.append(row)

        inserted = 0
        if to_insert:
            self.cursor.executemany(insert_sql, to_insert)
            self.conn.commit()
            # 预筛后理论上全部为新增; 以预筛计数为准, rowcount 仅作旁证
            inserted = len(to_insert)
        return inserted, duplicates_skipped, invalid_rows

    def close_connection(self):
        """关闭游标与(自建的)数据库连接, 可重复调用。"""
        if getattr(self, 'cursor', None) is not None:
            try:
                self.cursor.close()
            except Exception:
                pass
            self.cursor = None
        if self._owns_conn and self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None

    # ------------------------------------------------------------------ #
    # 采集主流程
    # ------------------------------------------------------------------ #
    def fetch_page(self, session, offset):
        """请求单页, 单独成方法便于测试替身覆盖。"""
        return session.get(MOVIE_URL.format(offset), headers=HEADERS)

    def get_shorts_comments(self):
        """
        从 start_offset 开始逐页抓取并入库, 直到:
          1. 翻到空 cmts 页 (EMPTY_PAGE);
          2. 接口非 200 / 请求异常 (HTTP_ERROR);
          3. JSON 解析失败或字段缺失 (PARSE_ERROR)。
        :return: 采集汇总 dict
        """
        stats = {
            'pages_attempted': 0,       # 尝试页数
            'pages_succeeded': 0,       # 成功页数(200 且解析入库成功)
            'inserted': 0,              # 新增条数
            'duplicates_skipped': 0,    # 跳过重复条数
            'invalid_rows': 0,          # 缺少 id 等无效行数
            'stop_reason': None,        # 终止原因
            'stop_reason_text': '',     # 终止原因(中文)
            'stop_offset': None,        # 终止页码
            'detail': '',               # 终止细节, 便于核对
        }

        session = self._injected_session or requests.Session()
        offset = self.start_offset

        try:
            while True:
                stats['pages_attempted'] += 1
                if self.verbose:
                    print('============抓取第', offset, '页短评============')
                    print('============>>>', MOVIE_URL.format(offset))

                # --- 结局二: 非 200 / 网络异常 ---
                try:
                    response = self.fetch_page(session, offset)
                except requests.exceptions.RequestException as exc:
                    stats['stop_reason'] = STOP_HTTP_ERROR
                    stats['detail'] = '请求异常: {}'.format(exc)
                    break

                status_code = getattr(response, 'status_code', None)
                if status_code != 200:
                    stats['stop_reason'] = STOP_HTTP_ERROR
                    stats['detail'] = 'HTTP 状态码 {}'.format(status_code)
                    if self.verbose:
                        print('>=== 抓取第 ', offset, ' 失败, 错误码为 ' + str(status_code))
                    break

                # --- 结局三: JSON 解析失败 / 字段缺失 ---
                try:
                    comments = self.parse_comments(response.text)
                except CommentParseError as exc:
                    stats['stop_reason'] = STOP_PARSE_ERROR
                    stats['detail'] = str(exc)
                    if self.verbose:
                        print('>=== 抓取第 ', offset, ' 页响应解析失败: ', exc)
                    break

                # --- 结局一: 正常翻到空页 ---
                if len(comments) == 0:
                    stats['pages_succeeded'] += 1
                    stats['stop_reason'] = STOP_EMPTY_PAGE
                    stats['detail'] = '第 {} 页 cmts 为空'.format(offset)
                    stats['stop_offset'] = offset
                    break

                data_list = []
                for comment in comments:
                    data = {column: comment.get(column) for column in COLUMNS}
                    if self.verbose:
                        print(data)
                    data_list.append(data)
                if self.verbose:
                    print('============解析到', len(data_list), '条短评数据============')

                try:
                    inserted, duplicated, invalid = self.insert_comments(data_list)
                except pymysql.Error as exc:
                    stats['stop_reason'] = STOP_DATABASE_ERROR
                    stats['detail'] = '入库异常: {}'.format(exc)
                    stats['stop_offset'] = offset
                    break

                stats['pages_succeeded'] += 1
                stats['inserted'] += inserted
                stats['duplicates_skipped'] += duplicated
                stats['invalid_rows'] += invalid

                offset += 1
                # 保持原有的每页随机休眠反爬节奏 (10~20 秒)
                self._sleeper(self._randint(10, 20))
        finally:
            if stats['stop_offset'] is None:
                stats['stop_offset'] = offset
            stats['stop_reason_text'] = STOP_REASON_TEXT.get(stats['stop_reason'], '')
            self._print_summary(stats)
            self.close_connection()

        return stats

    def _print_summary(self, stats):
        """打印可核对的采集汇总。"""
        line = '=' * 22 + ' 采集汇总 ' + '=' * 22
        print('\n' + line)
        print('尝试页数: {}'.format(stats['pages_attempted']))
        print('成功页数: {}'.format(stats['pages_succeeded']))
        print('新增条数: {}'.format(stats['inserted']))
        print('跳过重复条数: {}'.format(stats['duplicates_skipped']))
        print('无效行数(缺少id): {}'.format(stats['invalid_rows']))
        print('终止原因: {} ({})'.format(
            stats['stop_reason_text'], stats['stop_reason']))
        print('终止页码: {}'.format(stats['stop_offset']))
        if stats['detail']:
            print('终止细节: {}'.format(stats['detail']))
        print('=' * len(line))


if __name__ == '__main__':
    spider = WhiteSnakeSpider()
    spider.create_database()
    spider.get_shorts_comments()
