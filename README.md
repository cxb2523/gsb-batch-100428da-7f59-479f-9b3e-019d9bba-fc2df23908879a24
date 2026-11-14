

# 🛡️ Spider Defense Bypass Techniques

> 常见爬虫反爬技术突破 | Breaking through common anti-scraping techniques

本仓库聚焦于非JS逆向的爬虫技巧知识的研究与实战。通过系统记录各种爬虫知识点与对应不同网站的实际应用
，系统记录绕过策略与技术实现，帮助开发者掌握爬虫各种高阶爬取技巧。



💡 针对验证码、IP 限制、Scrapy框架使用、异步爬虫、自动化爬取等具体的技术难点进行深入剖析，提供使用思路和解决方案。

📷 本项目将长期更新，目标是构建一个系统、实用的爬虫技术知识案例库。

📌 同时本案例集的各图文博客讲解非常详细，非常推荐小白配合着博客把这些知识点一点点吃下来。

📚 博客同步发布：

👉 [我的CSDN主页](https://blog.csdn.net/2401_87328929)

👉 [CSDN对应专栏](https://blog.csdn.net/2401_87328929/category_12970268.html)


## 📦 环境依赖

运行本仓库示例代码前，请确保已安装以下核心依赖库：
```bash
pip install requests lxml aiohttp DrissionPage datarecorder loguru scrapy
```

--- 

## 🚅 目录跳转

| 站点                                                                                                            | 仓库                                                                                                                                                                                                        | 项目讲解                                                                                                                                    | 目标                          | 知识点                                                                               | 难度   |
|:--------------------------------------------------------------------------------------------------------------|:----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|:----------------------------------------------------------------------------------------------------------------------------------------|:----------------------------|:----------------------------------------------------------------------------------|:-----|
| [问政平台](https://wz.sun0769.com/political/index/supervise)                                                      | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/IP%E4%BB%A3%E7%90%86)                                                                                                                     | [📖](https://blog.csdn.net/2401_87328929/article/details/148193001)                                                                     | 学习如何使用 IP 代理绕过网站封禁机制        | [IP代理](https://blog.csdn.net/2401_87328929/article/details/148189096)             | ⭐    |
| [爬小说](https://yuenvjian.5000yan.com/)                                                                         | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/xpath%E8%BF%9B%E9%98%B6)                                                                                                                  | [📖](https://blog.csdn.net/2401_87328929/article/details/148098889)                                                                     | 掌握 XPath 进阶用法，提取复杂结构的小说内容   | [xpath进阶](https://blog.csdn.net/2401_87328929/article/details/148069059)          | ⭐    |
| [站长素材](https://sc.chinaz.com/tupian/index.html)/[微博图片](https://blog.sina.com.cn/s/blog_01ebcb8a0102zj25.html) | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/%E5%9B%BE%E7%89%87%E6%87%92%E5%8A%A0%E8%BD%BD)                                                                                            | [📖](https://blog.csdn.net/2401_87328929/article/details/148123963)/[📗](https://blog.csdn.net/2401_87328929/article/details/148170374) | 学习处理图片懒加载机制，正确抓取图片资源        | 图片懒加载                                                                             | ⭐⭐   |
| [下厨房(text)](https://www.xiachufang.com/search/)/[智慧职教(json)](https://www.icve.com.cn/index)                   | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/%E7%88%AC%E5%8F%96text%26json%E5%9E%8B%E6%95%B0%E6%8D%AE)                                                                                 | [📖](https://blog.csdn.net/2401_87328929/article/details/148074149)/[📗](https://blog.csdn.net/2401_87328929/article/details/148046380) | 学习提取网页中的文本数据，如菜谱、描述等        | 文本数据提取                                                                            | ⭐    |
| [小红书](https://www.xiaohongshu.com/explore)                                                                    | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/%E8%87%AA%E5%8A%A8%E5%8C%96%E7%88%AC%E8%99%AB/%E5%B0%8F%E7%BA%A2%E4%B9%A6%E7%AC%94%E8%AE%B0%E8%87%AA%E5%8A%A8%E5%8C%96%E9%87%87%E9%9B%86) | [📖](https://blog.csdn.net/2401_87328929/article/details/149253153)                                                                     | 实现模拟登录与数据抓取的自动化流程           | [自动化爬取](https://blog.csdn.net/2401_87328929/article/details/149252038)            | ⭐⭐⭐  |
| [豆瓣top250电影](https://movie.douban.com/top250?start=)                                                          | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/%E5%BC%82%E6%AD%A5%E7%88%AC%E8%99%AB)                                                                                                     | [📖](https://blog.csdn.net/2401_87328929/article/details/149298713)                                                                     | 掌握多进程、多线程、协程的异步爬取技术         | [异步爬取(多进程/多线程/协程)](https://blog.csdn.net/2401_87328929/article/details/149289576) | ⭐⭐⭐  |
| [B站](https://search.bilibili.com/all?keyword=%E7%BC%96%E7%A8%8B)                                              | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/scrapy%E6%A1%86%E6%9E%B6)                                                                                                                 | [📖](https://blog.csdn.net/2401_87328929/article/details/149533074)                                                                     | 学习使用 Scrapy 框架构建高效、可扩展的爬虫项目 | scrapy框架                                                                          | ⭐⭐⭐⭐ |
| [阿里法拍](https://sf.taobao.com/item_list.htm)                                                                   | [🏠](https://github.com/Annyfee/spider-defense-bypass/tree/main/%E8%87%AA%E5%8A%A8%E5%8C%96%E7%88%AC%E8%99%AB/cookie%E6%B1%A0%E6%9B%B4%E6%96%B0)                                                                                                                 | [📖](https://blog.csdn.net/2401_87328929/article/details/150268240)                                                                     | 学习半自动化爬虫思路与cookie池更新的实战运用   | cookie/半自动化                                                                       | ⭐⭐⭐ |

--- 

## ✨ 适合人群

- 希望系统学习爬虫各种技术知识的爬虫工程师。
- 对验证码、滑块等特定反爬绕过方法感兴趣的技术人员。
- 想要构建更稳定、更健全爬虫系统的工程师。

---

## 📌 相关项目推荐

- [js-spider-reverse](https://github.com/Annyfee/js-spider-reverse)：专注于 JavaScript 逆向分析与反调试。

## 🧭 更新计划

本仓库计划持续更新常见反爬技术的分析和绕过方法。

## ⭐️ 支持与反馈

如果你觉得这个项目对你有帮助，欢迎 Star、Fork、分享给更多人！

若有任何建议或问题，或有想一起研究的反爬技术，欢迎通过 [Issue](https://github.com/Annyfee/spider-defense-bypass/issues)
提出交流！

## ❗ 免责声明

⚠️ 本项目仅用于技术研究与学习，爬取目标均为公开页面内容，未涉及用户隐私及登录数据。

⚠️ 所有代码请勿用于商业用途，亦不得用于违反目标网站条款的行为。

⚠️ 如目标站方认为涉及侵权，请联系我进行删除与下架处理。
