#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二大脑 · 桌面窗口启动器

用 pywebview 起一个独立窗口，后台跑 brain.make_server 提供页面。
作用等同官方 exe 的窗口模式，但跑的是本目录源码，改动即时生效。
pywebview 不可用时自动退回默认浏览器，不会白屏。

带参数时走命令行（update / selftest / search …），方便打包成 exe 后复用同一入口。
"""
import json
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brain  # noqa: E402

try:
    import webview  # 必须模块级导入：Api.pick_folder 在别的线程里调用它
except Exception:
    webview = None

BASE_PORT = 8765
PORT_TRIES = 12
URL = f'http://127.0.0.1:{BASE_PORT}'


def _port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _is_ours(port):
    """端口上跑的是不是本应用（而不是恰好占了同一端口的别的程序）"""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=0.8) as r:
            return json.loads(r.read().decode('utf-8')).get('app') == 'second-brain'
    except Exception:
        return False


def _pick_port():
    """找一个能用的端口：空闲的优先，其次复用已在运行的本应用实例。
    全部被占返回 0。原实现直接用 8765，被别的程序占用时会直接抛异常退出。"""
    for port in range(BASE_PORT, BASE_PORT + PORT_TRIES):
        if not _port_busy(port):
            return port
        if _is_ours(port):
            return port
    return 0


def _start_server(port):
    if _port_busy(port):
        return None          # 已有实例在跑，直接复用
    srv = brain.make_server(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Api:
    """暴露给页面的原生能力：window.pywebview.api.pick_folder()
    页面里 ISGUI 为真时会走这里弹系统文件夹选择器，否则退回手输路径。"""

    def pick_folder(self):
        if webview is None:
            return ''
        try:
            picked = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if picked:
                return picked[0]
        except Exception:
            pass
        return ''


def main():
    global URL
    port = _pick_port()
    if not port:
        print(f'端口 {BASE_PORT}~{BASE_PORT + PORT_TRIES - 1} 都被占用，无法启动。')
        print('请关掉占用端口的程序后重试。')
        return
    URL = f'http://127.0.0.1:{port}'
    _start_server(port)

    if webview is None:
        webbrowser.open(URL)
        print(f'未安装 pywebview，已用浏览器打开：{URL}')
        # 浏览器模式下必须让主线程别退出，否则服务会被回收
        threading.Event().wait()
        return

    webview.create_window(
        'Second Brain · 第二大脑', URL,
        width=1360, height=880, min_size=(900, 600),
        text_select=True, js_api=Api(),
    )
    # 关窗即退出：daemon 线程随主进程结束
    webview.start()


if __name__ == '__main__':
    if len(sys.argv) > 1:
        brain.cli(sys.argv[1:])
    else:
        main()
