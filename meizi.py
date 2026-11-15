import requests
import re
import os
import time
import json
import datetime
from tqdm import tqdm
from multiprocessing import Pool


# 每个图集目录下的页级进度记录文件名
PROGRESS_FILE = 'progress.json'

# 失败原因分类
REASON_PAGE = '图集分页请求异常'
REASON_PARSE = '页面解析不到图片地址'
REASON_REQUEST = '图片下载请求异常'
REASON_SAVE = '图片保存失败'
REASON_MISSING = '下载任务未返回结果'


def formatError(exc):
    """把异常转成便于写入记录的简短文本。"""
    return type(exc).__name__ + ': ' + str(exc)


def parseImageUrl(html):
    """从图集分页中解析图片地址，解析不到时返回 None。"""
    urls = re.compile('<.*?="blur" src="(.*?)"').findall(html)
    if not urls:
        return None
    return urls[0]


def downloadImage(task):
    """
    下载单张图片。

    该函数运行在进程池中，所有异常都会转换成失败结果返回，
    保证单张图片失败不会中断同组其他图片，也便于主进程汇总每张图的结果。

    task 字段：index、page_url、album_dir、headers、request_kwargs
    返回字段：index、status(success/failed)、reason、page_url、image_url、file、error
    """
    index = task['index']
    pageUrl = task['page_url']
    albumDir = task['album_dir']
    headers = task.get('headers', {})
    requestKwargs = task.get('request_kwargs', {})

    result = {
        'index': index,
        'status': 'failed',
        'page_url': pageUrl,
    }

    # 1. 请求图集分页
    try:
        response = requests.Session().get(url=pageUrl, headers=headers, **requestKwargs)
        response.raise_for_status()
        html = response.text
    except Exception as exc:
        # 分页本身就取不到，单独归类，便于区分“网络/服务端问题”与“页面结构变化”
        result['reason'] = REASON_PAGE
        result['error'] = formatError(exc)
        return result

    # 2. 解析图片地址
    imgUrl = parseImageUrl(html)
    if not imgUrl:
        result['reason'] = REASON_PARSE
        result['error'] = '页面中未匹配到图片地址'
        return result
    result['image_url'] = imgUrl

    # 3. 请求图片
    try:
        img = requests.Session().get(url=imgUrl, headers=headers, **requestKwargs)
        img.raise_for_status()
        content = img.content
    except Exception as exc:
        result['reason'] = REASON_REQUEST
        result['error'] = formatError(exc)
        return result

    if not content:
        # 空响应不能当作成功，否则残缺图集会被记录成已完成
        result['reason'] = REASON_REQUEST
        result['error'] = '响应内容为空'
        return result

    # 4. 保存图片（文件名规则与旧版本一致：序号 + 图片地址后 4 位）
    fileName = str(index) + imgUrl[-4:]
    filePath = os.path.join(albumDir, fileName)
    try:
        with open(filePath, 'wb') as f:
            f.write(content)
    except Exception as exc:
        result['reason'] = REASON_SAVE
        result['error'] = formatError(exc)
        result['file'] = fileName
        return result

    result['status'] = 'success'
    result['file'] = fileName
    return result


class Mzitu:


    # Http Headers
    headers = {
        'user-agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36",
        'Connection': 'Keep-Alive',
        'Referer': "http://www.mzitu.com/"
    }


    # urls
    urls = [
        # 性感妹子
        "https://www.mzitu.com/xinggan/",
        # 日本妹子
        "https://www.mzitu.com/japan/",
        # 台湾妹子
        "https://www.mzitu.com/taiwan/",
        # 清纯妹子
        "https://www.mzitu.com/mm/",
        # 全部妹子
        "https://www.mzitu.com/",
    ]

    dirs = [
            # 性感妹子
            "xingan/",
            # 日本
            "japan/",
            # 台湾
            "taiwan/",
            # 清纯
            "mm/",
            # 全部
            "all/"
            ]

    path = 'data/'

    # 单组下载的进程池大小；设置为 1 时退化为串行，便于调试
    pool_size = 5
    # 提交任务之间的间隔，避免请求过于集中
    submit_interval = 0.4
    # 单次请求超时时间（秒）；避免个别请求挂起拖住整组，超时按请求异常记录
    timeout = 30
    # 当前页码目录前缀，main 中会按列表页更新
    pageNum = '1/'


    def __init__(self):

        os.system("clear")

        output = \
        """
         __  __ ________ _____ _   _
        |  \/  |__  /_ _|_   _| | | |
        | |\/| | / / | |  | | | | | |
        | |  | |/ /_ | |  | | | |_| |
        |_|  |_/____|___| |_|  \___/

        Author: Muxi_k
        Email: lqjxm666@163.com
        -----------------------------
        """
        selectList = \
        """
        妹子列表
            + 0: 性感妹子
            + 1: 日本妹子
            + 2: 台湾妹子
            + 3: 清纯妹子
            + 4: 全部
            + Q: 退出
        """
        if not os.path.exists('data'):
            os.mkdir('data')

        self.color();
        print(self.green, output, selectList)

        try:
            select = input("请输入:")
            os.system("clear")
            if select.lower() == 'q':exit("Info: 退出成功！")
            if int(select) not in [0,1,2,3,4]:exit(self.red + "Error: 输入错误！")
            self.url = self.urls[int(select)]
            self.dir = self.dirs[int(select)]
        except (TypeError, ValueError):
            exit(self.red + "Error: 输入错误！")
        except EOFError:
            exit(self.red + "Error: 未检测到输入！")

    #控制台颜色
    def color(self):
        self.green = "\033[0;33m"
        self.red   = "\033[0;31m"
        self.blue  = "\033[0;34m"



    # 获取首页所有链接的url
    def getIndexInfo(self, url):
        response = requests.Session().get(url=url, headers=self.headers)
        urlList = re.compile("https:\/\/www.mzitu.com\/\d+").findall(response.text)
        # 去重
        new = []
        for id in urlList:
            if id not in new:new.append(id)

        pageNum = re.compile("helli.*?<\/span>\\n.*?(\d+)<").findall(response.text)
        return [int(pageNum[0]), new]

    # 获取图集信息返回标题和分页数量
    def getImagesInfo(self, url):
        response = requests.Session().get(url=url, headers=self.headers)
        title = re.compile("<title>(.*?)<").findall(response.text)[0]
        title = title.replace('-','_')
        title = title.replace(' ','')
        title = title.replace('_妹子图','')
        title = title.replace('，','_')
        title = title.replace('！','_')

        self.title = title

        page = 10
        try:
            page = re.compile('….*?<span>(\d+).*?下一').findall(response.text)[0]
        except IndexError:
            pass
        return [title, int(page)]

    # 获取图片链接并保存（保留旧调用方式，返回单张图片的结果）
    def getImage(self, url, index):
        task = self.buildTask(url, index, self.albumDir())
        return downloadImage(task)

    # 创建目录
    def createDir(self, name, index):

        self.index = str(index)

        path = self.buildAlbumPath(name, index)

        isExists = os.path.exists(path)
        if not isExists:
            os.makedirs(path)
            print(self.red + "Info: 未下载，正在爬取！")
            return True
        else:
            print(self.green + "Info: 已存在，正在检查进度记录！")
            return False

    # 图集目录（沿用旧规则：data/分类/页码/序号_标题）
    def buildAlbumPath(self, name, index):
        name = name.strip()
        name = name.rstrip('\\')
        return self.path + self.dir + self.pageNum + str(index) + "_" + name

    # 当前图集在列表页中的序号（记录里用于人工核对）
    def albumIndex(self):
        try:
            return int(self.index)
        except (TypeError, ValueError, AttributeError):
            return self.index

    def albumDir(self):
        return self.buildAlbumPath(self.title, self.index)

    # 进度记录路径
    def progressPath(self, albumDir):
        return os.path.join(albumDir, PROGRESS_FILE)

    # 读取进度记录，缺失或损坏时返回 None
    def loadProgress(self, albumDir):
        path = self.progressPath(albumDir)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as exc:
            print(self.red + "Error: 进度记录读取失败，将按目录内已有文件推断 - " + formatError(exc))
            return None
        if not isinstance(data, dict) or not isinstance(data.get('images'), list):
            print(self.red + "Error: 进度记录格式异常，将按目录内已有文件推断")
            return None
        return data

    # 把记录中的图片条目转成 index -> entry 的映射
    def recordEntries(self, record):
        entries = {}
        for item in record.get('images', []):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get('index'))
            except (TypeError, ValueError):
                continue
            if index < 1:
                continue
            entry = dict(item)
            entry['index'] = index
            if entry.get('status') not in ('success', 'failed'):
                entry['status'] = 'failed'
            entries[index] = entry
        return entries

    # 目录已存在但记录缺失时，按目录内已有文件推断已完成的图片
    def inferEntriesFromDisk(self, albumDir):
        entries = {}
        if not os.path.isdir(albumDir):
            return entries
        for name in sorted(os.listdir(albumDir)):
            if name == PROGRESS_FILE or name.startswith(PROGRESS_FILE + '.'):
                continue
            filePath = os.path.join(albumDir, name)
            if not os.path.isfile(filePath):
                continue
            if not self.isUsableFile(filePath):
                # 空文件说明上次写入被打断，不能当作已完成
                continue
            # 文件名规则为“序号 + 图片地址后 4 位”，按开头序号归属到具体页
            match = re.match(r'^(\d+)', name)
            if not match:
                continue
            index = int(match.group(1))
            if index < 1:
                continue
            entries[index] = {
                'index': index,
                'status': 'success',
                'file': name,
                'source': 'disk',
            }
        return entries

    # 判断文件是否是完整可用的图片（存在且非空）
    def isUsableFile(self, filePath):
        try:
            return os.path.isfile(filePath) and os.path.getsize(filePath) > 0
        except OSError:
            return False

    # 判断记录中标记成功的图片文件是否还在
    def hasImageFile(self, albumDir, entry, index, diskEntries):
        fileName = entry.get('file')
        if fileName and self.isUsableFile(os.path.join(albumDir, fileName)):
            return True
        # 兼容旧记录缺少 file 字段的情况
        return index in diskEntries

    # 写入页级进度记录（先写临时文件再替换，避免写一半损坏）
    def writeProgress(self, albumDir, record):
        path = self.progressPath(albumDir)
        tmpPath = path + '.tmp'
        with open(tmpPath, 'w', encoding='utf-8') as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(tmpPath, path)

    # 汇总并保存进度记录，返回记录内容
    def saveProgress(self, albumDir, albumUrl, title, total, entries):
        images = []
        success = 0
        failed = 0
        for index in range(1, total + 1):
            entry = entries.get(index)
            if not isinstance(entry, dict):
                entry = {'index': index, 'status': 'failed', 'reason': REASON_MISSING}
            else:
                entry = dict(entry)
            entry['index'] = index
            if entry.get('status') == 'success':
                success += 1
            else:
                entry['status'] = 'failed'
                if not entry.get('reason'):
                    entry['reason'] = REASON_MISSING
                failed += 1
            images.append(entry)

        record = {
            'version': 1,
            'album_url': albumUrl,
            'title': title,
            'page': self.pageNum.strip('/'),
            'index': self.albumIndex(),
            'total': total,
            'success': success,
            'failed': failed,
            'complete': failed == 0,
            'updated_at': datetime.datetime.now().isoformat(timespec='seconds'),
            'images': images,
        }
        try:
            self.writeProgress(albumDir, record)
        except Exception as exc:
            print(self.red + "Error: 进度记录写入失败 - " + formatError(exc))
        return record

    # 构造单张图片的下载任务
    def buildTask(self, pageUrl, index, albumDir):
        requestKwargs = {}
        if self.timeout is not None:
            requestKwargs['timeout'] = self.timeout
        return {
            'index': index,
            'page_url': pageUrl,
            'album_dir': albumDir,
            'headers': dict(self.headers),
            'request_kwargs': requestKwargs,
        }

    # 串行执行单张图片任务
    def runTaskSerial(self, task):
        try:
            result = downloadImage(task)
        except Exception as exc:
            result = {
                'index': task['index'],
                'status': 'failed',
                'reason': REASON_REQUEST,
                'page_url': task['page_url'],
                'error': formatError(exc),
            }
        if not isinstance(result, dict):
            result = {
                'index': task['index'],
                'status': 'failed',
                'reason': REASON_MISSING,
                'page_url': task['page_url'],
                'error': '下载任务未返回结果',
            }
        return result

    # 执行一组图片的下载任务：等待全部结束，并收集每张图片的结果
    # onResult 会在每张图片出结果时被调用，便于及时把进度写入记录
    def runTasks(self, tasks, onResult=None):
        if not tasks:
            return []

        def emit(result, task):
            if onResult is not None:
                try:
                    onResult(task, result)
                except Exception as exc:
                    print(self.red + "Error: 进度记录更新失败 - " + formatError(exc))

        if self.pool_size <= 1:
            results = []
            for task in tqdm(tasks):
                result = self.runTaskSerial(task)
                results.append(result)
                emit(result, task)
            return results

        pool = Pool(self.pool_size)
        asyncResults = []
        results = []
        interrupted = False
        try:
            for task in tasks:
                asyncResults.append(pool.apply_async(func=downloadImage, args=(task,)))
                if self.submit_interval:
                    time.sleep(self.submit_interval)
            pool.close()  # 关闭进程池，不再接受新任务，已提交的任务继续执行

            for asyncResult, task in tqdm(list(zip(asyncResults, tasks)), total=len(asyncResults)):
                try:
                    result = asyncResult.get()  # 逐个等待并取回结果
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    # 进程异常也不能丢掉这一张，按失败记录后继续收集其余结果
                    result = {
                        'index': task['index'],
                        'status': 'failed',
                        'reason': REASON_REQUEST,
                        'page_url': task['page_url'],
                        'error': formatError(exc),
                    }
                results.append(result)
                emit(result, task)
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            # 正常结束时 wait/join 确保本组全部任务已收尾；
            # 用户中断时直接终止，避免等待长时间挂起的请求
            try:
                if interrupted:
                    pool.terminate()
                else:
                    pool.close()
                pool.join()
            except Exception:
                pool.terminate()
        return results

    # 下载一个图集，失败的单张图片保留在记录中，返回进度记录
    def downloadAlbum(self, albumUrl, title, total, index):
        # 目录名与文件名规则依赖 self.title / self.index，这里显式同步
        self.title = title
        self.index = str(index)
        albumDir = self.buildAlbumPath(title, index)
        # 目录名沿用旧规则；续爬或异常路径下目录可能尚未创建
        os.makedirs(albumDir, exist_ok=True)

        record = self.loadProgress(albumDir)
        diskEntries = self.inferEntriesFromDisk(albumDir)

        if record is None:
            entries = dict(diskEntries)
            if diskEntries:
                print(self.green + "Info: 未找到进度记录，按目录内已有文件推断已完成 "
                      + str(len(diskEntries)) + " 张")
            else:
                print(self.red + "Info: 未找到进度记录，目录内没有已下载图片")
        else:
            entries = self.recordEntries(record)
            if not entries and diskEntries:
                entries = dict(diskEntries)
                print(self.green + "Info: 进度记录无有效条目，按目录内已有文件推断已完成 "
                      + str(len(diskEntries)) + " 张")

        # 记录中缺少条目、但目录里已经有同名图片的，按已完成处理
        for diskIndex, diskEntry in diskEntries.items():
            if diskIndex not in entries:
                entries[diskIndex] = dict(diskEntry)
            elif entries[diskIndex].get('status') == 'success' and not entries[diskIndex].get('file'):
                entries[diskIndex]['file'] = diskEntry['file']

        # 只补下失败或缺失的图片，已成功的图片不再请求
        pending = []
        for imageIndex in range(1, total + 1):
            entry = entries.get(imageIndex)
            if entry and entry.get('status') == 'success' \
                    and self.hasImageFile(albumDir, entry, imageIndex, diskEntries):
                continue
            pending.append(imageIndex)

        print("Info: 图集共 " + str(total) + " 张，已完成 " + str(total - len(pending))
              + " 张，本次待下载 " + str(len(pending)) + " 张")

        if pending:
            tasks = [self.buildTask(albumUrl + "/" + str(k), k, albumDir) for k in pending]
            # 续爬时在原有尝试次数上累加，便于人工识别反复失败的页
            previousAttempts = {}
            for k in pending:
                entry = entries.get(k)
                try:
                    previousAttempts[k] = int(entry.get('attempts', 0)) if isinstance(entry, dict) else 0
                except (TypeError, ValueError):
                    previousAttempts[k] = 0

            def mergeResult(result):
                if not isinstance(result, dict):
                    return
                k = result.get('index')
                result['attempts'] = previousAttempts.get(k, 0) + 1
                entries[k] = result
                self.saveProgress(albumDir, albumUrl, title, total, entries)

            def onResult(task, result):
                # 单张出结果就落盘一次，进程中途退出也能保留已完成的页级结局
                if isinstance(result, dict):
                    result.setdefault('index', task['index'])
                mergeResult(result)

            results = self.runTasks(tasks, onResult=onResult)
            resultMap = {}
            for result in results:
                if isinstance(result, dict) and 'index' in result:
                    resultMap[result['index']] = result
            for k in pending:
                if k in resultMap:
                    if entries.get(k) is not resultMap[k]:
                        mergeResult(resultMap[k])
                else:
                    entries[k] = {
                        'index': k,
                        'status': 'failed',
                        'reason': REASON_MISSING,
                        'page_url': albumUrl + "/" + str(k),
                        'error': '下载任务未返回结果',
                        'attempts': previousAttempts.get(k, 0) + 1,
                    }

        return self.saveProgress(albumDir, albumUrl, title, total, entries)

    # 输出每组的失败明细和汇总
    def printGroupSummary(self, title, record):
        for item in record.get('images', []):
            if item.get('status') == 'success':
                continue
            line = "Info: 失败图片 " + str(item.get('index')) + "/" + str(record.get('total')) \
                   + " 原因:" + str(item.get('reason'))
            url = item.get('image_url') or item.get('page_url')
            if url:
                line += " URL:" + str(url)
            if item.get('error'):
                line += " 错误:" + str(item.get('error'))
            print(self.red + line)

        completeText = '是' if record.get('complete') else '否'
        color = self.green if record.get('complete') else self.red
        print(color + "Info: 本组结束 - " + title
              + " 成功数:" + str(record.get('success'))
              + " 失败数:" + str(record.get('failed'))
              + " 是否完成:" + completeText)

    # 询问是否继续下一组；非交互环境（stdin 已关闭）下自动继续
    def askContinue(self):
        while True:
            try:
                res = input(self.red + "Info: 以及爬取一组是否继续Y\\n: ")
            except (EOFError, OSError, ValueError):
                print("\nInfo: 未检测到输入，自动继续")
                return True
            if res.lower() == 'y' or res.lower() == '':
                return True
            elif res.lower() == 'n':
                return False

    # Main
    def main(self):

        imgType = self.url

        # 首页信息
        try:
            indexInfo = self.getIndexInfo(imgType)
        except Exception as exc:
            print(self.red + "Error: 获取首页信息失败 - " + formatError(exc))
            return
        indexPage = indexInfo[0]


        for i in range(1, indexPage+1):

            # 获取图集链接
            try:
                indexUrls = self.getIndexInfo(url=imgType + "page/" + str(i) +"/")
            except Exception as exc:
                print(self.red + "Error: 获取第" + str(i) + "页图集列表失败 - " + formatError(exc))
                continue

            print("Info: 正在爬取第" + str(i))
            for j in range(1, len(indexUrls[1])):

                self.pageNum = str(i) + "/"
                albumUrl = indexUrls[1][j]

                try:
                    info = self.getImagesInfo(url=albumUrl)
                    title = info[0]
                    total = int(info[1])
                except Exception as exc:
                    print(self.red + "Error: 获取图集信息失败 " + albumUrl + " - " + formatError(exc))
                    continue
                print("Info: 正在爬取 - " + self.title + " 图片总数:" + str(total))

                try:
                    self.createDir(title, j)
                    record = self.downloadAlbum(albumUrl, title, total, j)
                except Exception as exc:
                    print(self.red + "Error: 图集下载失败 " + albumUrl + " - " + formatError(exc))
                    continue
                self.printGroupSummary(title, record)

                time.sleep(1)

                if not self.askContinue():
                    return


if __name__ == '__main__':
    try:
        mezi = Mzitu()
        mezi.main()
    except KeyboardInterrupt:
        print("\n退出成功")
