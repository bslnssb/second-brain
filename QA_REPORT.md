# 第二大脑 QA 报告（2026-11-01 21:53:13）

**结论：98 通过 / 0 失败 / 1 警告 / 5 说明**

> 由 `qa_test.py` 自动生成。用真实索引库跑，只读，不写库、不建索引。

| 环境 | 值 |
|---|---|
| Python | 3.13.14 |
| SQLite | 3.53.1 |
| 数据库 | brain.db，2.37 GB |
| FTS5 | trigram |
| 平台 | win32 |

## A 检索边界

| 项目 | 结果 | 细节 | 耗时 |
|---|---|---|---|
| 空查询 | ✅ PASS | 5 条 | 0ms |
| 纯空格 | ✅ PASS | 5 条 | 0ms |
| 单个英文字符 | ✅ PASS | 5 条 | 0ms |
| 两个英文字符 | ✅ PASS | 5 条 | 0ms |
| 单个汉字 | ✅ PASS | 5 条 | 0ms |
| 两个汉字 | ✅ PASS | 5 条 | 1ms |
| 纯标点 | ✅ PASS | 5 条 | 11ms |
| 纯连字符 | ✅ PASS | 5 条 | 686ms |
| 一对引号 | ✅ PASS | 5 条 | 10ms |
| 单引号 | ✅ PASS | 5 条 | 1ms |
| 百分号 | ✅ PASS | 5 条 | 0ms |
| 双百分号 | ✅ PASS | 5 条 | 116ms |
| 下划线 | ✅ PASS | 5 条 | 0ms |
| 字母+下划线 | ✅ PASS | 5 条 | 13ms |
| C++ | ✅ PASS | 5 条 | 4ms |
| node.js | ✅ PASS | 5 条 | 2ms |
| 连字符词 | ✅ PASS | 5 条 | 2ms |
| 圆括号 | ✅ PASS | 1 条 | 5ms |
| 方括号 | ✅ PASS | 0 条 | 3ms |
| 花括号 | ✅ PASS | 0 条 | 4ms |
| FTS 关键字 AND | ✅ PASS | 5 条 | 6ms |
| FTS 关键字组合 | ✅ PASS | 0 条 | 566ms |
| 星号 | ✅ PASS | 5 条 | 0ms |
| SQL 注入尝试 | ✅ PASS | 0 条 | 429ms |
| SQL 注入变体 | ✅ PASS | 3 条 | 466ms |
| XSS 载荷 | ✅ PASS | 5 条 | 3ms |
| emoji | ✅ PASS | 0 条 | 461ms |
| 全角字母 | ✅ PASS | 0 条 | 488ms |
| 中英混合多词 | ✅ PASS | 5 条 | 1ms |
| 长中文词 | ✅ PASS | 0 条 | 3ms |
| 500 字符 | ✅ PASS | 0 条 | 30ms |
| 5000 字符 | ✅ PASS | 0 条 | 230ms |
| 换行与制表符 | ✅ PASS | 5 条 | 0ms |
| 首尾空格 | ✅ PASS | 0 条 | 3ms |
| 提取时清洗 NUL/控制字符 | ✅ PASS | '前 中 后 尾' |  |
| 保留换行与制表符 | ✅ PASS | '行1\n行2\t列\r\n' |  |
| 孤立代理项被净化 | ✅ PASS | 结果可 UTF-8 编码=True：'a?b' |  |
| 通配符字面匹配 '_' | ✅ PASS | LIKE(ESCAPE) 2867 篇 / 字面 instr 2868 篇；差集 1 篇全是含 NUL 的历史文档（LIKE 遇 NUL 截断） |  |
| 通配符字面匹配 '__' | ✅ PASS | LIKE(ESCAPE) 359 篇 / 字面 instr 360 篇；差集 1 篇全是含 NUL 的历史文档（LIKE 遇 NUL 截断） |  |
| 通配符字面匹配 '%' | ✅ PASS | LIKE(ESCAPE) 1496 篇 / 字面 instr 1497 篇；差集 1 篇全是含 NUL 的历史文档（LIKE 遇 NUL 截断） |  |
| 通配符字面匹配 '%%' | ✅ PASS | LIKE(ESCAPE) 41 篇 / 字面 instr 41 篇 |  |
| 库内含 NUL 字节的文档 | ⚠️ WARN | 24 篇（历史数据，NUL 之后的内容对 1~2 字短词检索不可见；提取器已加清洗，重新提取即可修复） |  |
| 短词结果确实含该词 | ✅ PASS | 0 条结果不含查询词 |  |
| 类型过滤 正常扩展名 | ✅ PASS | 10 条 |  |
| 类型过滤 带点扩展名 | ✅ PASS | 10 条 |  |
| 类型过滤 大写扩展名 | ✅ PASS | 10 条 |  |
| 类型过滤 带空格 | ✅ PASS | 10 条 |  |
| 类型过滤 空扩展名 | ✅ PASS | 10 条 |  |
| 类型过滤 扩展名注入 | ✅ PASS | 0 条 |  |
| 类型过滤 docx | ✅ PASS | 10 条 |  |
| 路径过滤 正常路径 | ✅ PASS | 10 条 |  |
| 路径过滤 不存在的路径 | ✅ PASS | 0 条 |  |
| 路径过滤 百分号 | ✅ PASS | 0 条 |  |
| 路径过滤 下划线 | ✅ PASS | 10 条 |  |
| 路径过滤 超长路径 | ✅ PASS | 0 条 |  |

## B 性能

| 项目 | 结果 | 细节 | 耗时 |
|---|---|---|---|
| --- 检索延迟（真实库，各跑 3 次取中位）--- | ℹ️ INFO |  |  |
| 常见中文四字词（FTS） | ✅ PASS | 中位 7ms（范围 7~9） | 7ms |
| 两字符（LIKE 全表降级） | ✅ PASS | 中位 12ms（范围 12~13） | 12ms |
| 单汉字（LIKE 全表降级） | ✅ PASS | 中位 11ms（范围 11~12） | 11ms |
| 高频单字（LIKE 最坏情况） | ✅ PASS | 中位 3ms（范围 2~3） | 3ms |
| 长中文词（FTS） | ✅ PASS | 中位 9ms（范围 9~10） | 9ms |
| 常见英文词（FTS） | ✅ PASS | 中位 95ms（范围 91~98） | 95ms |
| 中英混合（FTS 多词） | ✅ PASS | 中位 189ms（范围 189~194） | 189ms |
| 零命中（FTS） | ✅ PASS | 中位 4ms（范围 3~6） | 4ms |
| 零命中（LIKE 全表扫描） | ✅ PASS | 中位 51ms（范围 49~53） | 51ms |
| 最慢查询 | ✅ PASS | 189ms |  |
| 首页（含库内篇数统计） | ✅ PASS | 28 KB / 1.5ms | 2ms |

## C HTTP

| 项目 | 结果 | 细节 | 耗时 |
|---|---|---|---|
| /api/ping | ✅ PASS | 200 b'{"app": "second-brain", "ver": "v6.0"}' |  |
| 首页 | ✅ PASS | 200, 28 KB | 7ms |
| 英文首页 | ✅ PASS | 200, 28 KB |  |
| 未知路由 | ✅ PASS | 200 —— 返回首页 HTML（单页应用可接受，但不是 404） |  |
| 正常检索 | ✅ PASS | 200, 30 条 |  |
| 空查询 | ✅ PASS | 200, 30 条 |  |
| 中文 URL 编码 | ✅ PASS | 200, 30 条 |  |
| 三类参数同时使用 | ✅ PASS | 200, 5 条 |  |
| Host 伪造被拒 | ✅ PASS | 403 |  |
| 跨站 Origin 被拒 | ✅ PASS | 403 |  |
| POST 非 JSON 被拒 | ✅ PASS | 403 |  |
| 跨站 GET /open 打开本地文件 | ✅ PASS | 200 ok=False —— 跨站请求不得触发 os.startfile |  |
| /open 打开索引内文件（正常用法） | ✅ PASS | ok=True |  |
| /open 打开索引外的文件 | ✅ PASS | 已拒绝 |  |
| /open 指向任意 exe | ✅ PASS | 已拒绝 —— 否则就是个远程执行入口 |  |
| /open 不存在的文件 | ✅ PASS | ok=False |  |
| POST 垃圾 JSON | ✅ PASS | 200 b'{"ok": false, "err": "\xe8\x87\xb3\xe5\xb0\x91\xe8\xa6\x81\xe6\xb7\xbb\xe5\x8a\xa0\xe4\xb8\x80\xe4\xb8\xaa\xe6\x96\x87\xe4\xbb\xb6\xe5\xa4\xb9", "k": ' |  |
| POST 空 body | ✅ PASS | 200 b'{"ok": false, "err": "\xe8\x87\xb3\xe5\xb0\x91\xe8\xa6\x81\xe6\xb7\xbb\xe5\x8a\xa0\xe4\xb8\x80\xe4\xb8\xaa\xe6\x96\x87\xe4\xbb\xb6\xe5\xa4\xb9", "k": ' |  |
| POST 数组而非对象 | ✅ PASS | 400 b'{"ok": false, "err": "\xe8\xaf\xb7\xe6\xb1\x82\xe4\xbd\x93\xe5\xbf\x85\xe9\xa1\xbb\xe6\x98\xaf JSON \xe5\xaf\xb9\xe8\xb1\xa1", "k": ' |  |
| POST 类型错误字段 | ✅ PASS | 200 b'{"ok": true}' |  |
| 5 并发 vs 5 串行 | ℹ️ INFO | 串行 305ms / 并发 318ms = 1.04×（检索共用一条连接，按设计排队） | 318ms |
| 慢检索时心跳仍响应 | ✅ PASS | 1ms | 1ms |
| 半开连接时其他请求 | ✅ PASS | 仍可响应，1ms | 1ms |

## D 资源

| 项目 | 结果 | 细节 | 耗时 |
|---|---|---|---|
| 200 次检索后 SQLite 连接数 | ✅ PASS | 2 → 2（未关闭的连接会被 GC 兜底，但 fd 会先堆着） |  |
| 200 次检索后句柄数 | ✅ PASS | 196 → 196 |  |
| 200 次检索后内存 | ✅ PASS | +2 MB |  |

## E 界面

| 项目 | 结果 | 细节 | 耗时 |
|---|---|---|---|
| 字典键数 | ✅ PASS | zh 86 / en 86 |  |
| 中英键对齐 | ✅ PASS | 完全一致 |  |
| 页面引用的键都存在 | ✅ PASS | 55 个键全部有定义 |  |
| 英文模式的裸露中文 | ✅ PASS | 静态文案全部有字典覆盖 |  |

## F 库

| 项目 | 结果 | 细节 | 耗时 |
|---|---|---|---|
| 文档总数 | ℹ️ INFO | 8,626 篇；走 meta 0.5ms，走 docs(全表) 305ms —— 601 倍差距，页面每次加载都要这个数 | 1ms |
| 两种计数一致 | ✅ PASS | 8626 |  |
| integrity_check | ✅ PASS | ok |  |
| FTS5 integrity-check | ✅ PASS | 通过 |  |
| 未能入库的文件 | ℹ️ INFO | 无文本 234；doc 旧版格式，需另存为 docx/xlsx/pptx 80；空文件 38；临时文件 5；加密 1 |  |
| 标记成功却没正文 | ✅ PASS | 0 条 |  |
| 正文体积（200 篇抽样） | ℹ️ INFO | 平均 40 KB，最大样本 3935 KB | 23ms |

## 待处理问题

- **[WARN] A 检索边界 / 库内含 NUL 字节的文档** —— 24 篇（历史数据，NUL 之后的内容对 1~2 字短词检索不可见；提取器已加清洗，重新提取即可修复）
