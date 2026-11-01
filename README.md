# Second Brain

**English** | [简体中文](README.zh-CN.md)

Turn the documents scattered across your hard drive into a local knowledge base you can actually search. **Full-text search, CJK-friendly, completely offline** — no cloud, no indexing service, no account.

Built with Python + SQLite FTS5. A single `brain.db` holds all the text; it works with the network cable unplugged.

> Why not Everything? Everything searches file *names*. This searches what's written *inside* the files.
> Why not an online note app? Your contracts, medical records and internal training material don't belong on someone else's server.

---

## Features

- **Full-text search that understands Chinese/Japanese/Korean** — 3+ characters use the FTS5 trigram index (fast, with hit highlighting); 1–2 character queries automatically fall back to `LIKE`
- **Automatic OCR for scans** — image-only PDFs get their first 15 pages recognised offline (RapidOCR); no network after install
- **Wide format coverage** — PDF / DOCX / PPTX / XLSX / EPUB / HTML / TXT / MD
- **Incremental maintenance** — modified files are re-indexed, deleted files are pruned, unchanged files are skipped instantly
- **Resumable indexing** — interrupt a 50k-file build and restart it; it continues where it stopped
- **Bilingual UI** — the web interface has a 中 / EN switch in the top-right corner; it follows your browser language by default and remembers your choice
- **Quality self-test (rare, but it matters)** — `python brain.py selftest` validates retrieval accuracy, cross-checks two PDF extractors, measures OCR character error rate, fuzzes 9 kinds of malformed files and checks database integrity. The report is reproducible by anyone
- **Zero upload** — every parser runs locally; the HTTP server binds to `127.0.0.1` only

## Two ways to run it

| Way | Who it's for | How to start |
|---|---|---|
| **Download the exe** (no install) | You'd rather not touch a terminal | Grab `second-brain.exe` (with OCR, ~350 MB) or `second-brain-core.exe` (no OCR, ~36 MB) from [Releases](../../releases), drop it anywhere, double-click |
| **Run from source** | You want to read or change the code | Follow *Install* below |

The exe and the source version read and write the **same** `brain.db`, so you can mix them — drop the exe next to an existing library and it picks it up automatically.

## Install

Python 3.10+ required (3.12 recommended).

```bash
pip install -r requirements.txt
```

Create your scope configuration:

```bash
cp scope.example.json scope.json    # Windows: copy scope.example.json scope.json
```

Edit `scope.json` and put your folders in `include_dirs` (lowercase paths; backslashes must be doubled).

## Usage

```bash
python scan_docs.py                 # 1. scan disks, write the candidate list (paths only, contents untouched)
python brain.py index scope.json    # 2. build the index (resumable, shows count / rate / ETA)
python brain.py serve               # 3. open the web UI at http://127.0.0.1:8765
```

Routine maintenance is one command (rescan → incremental index → prune stale entries):

```bash
python brain.py update
```

Other commands:

```bash
python brain.py search <keywords> [limit] [ext] [path-contains]   # search from the terminal
python brain.py prune                                            # only remove entries for vanished files
python brain.py selftest                                         # quality self-test → eval_report.txt
```

### Desktop window

Don't want a console window? Run `python app_window.py` (or double-click `start.bat` on Windows): it opens a
**standalone desktop window** via pywebview — not a browser tab — backed by the local HTTP server.
If pywebview isn't installed it falls back to your default browser.

`update.bat` is the routine maintenance entry point — it's the same as `python brain.py update`.

## Configuring the index scope

Two fields matter in `scope.json`:

| Field | Meaning |
|---|---|
| `include_dirs` | Folders to index, matched by path prefix. Lowercase, doubled backslashes on Windows |
| `exclude_dirs` | Keywords; any path containing one is skipped |

`scan_docs.py` adds a second safety net: any directory whose name contains 「小说 / 书城 / 书架 / novel / fiction / 轻小说」 is never touched — it doesn't even appear in the candidate list.

**If your documents live under a system folder such as `C:\Program Files`** (portable installs, for example):
those folders are on the scanner's skip list. List them in `include_dirs` and each one is added as its
own scan root automatically (since v6.0), so nothing is missed.

## Supported formats and known limits

| Format | Supported | Notes |
|---|---|---|
| .txt / .md | Yes | Detects utf-8 / gbk / utf-16, gb18030 as a fallback |
| .pdf | Yes | pypdf reads the first 60 pages; image-only files get OCR on the first 15 |
| .docx / .pptx / .xlsx / .epub | Yes | zip + XML unpacking, no external dependencies |
| .html / .htm | Yes | Tags stripped, body text kept |
| .doc (legacy binary) | No | OLE2 compound format; pure-Python parsing is unreliable. Convert to .docx with WPS/LibreOffice first |
| .rtf | No | Not implemented |
| Vendor-encrypted documents | No | Files with an abnormal header are recorded as “encrypted” and skipped; they never pollute the index |
| Body length | 2 MB cap | Longer text is truncated so one giant file can't stall the index |

Files that fail to yield text are recorded with a reason in the `meta` table (encrypted / corrupted /
empty / temp file) and can be inspected in the UI or the `update` log — nothing disappears silently.

## Search syntax

Plain keywords work. Multiple space-separated words are combined with AND, and every word is treated
as a literal phrase, so `C++`, `node.js`, `a-b`, `"quoted"` and `(parens)` are all safe to type —
they never trigger an FTS syntax error.

Filters: the **Type** box takes an extension (`pdf`, `docx`, …), the **Path contains** box filters by
substring of the full path.

### URL parameters

Handy for bookmarks and shortcuts:

| Parameter | Effect |
|---|---|
| `?q=keyword` | Open the page with results already rendered |
| `?lang=zh` / `?lang=en` | Force the interface language for this link |

Example: `http://127.0.0.1:8765/?q=invoice&lang=en`

## Quality self-test

`eval_report.txt` is a real report from an actual run:

```
[PASS] A1 retrieval recall@3 = 5/5 —— recall@1=5/5, MRR=1.000
[PASS] A2 relevance ranking —— bm25 ordering effective
[PASS] A3 encrypted PDF excluded —— zero leakage confirmed
[PASS] B  dual extractor agreement pypdf vs pymupdf = 1.00
[PASS] C  OCR character error rate CER = 7.3%
[PASS] D  malformed-file fuzz: 9 cases —— zero crashes
[PASS] E  SQLite/FTS5 integrity check
[PASS] F  file-change auto re-index
[PASS] G1 failure attribution / G2 prune
OVERALL: PASS
```

The whole run uses synthetic data in a temporary directory — **your real index is never touched**.

## Privacy

- Parsing, OCR and indexing all happen locally; no network requests are made
- The HTTP server binds to `127.0.0.1` only, validates Host/Origin, and accepts `application/json` POSTs only
- The index is just `brain.db` next to the executable — delete it and everything is gone
- `.gitignore` excludes `brain.db`, `scan_raw.csv` and `scope.json`: they contain document text and local paths

## Project layout

```
brain.py            core: indexing / search / web server / pruning / self-test dispatch
scan_docs.py        disk scanner (exclusion rules, fiction-folder safety net)
app_window.py       desktop window launcher (pywebview)
selftest.py         quality self-test (synthetic data)
ocr_backfill.py     re-run indexing for files that previously yielded no text
gen_icon.py         generates the app icon
scope.example.json  scope configuration template
start.bat           Windows: open the desktop window
update.bat          Windows: rescan → index → prune in one click
README.zh-CN.md     Chinese version of this document
brain.db            (generated) SQLite index — docs = FTS5 table, meta = processed files
scan_raw.csv        (generated) last scan candidate list
eval_report.txt     last self-test report
```

## License

MIT
