"""Hermes Profile Manager - 后端服务（薄工厂）

架构：
  core/    纯逻辑（路径、文件、junction、技能库、config/env 解析、备份、监听）
  routes/  Flask Blueprint（misc/profiles/skills/shared/config_env/backup/inspect/hub）

本文件只负责：创建 Flask app、CORS、来源防护、注册蓝图、启动。
业务逻辑全部在 core/ 与 routes/。
"""
import os
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from core.paths import HERMES_HOME, HERMES_HOME_SOURCE
from core.watcher import init_watch
from routes import register_blueprints


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    # 仅允许本机来源跨域访问，防止任意网页调用本地 API 窃取/篡改配置
    CORS(app, resources={
        r"/api/*": {"origins": r"https?://(localhost|127\.0\.0\.1|::1)(:\d+)?"}
    })

    def _is_localhost_origin(origin):
        """判断 Origin 是否为本机来源（任意端口）"""
        if not origin:
            return True  # 同源请求 / 非浏览器请求不携带 Origin，放行
        try:
            host = urlparse(origin).hostname
        except Exception:
            return False
        return host in ("127.0.0.1", "localhost", "::1")

    @app.before_request
    def _guard_origin():
        """拦截非本机 Origin 的跨域请求（防 DNS rebinding / 恶意网页）"""
        origin = request.headers.get("Origin")
        if origin and not _is_localhost_origin(origin):
            return jsonify({"error": "origin not allowed"}), 403

    register_blueprints(app)
    return app


app = create_app()


if __name__ == "__main__":
    init_watch()
    port = int(os.environ.get("HERMES_PM_PORT", "18520"))
    print(f"\n  Hermes Profile Manager")
    print(f"  → http://127.0.0.1:{port}")
    print(f"  → HERMES_HOME = {HERMES_HOME}  (source: {HERMES_HOME_SOURCE})")
    if HERMES_HOME_SOURCE == "fallback":
        print(f"  ⚠ HERMES_HOME not detected! Using fallback. Set it via UI or env var.")
    print()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
