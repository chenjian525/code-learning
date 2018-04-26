import requests
import time
import redis
import signal
from bs4 import BeautifulSoup
import logging


logging.basicConfig(level=logging.INFO)


def redis_client():
    return redis.StrictRedis(
        host='localhost',
        port=6379,
        db='0',
        password='',
        connection_pool=None,
        decode_responses=True
    )


r = redis_client()


def on_terminal(pages):
    def handler(signum, frame):
        logging.info('reset flag to exit')
        pages.clear()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, 'SIGQUIT'):
        signal.signal(signal.SIGQUIT, handler)


def process_data(string):
    soup = BeautifulSoup(string, 'lxml')
    all_trs = soup.select('#ip_list tr')

    def concat_proxy(l):
        return ':'.join([str(l[1].string), str(l[2].string)])

    pipe = r.pipeline()
    for tr in all_trs[1:]:
        tds = tr.select('td')
        proxy = concat_proxy(tds)
        pipe.lpush('proxies', proxy)
        logging.info('success push proxy %s into redis.', proxy)
    pipe.execute()


def fetch_url(f):
    session = requests.session()
    url = 'http://www.xicidaili.com/wt/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; U; Android 0.5; en-us) AppleWebKit/522  (KHTML, like Gecko) Safari/419.3',
        'Connection': 'keep-alive',
        'Host': 'www.xicidaili.com',
        'Pragma': 'no-cache',
        'Referer': 'http://www.xicidaili.com/',
    }
    proxies = {'http': '60.185.202.15:43281'}  # todo proxy here
    num = 1
    while f:
        c = url + str(num)
        try:
            data = session.get(c, headers=headers)
        except Exception as e:
            logging.info('use proxy: %s failed. %s', proxies, e)
        else:
            process_data(data.text)
        num += 1
        time.sleep(0.5)
    logging.info('receive exit signal, now the num is %s', num)
    logging.info('Bye bye!')


if __name__ == '__main__':
    flag = [True]
    on_terminal(flag)
    fetch_url(flag)
