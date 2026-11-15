# Mzitu 

[![github](https://img.shields.io/badge/run-passing-brightgreen.svg)](https://github.com/snowdreams1006)


使用python制作的用于爬取妹子图官网的爬虫程序

- 环境要求
    - requests (pip)
    - python3
    - tqdm
    
-  使用

    ```sh
    $ git clone https://github.com/muxik/Mzitu && cd Mzitu
    ```

    ```sh
    $ python3 meizi.py
    ```

### 下载进度与续爬

每个图集目录（目录名仍为 `data/分类/页码/序号_标题/`）下会生成 `progress.json`，
逐张记录该图集的下载结局：

```json
{
  "total": 8,
  "success": 7,
  "failed": 1,
  "complete": false,
  "images": [
    {"index": 1, "status": "success", "file": "1.jpg", "attempts": 1},
    {"index": 2, "status": "failed", "reason": "页面解析不到图片地址",
     "page_url": "https://www.mzitu.com/xxx/2"}
  ]
}
```

- 单张图片失败只记录在案，不会中断整组；其余图片继续下载。
- 失败原因区分「图集分页请求异常」「页面解析不到图片地址」「图片下载请求异常」
  等类别，记录中附上对应 `page_url` / `image_url` 便于排查。
- 全部图片成功时 `complete` 为 `true`；否则再次运行时会读取记录，
  只补下失败或缺失的图片，已成功的图片不会重复请求。
- 图集目录已存在但记录缺失（例如旧版本下载的目录）时，会按目录内已有图片
  推断已完成项，避免整组重下。
- 某一页反复失败时，记录中的失败条目和 `attempts` 次数会保留下来，
  继续处理其余图片。
- 每组结束时控制台输出成功数、失败数与是否完成。

### 公告

- **2020/10/15  版本1.2.1**
    - 恢复维护
    - 使用异步
    - 优化的bug
    - 使用多线程
    
