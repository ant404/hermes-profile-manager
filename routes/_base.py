"""routes 模块公共导入基座。

每个 routes 文件顶部 `from ._base import *`，重建原单体 app.py 的全局命名空间
（core 各模块函数 + flask 工具 + 常量），保证路由函数体原封不动搬运即可运行。

只显式导出 core 中真实存在的名字（已逐一核对）。
"""
# ── flask ──
from flask import Blueprint, request, jsonify, send_from_directory

# ── stdlib（原 app.py 顶部导入，路由函数体直接用）──
import os
import re
import json
import shutil
import hashlib
from pathlib import Path
from datetime import datetime

# ── core.paths ──
from core.paths import (
    HERMES_HOME, HERMES_HOME_SOURCE, PROFILES_DIR, BUILTIN_SKILLS_DIR,
    HUB_DIR, SHARED_SKILLS_DIR, BACKUP_FILES, PROFILE_FILES,
    get_app_dir, get_profile_names, get_profile_path,
    get_file_path, get_skills_dir,
)

# ── core.files ──
from core.files import (
    read_file_safe, atomic_write_text, make_backup,
    get_file_signature, _log_operation,
)

# ── core.junctions（路由里用带下划线的原名）──
from core.junctions import (
    is_junction as _is_junction,
    create_junction as _create_junction,
    remove_junction as _remove_junction,
    get_junction_target as _get_junction_target,
)

# ── core.skills_lib ──
from core.skills_lib import (
    list_skills, parse_skill_frontmatter, read_skill_file, save_skill_file,
    _validate_skill_name, _resolve_skill_dir, _cleanup_empty_parent,
    _find_skill_dir_in, _skill_content_hash, _extract_install_junction,
    _add_skill_usage, _remove_skill_usage, _read_usage_json, _write_usage_json,
)

# ── core.parsers ──
from core.parsers import (
    _count_toolsets, _count_mcp,
    _parse_toolsets_registry, _parse_enabled_toolsets, _parse_mcp_servers,
)

# ── core.config_parser ──
from core.config_parser import (
    _load_yaml_file, _dump_yaml, _get_nested, _set_nested, CONFIG_SCHEMA,
)

# ── core.env_parser ──
from core.env_parser import (
    parse_env, patch_env_text, _split_value_comment, _unquote,
    _parse_env_line, _format_env_line, _comment_text,
)

# ── core.sources ──
from core.sources import (
    load_sources, save_sources, DEFAULT_SOURCES,
    CLAWHUB_BASE, SOURCES_CONFIG_FILE,
)

# ── core.backup_lib ──
from core.backup_lib import (
    _backup_root, _list_backup_dirs,
    _profile_backup_source, _profile_backup_dest,
    _migrate_hub_layout, _fix_shared_skill_junctions,
)

# ── core.watcher ──
from core.watcher import _watch_state, init_watch, check_changes

# ── 补充：backup.py 用到的 OPERATIONS_LOG（在 core.paths 定义）──
from core.paths import OPERATIONS_LOG


# ── 关键：定义 __all__，让 from ._base import * 也能导出带下划线的名字 ──
# Python 的 import * 默认跳过 _ 开头的名字，导致 _log_operation / _count_toolsets /
# _load_yaml_file 等无法被路由文件继承。导出所有非 dunder 全局名（含模块对象
# os/re/json/shutil 与带下划线的函数/常量）。
__all__ = [
    name for name in list(globals())
    if not name.startswith("__")                       # 只排除 __name__ 等 dunder
]
