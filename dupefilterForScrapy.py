import math
import mmh3
import redis
import random
import BitVector
import logging
from scrapy.dupefilters import BaseDupeFilter
from scrapy.utils.request import request_fingerprint


class CustomDupeFilter(BaseDupeFilter):
    def __init__(self, debug=False):
        self.logdupes = True
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        self.bloom = BloomFilter(1000)

    @classmethod
    def from_settings(cls, settings):
        debug = settings.getbool('DUPEFILTER_DEBUG')
        return cls(debug)

    def request_seen(self, request):
        fp = self.request_fingerprint(request)
        if self.bloom.is_exist(fp):
            return True
        self.bloom.add(fp)

    @staticmethod
    def request_fingerprint(request):
        return request_fingerprint(request)

    # def close(self, reason):
    #     if self.file:
    #         self.file.close()

    def log(self, request, spider):
        if self.debug:
            msg = "Filtered duplicate request: %(request)s"
            self.logger.debug(msg, {'request': request}, extra={'spider': spider})
        elif self.logdupes:
            msg = ("Filtered duplicate request: %(request)s"
                   " - no more duplicates will be shown"
                   " (see DUPEFILTER_DEBUG to show all duplicates)")
            self.logger.debug(msg, {'request': request}, extra={'spider': spider})
            self.logdupes = False

        spider.crawler.stats.inc_value('dupefilter/filtered', spider=spider)  # 这个在干什么


class BloomFilter(object):
    SEEDS = [31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139,
             149, 151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229, 233, 239, 241, 251, 257,
             263, 269, 271, 277, 281, 283, 293, 307, 311, 313, 317, 331, 337, 347, 349, 353, 359, 367, 373, 379, 383,
             389, 397, 401, 409, 419, 421, 431, 433, 439, 443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503, 509,
             521, 523, 541, 547, 557, 563, 569, 571, 577, 587, 593, 599, 601]

    def __init__(self, capacity=1000000000, error_rate=0.00000001, conn=None, key='BloomFilter'):
        self.m = math.ceil(capacity*math.log2(math.e)*math.log2(1/error_rate))
        self.k = math.ceil(math.log1p(2)*self.m/capacity)
        self.mem = math.ceil(self.m/8/1024/1024)
        self.blockNum = math.ceil(self.mem/512)
        self.redis = conn
        self.N = 2 ** 31 - 1
        self.key = key
        self.seeds = random.sample(self.SEEDS, self.k)

        if not self.redis:
            self.bitset = BitVector.BitVector(size=2 << 9)
        print(self.mem)
        print(self.k)

    def get_hashes(self, value):
        hashes = list()
        for seed in self.seeds:
            ret = mmh3.hash(value, seed)
            if ret < 0:
                ret = self.N - ret
            ret = ret % 1000
            hashes.append(ret)
        return hashes

    def is_exist(self, value):
        name = self.key + '_' + str(ord(value[0]) % self.blockNum)
        hashes = self.get_hashes(value)
        exist = True
        for ind in hashes:
            if self.redis:
                exist = exist & self.redis.getbit(name, ind)
            else:
                exist = exist & self.bitset[ind]
        return exist

    def add(self, value):
        hashes = self.get_hashes(value)
        name = self.key + '_' + str(ord(value[0]) % self.blockNum)
        for ind in hashes:
            if self.redis:
                self.redis.setbit(name, ind, 'hello bloomfilter')
            else:
                self.bitset[ind] = 1


def prime_gen(nums, start=2):
    def odd_iter():
        n = 1
        while True:
            n += 2
            yield n

    def not_divisible(n):
        return lambda x: x % n > 0

    it = odd_iter()
    if start <= 2:
        yield 2
        nums -= 1

    while nums:
        prime = next(it)
        if prime >= start:
            yield prime
            nums -= 1
        it = filter(not_divisible(prime), it)


if __name__ == '__main__':
    b = BloomFilter(1000, 0.00000001)
    b.add('ckming')
    b.add('zhoubin')
    print('hello world')
    print(b.is_exist('ckming'))
    print(b.is_exist('hello python'))
    print(b.is_exist('zhoubin'))
