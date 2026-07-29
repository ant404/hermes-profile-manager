"""技能中心来源配置（clawhub / skills-sh / custom）的加载与保存。"""
import json
from pathlib import Path

from .paths import get_app_dir


CLAWHUB_BASE = "https://clawhub.ai/api/v1"
SOURCES_CONFIG_FILE = get_app_dir() / ".hermes_hub_sources.json"

DEFAULT_SOURCES = [
    {"id": "clawhub", "name": "ClawHub", "type": "clawhub", "url": "https://clawhub.ai/api/v1", "enabled": True},
    {"id": "skills-sh", "name": "Skills.sh", "type": "skills-sh", "url": "https://www.skills.sh", "enabled": True},
]


def load_sources():
    """加载技能源配置；若配置文件不存在或损坏则返回默认源（副本）。"""
    if SOURCES_CONFIG_FILE.exists():
        try:
            data = json.loads(SOURCES_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return [dict(s) for s in DEFAULT_SOURCES]


def save_sources(sources):
    """保存技能源配置到本地文件（utf-8，2 空格缩进）。"""
    try:
        SOURCES_CONFIG_FILE.write_text(
            json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
