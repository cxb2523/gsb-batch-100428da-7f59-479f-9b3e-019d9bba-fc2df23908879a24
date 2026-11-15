from selenium import webdriver
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from exceptions import *
from imagehelper import *
from time import sleep
from sys import exit
import asyncio
import os
import pickle

class Pinterest():
    def __init__(self, login, pw):
        self.domains = [".pinterest.com", ".www.pinterest.com", "www.pinterest.com", ".www.pinterest.co.kr", "www.pinterest.co.kr"]
        self.piclist = []
        self.currentdir = os.getcwd()
        self.user_agent = ""
        options = webdriver.ChromeOptions()
        # options.add_argument('headless')
        options.add_argument('window-size=1920x1080')
        options.add_argument("disable-gpu")
        options.add_argument("--log-level=3")
        self.driver = webdriver.Chrome(options=options)
        self.user_agent = self.driver.execute_script("return navigator.userAgent;")
        if os.path.exists("cookies.pkl"):
            print("Loading cookies...")
            self.driver.get("https://pinterest.com")
            self.driver.implicitly_wait(3)
            sleep(2)
            cookies = pickle.load(open("cookies.pkl", "rb"))
            for cookie in cookies:
                # print(cookie)
                for domain in self.domains:
                    try:
                        self.driver.add_cookie({
                            "domain": domain,
                            "name": cookie["name"],
                            "value": cookie["value"],
                            "path": '/'
                            # "expiry": None
                        })
                    except:
                        pass
            self.driver.get("https://pinterest.com")
            self.driver.implicitly_wait(3)
            try:
                self.driver.find_element(By.XPATH, '//*[@id="HeaderContent"]')
                return
            except:
                print("Failed to login from cookies.. login manually")
                # input()
                # pass
        try:
            self.driver.get("https://pinterest.com/login")
            self.driver.implicitly_wait(3)
            for i in range(3):
                try:
                    self.driver.find_element(By.ID, "email")
                    break
                except:
                    sleep(1)
            emailelem = self.driver.find_element(By.ID, "email")
            passelem = self.driver.find_element(By.ID, "password")
            emailelem.send_keys(login)
            passelem.send_keys(pw)
            sleep(1)
            self.driver.find_element(By.XPATH, "//button[@type='submit']").click()
        except Exception as e:
            raise e
        
        while True:
            try:
                self.driver.find_element(By.XPATH, '//*[@id="HeaderContent"]')
                break
            except:
                sleep(1)
                try:
                    self.driver.find_element(By.XPATH, "//button[@type='submit']").click()
                except:
                    pass
        self.dump()
        return

    def dump(self):
        cookies = self.driver.get_cookies()
        pickle.dump(cookies, open("cookies.pkl","wb"))

    def crawl(self, dir):
        timeout = 0
        height = self.driver.execute_script("return document.body.scrollHeight")
        while True:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            sleep(2)
            now_height = self.driver.execute_script("return document.body.scrollHeight")

            # If height changed
            if now_height != height:
                self.download_image(dir)
                break
            else:
                timeout += 1
                if timeout >= 5:
                    print("It seems we find the end of current page, stop crawling.")
                    raise EndPageException
        sleep(2)
        return
    
    def single_download(self, n=-1, url="https://pinterest.com/", dir="./download"):
        global loop
        fail_image = 0
        if n == -1:
            n = 999999999
        loop = asyncio.get_event_loop()

        # Create directory
        if dir[0] == "/":
            dir = dir[1:]
        directory = os.path.join(self.currentdir, dir)
        if not os.path.exists(directory):
            os.mkdir(directory)
        
        self.driver.get(url)
        self.driver.implicitly_wait(3)
        for i in range(n):
            try:
                self.crawl(dir)
            except EndPageException as e:
                break
            download_pic_count = len(self.piclist)
            print(f"Scroll down {i} Page, downloaded {download_pic_count} images.")
        download_pic_count = len(self.piclist)
        print(f"Totally downloaded {download_pic_count} images.")
        loop.close
        return fail_image
    
    def batch_download(self, n=-1, url_list=[], dir_list=[], dir="./download"):
        global loop
        loop = asyncio.get_event_loop()
        url_count = len(url_list)
        dir_count = len(dir_list)
        if n == -1:
            n = 9999999
        if dir_list != [] and url_count != dir_count:
            print("url_list and dir_list must have same length.")
            exit()
        if url_count == 0:
            print("No link to crawl.")
            return []

        # Each link is bound to its own directory, tab handle, status and
        # counters. The input lists are never mutated, so closing a tab or
        # finishing one item can never shift the remaining items.
        tasks = []
        for uindex in range(url_count):
            target_dir = dir_list[uindex] if dir_count != 0 else dir
            if target_dir[0] == "/":
                target_dir = target_dir[1:]
            target_dir = os.path.join(self.currentdir, target_dir)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)
            tasks.append({
                "url": url_list[uindex],
                "dir": target_dir,
                "handle": None,
                "status": "active",   # active | ended | failed | completed
                "downloaded": 0,
                "reason": "",
            })

        # Open one tab per link and remember the actual window handle, so a
        # later reordering of window_handles cannot bind a link to another tab.
        for i in range(url_count - 1):
            self.driver.execute_script("window.open('','_blank');")

        handles = self.driver.window_handles
        for uindex, task in enumerate(tasks):
            task["handle"] = handles[uindex]
            self.driver.switch_to.window(handles[uindex])
            self.driver.get(task["url"])
            self.driver.implicitly_wait(3)

        for i in range(n):
            active_tasks = [task for task in tasks if task["status"] == "active"]
            if not active_tasks:
                break

            for task in active_tasks:
                # Snapshot how many images were already known before this
                # scroll, so this item's count stays independent even though
                # piclist itself is shared with single mode.
                before_count = len(self.piclist)
                try:
                    self.driver.switch_to.window(task["handle"])
                except Exception as e:
                    task["status"] = "failed"
                    task["reason"] = f"switch to tab failed: {e}"
                    print(f"Failed to switch tab for {task['url']}: {task['reason']}")
                    continue

                try:
                    self.crawl(task["dir"])
                except EndPageException:
                    # Normal end of this link: close only its own tab and keep
                    # processing every remaining link.
                    task["status"] = "ended"
                    task["downloaded"] += len(self.piclist) - before_count
                    try:
                        self.driver.switch_to.window(task["handle"])
                        self.driver.close()
                    except Exception as e:
                        # The tab/window is already gone; the item itself is
                        # still a normal end, just record the cleanup issue.
                        print(f"Note: could not close tab for {task['url']}: {e}")
                    print(f"Reached the end of {task['url']}, downloaded {task['downloaded']} images, tab closed.")
                    continue
                except Exception as e:
                    # Page read / crawl failure: keep the item and its record,
                    # do not interrupt the rest of the batch.
                    task["status"] = "failed"
                    task["reason"] = f"crawl failed: {e}"
                    task["downloaded"] += len(self.piclist) - before_count
                    print(f"Failed while crawling {task['url']}: {task['reason']}")
                    continue

                task["downloaded"] += len(self.piclist) - before_count
                print(f"Scroll down from {task['url']}, {i} Page, downloaded {task['downloaded']} images.")

        for task in tasks:
            if task["status"] == "active":
                task["status"] = "completed"

        return self._print_batch_results(tasks)

    def _print_batch_results(self, tasks):
        print("===== Batch download result =====")
        for task in tasks:
            if task["status"] == "failed":
                print(
                    f"[FAILED] {task['url']} -> {task['dir']} | "
                    f"downloaded {task['downloaded']} images | reason: {task['reason']}"
                )
            else:
                print(
                    f"[{task['status'].upper()}] {task['url']} -> {task['dir']} | "
                    f"downloaded {task['downloaded']} images"
                )
        print("=================================")
        return tasks


    def download_image(self, dir="./download"):
        page_pic_list=[]
        req = self.driver.page_source
        soup = BeautifulSoup(req, 'html.parser')
        pics = soup.find_all("img")
        if pics is None:
            return 0
        for pic in pics:
            src = pic.get("src")
            
            # Profile image, skip
            if "75x75_RS" in src:
                continue

            if src not in self.piclist:
                self.piclist.append(src)
                page_pic_list.append(src)
        
        fail_image = sum(loop.run_until_complete(download_image_host(page_pic_list,dir)))
        return fail_image

    
    def getdriver(self):
        return self.driver