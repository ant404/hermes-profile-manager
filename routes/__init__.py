"""Blueprint 注册：把所有 routes 模块的蓝图挂到 Flask app 上。

用法（app.py / 应用工厂）：
    from flask import Flask
    from routes import register_blueprints
    app = Flask(__name__, static_folder=".")
    register_blueprints(app)
"""
from flask import Flask

from .misc import bp as misc_bp
from .profiles import bp as profiles_bp
from .skills import bp as skills_bp
from .shared import bp as shared_bp
from .config_env import bp as config_env_bp
from .backup import bp as backup_bp
from .inspect import bp as inspect_bp
from .hub import bp as hub_bp

ALL_BLUEPRINTS = [
    misc_bp, profiles_bp, skills_bp, shared_bp,
    config_env_bp, backup_bp, inspect_bp, hub_bp,
]


def register_blueprints(app: Flask):
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
    return app
