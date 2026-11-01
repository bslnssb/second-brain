# QA findings — 2026-11-01

Before the first push, the app was put through `qa_test.py` (98 checks against a real
8,626-document / 2.37 GB library) instead of only the synthetic self-test. Seven real
defects surfaced. All are fixed, and every one of them now has a check that would fail
if it came back.

Run it yourself:

```
python qa_test.py            # writes QA_REPORT.md next to the script
python qa_test.py --quick    # skips the concurrency / leak sections
```

| # | Finding | Symptom | Root cause | Measured |
|---|---|---|---|---|
| 1 | `count(*)` on the FTS5 table | Every page load blocked for ~0.3 s | `docs` is a virtual table; counting materialises every row **including the full body** | 288 ms → **1.6 ms** |
| 2 | Filename fallback scanned `docs` | Zero-hit and 1–2 character searches were slow | Same reason: reading `path` still reads the whole row | 280 ms → **4 ms** |
| 3 | `LIKE` wildcards unescaped | Searching `_` returned the entire library; hint text claimed matches that did not exist | `%` and `_` are wildcards; the short-query path fed them straight in | 8,626 documents matched instead of 2,867 |
| 4 | `LIKE` truncated at NUL | 24 PDFs could not be found by 1–2 character queries past their first NUL byte | SQLite's `like()` works on C strings and stops at `\x00` | 24 documents affected |
| 5 | `/open` accepted any path | Any web page you visit could make the local service run `os.startfile` | `Host`/`Origin` are not sent on simple cross-site GETs (e.g. `<img src>`), and the path was never checked | Arbitrary `.exe` was launchable |
| 6 | `POST` with a JSON array | Connection dropped, UI showed "Failed to fetch" with no explanation | `req.get(...)` on a list raises `AttributeError` mid-handler | Aborted → **400** |
| 7 | Single-threaded server, no socket timeout | One client that never finishes its request froze the whole UI | `HTTPServer` handles one connection at a time and `timeout` was unset | Now answered in **1.2 ms** while a request is stalled |

## What changed

- `_count_docs()` — counts via `meta WHERE reason IS NULL` (a real table, and exactly the
  set of successfully indexed files) instead of counting the FTS5 table.
- `search_rows()` — the filename fallback scans `meta`, not `docs`.
- `_like_esc()` + `ESCAPE '\'` — `%`, `_` and `\` are matched literally.
- `_clean_text()` — strips NUL and other control characters at extraction time (keeps
  `\t`, `\n`, `\r`), alongside the existing lone-surrogate replacement.
- `/open` — only opens paths that exist in the index, and refuses `Sec-Fetch-Site:
  cross-site / same-site`. `Host` and `Origin` checks stay as they were.
- `do_POST` — rejects non-object bodies with 400 and a translated message.
- `QuietServer(ThreadingHTTPServer)` — one thread per connection, 15 s socket timeout,
  and connection resets are no longer printed as tracebacks.
- `conn()` + `con_lock` — one shared read connection instead of a new one per request
  (200 requests used to leave 200 unclosed connections behind).

## Performance, before → after (same library, real queries)

| Query | Before | After |
|---|---|---|
| common 4-character Chinese word | 7 ms | 9 ms |
| 2-character query (`AI`) | 10 ms | 12 ms |
| word with no hits (3+ chars) | 277 ms | **4 ms** |
| word with no hits (1–2 chars) | 317 ms | **50 ms** |
| page load | 290 ms | **1.6 ms** |
| slowest query measured | 317 ms | 180 ms |

## Known limitation, not fixed

24 documents still contain NUL bytes because they were ingested before the extraction
fix. Everything after the first NUL is invisible to 1–2 character queries on those
files; 3+ character queries go through FTS and are unaffected. Re-extracting them
(`python ocr_backfill.py --all pdf`) clears it — deliberately not done here, since it
means touching the index.
