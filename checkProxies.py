import time
import queue
import redis
import signal
import threading
import urllib.request
import http.cookiejar

lock = threading.Lock()


def sprint(*a, **b):
    with lock:
        print(*a, **b)

now = lambda: time.time()


class PrintWorker(threading.Thread):
    def __init__(self, result_queue, filename):
        super(PrintWorker, self).__init__()
        self.queue = result_queue
        self.output = open(filename, 'a')
        self.shutdown = False

    def write(self, data):
        self.output.write('\n'+data)

    def run(self):
        while not self.shutdown:
            data = self.queue.get()
            self.write(data)
            self.queue.task_done()

    def terminate(self):
        self.shutdown = True
        self.output.close()


class ProcessWorker(threading.Thread):
    def __init__(self, i, in_queue, out_queue):
        super(ProcessWorker, self).__init__()
        self.id = i
        self.con_queue = in_queue
        self.pro_queue = out_queue

    def run(self):
        while True:
            task = self.con_queue.get()
            result = self.process(task)
            if result is not None:
                self.pro_queue.put(task)
            self.con_queue.task_done()

    def process(self, task):
        log_msg = ('Thread #%3d. Trying HTTP proxy %21s' % (self.id, task))

        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj),
            urllib.request.HTTPRedirectHandler(),
            urllib.request.ProxyHandler({'http': task})
        )
        opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_4) AppleWebKit/537.36'
                                            ' (KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36')]
        urllib.request.install_opener(opener)
        req = urllib.request.Request(url='http://www.baidu.com')
        try:
            t1 = now()
            resp = urllib.request.urlopen(req, timeout=10).read()
            t2 = now()
        except Exception as e:
            log_msg += ' Failed. %s' % str(e)
            sprint(log_msg)
            return None

        log_msg += ' response time: %s' % str((t2-t1)*1000)
        sprint(log_msg)
        return task

    def terminate(self):
        sprint('Thread #%3d is down...' % self.id)


def redis_client():
    return redis.StrictRedis(
        host='localhost',
        port=6379,
        db='0',
        password='',
        connection_pool=None,
        decode_responses=True
    )


def main():
    _flag = [True]
    r = redis_client()
    task_queue = queue.Queue()
    result_queue = queue.Queue()

    workers = []
    for i in range(1, 11):
        t = ProcessWorker(i, task_queue, result_queue)
        t.setDaemon(True)
        t.start()
        workers.append(t)

    printer = PrintWorker(result_queue, 'proxy.txt')
    printer.setDaemon(True)
    printer.start()

    def on_terminal(flag):

        def handler(signum, frame):
            flag.clear()
            print('stop push item to task queue')

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
        if hasattr(signal, 'SIGQUIT'):
            signal.signal(signal.SIGQUIT, handler)

    on_terminal(_flag)
    print('start to get proxy from redis')
    while _flag:
        proxy = r.brpop('proxies', timeout=10)  # 从redis里取，放进task_queue
        if proxy:
            task_queue.put(proxy[1])

    task_queue.join()
    result_queue.join()

    printer.terminate()

    for process_worker in workers:
        process_worker.terminate()

    print('Bye bye.')


if __name__ == '__main__':
    main()
