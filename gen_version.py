# -*- coding: utf-8 -*-
"""把 brain.APP_VERSION / BUILD_DATE 写进 Windows 版本资源。

PyInstaller 的 EXE(version=...) 吃这个文件，打出来的 exe 在
「属性 → 详细信息」里就能看到版本号和描述，用户不用靠文件名猜是 v6.5 还是 v0.8。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brain  # noqa: E402


def vs(s):
    m = [int(x) for x in (s or '0').lstrip('vV').split('.') if x.isdigit()]
    while len(m) < 3:
        m.append(0)
    return m[:3]


def main():
    v = vs(brain.APP_VERSION)
    build = brain.BUILD_DATE or '2026-01-01'
    num = (v[0], v[1], v[2], 0)
    vstr = '%d.%d.%d' % (v[0], v[1], v[2])
    txt = '''# 自动生成，别手改。由 gen_version.py 从 brain.APP_VERSION 读取。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%r,
    prodvers=%r,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'bslnssb'),
        StringStruct('FileDescription', '第二大脑 · 本地文件搜索（文件名 + 文档正文）'),
        StringStruct('FileVersion', %r),
        StringStruct('InternalName', 'SecondBrain'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'SecondBrain.exe'),
        StringStruct('ProductName', '第二大脑 (Second Brain)'),
        StringStruct('ProductVersion', %r),
        StringStruct('Comments', 'build %s')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
''' % (num, num, vstr, vstr, build)
    out = HERE / 'version_info.txt'
    out.write_text(txt, encoding='utf-8')
    print('wrote %s  (%s, build %s)' % (out.name, vstr, build))


if __name__ == '__main__':
    main()
