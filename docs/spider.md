## talospider - spider

#### 2.1.spider

当需要爬取有层次的页面时，这里以爬取豆瓣250全部电影为例子，这时候`spider`部分就派上了用场：

```python
# !/usr/bin/env python
from talospider import Spider, Item, TextField, AttrField, Request
from talospider.utils import get_random_user_agent


# 定义继承自item的爬虫类
class DoubanItem(Item):
    target_item = TextField(css_select='div.item')
    title = TextField(css_select='span.title')
    cover = AttrField(css_select='div.pic>a>img', attr='src')
    abstract = TextField(css_select='span.inq')

    def tal_title(self, title):
        if isinstance(title, str):
            return title
        else:
            return ''.join([i.text.strip().replace('\xa0', '') for i in title])


class DoubanSpider(Spider):
    # 定义起始url，必须
    start_urls = ['https://movie.douban.com/top250']
    # requests配置
    request_config = {
        'RETRIES': 3,
        'DELAY': 0,
        'TIMEOUT': 20
    }
	# 解析函数 必须有
    def parse(self, res):
        # 将html转化为etree
        etree = self.e_html(res.html)
        # 提取目标值生成新的url
        pages = [i.get('href') for i in etree.cssselect('.paginator>a')]
        pages.insert(0, '?start=0&filter=')
        headers = {
            "User-Agent": get_random_user_agent()
        }
        for page in pages:
            url = self.start_urls[0] + page
            yield Request(url, request_config=self.request_config, headers=headers, callback=self.parse_item)

    def parse_item(self, res):
        items_data = DoubanItem.get_items(html=res.html)
        # result = []
        for item in items_data:
            # result.append({
            #     'title': item.title,
            #     'cover': item.cover,
            #     'abstract': item.abstract,
            # })
            # 保存
            with open('douban250.txt', 'a+') as f:
                f.writelines(item.title + '\n')


if __name__ == '__main__':
    DoubanSpider.start()
```

控制台：

```shell
/Users/howie/anaconda3/envs/work3/bin/python /Users/howie/Documents/programming/python/git/talospider/examples/douban_page_by_spider.py
2017-06-07 23:17:30,346 - talospider - INFO: talospider started
2017-06-07 23:17:30,693 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250
2017-06-07 23:17:31,074 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=25&filter=
2017-06-07 23:17:31,416 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=50&filter=
2017-06-07 23:17:31,853 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=75&filter=
2017-06-07 23:17:32,523 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=100&filter=
2017-06-07 23:17:33,032 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=125&filter=
2017-06-07 23:17:33,537 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=150&filter=
2017-06-07 23:17:33,990 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=175&filter=
2017-06-07 23:17:34,406 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=200&filter=
2017-06-07 23:17:34,787 - talospider_requests - INFO: GET a url: https://movie.douban.com/top250?start=225&filter=
2017-06-07 23:17:34,809 - talospider - INFO: Time usage：0:00:04.462108

Process finished with exit code 0
```

此时当前目录会生成`douban250.txt`，具体见[douban_page_by_spider.py](../examples/douban_page_by_spider.py)。

#### 2.2.请求结局记录（RequestOutcome）

每个 `Request` 执行后都会产生一条 `RequestOutcome` 结局记录，无论成功还是失败。字段如下：

| 字段 | 说明 |
| --- | --- |
| `url` | 最终 URL（跟随重定向后的地址；连接层失败时为请求时传入的 URL） |
| `method` | HTTP 方法（GET/POST） |
| `success` | 是否最终拿到可用响应体 |
| `attempts` | 总尝试次数（首次请求 + 每次重试） |
| `status_code` | 最后一次响应的状态码，未收到响应时为 `None` |
| `error_type` | 可区分的失败类型：`http_4xx` / `http_5xx` / `connection_error` / `request_error` / `decode_error` / `missing_schema`，成功时为 `None` |
| `elapsed` | 整条请求（含重试）耗时，单位秒 |

重试触发条件保持不变：仅 5xx 且仍有 `RETRIES` 额度时才重试，每次重试都会计入 `attempts`；4xx、连接错误、解码失败等不重试的情况也会各自留下对应 `error_type` 的结局。

`Spider.start()` 运行结束后，可通过返回值或类属性 `Spider.request_outcomes` 取到本次运行所有请求的结局列表（包含 `parse` 中 `yield` 出的后续 `Request`），按完成先后排列，每次运行开始时自动清空。多进程模式（`set_mul=True`）下子进程产生的结局会随进程池结果回到主进程汇总，不会因进程边界丢失：

```python
if __name__ == '__main__':
    outcomes = DoubanSpider.start()
    for outcome in outcomes:
        print(outcome.as_dict())
```

