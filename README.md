# crawler-for-github-trending  
50 lines, minimalist node crawler for [GitHub Trending](https://github.com/trending?since=daily).  
一个50行的 node 爬虫，一个简单的 [axios](https://github.com/axios/axios), [express](https://github.com/expressjs/express), [cheerio](https://github.com/cheeriojs/cheerio) 体验项目。  

## Usage  
一篇简单的介绍 https://juejin.cn/post/6844903827024396296

首先保证电脑已存在 node10.0+ 环境，然后  

1.拉取本项目  
```
git clone https://github.com/poozhu/crawler-for-github-trending.git
cd crawler-for-github-trending
npm i
node index.js
```
2.或者下载本项目压缩包，解压
```
cd crawler-for-github-trending-master  // 进入项目文件夹
npm i
node index.js
```

## Examples  
当启动项目后，可以看到控制台输出
```
Listening on port 3000!
```
此时打开浏览器，访问 http://localhost:3000/
```
http://localhost:3000/list/:time/:language // time 表示周期，language 代表语言  例如：

http://localhost:3000/list/daily  // 代表今日 可选参数：weekly,monthly
http://localhost:3000/list/daily/JavaScript  // 代表今日的 JavaScript 分类 可选参数：任意语言
```

稍微等待即可看到爬取完毕的返回数据（`data` 中榜单条目字段保持原样）：
```
{
 "outcome": "success",   // 结局标记：success / cache / empty / error / invalid_time
 "cached": false,        // 是否来自缓存
 "data": [
  {
   "title": "lib-pku / libpku",
   "links": "https://github.com/lib-pku/libpku",
   "description": "贵校课程资料民间整理",
   "language": "JavaScript",
   "stars": "14,297",
   "forks": "4,360",
   "info": "3,121 stars this week"
  },
  ...
 ]
}
```

## 缓存与结局标记
- 响应统一为 JSON 对象，`outcome` 字段标明本次请求结局：
  - `success`：实时抓取并解析成功；
  - `cache`：来自未过期缓存（上游失败时若存在未过期缓存也会回退返回缓存）；
  - `empty`：页面取到但解析出零条目（与上游失败区分）；
  - `error`：上游请求失败（网络错误或非 2xx）且无可用缓存，HTTP 502；
  - `invalid_time`：`time` 不是 `daily` / `weekly` / `monthly`，HTTP 400，不发起上游抓取。
- 缓存按 `time + language` 组合区分，默认有效期 5 分钟，有效期内不重复请求上游。
- 单个条目解析异常时跳过该条目，保留其余条目。

## 测试
```
npm test   // 使用 node:test，假上游页面 + 假请求，不访问真实 GitHub
```

## More
本项目每次访问都会实时爬取数据，所以数据返回速度会比较慢，期望作为接口数据建议定时爬取到数据库。

但了解项目代码可以带来以上各个 node 模块和爬虫最基础的用法和概念，希望可以帮到大家。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=poozhu/crawler-for-github-trending&type=Date)](https://star-history.com/#poozhu/crawler-for-github-trending&Date)

