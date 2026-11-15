import time
import json
import requests
import pymysql
import traceback
import os
from dotenv import load_dotenv

import hotsearch

load_dotenv()

def get_tencent_data():
    url = "https://view.inews.qq.com/g2/getOnsInfo?name=disease_other"
    url1 = "https://view.inews.qq.com/g2/getOnsInfo?name=disease_h5"
    
    headers = {
        "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        }
    r = requests.get(url, headers)
    r1 = requests.get(url1, headers)

    res = json.loads(r.text)
    res1 = json.loads(r1.text)

    data_all = json.loads(res["data"])
    data_all1 = json.loads(res1["data"])
    
    history = {}
    for i in data_all["chinaDayList"]:
        ds = "2020." + i["date"]
        tup = time.strptime(ds, "%Y.%m.%d")  # MATCH THE FORMAT
        ds = time.strftime("%Y-%m-%d", tup)  # CHANGE THE FORMAT
        confirm = i["confirm"]
        suspect = i["suspect"]
        heal = i["heal"]
        dead = i["dead"]
        history[ds] = {"confirm": confirm, "suspect": suspect, "heal": heal, "dead": dead}
    for i in data_all["chinaDayAddList"]:
        ds = "2020." + i["date"]
        tup = time.strptime(ds, "%Y.%m.%d")  
        ds = time.strftime("%Y-%m-%d", tup)  
        confirm = i["confirm"]
        suspect = i["suspect"]
        heal = i["heal"]
        dead = i["dead"]
        history[ds].update({"confirm_add": confirm, "suspect_add": suspect, "heal_add": heal, "dead_add": dead})

    details = []
    update_time = data_all1["lastUpdateTime"]
    data_country = data_all1["areaTree"]
    data_province = data_country[0]["children"]
    for pro_infos in data_province:
        province = pro_infos["name"]
        for city_infos in pro_infos["children"]:
            city = city_infos["name"]
            confirm = city_infos["total"]["confirm"]
            confirm_add = city_infos["today"]["confirm"]
            heal = city_infos["total"]["heal"]
            dead = city_infos["total"]["dead"]
            details.append([update_time, province, city, confirm, confirm_add, heal, dead])
    return history, details


def get_conn():
    conn = pymysql.connect(
        host= os.getenv("MYSQL_HOST"),
        user= os.getenv("MYSQL_USER"),
        password= os.getenv("MYSQL_PASSWORD"), 
        db= os.getenv("MYSQL_DATABASE"), 
        charset="utf8")

    cursor = conn.cursor()
    return conn, cursor


def close_conn(conn, cursor):
    if cursor:
        cursor.close()
    if conn:
        conn.close()

def update_details():
    cursor = None
    conn = None
    try:
        li = get_tencent_data()[1]#0 means history data, 1 means the latest data
        conn,cursor = get_conn()
        sql = "insert into details(update_time,province,city,confirm,confirm_add,heal,dead) values(%s,%s,%s,%s,%s,%s,%s)"
        sql_query = 'select %s=(select update_time from details order by id desc limit 1)'
        #compare the latest data with the data in database
        cursor.execute(sql_query,li[0][0])
        if not cursor.fetchone()[0]:
            print(f"{time.asctime()}start update details data")
            for item in li:
                cursor.execute(sql,item)
            conn.commit()
            print(f"{time.asctime()}update details data")
        else:
            print(f"{time.asctime()}complete update details data")
    except:
        traceback.print_exc()
    finally:
        close_conn(conn,cursor)

#insert history data
def insert_history():
    cursor = None
    conn = None
    try:
        dic = get_tencent_data()[0]#0 means history data, 1 means the latest data
        print(f"{time.asctime()}Start insert history data")
        conn,cursor = get_conn()
        sql = "insert into history values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        for k,v in dic.items():
            cursor.execute(sql,[k, v.get("confirm"),v.get("confirm_add"),v.get("suspect"),
                           v.get("suspect_add"),v.get("heal"),v.get("heal_add"),
                           v.get("dead"),v.get("dead_add")])
        conn.commit()
        print(f"{time.asctime()}Complete insert history data")
    except:
        traceback.print_exc()
    finally:
        close_conn(conn,cursor)

#update history data
def update_history():
    cursor = None
    conn = None
    try:
        dic = get_tencent_data()[0]#0 means history data, 1 means the latest data
        print(f"{time.asctime()}Start update history data")
        conn,cursor = get_conn()
        sql = "insert into history values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        sql_query = "select confirm from history where ds=%s"
        for k,v in dic.items():
            if not cursor.execute(sql_query,k):
                cursor.execute(sql,[k, v.get("confirm"),v.get("confirm_add"),v.get("suspect"),
                               v.get("suspect_add"),v.get("heal"),v.get("heal_add"),
                               v.get("dead"),v.get("dead_add")])
        conn.commit()
        print(f"{time.asctime()}Complete update history data")
    except:
        traceback.print_exc()
    finally:
        close_conn(conn,cursor)

# get the hot search data from baidu
#
# The scrape/parse/store logic lives in hotsearch.py so this file and
# spider.py share a single implementation.  The helpers below stay as thin
# wrappers for backwards compatibility.
def get_baidu_hot():
    """Return ``[[title, heat], ...]`` for the current Baidu hot search."""
    result = hotsearch.collect_hotsearch()
    return [[item.title, item.heat] for item in result.items]


def update_hotsearch():
    """Refresh the hotsearch table and report one auditable summary line.

    Raises :class:`hotsearch.HotSearchCollectionError` when the run stored
    nothing at all (browser start, page load, parse or store failed).
    """
    return hotsearch.update_hotsearch()