"""路径检测与 profile 定位。

集中管理 HERMES_HOME 的探测、profile 目录解析、共享库/备份根目录。
所有路径常量在此处计算一次，供 core 其他模块与 routes 使用。
"""
import os
import sys
from pathlib import Path


def get_app_dir():
    """获取应用所在目录（用于存放 .hermes_home 等持久化配置）。
    - dev 模式：脚本所在目录
    - frozen 模式（PyInstaller onefile/onedir）：exe 所在目录
      注意：不能用 __file__ 或 sys._MEIPASS，它们在 onefile 模式下指向临时解压目录，
      退出后会被删除，导致 .hermes_home 配置丢失。"""
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable))
    # core/paths.py → 上一级即应用根目录
    return Path(__file__).resolve().parent.parent


def detect_hermes_home():
    """按优先级自动探测 HERMES_HOME 路径。

    优先级（高 → 低）：
      1. .hermes_home 配置文件（用户在界面里手动选择，最明确的意图）
      2. HERMES_HOME 环境变量（隐式继承，可能是父进程遗留）
      3. 常见安装路径
      4. 回退到 D:\\hermes

    注意：配置文件优先于环境变量，否则用户在界面里设置的路径会被
    父进程遗留的环境变量覆盖，导致"设置了不生效"。"""
    candidates = []

    # 1. .hermes_home 配置文件 (用户上次在界面里手动选择)
    config_file = get_app_dir() / ".hermes_home"
    if config_file.exists():
        try:
            # utf-8-sig 自动跳过可能存在的 BOM（PowerShell/记事本写入时可能带）
            content = config_file.read_text(encoding="utf-8-sig").strip()
            if content:
                candidates.append(("config_file", Path(content)))
        except Exception:
            pass  # 配置文件损坏则忽略

    # 2. 环境变量
    env_val = os.environ.get("HERMES_HOME")
    if env_val:
        candidates.append(("env:HERMES_HOME", Path(env_val)))

    # 3. 常见路径
    home = Path.home()
    common_paths = [
        r"D:\hermes",                       # 用户自定义
        str(home / ".hermes"),             # 默认安装
        str(home / "AppData" / "Local" / "hermes"),
        str(home / ".local" / "share" / "hermes"),
    ]
    for p in common_paths:
        candidates.append((f"common:{p}", Path(p)))

    # 验证每个候选：必须存在 config.yaml (或 profiles/ 目录)
    for source, path in candidates:
        if path and path.exists():
            if (path / "config.yaml").exists() or (path / "profiles").exists():
                return path, source

    # 没找到，回退到 D:\hermes (会显示警告)
    return Path(r"D:\hermes"), "fallback"


HERMES_HOME, HERMES_HOME_SOURCE = detect_hermes_home()
PROFILES_DIR = HERMES_HOME / "profiles"
# 内置技能库（hermes-agent/skills）：只读，与用户 profile skills 合并展示
BUILTIN_SKILLS_DIR = HERMES_HOME / "hermes-agent" / "skills"

# Hermes 资产中心（备份 + 共享技能库）
HUB_DIR_NAME = "AAAHermesHub"
HUB_DIR = get_app_dir() / HUB_DIR_NAME
SHARED_SKILLS_DIR = HUB_DIR / "shared-skills"
LOGS_DIR = HUB_DIR / "logs"
OPERATIONS_LOG = LOGS_DIR / "operations.log"

# 每个 profile 包含的可编辑文件 (相对于 profile 根目录)
PROFILE_FILES = {
    "config.yaml": {"sub": "", "lang": "yaml"},
    ".env": {"sub": "", "lang": "ini"},
    "SOUL.md": {"sub": "", "lang": "markdown"},
    "MEMORY.md": {"sub": "memories", "lang": "markdown"},
    "USER.md": {"sub": "memories", "lang": "markdown"},
}

# 备份时包含的文件
BACKUP_FILES = ["config.yaml", ".env", "SOUL.md", "MEMORY.md", "USER.md"]


def get_profile_names():
    """扫描所有 profile 名 (default 是 root，其余是 profiles/ 下的目录)"""
    names = []
    if (HERMES_HOME / "config.yaml").exists():
        names.append("default")
    if PROFILES_DIR.exists():
        for d in sorted(PROFILES_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("_"):
                if (d / "config.yaml").exists():
                    names.append(d.name)
    return names


def get_profile_path(name):
    if name == "default":
        return HERMES_HOME
    return PROFILES_DIR / name


def get_file_path(profile_name, file_key):
    info = PROFILE_FILES.get(file_key)
    if not info:
        return None
    base = get_profile_path(profile_name)
    if info["sub"]:
        return base / info["sub"] / file_key
    return base / file_key


def get_skills_dir(profile_name):
    """获取 profile 的 skills 目录路径"""
    return get_profile_path(profile_name) / "skills"
