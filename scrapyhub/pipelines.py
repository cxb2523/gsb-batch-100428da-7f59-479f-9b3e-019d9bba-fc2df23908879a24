# scrapyhub/pipelines.py
from datetime import datetime
from typing import Any

from .items import validate_ranking_item
from .utils.db_utils import DBPoolManager


def _error_code(exc):
    """Return MySQL's numeric error code when the driver exposes one."""
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _is_duplicate_key_error(exc):
    """Recognise a duplicate-entry error without importing a driver eagerly."""
    code = _error_code(exc)
    if code is not None:
        # 1062 is ER_DUP_ENTRY; other integrity errors must still surface.
        return code == 1062
    if "duplicate entry" in str(exc).lower():
        return True
    return type(exc).__name__ in {"DuplicateKeyError"}


def _is_duplicate_index_error(exc):
    """Recognise an already-existing index when adding the unique key."""
    if _error_code(exc) == 1061:
        return True
    return "duplicate key name" in str(exc).lower()


class RankingPipeline:
    """统一的排行榜数据管道"""

    SOURCE_MAP = {
        'weibo': 1,
        'zhihu': 2,
        'baidu': 3,
        '36kr': 4,
        'douyin': 5,
        'wallstreetcn': 6,
        'thepaper': 7,
        'toutiao': 8,
        'bilibili': 9,
        'juejin': 10,
    }

    def __init__(self, db_settings):
        self.db_settings = db_settings
        self.batch_timestamp = datetime.now()
        self.conn: Any = None
        self.cursor: Any = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.get('MYSQL_SETTINGS'))

    def open_spider(self, spider):
        """爬虫开启时初始化"""
        try:
            self.conn = DBPoolManager.get_connection(self.db_settings)
            self.cursor = self.conn.cursor()
            self._ensure_table(spider)
            spider.logger.info(f"数据库连接成功，批次时间: {self.batch_timestamp}")
        except Exception as e:
            spider.logger.error(f"数据库连接失败: {e}")
            raise

    def close_spider(self, spider):
        """爬虫关闭时清理"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
        spider.logger.info(f"批次结束，时间戳: {self.batch_timestamp}")

    def process_item(self, item, spider):
        """Validate, de-duplicate, and persist one ranking item."""
        normalized = validate_ranking_item(item)
        for key, value in normalized.items():
            item[key] = value

        # 去重键是规范化后的标题，与来源编号无关。validate_ranking_item 已统一
        # 去除首尾空白，因此这里直接使用它的结果。
        title = normalized['title']
        source_id = self.SOURCE_MAP.get(normalized['source'], 0)

        # 检查是否存在
        check_sql = "SELECT id FROM ranking WHERE title = %s"
        self.cursor.execute(check_sql, (title,))
        result = self.cursor.fetchone()

        if result:
            # 更新已有记录；created_at 保留首次收录时间，不在此处覆盖。
            self._update_record(title, normalized['hot_rank'], source_id)
            spider.logger.debug(f"Updated record: {title}")
        else:
            # 插入新记录
            insert_sql = """
                INSERT INTO ranking
                (title, url, hot_rank, batch_timestamp, created_at, source)
                VALUES (%s, %s, %s, %s, NOW(), %s)
            """
            try:
                self.cursor.execute(insert_sql, (
                    title,
                    normalized['url'],
                    normalized['hot_rank'],
                    self.batch_timestamp,
                    source_id
                ))
            except Exception as exc:
                # 并发场景下另一个进程可能刚插入同一标题：唯一键冲突时转为更新。
                if not _is_duplicate_key_error(exc):
                    raise
                self._update_record(title, normalized['hot_rank'], source_id)
                spider.logger.debug(f"Updated record after duplicate key: {title}")
            else:
                spider.logger.debug(f"Inserted record: {title}")
        return item

    def _update_record(self, title, hot_rank, source_id):
        """更新热度、来源编号和批次时间，保留 created_at。"""
        update_sql = """
            UPDATE ranking
            SET hot_rank = %s, source = %s, batch_timestamp = %s
            WHERE title = %s
        """
        self.cursor.execute(update_sql, (hot_rank, source_id, self.batch_timestamp, title))

    def _ensure_table(self, spider=None):
        """确保表存在，并保证 title 上有跨来源去重所需的唯一约束。"""
        create_sql = '''
            CREATE TABLE IF NOT EXISTS ranking (
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                url VARCHAR(512) NOT NULL,
                hot_rank INT,
                source TINYINT DEFAULT 0,
                batch_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_title (title),
                INDEX idx_batch_timestamp (batch_timestamp),
                INDEX idx_source (source)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        '''
        self.cursor.execute(create_sql)
        self._ensure_title_unique_index(spider)

    def _ensure_title_unique_index(self, spider=None):
        """为已存在但尚未带唯一约束的旧表补充约束；重复执行不会失败。"""
        if self._has_unique_title_index():
            return
        try:
            self.cursor.execute(
                "ALTER TABLE ranking ADD UNIQUE KEY uniq_title (title)"
            )
        except Exception as exc:
            # 唯一键已存在（例如另一进程刚完成升级）时无需重复创建。
            if not _is_duplicate_index_error(exc):
                self._log_upgrade_failure(exc, spider)

    def _log_upgrade_failure(self, exc, spider):
        """记录无法升级唯一约束的原因，让爬虫继续采集而不是整体失败。

        典型原因是历史数据里已有同标题多条记录，需要人工合并后再加约束。
        """
        message = (
            "无法为 ranking.title 添加唯一约束（存在历史重复标题或权限不足），"
            f"并发插入保护未生效: {exc}"
        )
        logger = getattr(spider, "logger", None)
        if logger is not None:
            logger.warning(message)
        else:
            import logging

            logging.getLogger(__name__).warning(message)

    def _has_unique_title_index(self):
        """跳过已存在的唯一索引，避免 MySQL 不支持 ADD INDEX IF NOT EXISTS。"""
        try:
            self.cursor.execute("SHOW INDEX FROM ranking")
        except Exception:
            # 旧驱动/方言不支持 SHOW INDEX 时交由 ALTER 的重复键处理。
            return False
        fetchall = getattr(self.cursor, "fetchall", None)
        if fetchall is None:
            return False
        for row in fetchall() or ():
            # SHOW INDEX 列顺序: Table, Non_unique, Key_name, Seq_in_index, Column_name, ...
            if len(row) < 5:
                continue
            non_unique, key_name, column_name = row[1], row[2], row[4]
            if column_name == "title" and key_name == "uniq_title" and not non_unique:
                return True
        return False
