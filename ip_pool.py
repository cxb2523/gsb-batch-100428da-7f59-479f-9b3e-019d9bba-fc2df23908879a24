# coding:utf-8
from __future__ import print_function

import datetime
import random
import time

import config as cfg
import pymysql as mdb
import requests
from lxml import etree
from multiprocessing import Manager, Process


# Every source is crawled on its own, so one broken site can never stop the
# others.  ``{page}`` is replaced with a 1-based page number.
PROXY_SOURCES = [
    {
        'name': '66ip',
        'url_template': 'http://www.66ip.cn/{page}.html',
        'url_xpath': '/html/body/div[last()]//table//tr[position()>1]/td[1]/text()',
        'port_xpath': '/html/body/div[last()]//table//tr[position()>1]/td[2]/text()',
    },
    {
        'name': 'xicidaili',
        'url_template': 'http://www.xicidaili.com/nn/{page}',
        'url_xpath': '//table[@id="ip_list"]//tr[position()>1]/td[position()=2]/text()',
        'port_xpath': '//table[@id="ip_list"]//tr[position()>1]/td[position()=3]/text()',
    },
    {
        'name': 'mimiip',
        'url_template': 'http://www.mimiip.com/gngao/{page}',
        'url_xpath': '//table[@class="list"]//tr[position()>1]/td[1]/text()',
        'port_xpath': '//table[@class="list"]//tr[position()>1]/td[2]/text()',
    },
    {
        'name': 'kuaidaili',
        'url_template': 'http://www.kuaidaili.com/free/inha/{page}/',
        'url_xpath': '//td[@data-title="IP"]/text()',
        'port_xpath': '//td[@data-title="PORT"]/text()',
    },
]


def print_source_outcomes(outcomes):
    """
    Print one line per source so operators can see which source worked,
    how many proxies it produced and why it failed.
    """
    print(">>>>>>>>>>>>> Crawl results by source <<<<<<<<<<")
    for outcome in outcomes:
        if outcome['success']:
            print("[OK]   %s: %d proxies" % (outcome['name'], outcome['count']))
        else:
            print("[FAIL] %s: %d proxies, reason: %s" %
                  (outcome['name'], outcome['count'], outcome['error']))


class IPFactory(object):
    """
    * crawl
    * evaluation
    * storage
    """

    def __init__(self, init_db=True):
        self.page_num = cfg.page_num
        self.timeout = cfg.timeout
        self.all_ip = set()

        # init database.  The crawl entry point can skip this when only the
        # collection result is needed.
        if init_db:
            self.create_db()

    def create_db(self):
        """
        create database if not exists.
        """

        '''sql statement'''
        drop_db_str = 'drop database if exists ' + cfg.DB_NAME + ' ;'
        create_db_str = 'create database ' + cfg.DB_NAME + ' ;'
        use_db_str = 'use ' + cfg.DB_NAME + ' ;'
        create_table_str = "CREATE TABLE " + cfg.TABLE_NAME + """(
          `content` varchar(30) NOT NULL,
          `test_times` int(5) NOT NULL DEFAULT '0',
          `failure_times` int(5) NOT NULL DEFAULT '0',
          `success_rate` float(5,2) NOT NULL DEFAULT '0.00',
          `avg_response_time` float NOT NULL DEFAULT '0',
          `score` float(5,2) NOT NULL DEFAULT '0.00'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8;"""

        # database connection
        conn = mdb.connect(cfg.host, cfg.user, cfg.passwd)
        cursor = conn.cursor()
        try:
            cursor.execute(drop_db_str)
            cursor.execute(create_db_str)
            cursor.execute(use_db_str)
            cursor.execute(create_table_str)
            conn.commit()
        except OSError:
            print("cannot create database! please check your username & password.")
        finally:
            cursor.close()
            conn.close()

    def get_content(self, url, url_xpath, port_xpath):
        """
        parse web html using xpath
        return ip list.

        Request and parse errors are raised on purpose: the caller needs to
        know that a source failed instead of silently treating it as empty.
        """
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 6.3; Trident/7.0; rv:11.0) like Gecko'}

        results = requests.get(url, headers=headers, timeout=4)
        tree = etree.HTML(results.text)

        # parse [ip:port] pairs.
        url_results = tree.xpath(url_xpath)
        port_results = tree.xpath(port_xpath)
        urls = [line.strip() for line in url_results]
        ports = [line.strip() for line in port_results]

        if len(urls) != len(ports):
            raise ValueError('ip/port count mismatch: %d ip(s), %d port(s)' %
                             (len(urls), len(ports)))

        ip_list = []
        for i in range(len(urls)):
            # match each ip with it's port to the format like "127.0.0.1:80"
            ip_list.append(urls[i] + ":" + ports[i])

        return ip_list

    def crawl_source(self, source):
        """
        Crawl one source and return ``(captured_set, outcome)``.

        A failure on any page stops this source only.  Proxies captured before
        the failure are still returned, while the outcome records the failure
        reason for operators.  An empty result is reported as a failure too.
        """
        name = source['name']
        captured = set()
        error = ''

        for page in range(1, self.page_num + 1):
            try:
                url = source['url_template'].format(page=page)
                results = self.get_content(url, source['url_xpath'], source['port_xpath'])
            except Exception as e:
                error = str(e) or e.__class__.__name__
                break

            captured.update(results)
            if results:
                # wait 0.5 secs.
                time.sleep(0.5)

        if not error and not captured:
            error = 'no proxies parsed'

        outcome = {
            'name': name,
            'success': not error,
            'count': len(captured),
            'error': error,
        }
        return captured, outcome

    def crawl_all(self):
        """
        Crawl every source independently, then merge and de-duplicate the
        results.

        Returns ``(current_all_ip, outcomes)`` where ``current_all_ip`` is the
        merged, de-duplicated set of proxies captured in this round (entries
        already crawled in an earlier round are excluded, as before) and
        ``outcomes`` contains one record per source: ``name``, ``success``,
        ``count`` (proxies parsed from that source) and ``error`` (empty
        string when the source succeeded).
        """
        previously_crawled = set(self.all_ip)
        captured_all = set()
        outcomes = []

        for source in PROXY_SOURCES:
            try:
                captured, outcome = self.crawl_source(source)
            except Exception as e:
                # Last-resort guard: a broken source must never abort the
                # whole round.
                captured = set()
                outcome = {
                    'name': source.get('name', 'unknown'),
                    'success': False,
                    'count': 0,
                    'error': str(e) or e.__class__.__name__,
                }
            captured_all.update(captured)
            outcomes.append(outcome)

        # keep the crawl history used by the original implementation.
        self.all_ip.update(captured_all)
        current_all_ip = captured_all - previously_crawled

        print(current_all_ip)
        print(">>>>>>>>>>>>>> All proxies has been crawled <<<<<<<<<<<")
        return current_all_ip, outcomes

    def get_all_ip(self):
        """
        Backwards compatible helper: return only the merged proxy set.
        Use :meth:`crawl_all` (or :func:`crawl_all_sources`) when the
        per-source outcomes are needed.
        """
        current_ips, _ = self.crawl_all()
        return current_ips

    def get_valid_ip(self, ip_set, manager_list, timeout):
        """
        test if ip is valid.
        """
        # request url.
        # url = 'http://httpbin.org/get?show_env=1'
        url = 'http://github.com'

        # check proxy one by one
        for p in ip_set:
            proxy = {'http': 'http://'+p}
            try:
                start = time.time()
                r = requests.get(url, proxies=proxy, timeout=timeout)
                end = time.time()

                # judge if proxy valid
                if r.status_code == 200:
                    print('succeed: ' + p + '\t' + " succeed in " + format(end-start, '0.4f') + 's!')
                    # add to result
                    manager_list.append(p)
            except Exception:
                print(p + "\t timeout.")

    def multi_thread_validation(self, ip_set, manager_list, timeout, thread=50):
        """
        use multiple process to accelerate the judgement of valid proxies.
        """
        ip_set = set(ip_set)
        if not ip_set:
            return

        if len(ip_set) < thread:
            thread = len(ip_set)
        thread = max(thread, 1)

        # divide ip_set to blocks for later multiprocess.
        slice_len = len(ip_set) // thread

        jobs = []
        for i in range(thread-1):
            part = set(random.sample(list(ip_set), slice_len))
            ip_set -= part
            p = Process(target=self.get_valid_ip, args=(part, manager_list, timeout))
            jobs.append(p)
            p.start()

        # the last slice of ip_set.
        p = Process(target=self.get_valid_ip, args=(ip_set, manager_list, timeout))
        p.start()
        jobs.append(p)

        # join threads
        for job in jobs:
            job.join()

    def save_to_db(self, valid_ips):
        """
        save all valid proxies into db
        """
        # Take a plain snapshot first: a Manager list proxy must never leak
        # into the storage loop, otherwise entries can be lost or raise.
        valid_ips = list(valid_ips)

        if len(valid_ips) == 0:
            print("not proxy available for this time.")
            return

        # store valid proxies into db.
        print("\n>>>>>>>>>>>>>>>>>>>> Insert to database Start  <<<<<<<<<<<<<<<<<<<<<<")
        conn = mdb.connect(cfg.host, cfg.user, cfg.passwd, cfg.DB_NAME)
        cursor = conn.cursor()
        try:
            for item in valid_ips:
                # check if current ip exists.
                item_exist = cursor.execute('SELECT * FROM %s WHERE content="%s"' %(cfg.TABLE_NAME, item))

                # it's new ip
                if item_exist == 0:
                    n = cursor.execute('INSERT INTO %s VALUES("%s", 1, 0, 0, 1.0, 2.5)' %(cfg.TABLE_NAME, item))
                    conn.commit()

                    if n:
                        print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")+" "+item+" insert successfully.")
                    else:
                        print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")+" "+item+" insert failed.")

                else:
                    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")+" "+ item + " exists.")
        except Exception as e:
            print("store to db failed：" + str(e))
        finally:
            cursor.close()
            conn.close()
        print(">>>>>>>>>>>>>>>>>>>> Insert to database Ended  <<<<<<<<<<<<<<<<<<<<<<")
        print("Finished.")

    def get_proxies(self, manager_list):
        ip_list = []

        # db connection
        conn = mdb.connect(cfg.host, cfg.user, cfg.passwd, cfg.DB_NAME)
        cursor = conn.cursor()

        try:
            ip_exist = cursor.execute('SELECT * FROM %s ' % cfg.TABLE_NAME)
            result = cursor.fetchall()

            # if exists proxies in db, fetch and return.
            if len(result):
                for item in result:
                    ip_list.append(item[0])
            else:
                # crawl more proxies.
                current_ips, outcomes = self.crawl_all()
                print_source_outcomes(outcomes)
                self.multi_thread_validation(current_ips, manager_list, cfg.timeout)
                valid_ips = list(manager_list)
                self.save_to_db(valid_ips)
                ip_list.extend(valid_ips)
        except Exception as e:
            print("get ip from database failed." + str(e))
        finally:
            cursor.close()
            conn.close()

        return ip_list


def crawl_all_sources(factory=None):
    """
    Directly callable crawl entry point.

    Crawls every configured source independently, merges and de-duplicates the
    results, and returns ``(merged_ip_set, outcomes)``.

    ``factory`` may be an existing :class:`IPFactory` whose crawl state should
    be reused.  When omitted a crawl-only factory is built, so this function
    does not touch the database.
    """
    if factory is None:
        factory = IPFactory(init_db=False)
    return factory.crawl_all()


def validate_and_save(factory, ip_set):
    """
    Run the original multiprocess validation on ``ip_set`` and store every
    proxy that passed.  Returns the plain list of valid proxies.
    """
    manager = Manager()
    manager_list = manager.list()
    try:
        factory.multi_thread_validation(ip_set, manager_list, cfg.timeout)
        # Copy the Manager proxy into a normal list before using it; this is
        # what has to be saved, all of it.
        valid_ips = list(manager_list)
    finally:
        manager.shutdown()

    print("\n>>>>>>>>>>>>> Valid proxies <<<<<<<<<<")
    print(valid_ips)
    print('')
    factory.save_to_db(valid_ips)
    return valid_ips


def main():
    ip_pool = IPFactory()
    while True:
        current_ips, outcomes = crawl_all_sources(ip_pool)
        print_source_outcomes(outcomes)
        validate_and_save(ip_pool, current_ips)
        time.sleep(cfg.CHECK_TIME_INTERVAL)


if __name__ == '__main__':
    main()
