"""本地素材服务（端口 30010）—— SSRF 插件的确定性合法拉取源（D5 评审 P1-2）。

叙事：edu-lite 的合法远程头像来自"本地素材服务"；其余内网目标全部拒绝。
全本地、零公网依赖，演示确定性由它保证。
部署: nohup python3 asset_server.py > /tmp/asset_server.log 2>&1 &
"""
import pathlib

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent / "files"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("asset server on http://127.0.0.1:30010/ , serving", ROOT)
    ThreadingHTTPServer(("127.0.0.1", 30010), Handler).serve_forever()
