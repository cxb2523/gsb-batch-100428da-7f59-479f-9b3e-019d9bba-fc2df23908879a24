import json
import os
import random
import requests
from bs4 import BeautifulSoup

from config import HOME_DIR
from utils.user_agent_utils.user_agent import UserAgent

ua = UserAgent()

PROXY_LIST_FILE = os.path.join(HOME_DIR, "utils/proxy_utils/proxy_list.json")


class Proxy:
    def __init__(self):
        self.proxy_list = self.read_proxy_list()
        if not self.proxy_list:
            self.proxy_list = self.update_proxy_list()
            self.write_proxy_list()
        self.count = 0
        self.proxy = None

    @staticmethod
    def read_proxy_list():
        if os.path.exists(PROXY_LIST_FILE):
            with open(PROXY_LIST_FILE, "r") as fp:
                return json.load(fp)
        return None

    @staticmethod
    def update_proxy_list():
        # Retrieve latest proxies
        url = 'https://www.sslproxies.org/'
        try:
            response = requests.get(url, headers={'User-Agent': ua.user_agent()})
        except:
            raise Exception("Could not scrape proxies from {}!".format(url))

        soup = BeautifulSoup(response.text, 'html.parser')

        proxies_table = soup.find(id='proxylisttable')
        proxy_list = []
        # Save proxies in a file array
        for row in proxies_table.tbody.find_all('tr'):
            proxy_list.append(row.find_all('td')[0].string + ':' + row.find_all('td')[1].string)
        return proxy_list

    def write_proxy_list(self):
        with open(PROXY_LIST_FILE, "w") as fp:
            json.dump(self.proxy_list, fp)

    def generate_proxy(self, force_refresh=False):
        """Choose a random proxy; keeps the same proxy for some number of times then changes it.

        Returns None when no proxy is available instead of raising.
        """
        if not self.proxy_list:
            return None
        if force_refresh or self.proxy is None or self.count % 10 == 0:
            # prefer a proxy different from the current one when possible
            choices = [p for p in self.proxy_list if p != self.proxy] or self.proxy_list
            self.proxy = random.choice(choices)
        self.count += 1
        return {
            "http": self.proxy,
            "https": self.proxy
        }


if __name__ == '__main__':
    pro = Proxy()
    print(pro.generate_proxy())
