from functools import partial
from collections import namedtuple, MutableMapping, OrderedDict
from pymysql.connections import Connection
from threading import RLock
import heapq
import time

_Null = object()
key_fields = (
    'host',
    'port'
)
PoolKey = namedtuple('PoolKey', key_fields)


def key_normalizer(keyCls, conn_args):
    return keyCls(**conn_args)


class Container(MutableMapping):
    ContainerCls = OrderedDict

    def __init__(self, maxsize=None, dispose_func=None):
        self._maxsize = maxsize
        self.dispose_func = dispose_func
        self._container = self.ContainerCls()
        self.lock = RLock()

    def keys(self):
        with self.lock:
            return self._container.keys()

    def __getitem__(self, item):
        if not item:
            return None

        with self.lock:
            for key in self.keys():
                if item in key:
                    conn = self._container.pop(key)
                    self._container[key] = conn
                    return conn
            return None

    def __setitem__(self, key, item):
        with self.lock:
            evicted_value = self._container.get(key, _Null)
            self._container[key] = item
            if len(self._container) > self._maxsize:
                _key, v = self._container.popitem(last=False)
                if self.dispose_func:
                    self.dispose_func(v)

        if self.dispose_func and evicted_value is not _Null:
            self.dispose_func(evicted_value)

    def __delitem__(self, item):
        with self.lock:
            v = self._container.pop(item)
        if self.dispose_func:
            self.dispose_func(v)

    def clear(self):
        with self.lock:
            vs = self._container.values()
            self._container.clear()

        if self.dispose_func:
            for v in vs:
                self.dispose_func(v)

    def __len__(self):
        with self.lock:
            return len(self._container)

    def __iter__(self):
        raise NotImplementedError('Iteration over this class is unlikely to be threadsafe.')


class MysqlPoolManager(object):
    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, '_instance'):
            cls._instance = super(MysqlPoolManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    def new(cls, *args, replace_original_instance=False, **kwargs):
        ins = super(MysqlPoolManager, cls).__new__(cls)
        if replace_original_instance:
            cls._instance = ins
        return ins

    def __init__(self, num_pools=10):
        if not hasattr(self, 'pools'):
            self.pools = Container(num_pools, dispose_func=lambda p: p.close_pool())
            self.host_key_constructor = partial(key_normalizer, PoolKey)
            self.all_alias = set()
            self.poolCls = MysqlConnectionPool

    def _get_pool(self, alias=None, host=None, port=None, user=None, password='', unix_socket=None):
        host_key = ''
        if host and port:
            args = dict(host=host, port=port)
            host_key = self.host_key_constructor(args)
        pool = self.get_exists_pool(alias=alias, host_key=host_key, unix_socket=unix_socket)
        if not pool:
            pool = self.new_pool(alias=alias, host=host, port=port, user=user,
                                 password=password, unix_socket=unix_socket)
        if pool is None:
            raise ConnectionError("con't create new connection pool, please check conn variables.")
        return pool

    def get_exists_pool(self, alias=None, host_key=None, unix_socket=None):
        res = self.pools[alias] or self.pools[host_key] or self.pools[unix_socket]
        return res

    def new_pool(self, alias=None, host=None, port=None, user=None, password='', unix_socket=None):
        if alias and alias in self.all_alias:
            raise ValueError('alias name already had, please change it.')
        pool = self.poolCls(host=host, port=port, user=user, password=password, unix_socket=unix_socket)
        if pool:
            k1 = alias or ''
            k2 = ''
            if host and port:
                k2 = self.host_key_constructor(dict(host=host, port=port))
            k3 = unix_socket or ''
            self.pools[(k1, k2, k3)] = pool
            return pool
        return

    def clear(self):
        self.pools.clear()

    def get_conn(self, alias=None, host=None, port=None, user=None, password='', unix_socket=None, force=False):
        pool = self._get_pool(alias=alias, host=host, port=port, user=user, password=password, unix_socket=unix_socket)
        conn = pool.get_conn(force=force)
        return conn


# class Connection(object):  # pymysql Connection
#     def __init__(self, *args, **kwargs):
#         self.conn_success = True
#         self._sock = 123
#
#     @property
#     def open(self):
#         return self._sock is not None
#
#     def close(self):
#         self._sock = None
#
#     def execute(self):
#         print('execute success')


class MysqlConnection(Connection):
    def __init__(self, *args, pool=None, **kwargs):
        self.pool = pool
        super().__init__(*args, **kwargs)

    def close(self, close_conn=False):
        if self.pool.pool_closed is False and not close_conn:
            self.pool.put(self)
        else:
            super().close()


class MaxConnectionExceeded(ValueError):
    pass


class MysqlConnectionPool(object):
    def __init__(self, max_connections=20, stale_timeout=3600,
                 timeout=10, **conn_kw):
        self.max_connections = max_connections
        self.stale_timeout = stale_timeout or 3600
        conn_kw.update(dict(connect_timeout=timeout, charset='utf8mb4'))
        self.conn_args = conn_kw
        self.in_use = {}
        self.connections = []
        self.pool_closed = False

    def conn(self):
        return MysqlConnection(pool=self, **self.conn_args)

    def _is_stale(self, ts):
        return ts + self.stale_timeout < time.time()

    def get_conn(self, force=False):
        while True:
            try:
                ts, conn = heapq.heappop(self.connections)
                key = id(conn)
            except IndexError:
                ts = conn = None
                break
            else:
                if not conn.open:
                    ts = conn = None
                elif self.stale_timeout and self._is_stale(ts):
                    conn.close(True)
                    ts = conn = None
                else:
                    break

        if conn is None:
            if self.max_connections and len(self.in_use) >= self.max_connections and not force:
                raise MaxConnectionExceeded('Exceeded maximum connections')
            conn = self.conn()
            key, ts = id(conn), time.time()

        self.in_use[key] = ts
        return conn

    def put(self, conn):
        key = id(conn)
        if key in self.in_use:
            ts = self.in_use[key]
            del self.in_use[key]
            heapq.heappush(self.connections, (ts, conn))

    def close_pool(self):
        conns, self.connections = self.connections, None
        for _, conn in conns:
            conn.close()
        self.pool_closed = True


if __name__ == '__main__':
    manager = MysqlPoolManager(num_pools=20)
    c1 = manager.get_conn(alias='p1', host='127.0.0.1', port=1565, user='ckm', password='123')
    c2 = manager.get_conn(alias='p1')
    c1.execute()
    c2.execute()
    c2.close()
    c3 = manager.get_conn(host='127.0.0.1', port=1565)
    c3.execute()
    l = []
    for i in range(17):
        l.append(manager.get_conn(alias='p1'))
        l[-1].execute()
    c4 = manager.get_conn('p1')
    c4.execute()
    manager.get_conn('p1', force=True).execute()
    manager.get_conn('p1').execute()
