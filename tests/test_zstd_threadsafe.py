# -*- coding: utf-8 -*-
"""zstd 上下文的并发回归测试（第 378 项闪退的防线）。

背景：`brain.py` 早期版本把 ZstdCompressor / ZstdDecompressor 缓存在**模块级 dict** 里，
全进程共用。python-zstandard 的对象不是线程安全的 —— 同一个对象被多个线程同时
compress / decompress，C 侧上下文互相写坏，轻则 `Data corruption detected`，
重则 backend_c.*.pyd 里 0xc0000005 直接结束进程（2026-11-21 18:09 连崩两次）。

这个用例就是把这个状态钉住：多线程同时压/解，任何一次结果不对就失败。
修法之后（threading.local，每线程一套）必须全绿。
"""
from __future__ import annotations

import threading
import unittest

try:
    import brain as b
except Exception:  # pragma: no cover
    b = None


@unittest.skipIf(b is None, 'brain 导入失败')
class TestZstdThreadSafety(unittest.TestCase):
    N_THREADS = 8
    LOOPS = 200

    def test_concurrent_pack_unpack_is_clean(self):
        text = 'Memex 正文压缩并发回归 ' * 400
        errs = []
        done = []

        def worker(i):
            try:
                for k in range(self.LOOPS):
                    blob = b._z_pack(text)
                    out = b._z_unpack(blob, n=None)
                    if out != text:
                        errs.append('roundtrip mismatch @%d/%d' % (i, k))
                        return
                done.append(i)
            except Exception as e:                      # noqa: BLE001
                errs.append('%s: %r' % (i, e))

        ts = [threading.Thread(target=worker, args=(i,))
              for i in range(self.N_THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        self.assertEqual(errs, [], '并发压/解出现错误（多半是 zstd 上下文又被共享了）：%r' % errs[:3])
        self.assertEqual(len(done), self.N_THREADS)

    def test_compressor_is_per_thread(self):
        """两个线程拿到的 compressor 不能是同一个对象（否则并发时还是会被写坏）。"""
        seen = {}

        def grab(i):
            # 必须把对象本身留住：只存 id 的话线程结束后对象被回收，
            # 下一个线程可能分配到同一个地址，id 反而"相等"—— 那是假失败。
            seen[i] = b._zc()

        ts = [threading.Thread(target=grab, args=(i,)) for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        ids = [id(v) for v in seen.values()]
        self.assertEqual(len(set(ids)), 4, 'compressor 被多个线程共用：%r' % ids)


if __name__ == '__main__':
    unittest.main()
