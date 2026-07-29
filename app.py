"""Hermes Profile Manager - 后端服务

自动检测 HERMES_HOME，扫描 profiles，
提供 REST API 实时读取和改写各 profile 的：
  - config.yaml / .env / SOUL.md / MEMORY.md / USER.md
  - skills/*/SKILL.md (skill 管理)
"""
import os
import sys
import json
import time
import shutil
import re
import io
import hashlib
import zipfile
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS


# ── HERMES_HOME 自动检测 ─────────────────────────────────
def get_app_dir():
    """获取应用所在目录（用于存放 .hermes_home 等持久化配置）。
    - dev 模式：脚本所在目录（d:\\hermes\\AAAworkspace\\hermes-profile-manager）
    - frozen 模式（PyInstaller onefile）：exe 所在目录
      注意：不能用 __file__ 或 sys._MEIPASS，它们在 onefile 模式下指向临时解压目录，
      退出后会被删除，导致 .hermes_home 配置丢失。"""
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable))
    return Path(__file__).parent


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
# hermes 显示的"技能 85"即 = 用户自定义 skills + 此内置库 + MCP，单独扫 profile 只看到一部分
BUILTIN_SKILLS_DIR = HERMES_HOME / "hermes-agent" / "skills"

# Hermes 资产中心（备份 + 共享技能库）
# 放在程序所在目录（get_app_dir()），而非 HERMES_HOME：
#   - 重建 exe 时不会被删除（PyInstaller 只覆盖 exe，不清理同目录其他文件）
#   - 与 hermes 主目录分离，hermes 更新不影响备份和共享技能
# 下设：
#   backups/<timestamp>/<profile>/   — 配置备份
#   shared-skills/<skill_name>/      — 跨 profile 共享的技能（通过 NTFS junction 引用）
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

app = Flask(__name__, static_folder=".")
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


# ── 工具函数 ──────────────────────────────────────────────
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


# 内置 skills 扫描缓存（builtin 只读，进程内不变，避免 polling 时重复 rglob）
_builtin_skills_cache = None


def _count_toolsets(profile_name):
    """返回 (工具集总数, 该 profile 启用数)。
    总数来自 hermes-agent/toolsets.py 的 TOOLSETS dict 顶层 key；
    启用数来自 config.yaml 的 platform_toolsets 列表项数。"""
    total = 0
    ts_file = HERMES_HOME / "hermes-agent" / "toolsets.py"
    if ts_file.exists():
        try:
            t = ts_file.read_text(encoding="utf-8", errors="replace")
            total = len(re.findall(r'^    "([a-z_]+)":\s*\{', t, re.M))
        except Exception:
            pass
    enabled = 0
    cfg = get_profile_path(profile_name) / "config.yaml"
    if cfg.exists():
        try:
            t = cfg.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'platform_toolsets:\s*\n((?:[ \t]{2,}.*\n)+)', t)
            if m:
                enabled = len(re.findall(r'^\s+-\s+\S', m.group(1), re.M))
        except Exception:
            pass
    return total, enabled


def _count_mcp(profile_name):
    """统计 profile 的 MCP server 数（config.yaml 的 mcp_servers 段列表项数）"""
    cfg = get_profile_path(profile_name) / "config.yaml"
    if not cfg.exists():
        return 0
    try:
        t = cfg.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'mcp_servers?:\s*\n((?:[ \t]{2,}.*\n)+)', t)
        if not m:
            return 0
        return len(re.findall(r'^\s+-\s+\S', m.group(1), re.M))
    except Exception:
        return 0


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


def _read_usage_json(skills_dir):
    """读取 skills 目录下的 .usage.json，返回 dict。文件不存在或格式错误时返回 {}。"""
    fp = Path(skills_dir) / ".usage.json"
    if not fp.exists():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_usage_json(skills_dir, data):
    """写入 .usage.json 到 skills 目录。"""
    fp = Path(skills_dir) / ".usage.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(fp, json.dumps(data, indent=2, ensure_ascii=False))


def _usage_key(skill_name, category=""):
    """构造 .usage.json 的键：分类存在时用 category/skill_name，否则用 skill_name。"""
    return f"{category}/{skill_name}" if category else skill_name


def _add_skill_usage(profile_name, skill_name, category=""):
    """在 profile 的 .usage.json 中添加 skill 条目（已存在则不覆盖）。"""
    skills_dir = get_skills_dir(profile_name)
    usage = _read_usage_json(skills_dir)
    key = _usage_key(skill_name, category)
    if key not in usage:
        now = datetime.now().astimezone().isoformat()
        usage[key] = {
            "archived_at": None,
            "created_at": now,
            "created_by": None,
            "last_patched_at": None,
            "last_used_at": now,
            "last_viewed_at": now,
            "patch_count": 0,
            "pinned": False,
            "state": "active",
            "use_count": 0,
            "view_count": 0,
        }
        _write_usage_json(skills_dir, usage)


def _remove_skill_usage(profile_name, skill_name, category=""):
    """从 profile 的 .usage.json 中移除 skill 条目。"""
    skills_dir = get_skills_dir(profile_name)
    usage = _read_usage_json(skills_dir)
    key = _usage_key(skill_name, category)
    changed = False
    if key in usage:
        del usage[key]
        changed = True
    # 同时尝试用纯 skill_name 移除（兼容旧数据无分类前缀的情况）
    if skill_name in usage:
        del usage[skill_name]
        changed = True
    if changed:
        _write_usage_json(skills_dir, usage)


def read_file_safe(path):
    try:
        if not path.exists():
            return "", None
        return path.read_text(encoding="utf-8"), None
    except Exception as e:
        return "", str(e)


def atomic_write_text(path, content, encoding="utf-8"):
    """原子写入文本：先写同目录临时文件，再 os.replace 替换目标。
    避免写入过程中崩溃/断电导致目标文件被截断损坏。
    注意：不用 tempfile.mkstemp，因为它在 Windows 上对无写权限目录会挂起
    （而非立即报错）；改用普通 open() 写唯一命名的临时文件，权限不足会立即 PermissionError。"""
    import random, string
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    tmp = path.parent / f"{path.name}.{os.getpid()}.{suffix}.tmp"
    try:
        # 先写临时文件；若目标目录不可写，这里立即 PermissionError，不会挂起
        with open(tmp, "wb") as f:
            f.write(content.encode(encoding))
        # 原子替换（同文件系统下 os.replace 是原子的）
        os.replace(tmp, str(path))
    except Exception:
        # 清理残留临时文件
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def make_backup(path):
    """备份文件到 .backups/。返回警告字符串（失败时）或 None（成功/文件不存在）。
    备份失败不阻止保存：原子写入本身已防止文件损坏，备份只是“可撤销”的便利。
    但失败时返回警告，由调用方透传给前端，不再静默吞错。"""
    if not path.exists():
        return None
    try:
        backup_dir = path.parent / ".backups"
        backup_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{path.stem}_{ts}{path.suffix if path.suffix else '.bak'}"
        shutil.copy2(path, backup_dir / backup_name)
        return None
    except Exception as e:
        return f"backup failed: {e}"


# ── 操作日志 ──────────────────────────────────────────────
def _log_operation(action, result="success", **kwargs):
    """记录操作日志到 AAAHermesHub/logs/operations.log（JSONL 格式）。
    用于出问题后查询和恢复：每条记录含时间、动作、相关 profile/skill、结果、详情。
    日志失败不影响主操作（catch 所有异常静默吞掉）。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "result": result,
        }
        entry.update(kwargs)
        with open(OPERATIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Junction / 共享技能 ─────────────────────────────────────
def _is_junction(path):
    """判断路径是否为 NTFS junction（目录连接点）。
    用 GetFileAttributesW 检查 FILE_ATTRIBUTE_REPARSE_POINT（兼容 Python 3.11）。"""
    try:
        import ctypes
        GetFileAttributes = ctypes.windll.kernel32.GetFileAttributesW
        GetFileAttributes.argtypes = [ctypes.c_wchar_p]
        GetFileAttributes.restype = ctypes.c_uint32
        attrs = GetFileAttributes(str(path))
        if attrs == 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
            return False
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def _create_junction(link_path, target_path):
    """创建 NTFS junction（目录连接点），无需管理员权限。
    link_path: junction 路径（如 profile/skills/my_skill）
    target_path: 目标路径（如 shared-skills/my_skill）"""
    link_path = Path(link_path)
    target_path = Path(target_path).resolve()
    if link_path.exists():
        raise FileExistsError(f"目标位置已存在: {link_path}")
    if not target_path.exists():
        raise FileNotFoundError(f"共享技能目录不存在: {target_path}")
    # 使用 PowerShell New-Item -ItemType Junction 创建（junction 无需管理员权限）
    import subprocess
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"New-Item -ItemType Junction -Path '{link_path}' -Target '{target_path}'"],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise RuntimeError(f"创建 junction 失败: {result.stderr.strip() or result.stdout.strip()}")
    return True


def _remove_junction(link_path):
    """删除 junction 本身（不删除目标内容）。
    用 Win32 RemoveDirectoryW 直接删除 reparse point，安全且不递归目标。"""
    link_path = Path(link_path)
    if not _is_junction(link_path):
        raise ValueError(f"不是 junction: {link_path}")
    import ctypes
    RemoveDirectory = ctypes.windll.kernel32.RemoveDirectoryW
    RemoveDirectory.argtypes = [ctypes.c_wchar_p]
    RemoveDirectory.restype = ctypes.c_bool
    if not RemoveDirectory(str(link_path)):
        err = ctypes.windll.kernel32.GetLastError()
        raise RuntimeError(f"删除 junction 失败 (Win32 error {err}): {link_path}")
    return True


def _get_junction_target(path):
    """获取 junction 的目标路径（用 fsutil 解析 reparse point）"""
    import subprocess
    try:
        result = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(path)],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        # 输出含 "Substitute Name: <程序所在目录>/AAAHermesHub/shared-skills/<skill>"
        for line in result.stdout.splitlines():
            if "Substitute Name:" in line:
                target = line.split("Substitute Name:", 1)[1].strip()
                # 去掉可能的 \??\ 前缀
                if target.startswith("\\??\\"):
                    target = target[4:]
                return Path(target)
    except Exception:
        pass
    return None


# ── Skill 解析 ────────────────────────────────────────────
def parse_skill_frontmatter(content):
    """解析 SKILL.md 的 YAML frontmatter，返回 (meta_dict, body_str)"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    fm_text = parts[1].strip()
    body = parts[2].lstrip("\n")
    meta = {}
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # 去掉引号
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            meta[key] = val
    return meta, body


def _scan_skills_dir(skills_dir, source):
    """扫描目录下所有 SKILL.md（用 os.walk，支持 builtin 嵌套结构 category/skill）。
    source 标记来源 (user/builtin)。builtin 只读。跳过路径中含 . 或 _ 开头段的目录。
    用 os.walk + onerror 跳过失效的 junction/不可访问的目录（避免 rglob 遇到断链崩溃）。"""
    if not skills_dir.exists():
        return []
    skills = []
    # 读取 .usage.json（仅 user 目录有）
    usage_data = _read_usage_json(skills_dir) if source == "user" else {}
    # 用 os.walk 安全收集 SKILL.md（onerror 跳过失效 junction）
    skill_mds = []
    for root, dirs, files in os.walk(skills_dir, onerror=lambda e: None):
        # 原地过滤 dirs，跳过 . 或 _ 开头的目录（不让 os.walk 递归进去）
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("_")]
        if "SKILL.md" in files:
            skill_mds.append(Path(root) / "SKILL.md")
    for skill_md in sorted(skill_mds):
        d = skill_md.parent
        rel = d.relative_to(skills_dir)
        # 跳过 .archive / .hub / .trash / _internal 等隐藏/内部目录
        if any(part.startswith(".") or part.startswith("_") for part in rel.parts):
            continue
        content, err = read_file_safe(skill_md)
        if err:
            continue
        meta, body = parse_skill_frontmatter(content)
        # SKILL.md 内容哈希，用于判断 user 是否修改过同名内置技能
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        # 统计子文件
        sub_files = []
        for sub in d.rglob("*"):
            if sub.is_file() and sub.name != "SKILL.md" and not sub.name.startswith("."):
                rel_sub = sub.relative_to(d)
                if any(part.startswith(".") or part.startswith("_") for part in rel_sub.parts):
                    continue
                sub_files.append(str(rel_sub).replace("\\", "/"))
        # category 优先从路径提取（支持 security/skill 嵌套），frontmatter 作为 fallback
        path_category = str(rel.parent).replace("\\", "/") if str(rel.parent) != "." else ""
        fm_category = ""
        if isinstance(meta.get("metadata"), dict):
            fm_category = meta.get("metadata", {}).get("hermes", {}).get("category", "")
        # 判定是否为共享：skill_dir 自身是 junction，或祖先目录是 junction（分类级 junction）
        is_shared = _is_junction(d) if source == "user" else False
        if not is_shared and source == "user":
            current = d.parent
            while current != skills_dir and current.parent != current:
                if _is_junction(current):
                    is_shared = True
                    break
                current = current.parent
        # 从 .usage.json 读取启用状态
        cat = path_category or fm_category
        usage_key = _usage_key(d.name, cat)
        usage_entry = usage_data.get(usage_key) or usage_data.get(d.name, {})
        skill_state = usage_entry.get("state", "active") if usage_entry else "active"
        skills.append({
            "name": d.name,
            "description": meta.get("description", ""),
            "version": meta.get("version", ""),
            "category": cat,
            "path": str(d),
            "skill_md_path": str(skill_md),
            "content_size": len(content),
            "content_hash": content_hash,
            "sub_files": sub_files,
            "modified": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "source": "shared" if is_shared else source,
            "location": source,  # 物理位置：user=在profile/skills/，builtin=在hermes-agent/skills/
            "enabled": skill_state == "active",
        })
    return skills


def list_skills(profile_name):
    """列出某个 profile 的所有 skill = 用户自定义 skills + 内置 skills。

    同名去重（user 优先覆盖 builtin），并细分来源：
      - source="builtin": 仅内置库有；或 user 有同名但内容与 builtin 完全一致（未修改的副本）
      - source="user":    user 与 builtin 同名且 SKILL.md 内容不同（用户修改过内置技能）
      - source="custom":  user 独有（builtin 没有，用户自建或新装）

    builtin 扫描结果缓存（只读不变），避免 polling 时重复 rglob。
    注意：builtin 缓存对象共享，返回时浅拷贝避免被前端修改污染缓存。"""
    global _builtin_skills_cache
    user = _scan_skills_dir(get_skills_dir(profile_name), "user")
    if _builtin_skills_cache is None:
        _builtin_skills_cache = _scan_skills_dir(BUILTIN_SKILLS_DIR, "builtin")
    builtin = _builtin_skills_cache
    # builtin name → skill 映射（含 content_hash 用于判断是否修改过）
    builtin_by_name = {s["name"].lower(): s for s in builtin}
    user_names = set()
    result = []
    for s in user:
        name_lower = s["name"].lower()
        user_names.add(name_lower)
        # 共享技能（junction）：保持 shared 标记，不与 builtin 比较
        if s.get("source") == "shared":
            result.append(s)
            continue
        if name_lower in builtin_by_name:
            b = builtin_by_name[name_lower]
            # 比较 SKILL.md 哈希：内容相同 → 未修改的内置副本（标 builtin）；
            # 内容不同 → 用户修改过（标 user，显示"内置→用户"）
            if s.get("content_hash") and b.get("content_hash") and s["content_hash"] == b["content_hash"]:
                s["source"] = "builtin"
            else:
                s["source"] = "user"
        else:
            s["source"] = "custom"
        result.append(s)
    # 只展示 profile/skills 目录中实际存在的 skill，不追加未安装的内置技能
    # （删除后即从列表消失，避免"删除变内置"问题；内置技能通过技能中心安装）
    return result


def _validate_skill_name(skill_name):
    """严格校验 skill 名：仅允许字母数字及 -_.，禁止路径分隔符和 .. 防穿越"""
    if not skill_name:
        return False
    if skill_name in (".", ".."):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", skill_name))


def _resolve_skill_dir(profile_name, skill_name):
    """定位 skill 目录：先查用户 skills，再查内置 skills（均支持 category/skill 嵌套）。
    返回 (dir, source) 或 (None, None)。

    注意：用户 skills 目录（profile/skills）可能含分类子目录，如
    skills/security/blindkey-credential-vault/，不能用 skills_dir/skill_name
    直接拼接（会漏掉 category 层），必须用 rglob 查 <skill_name>/SKILL.md。
    rglob 可能因断链 junction 抛 OSError，用 try/except 保护。
    同名 skill 可能存在多个（如顶层 junction + superpowers/skills/ 下的副本），
    优先返回非 junction 的匹配（junction 版本已是共享，用户想抽取的是独立副本）。"""
    user_skills = get_skills_dir(profile_name)
    if user_skills.exists():
        try:
            matches = []
            for p in user_skills.rglob(f"{skill_name}/SKILL.md"):
                rel = p.parent.relative_to(user_skills)
                if any(part.startswith(".") or part.startswith("_") for part in rel.parts):
                    continue
                matches.append(p.parent)
            if matches:
                # 优先返回非 junction 的匹配（junction = 已共享，用户想抽取独立副本）
                for m in matches:
                    if not _is_junction(m):
                        # 还要检查祖先不是 junction（分类级 junction 下的不算独立副本）
                        current = m.parent
                        is_under_junction = False
                        while current != user_skills and current.parent != current:
                            if _is_junction(current):
                                is_under_junction = True
                                break
                            current = current.parent
                        if not is_under_junction:
                            return m, "user"
                # 所有匹配都是 junction 或在分类级 junction 下 → 返回第一个
                return matches[0], "user"
        except OSError:
            pass  # 断链 junction 导致 rglob 失败，跳过
    # builtin：rglob 查 <skill_name>/SKILL.md（支持 category/skill 嵌套）
    if BUILTIN_SKILLS_DIR.exists():
        try:
            for p in BUILTIN_SKILLS_DIR.rglob(f"{skill_name}/SKILL.md"):
                rel = p.parent.relative_to(BUILTIN_SKILLS_DIR)
                if any(part.startswith(".") or part.startswith("_") for part in rel.parts):
                    continue
                return p.parent, "builtin"
        except OSError:
            pass
    return None, None


def read_skill_file(profile_name, skill_name, file_path="SKILL.md"):
    """读取 skill 内的文件（支持用户/内置 skill）"""
    if not _validate_skill_name(skill_name):
        return None, "invalid skill name"
    skill_dir, source = _resolve_skill_dir(profile_name, skill_name)
    if not skill_dir:
        return None, "skill not found"
    # 安全检查：解析后目标必须落在 skill_dir 内（路径边界判断，非字符串前缀）
    target = (skill_dir / file_path).resolve()
    skill_dir_resolved = skill_dir.resolve()
    try:
        target.relative_to(skill_dir_resolved)
    except ValueError:
        return None, "path traversal blocked"
    if not target.exists():
        return None, "file not found"
    content, err = read_file_safe(target)
    if err:
        return None, err
    return {"content": content, "path": str(target), "source": source}, None


def save_skill_file(profile_name, skill_name, file_path, content):
    """保存 skill 内的文件。返回 (err, warn)：
    err 非 None 表示写入失败；warn 非 None 表示备份失败但写入成功。
    内置 skill (builtin) 只读，拒绝写入。"""
    if not _validate_skill_name(skill_name):
        return "invalid skill name", None
    skill_dir, source = _resolve_skill_dir(profile_name, skill_name)
    if not skill_dir:
        return "skill not found", None
    if source == "builtin":
        return "内置 skill 只读，不可编辑（如需修改请复制到用户 skills）", None
    target = (skill_dir / file_path).resolve()
    skill_dir_resolved = skill_dir.resolve()
    try:
        target.relative_to(skill_dir_resolved)
    except ValueError:
        return "path traversal blocked", None
    # 备份失败不阻止保存（原子写入已防损坏），但记录警告
    backup_warn = make_backup(target)
    try:
        atomic_write_text(target, content)
        return None, backup_warn
    except Exception as e:
        return str(e), None


# ── 文件监听 (轮询 mtime+size) ────────────────────────────
_watch_state = {}


def get_file_signature(path):
    try:
        if not path.exists():
            return None
        stat = path.stat()
        return (stat.st_mtime, stat.st_size)
    except Exception:
        return None


def init_watch():
    for profile in get_profile_names():
        for file_key in PROFILE_FILES:
            fp = get_file_path(profile, file_key)
            key = f"{profile}:{file_key}"
            _watch_state[key] = get_file_signature(fp)


def check_changes():
    changes = []
    for profile in get_profile_names():
        for file_key in PROFILE_FILES:
            fp = get_file_path(profile, file_key)
            key = f"{profile}:{file_key}"
            current = get_file_signature(fp)
            if _watch_state.get(key) != current:
                changes.append({"profile": profile, "file": file_key})
                _watch_state[key] = current
    return changes


# ── API 路由 ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/info")
def api_info():
    """返回 HERMES_HOME 信息"""
    return jsonify({
        "hermes_home": str(HERMES_HOME),
        "source": HERMES_HOME_SOURCE,
        "detected": HERMES_HOME_SOURCE != "fallback",
        "profiles_dir": str(PROFILES_DIR),
    })


@app.route("/api/hermes-home", methods=["PUT"])
def api_set_hermes_home():
    """手动设置 HERMES_HOME"""
    data = request.get_json()
    path = data.get("path", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"error": "path does not exist"}), 400
    if not (p / "config.yaml").exists() and not (p / "profiles").exists():
        return jsonify({"error": "not a valid hermes home (no config.yaml or profiles/)"}), 400
    # 保存到配置文件（写在 exe 所在目录，onefile 模式下才能持久化）
    config_file = get_app_dir() / ".hermes_home"
    try:
        atomic_write_text(config_file, str(p))
    except Exception as e:
        return jsonify({"error": f"cannot save config: {e}"}), 500
    return jsonify({"ok": True, "message": "Please restart the server to apply the new HERMES_HOME"})


# ── 主题持久化（.hermes_theme，跨重启保活） ───────────────
# 说明：pywebview 的 localStorage 在某些环境下重启后丢失，
# 改用后端文件持久化，前端启动时读取 API 应用主题。
THEME_FILE = ".hermes_theme"


@app.route("/api/theme")
def api_get_theme():
    f = get_app_dir() / THEME_FILE
    theme = "dark"
    if f.exists():
        try:
            v = f.read_text(encoding="utf-8-sig").strip().lower()
            if v in ("dark", "light"):
                theme = v
        except Exception:
            pass
    return jsonify({"theme": theme})


@app.route("/api/theme", methods=["PUT"])
def api_set_theme():
    data = request.get_json() or {}
    theme = (data.get("theme") or "").strip().lower()
    if theme not in ("dark", "light"):
        return jsonify({"error": "invalid theme"}), 400
    try:
        atomic_write_text(get_app_dir() / THEME_FILE, theme)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "theme": theme})


@app.route("/api/profiles")
def api_profiles():
    profiles = []
    for name in get_profile_names():
        files = {}
        for fk, info in PROFILE_FILES.items():
            fp = get_file_path(name, fk)
            sig = get_file_signature(fp)
            files[fk] = {
                "exists": fp.exists(),
                "size": sig[1] if sig else 0,
                "mtime": sig[0] if sig else None,
                "lang": info["lang"],
                "sub": info["sub"],
            }
        # skill 数量（user + builtin，与 skills 列表口径一致）
        skill_count = len(list_skills(name))
        # 工具集 + MCP 统计
        toolset_total, toolset_enabled = _count_toolsets(name)
        mcp_count = _count_mcp(name)
        profiles.append({"name": name, "files": files, "skill_count": skill_count,
                          "toolset_total": toolset_total, "toolset_enabled": toolset_enabled,
                          "mcp_count": mcp_count})
    return jsonify({"profiles": profiles})


@app.route("/api/profile/<profile_name>/<file_key>")
def api_read_file(profile_name, file_key):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, file_key)
    if not fp:
        return jsonify({"error": "invalid file key"}), 400
    content, err = read_file_safe(fp)
    if err:
        return jsonify({"error": err}), 500
    return jsonify({
        "content": content,
        "path": str(fp),
        "exists": fp.exists(),
        "lang": PROFILE_FILES[file_key]["lang"],
    })


@app.route("/api/profile/<profile_name>/<file_key>", methods=["PUT"])
def api_save_file(profile_name, file_key):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, file_key)
    if not fp:
        return jsonify({"error": "invalid file key"}), 400
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "missing content"}), 400
    content = data["content"]
    try:
        warn = make_backup(fp)
        atomic_write_text(fp, content)
        key = f"{profile_name}:{file_key}"
        _watch_state[key] = get_file_signature(fp)
        _log_operation("save_file", profile=profile_name, file=file_key, path=str(fp))
        resp = {"ok": True, "path": str(fp)}
        if warn:
            resp["warning"] = warn
        return jsonify(resp)
    except Exception as e:
        _log_operation("save_file", result="error", profile=profile_name, file=file_key, error=str(e))
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/<profile_name>", methods=["POST"])
def api_create_profile(profile_name):
    """新建 profile 已禁用：profile 创建涉及目录结构/配置模板初始化，
    手动创建易出问题。如需新建请用 hermes CLI 或手动操作 profiles/ 目录。"""
    return jsonify({"error": "已禁用 Profile 新建功能。如需新建请用 hermes CLI 或手动操作 profiles/ 目录。"}), 403


@app.route("/api/profile/<profile_name>", methods=["DELETE"])
def api_delete_profile(profile_name):
    """删除 profile 已禁用：hermes 运行时可能正在使用某 profile，
    且 sessions 表无可靠激活 profile 标记，误删风险高。
    如需删除请手动操作 profiles/ 目录。"""
    return jsonify({"error": "为安全起见已禁用 Profile 删除功能。如需删除请手动操作 profiles/ 目录。"}), 403


@app.route("/api/profile/<profile_name>/<file_key>/copy", methods=["POST"])
def api_copy_file(profile_name, file_key):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    data = request.get_json()
    source_profile = data.get("source_profile")
    if not source_profile or source_profile not in get_profile_names():
        return jsonify({"error": "invalid source profile"}), 400
    src_fp = get_file_path(source_profile, file_key)
    dst_fp = get_file_path(profile_name, file_key)
    if not src_fp.exists():
        return jsonify({"error": "source file not found"}), 404
    warn = make_backup(dst_fp)
    dst_fp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_fp, dst_fp)
    key = f"{profile_name}:{file_key}"
    _watch_state[key] = get_file_signature(dst_fp)
    resp = {"ok": True}
    if warn:
        resp["warning"] = warn
    return jsonify(resp)


# ── Skill API ─────────────────────────────────────────────

@app.route("/api/profile/<profile_name>/skills")
def api_list_skills(profile_name):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    skills = list_skills(profile_name)
    return jsonify({"skills": skills, "count": len(skills)})


@app.route("/api/profile/<profile_name>/skills/<skill_name>/<path:file_path>")
def api_read_skill_file(profile_name, skill_name, file_path):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    result, err = read_skill_file(profile_name, skill_name, file_path)
    if err:
        return jsonify({"error": err}), 404
    # 推断语言
    ext = Path(file_path).suffix.lstrip(".")
    lang_map = {"md": "markdown", "py": "python", "sh": "bash", "js": "javascript",
                "ts": "typescript", "json": "json", "yaml": "yaml", "yml": "yaml"}
    lang = lang_map.get(ext, "text")
    result["lang"] = lang
    result["skill_name"] = skill_name
    result["file_path"] = file_path
    return jsonify(result)


@app.route("/api/profile/<profile_name>/skills/<skill_name>/<path:file_path>", methods=["PUT"])
def api_save_skill_file(profile_name, skill_name, file_path):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "missing content"}), 400
    err, warn = save_skill_file(profile_name, skill_name, file_path, data["content"])
    if err:
        _log_operation("save_skill", result="error", profile=profile_name, skill=skill_name, file=file_path, error=err)
        return jsonify({"error": err}), 500
    _log_operation("save_skill", profile=profile_name, skill=skill_name, file=file_path)
    resp = {"ok": True}
    if warn:
        resp["warning"] = warn
    return jsonify(resp)


def _cleanup_empty_parent(skill_dir, skills_dir):
    """删除 skill 后检查父目录（分类目录）是否为空，为空则清理。
    只清理 skills_dir 的直接子目录（分类目录），不递归向上清理。
    跳过 junction 和含隐藏文件（.trash 等）的目录。"""
    try:
        parent = Path(skill_dir).parent
        if parent == skills_dir or parent.parent != skills_dir:
            return  # 不是分类目录（是 skills_dir 本身或更深层）
        if _is_junction(parent):
            return  # 分类级 junction，不删
        # 检查是否为空（忽略 . 开头的隐藏文件/目录）
        remaining = [p for p in parent.iterdir() if not p.name.startswith(".")]
        if not remaining:
            shutil.rmtree(str(parent))
    except Exception:
        pass


@app.route("/api/profile/<profile_name>/skills/<skill_name>", methods=["DELETE"])
def api_delete_skill(profile_name, skill_name):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    skill_dir, source = _resolve_skill_dir(profile_name, skill_name)
    if not skill_dir:
        return jsonify({"error": "skill not found"}), 404
    if source == "builtin":
        return jsonify({"error": "内置 skill 只读，不可删除"}), 403
    skills_dir = get_skills_dir(profile_name)
    # 检查祖先是否为 junction（分类级共享引用）：
    # 此时 skill_dir 实际在共享库内，直接 rmtree 会删共享库内容。
    # 删除整个分类 junction（不影响共享库），而非单个子 skill。
    current = Path(skill_dir).parent
    while current != skills_dir and current.parent != current:
        if _is_junction(current):
            try:
                cat_name = current.name
                _remove_junction(str(current))
            except Exception as e:
                _log_operation("delete_skill", result="error", profile=profile_name, skill=skill_name, error=str(e))
                return jsonify({"error": f"删除分类共享引用失败: {e}"}), 500
            _log_operation("delete_skill", profile=profile_name, skill=skill_name, shared=True,
                           detail=f"删除分类级 junction '{cat_name}'（共享库内容未动）")
            # 同步 .usage.json：移除该分类下所有子 skill 的条目
            _remove_skill_usage(profile_name, skill_name, cat_name)
            return jsonify({"ok": True, "shared": True, "category_junction": cat_name,
                            "message": f"'{skill_name}' 属于分类级共享引用 '{cat_name}'，已删除整个分类引用（共享库内容未删除）"})
        current = current.parent
    # 获取 skill 的分类（用于 .usage.json 键）
    try:
        rel = Path(skill_dir).relative_to(skills_dir)
        del_category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
    except ValueError:
        del_category = ""
    # 共享 skill（junction）：只删除 junction，不影响共享库内容
    if _is_junction(skill_dir):
        try:
            _remove_junction(str(skill_dir))
        except Exception as e:
            _log_operation("delete_skill", result="error", profile=profile_name, skill=skill_name, error=str(e))
            return jsonify({"error": f"删除共享引用失败: {e}"}), 500
        _cleanup_empty_parent(skill_dir, skills_dir)
        _remove_skill_usage(profile_name, skill_name, del_category)
        _log_operation("delete_skill", profile=profile_name, skill=skill_name, shared=True,
                       detail="仅删除 junction，共享库内容未动")
        return jsonify({"ok": True, "shared": True,
                        "message": f"已删除共享引用 '{skill_name}'（共享库内容未删除，其他 profile 不受影响）"})
    # 非共享 skill：直接删除
    shutil.rmtree(str(skill_dir))
    _cleanup_empty_parent(skill_dir, skills_dir)
    _remove_skill_usage(profile_name, skill_name, del_category)
    _log_operation("delete_skill", profile=profile_name, skill=skill_name)
    return jsonify({"ok": True})


@app.route("/api/profile/<profile_name>/skills/<skill_name>/copy", methods=["POST"])
def api_copy_skill(profile_name, skill_name):
    """从其他 profile（或内置库）复制 skill 到当前 profile。
    body: {source_profile} — 源 profile 名；若源 profile 无此 skill 则回退查内置库。"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    data = request.get_json() or {}
    source_profile = data.get("source_profile")
    if not source_profile or source_profile not in get_profile_names():
        return jsonify({"error": "invalid source profile"}), 400
    # 源：先查源 profile 的用户 skills，再查内置库（支持嵌套）
    src, src_source = _resolve_skill_dir(source_profile, skill_name)
    if not src:
        return jsonify({"error": "source skill not found"}), 404
    dst = get_skills_dir(profile_name) / skill_name
    if dst.exists():
        # 备份
        trash = dst.parent / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{skill_name}_{ts}")
    shutil.copytree(src, dst)
    _add_skill_usage(profile_name, skill_name)
    _log_operation("copy_skill", profile=profile_name, skill=skill_name, source_profile=source_profile, source=src_source)
    return jsonify({"ok": True, "source": src_source})


@app.route("/api/profile/<profile_name>/skills/<skill_name>/copy-to", methods=["POST"])
def api_copy_skill_to(profile_name, skill_name):
    """把当前 profile 的 skill 复制或移动到目标 profile。
    body: {target_profile, move} — move=true 时移动（仅用户 skill 可移动，builtin 只复制）。"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    data = request.get_json() or {}
    target_profile = data.get("target_profile")
    move = bool(data.get("move", False))
    if not target_profile or target_profile not in get_profile_names():
        return jsonify({"error": "invalid target profile"}), 400
    if target_profile == profile_name:
        return jsonify({"error": "目标 profile 与源相同"}), 400
    # 源：先用户再内置（支持 builtin 嵌套结构）
    src_user = get_skills_dir(profile_name) / skill_name
    src, src_source = _resolve_skill_dir(profile_name, skill_name)
    if not src:
        return jsonify({"error": "source skill not found"}), 404
    dst = get_skills_dir(target_profile) / skill_name
    if dst.exists():
        trash = dst.parent / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{skill_name}_{ts}")
    shutil.copytree(src, dst)
    moved = False
    # 移动：仅当源是用户 skill 时删除源；内置 skill 不删（只复制）
    if move and src_source == "user" and src_user.exists():
        trash = src_user.parent / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(src_user), trash / f"{skill_name}_{ts}")
        moved = True
    _add_skill_usage(target_profile, skill_name)
    if moved:
        _remove_skill_usage(profile_name, skill_name)
    _log_operation("copy_skill_to", profile=profile_name, skill=skill_name,
                   target_profile=target_profile, move=moved, source=src_source)
    return jsonify({"ok": True, "moved": moved, "target_profile": target_profile,
                     "source": src_source})


@app.route("/api/profile/<profile_name>/skills/<skill_name>/state", methods=["PUT"])
def api_set_skill_state(profile_name, skill_name):
    """切换 skill 的启用/禁用状态（写入 .usage.json 的 state 字段）。
    body: {enabled: true/false}"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", True))
    # 查找 skill 目录以获取分类
    skill_dir, source = _resolve_skill_dir(profile_name, skill_name)
    if not skill_dir:
        return jsonify({"error": "skill not found"}), 404
    skills_dir = get_skills_dir(profile_name)
    category = ""
    try:
        rel = Path(skill_dir).relative_to(skills_dir)
        category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
    except ValueError:
        pass
    # 更新 .usage.json
    usage = _read_usage_json(skills_dir)
    key = _usage_key(skill_name, category)
    now = datetime.now().astimezone().isoformat()
    if key not in usage:
        # 兼容旧数据：尝试纯 skill_name
        if skill_name in usage:
            key = skill_name
        else:
            usage[key] = {
                "archived_at": None,
                "created_at": now,
                "created_by": None,
                "last_patched_at": None,
                "last_used_at": now,
                "last_viewed_at": now,
                "patch_count": 0,
                "pinned": False,
                "state": "active",
                "use_count": 0,
                "view_count": 0,
            }
    entry = usage[key]
    if enabled:
        entry["state"] = "active"
        entry["archived_at"] = None
    else:
        entry["state"] = "archived"
        entry["archived_at"] = now
    _write_usage_json(skills_dir, usage)
    _log_operation("set_skill_state", profile=profile_name, skill=skill_name,
                   enabled=enabled, category=category)
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/poll")
def api_poll():
    changes = check_changes()
    return jsonify({"changes": changes})


# ── 共享技能 API ────────────────────────────────────────────

def _find_skill_dir_in(base_dir, skill_name):
    """在 base_dir 中查找 skill 目录（支持 category/skill_name 嵌套）。
    返回 (skill_dir, category) 或 (None, "")。category 如 "security" 或 ""（顶层）"""
    if not base_dir.exists():
        return None, ""
    # 顶层
    top = base_dir / skill_name
    if top.is_dir():
        return top, ""
    # 嵌套分类
    for p in base_dir.rglob(skill_name):
        if p.is_dir() and p.name == skill_name:
            try:
                rel = p.parent.relative_to(base_dir)
                category = "/".join(rel.parts) if rel.parts else ""
            except ValueError:
                category = ""
            if (p / "SKILL.md").exists() or _is_junction(p):
                return p, category
    return None, ""


@app.route("/api/skills/shared/list")
def api_shared_list():
    """列出共享技能库（AAAHermesHub/shared-skills/）里的所有 skill，
    并统计每个 skill 被多少 profile 引用（junction）。
    同时返回分类目录信息，支持分类级引用。"""
    if not SHARED_SKILLS_DIR.exists():
        return jsonify({"skills": [], "categories": []})
    skills = []
    # 收集所有 profile 的 junction 引用
    # skill_ref_counts: skill 名 → 引用数（skill 级 junction）
    # cat_ref_counts: 分类名 → 引用数（分类级 junction，目录本身是 junction 但无 SKILL.md）
    skill_ref_counts = {}
    cat_ref_counts = {}
    for pname in get_profile_names():
        sdir = get_skills_dir(pname)
        if not sdir.exists():
            continue
        for d in sdir.rglob("*"):
            if not (d.is_dir() and _is_junction(d)):
                continue
            if (d / "SKILL.md").exists():
                skill_ref_counts[d.name] = skill_ref_counts.get(d.name, 0) + 1
            else:
                # 分类级 junction：目录是 junction 但无 SKILL.md，子目录有 SKILL.md
                try:
                    has_sub = any((d / sub / "SKILL.md").exists() for sub in os.listdir(d))
                except Exception:
                    has_sub = False
                if has_sub:
                    cat_ref_counts[d.name] = cat_ref_counts.get(d.name, 0) + 1
    # 扫描共享库（支持 category/skill_name 嵌套）
    categories = {}  # category → info
    for skill_md in sorted(SHARED_SKILLS_DIR.rglob("SKILL.md")):
        d = skill_md.parent
        rel = d.relative_to(SHARED_SKILLS_DIR)
        if any(part.startswith(".") or part.startswith("_") for part in rel.parts):
            continue
        category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
        content, err = read_file_safe(skill_md)
        if err:
            continue
        meta, _ = parse_skill_frontmatter(content)
        skills.append({
            "name": d.name,
            "category": category,
            "description": meta.get("description", ""),
            "modified": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "ref_count": skill_ref_counts.get(d.name, 0),
        })
        if category:
            if category not in categories:
                categories[category] = {"name": category, "skills": [], "ref_count": 0}
            categories[category]["skills"].append(d.name)
    # 填充分类引用计数和子 skill 数
    for cat_info in categories.values():
        cat_info["ref_count"] = cat_ref_counts.get(cat_info["name"], 0)
        cat_info["skill_count"] = len(cat_info["skills"])
    return jsonify({
        "skills": skills,
        "categories": sorted(categories.values(), key=lambda c: c["name"]),
    })


def _skill_content_hash(p):
    """计算 skill 目录的内容哈希（按相对路径排序后聚合所有非隐藏文件）。
    用于抽取冲突时判断本地与共享库版本是否一致。"""
    p = Path(p)
    h = hashlib.md5()
    for f in sorted(p.rglob("*")):
        if f.is_file() and not f.name.startswith(".") and not any(part.startswith(".") or part.startswith("_") for part in f.relative_to(p).parts):
            try:
                h.update(str(f.relative_to(p)).replace("\\", "/").encode())
                h.update(f.read_bytes())
            except Exception:
                pass
    return h.hexdigest()


def _extract_install_junction(profile, skill_name, category, source, shared_dir, skill_dir):
    """抽取流程的"替换本地为 junction"步骤。
    - 用户 skill：移到 profile/skills/.trash/，原位置建 junction
    - 内置 skill：不动 hermes-agent/skills/，在 profile/skills/<category>/<name> 建 junction 遮蔽内置
      （profile 级 junction 优先级高于 builtin，hermes-agent 内置保持原状供其他 profile 使用）"""
    skills_dir = get_skills_dir(profile)
    if source == "builtin":
        target = skills_dir / category / skill_name if category else skills_dir / skill_name
        if target.exists():
            raise RuntimeError(f"profile 已存在同名目录 {target}，无法创建 junction 遮蔽内置（请先抽取用户版本或删除该目录）")
        target.parent.mkdir(parents=True, exist_ok=True)
        _create_junction(str(target), str(shared_dir))
    else:
        # 用户 skill：移到 .trash 后建 junction（与 delete_skill / copy-to 一致的 .trash 约定）
        trash = skill_dir.parent / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(skill_dir), str(trash / f"{skill_name}_{ts}"))
        _create_junction(str(skill_dir), str(shared_dir))


@app.route("/api/skills/shared/extract", methods=["POST"])
def api_shared_extract():
    """抽取 profile 的 skill 到共享库，原位置替换为 junction（共同进化）。
    body: {profile, skill_name, force?}
    - 用户 skill：移到 .trash，原位置建 junction
    - 内置 skill：复制到共享库，在 profile/skills/ 下建 junction 遮蔽内置（hermes-agent 不动）
    - 冲突处理（共享库已存在同名同分类）：
      * 内容相同 → 自动替换本地（移到 .trash + 建 junction）
      * 内容不同 + force=false → 返回 409 differs=true，由前端提示
      * 内容不同 + force=true → 用户确认放弃本地版本，本地移到 .trash + 建 junction（共享库版本保留）
    保留分类结构：skills/security/X → shared-skills/security/X"""
    data = request.get_json() or {}
    profile = data.get("profile")
    skill_name = data.get("skill_name")
    force = bool(data.get("force", False))
    if not profile or profile not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    skill_dir, source = _resolve_skill_dir(profile, skill_name)
    if not skill_dir:
        return jsonify({"error": "skill not found"}), 404
    # 已是共享技能（skill_dir 自身是 junction）
    if _is_junction(skill_dir):
        return jsonify({"error": "该技能已是共享 junction"}), 400
    # 检测分类级 junction：skill_dir 的祖先目录是 junction → 已通过分类级 junction 共享
    skills_dir = get_skills_dir(profile)
    current = skill_dir.parent
    while current != skills_dir and current.parent != current:
        if _is_junction(current):
            return jsonify({"error": "该技能已通过分类级 junction 共享", "category_junction": str(current)}), 400
        current = current.parent
    # 获取分类：用户 skill 相对 profile/skills/，内置 skill 相对 hermes-agent/skills/
    if source == "builtin":
        try:
            rel = skill_dir.relative_to(BUILTIN_SKILLS_DIR)
            category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
        except ValueError:
            category = ""
    else:
        try:
            rel = skill_dir.relative_to(skills_dir)
            category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
        except ValueError:
            category = ""
    # 共享库目标（保留分类结构）
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    shared_dir = SHARED_SKILLS_DIR / category / skill_name if category else SHARED_SKILLS_DIR / skill_name
    try:
        if shared_dir.exists():
            # 内容比对决定是自动合并还是需要用户确认
            local_hash = _skill_content_hash(skill_dir)
            shared_hash = _skill_content_hash(shared_dir)
            if local_hash == shared_hash:
                # 内容相同：直接替换本地为 junction（共享库不动，本地备份到 .trash）
                _extract_install_junction(profile, skill_name, category, source, shared_dir, skill_dir)
                _log_operation("extract", profile=profile, skill=skill_name, source=source,
                               category=category, shared_dir=str(shared_dir),
                               conflict="same_content", detail="内容相同，本地移到 .trash + 建 junction")
                return jsonify({"ok": True, "message": f"'{skill_name}' 内容与共享库一致，已替换为 junction（原本地版本备份到 .trash/）"})
            # 内容不同：需用户确认是否放弃本地版本
            if not force:
                return jsonify({"error": "conflict", "differs": True,
                                "message": f"共享库已存在 '{skill_name}' 且内容不同。force=true 表示确认放弃本地版本（移到 .trash），用共享库版本建立 junction"}), 409
            # force=true：用户已确认放弃本地版本（共享库保留原内容，本地移到 .trash + 建 junction）
            _extract_install_junction(profile, skill_name, category, source, shared_dir, skill_dir)
            _log_operation("extract", profile=profile, skill=skill_name, source=source,
                           category=category, shared_dir=str(shared_dir),
                           conflict="diff_content", force=True,
                           detail="内容不同，用户确认放弃本地版本，本地移到 .trash + 建 junction")
            return jsonify({"ok": True, "message": f"'{skill_name}' 已替换为共享库版本（本地原版本已移动到 .trash/，可恢复；共享库内容未改动）"})
        # 无冲突：复制本地到共享库，替换本地为 junction
        shared_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(skill_dir), str(shared_dir))
        _extract_install_junction(profile, skill_name, category, source, shared_dir, skill_dir)
        _log_operation("extract", profile=profile, skill=skill_name, source=source,
                       category=category, shared_dir=str(shared_dir),
                       detail="无冲突，复制本地到共享库 + 本地建 junction")
    except Exception as e:
        _log_operation("extract", result="error", profile=profile, skill=skill_name,
                       error=str(e))
        return jsonify({"error": f"抽取失败: {e}"}), 500
    return jsonify({"ok": True, "message": f"已抽取 '{skill_name}' 到共享库，原位置已替换为 junction（所有引用此共享库的 profile 将共同进化）"})


@app.route("/api/skills/shared/link", methods=["POST"])
def api_shared_link():
    """从共享库引用 skill 到 profile（创建 junction）。
    body: {profile, skill_name, link_category?}
    - link_category=true: 引用整个分类目录（分类级 junction），所有子 skill 同步可用
    - 默认: 引用单个 skill（skill 级 junction）
    保留分类结构：shared-skills/security/X → skills/security/X"""
    data = request.get_json() or {}
    profile = data.get("profile")
    skill_name = data.get("skill_name")
    link_category = data.get("link_category", False)
    if not profile or profile not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    if link_category:
        # 分类级引用：shared-skills/<category>/ → profile/skills/<category>/
        shared_dir = SHARED_SKILLS_DIR / skill_name
        if not shared_dir.exists() or not shared_dir.is_dir():
            return jsonify({"error": f"共享库中不存在分类 '{skill_name}'"}), 404
        try:
            has_sub = any((shared_dir / sub / "SKILL.md").exists() for sub in os.listdir(shared_dir))
        except Exception:
            has_sub = False
        if not has_sub:
            return jsonify({"error": f"'{skill_name}' 不是分类目录（无子 skill）"}), 400
        target = get_skills_dir(profile) / skill_name
        if target.exists():
            return jsonify({"error": "conflict", "message": f"分类 '{skill_name}' 已存在于 {profile}，请先删除或重命名"}), 409
        try:
            _create_junction(str(target), str(shared_dir))
        except Exception as e:
            _log_operation("link_shared", result="error", profile=profile, skill=skill_name, error=str(e))
            return jsonify({"error": f"引用分类失败: {e}"}), 500
        sub_count = sum(1 for sub in os.listdir(shared_dir) if (shared_dir / sub / "SKILL.md").exists())
        # 为分类下所有子 skill 添加 .usage.json 条目
        for sub in os.listdir(shared_dir):
            if (shared_dir / sub / "SKILL.md").exists():
                _add_skill_usage(profile, sub, skill_name)
        _log_operation("link_shared", profile=profile, skill=skill_name,
                       shared_dir=str(shared_dir), target=str(target), category_level=True)
        return jsonify({"ok": True, "message": f"已引用分类 '{skill_name}'（{sub_count} 个子 skill）到 {profile}"})
    # 单个 skill 引用（原有逻辑）
    shared_dir, category = _find_skill_dir_in(SHARED_SKILLS_DIR, skill_name)
    if not shared_dir:
        return jsonify({"error": "共享库中不存在此 skill"}), 404
    target = get_skills_dir(profile) / category / skill_name if category else get_skills_dir(profile) / skill_name
    if target.exists():
        return jsonify({"error": "conflict", "message": f"'{skill_name}' 已存在于 {profile}，请先删除或重命名"}), 409
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _create_junction(str(target), str(shared_dir))
    except Exception as e:
        _log_operation("link_shared", result="error", profile=profile, skill=skill_name, error=str(e))
        return jsonify({"error": f"引用失败: {e}"}), 500
    _add_skill_usage(profile, skill_name, category)
    _log_operation("link_shared", profile=profile, skill=skill_name,
                   shared_dir=str(shared_dir), target=str(target))
    return jsonify({"ok": True, "message": f"已从共享库引用 '{skill_name}' 到 {profile}（修改共享库内容时同步生效）"})


@app.route("/api/skills/shared/unlink", methods=["POST"])
def api_shared_unlink():
    """解除共享：删除 junction，复制独立副本回 profile（断开共同进化）。
    body: {profile, skill_name}"""
    data = request.get_json() or {}
    profile = data.get("profile")
    skill_name = data.get("skill_name")
    if not profile or profile not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    # 在 profile 中查找 junction（支持嵌套分类）
    target, _ = _find_skill_dir_in(get_skills_dir(profile), skill_name)
    if not target or not _is_junction(target):
        return jsonify({"error": "该技能不是共享 junction"}), 400
    # 在共享库中查找源
    shared_dir, _ = _find_skill_dir_in(SHARED_SKILLS_DIR, skill_name)
    if not shared_dir:
        return jsonify({"error": "共享源不存在，无法复制独立副本"}), 404
    try:
        # 删除 junction（不删除共享库内容）
        _remove_junction(str(target))
        # 复制独立副本到原位置（保留分类目录）
        shutil.copytree(str(shared_dir), str(target))
    except Exception as e:
        _log_operation("unlink_shared", result="error", profile=profile, skill=skill_name, error=str(e))
        return jsonify({"error": f"解除共享失败: {e}"}), 500
    _log_operation("unlink_shared", profile=profile, skill=skill_name,
                   shared_dir=str(shared_dir), target=str(target),
                   detail="删除 junction，复制独立副本到 profile")
    return jsonify({"ok": True, "message": f"已解除 '{skill_name}' 的共享，现为独立副本（修改不再影响其他 profile）"})


@app.route("/api/skills/shared/delete", methods=["POST"])
def api_shared_delete():
    """从共享库删除 skill：移到 .trash（可恢复），并为所有引用的 profile 复制独立副本。
    body: {skill_name}"""
    data = request.get_json() or {}
    skill_name = data.get("skill_name")
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    # 在共享库中查找（支持嵌套分类）
    shared_dir, _ = _find_skill_dir_in(SHARED_SKILLS_DIR, skill_name)
    if not shared_dir:
        return jsonify({"error": "共享库中不存在此 skill"}), 404
    # 解除所有 profile 的 junction，并复制独立副本回 profile
    unlinked = []
    for pname in get_profile_names():
        target, _ = _find_skill_dir_in(get_skills_dir(pname), skill_name)
        if target and _is_junction(target):
            try:
                _remove_junction(str(target))
                # 复制独立副本到原位置（profile 不丢失 skill，变为独立版本）
                shutil.copytree(str(shared_dir), str(target))
                unlinked.append(pname)
            except Exception:
                pass
    # 移到 shared-skills/.trash/（可恢复，不直接删除）
    trash = SHARED_SKILLS_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = trash / f"{skill_name}_{ts}"
    try:
        shutil.move(str(shared_dir), str(dest))
    except Exception as e:
        _log_operation("delete_shared", result="error", skill=skill_name, error=str(e))
        return jsonify({"error": f"删除共享 skill 失败: {e}"}), 500
    _log_operation("delete_shared", skill=skill_name, shared_dir=str(dest),
                   unlinked_profiles=unlinked,
                   detail=f"移到 .trash/，{len(unlinked)} 个 profile 获得独立副本")
    return jsonify({"ok": True, "message": f"已从共享库删除 '{skill_name}'（移到 .trash/，可恢复），并为 {len(unlinked)} 个 profile 复制了独立副本: {', '.join(unlinked) if unlinked else '无'}"})


@app.route("/api/skills/fix-junctions", methods=["POST"])
def api_fix_junctions():
    """一键修复失效的共享技能 junction。
    当 AAAHermesHub 目录移动后，profile 中的 junction 可能指向旧路径导致失效。
    此接口检测所有指向 shared-skills 但目标已不存在的 junction，在新的
    SHARED_SKILLS_DIR 中查找同名技能并重新链接。"""
    fixed, details = _fix_shared_skill_junctions()
    _log_operation("fix_junctions", fixed=fixed, details=details)
    return jsonify({
        "ok": True,
        "fixed": fixed,
        "details": details,
        "message": f"修复了 {fixed} 个失效的 junction" if fixed
                   else "没有需要修复的 junction（所有链接正常）"
    })


@app.route("/api/profile/<name>/open-dir", methods=["POST"])
def api_open_profile_dir(name):
    """在文件管理器中打开 profile 根目录"""
    if name not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    path = get_profile_path(name)
    try:
        os.startfile(str(path))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": f"打开目录失败: {e}"}), 500


# ── 模型发现 API ──────────────────────────────────────────
import urllib.request as _urlreq
import urllib.error as _urlerr


@app.route("/api/profile/<profile_name>/discover-models/<int:cp_index>", methods=["POST"])
def api_discover_models(profile_name, cp_index):
    """请求 provider 的 /v1/models 端点获取模型列表。
    支持前端直接传入 provider 配置（未保存时也能探测）。"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    body = request.get_json(silent=True) or {}
    cp = body.get("provider")
    if not cp:
        # 回退：从磁盘文件读取
        fp = get_file_path(profile_name, "config.yaml")
        data, err = _load_yaml_file(fp)
        if err or data is None:
            return jsonify({"error": "cannot load config"}), 500
        cp_list = data.get("custom_providers", [])
        if cp_index < 0 or cp_index >= len(cp_list):
            return jsonify({"error": "invalid provider index"}), 400
        cp = cp_list[cp_index]
    base_url = str(cp.get("base_url", "")).rstrip("/")
    api_key = str(cp.get("api_key", ""))
    if not base_url:
        return jsonify({"error": "base_url is empty"}), 400
    # 协议白名单：仅允许 http/https，阻止 file:// / gopher:// 等危险协议
    try:
        parsed = urlparse(base_url)
    except Exception:
        return jsonify({"error": "invalid base_url"}), 400
    if parsed.scheme not in ("http", "https"):
        return jsonify({"error": f"scheme not allowed: {parsed.scheme or '(missing)'}"}), 400
    if not parsed.hostname:
        return jsonify({"error": "base_url missing host"}), 400

    # 构造 /v1/models 或 /models 请求
    models_url = f"{base_url}/models"
    if not models_url.endswith("/v1/models") and not "/v1/models" in models_url:
        if "/v1" not in models_url:
            models_url = f"{base_url}/v1/models"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = _urlreq.Request(models_url, headers=headers, method="GET")
        with _urlreq.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        # OpenAI 格式: {data: [{id: "model-name"}, ...]}
        models = []
        if isinstance(body, dict) and "data" in body:
            for m in body["data"]:
                mid = m.get("id") or m.get("name")
                if mid:
                    models.append(mid)
        elif isinstance(body, list):
            for m in body:
                mid = m.get("id") or m.get("name") if isinstance(m, dict) else str(m)
                if mid:
                    models.append(mid)
        models.sort()
        return jsonify({"models": models, "count": len(models), "url": models_url})
    except _urlerr.HTTPError as e:
        return jsonify({"error": f"HTTP {e.code}: {e.reason}", "url": models_url}), 502
    except _urlerr.URLError as e:
        return jsonify({"error": f"连接失败: {e.reason}", "url": models_url}), 502
    except Exception as e:
        return jsonify({"error": str(e), "url": models_url}), 500


# ── 结构化配置 API (config.yaml + .env + custom_providers) ──

import io
from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _load_yaml_file(path):
    """用 ruamel.yaml 加载，保留注释和格式"""
    if not path.exists():
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.load(f)
        return data, None
    except Exception as e:
        return None, str(e)


def _dump_yaml(data):
    """ruamel.yaml 对象 -> 字符串"""
    buf = io.StringIO()
    _yaml.dump(data, buf)
    return buf.getvalue()


# config.yaml 的可编辑字段定义 (分组)
# 每个字段: key_path -> {label, type, options?, placeholder?, help?}
CONFIG_SCHEMA = [
    {
        "group": "模型设置",
        "icon": "M",
        "fields": [
            {"key": "model.provider", "label": "模型提供商", "type": "provider_select", "help": "选择模型来源"},
            {"key": "model.default", "label": "默认模型", "type": "model_select", "help": "根据 provider 选择模型"},
        ],
    },
    {
        "group": "Agent 设置",
        "icon": "A",
        "fields": [
            {"key": "agent.max_turns", "label": "最大轮数", "type": "number"},
            {"key": "agent.reasoning_effort", "label": "推理强度", "type": "select", "options": ["low", "medium", "high"]},
            {"key": "agent.verbose", "label": "详细输出", "type": "bool"},
            {"key": "agent.image_input_mode", "label": "图片输入模式", "type": "select", "options": ["auto", "text", "vision"]},
        ],
    },
    {
        "group": "终端",
        "icon": "T",
        "fields": [
            {"key": "terminal.backend", "label": "后端", "type": "select", "options": ["local", "docker"]},
            {"key": "terminal.timeout", "label": "超时(秒)", "type": "number"},
            {"key": "terminal.cwd", "label": "工作目录", "type": "text"},
        ],
    },
    {
        "group": "显示",
        "icon": "D",
        "fields": [
            {"key": "display.language", "label": "语言", "type": "select", "options": ["zh", "en", "ja"]},
            {"key": "display.compact", "label": "紧凑模式", "type": "bool"},
            {"key": "display.show_reasoning", "label": "显示推理", "type": "bool"},
            {"key": "display.streaming", "label": "流式输出", "type": "bool"},
            {"key": "display.skin", "label": "皮肤", "type": "text"},
            {"key": "display.pet.enabled", "label": "桌宠启用", "type": "bool"},
            {"key": "display.pet.slug", "label": "桌宠名称", "type": "text"},
            {"key": "display.pet.scale", "label": "桌宠缩放", "type": "text"},
        ],
    },
    {
        "group": "语音",
        "icon": "V",
        "fields": [
            {"key": "tts.provider", "label": "TTS 提供商", "type": "select", "options": ["edge", "openai", "minimax", "elevenlabs", ""]},
            {"key": "tts.edge.voice", "label": "Edge 语音", "type": "text"},
            {"key": "stt.enabled", "label": "STT 启用", "type": "bool"},
            {"key": "stt.local.model", "label": "STT 本地模型", "type": "text"},
            {"key": "stt.local.language", "label": "STT 语言", "type": "text"},
            {"key": "voice.record_key", "label": "录音快捷键", "type": "text"},
            {"key": "voice.auto_tts", "label": "自动 TTS", "type": "bool"},
        ],
    },
    {
        "group": "记忆",
        "icon": "K",
        "fields": [
            {"key": "memory.memory_enabled", "label": "记忆启用", "type": "bool"},
            {"key": "memory.user_profile_enabled", "label": "用户档案启用", "type": "bool"},
            {"key": "memory.memory_char_limit", "label": "记忆字符上限", "type": "number"},
            {"key": "memory.user_char_limit", "label": "用户档案上限", "type": "number"},
            {"key": "memory.nudge_interval", "label": "提醒间隔", "type": "number"},
            {"key": "memory.flush_min_turns", "label": "最小刷新轮数", "type": "number"},
        ],
    },
    {
        "group": "审批与安全",
        "icon": "S",
        "fields": [
            {"key": "approvals.mode", "label": "审批模式", "type": "select", "options": ["smart", "manual", "auto"]},
            {"key": "compression.enabled", "label": "压缩启用", "type": "bool"},
            {"key": "compression.threshold", "label": "压缩阈值", "type": "text"},
            {"key": "code_execution.timeout", "label": "代码执行超时", "type": "number"},
            {"key": "code_execution.max_tool_calls", "label": "最大工具调用", "type": "number"},
        ],
    },
    {
        "group": "会话",
        "icon": "R",
        "fields": [
            {"key": "session_reset.mode", "label": "重置模式", "type": "select", "options": ["none", "idle", "schedule"]},
            {"key": "session_reset.idle_minutes", "label": "空闲分钟", "type": "number"},
            {"key": "delegation.max_iterations", "label": "委派最大迭代", "type": "number"},
        ],
    },
]


@app.route("/api/profile/<profile_name>/config-schema")
def api_config_schema(profile_name):
    """返回 config.yaml 的字段 schema"""
    return jsonify({"schema": CONFIG_SCHEMA})


def _get_nested(data, key_path):
    """从嵌套 dict 获取值, key_path = 'a.b.c'"""
    keys = key_path.split(".")
    val = data
    for k in keys:
        if val is None:
            return None
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return None
    return val


def _set_nested(data, key_path, value):
    """在嵌套 dict 中设置值, key_path = 'a.b.c'"""
    keys = key_path.split(".")
    d = data
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


@app.route("/api/profile/<profile_name>/config")
def api_read_config(profile_name):
    """结构化读取 config.yaml"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, "config.yaml")
    data, err = _load_yaml_file(fp)
    if err:
        return jsonify({"error": err}), 500
    if data is None:
        data = {}

    # 提取所有 schema 字段的值
    values = {}
    for group in CONFIG_SCHEMA:
        for field in group["fields"]:
            val = _get_nested(data, field["key"])
            values[field["key"]] = val

    # 单独处理 custom_providers
    custom_providers = []
    cp_raw = data.get("custom_providers", [])
    if cp_raw:
        for i, cp in enumerate(cp_raw):
            # models 可能是 list 或 dict，统一转成 list of strings
            raw_models = cp.get("models", [])
            if isinstance(raw_models, dict):
                models_list = list(raw_models.keys())
                models_format = "dict"
            elif isinstance(raw_models, list):
                models_list = list(raw_models)
                models_format = "list"
            else:
                models_list = []
                models_format = "list"
            custom_providers.append({
                "index": i,
                "name": cp.get("name", ""),
                "base_url": cp.get("base_url", ""),
                "api_key": cp.get("api_key", ""),
                "discover_models": cp.get("discover_models", False),
                "models": models_list,
                "models_format": models_format,
            })

    # 单独处理 mcp_servers（YAML 里是 dict: {name: {command, args, enabled}}）
    mcp_servers = []
    mcp_raw = data.get("mcp_servers", {})
    if mcp_raw and isinstance(mcp_raw, dict):
        for name, cfg in mcp_raw.items():
            if not isinstance(cfg, dict):
                continue
            args_raw = cfg.get("args", []) or []
            mcp_servers.append({
                "name": name,
                "command": cfg.get("command", ""),
                "args": [str(a) for a in args_raw] if isinstance(args_raw, list) else [],
                "enabled": cfg.get("enabled", True),
            })

    return jsonify({
        "values": values,
        "custom_providers": custom_providers,
        "mcp_servers": mcp_servers,
        "path": str(fp),
    })


@app.route("/api/profile/<profile_name>/config", methods=["PUT"])
def api_save_config(profile_name):
    """结构化保存 config.yaml"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, "config.yaml")

    # 先加载原始 YAML (保留格式和注释)
    data, err = _load_yaml_file(fp)
    if err:
        return jsonify({"error": f"load error: {err}"}), 500
    if data is None:
        data = {}

    body = request.get_json()
    values = body.get("values", {})
    custom_providers = body.get("custom_providers", [])
    mcp_servers = body.get("mcp_servers", [])

    # 应用 schema 字段
    for key_path, value in values.items():
        if value is not None:
            _set_nested(data, key_path, value)

    # 处理 custom_providers：在原有条目上原地更新，保留前端未识别的额外字段
    # （如 headers / timeout / proxy 等），避免结构化保存静默丢字段
    existing_cp = data.get("custom_providers", []) or []
    new_cp = []
    for i, cp in enumerate(custom_providers):
        # 以同索引的原有条目为底，保留未知字段
        base = dict(existing_cp[i]) if i < len(existing_cp) and isinstance(existing_cp[i], dict) else {}
        base["name"] = cp.get("name", "")
        base["base_url"] = cp.get("base_url", "")
        # api_key：用户清空则移除
        if cp.get("api_key"):
            base["api_key"] = cp["api_key"]
        elif "api_key" in base:
            del base["api_key"]
        if "discover_models" in cp:
            base["discover_models"] = cp["discover_models"]
        models = cp.get("models", []) or []
        models_format = cp.get("models_format", "list")
        if models:
            if models_format == "dict":
                # 保存为 dict 格式 {model_name: {}}
                base["models"] = {m: {} for m in models if m}
            else:
                base["models"] = [m for m in models if m]
        elif "models" in base:
            del base["models"]
        new_cp.append(base)
    data["custom_providers"] = new_cp

    # 处理 mcp_servers：构建 dict {name: {command, args, enabled}}，
    # 保留原有条目里前端未识别的额外字段（如 env / type 等）
    existing_mcp = data.get("mcp_servers", {}) or {}
    if not isinstance(existing_mcp, dict):
        existing_mcp = {}
    new_mcp = {}
    seen_names = set()
    for m in mcp_servers:
        name = (m.get("name") or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        cfg = {}
        if m.get("command"):
            cfg["command"] = m["command"]
        args = [a for a in (m.get("args") or []) if a]
        if args:
            cfg["args"] = args
        # enabled：始终写入，保留用户配置（避免复制/编辑时丢失 enabled 字段）
        cfg["enabled"] = m.get("enabled", True)
        # 保留原有额外字段
        if name in existing_mcp and isinstance(existing_mcp[name], dict):
            for k, v in existing_mcp[name].items():
                if k not in cfg and k not in ("command", "args", "enabled"):
                    cfg[k] = v
        new_mcp[name] = cfg
    data["mcp_servers"] = new_mcp

    # 备份并写入
    try:
        warn = make_backup(fp)
        output = _dump_yaml(data)
        atomic_write_text(fp, output)
        key = f"{profile_name}:config.yaml"
        _watch_state[key] = get_file_signature(fp)
        resp = {"ok": True, "path": str(fp)}
        if warn:
            resp["warning"] = warn
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/<profile_name>/config/raw")
def api_read_config_raw(profile_name):
    """读取 config.yaml 原始文本 (高级模式用)"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, "config.yaml")
    content, err = read_file_safe(fp)
    if err:
        return jsonify({"error": err}), 500
    return jsonify({"content": content, "path": str(fp), "lang": "yaml"})


@app.route("/api/profile/<profile_name>/config/raw", methods=["PUT"])
def api_save_config_raw(profile_name):
    """保存 config.yaml 原始文本 (高级模式用)"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, "config.yaml")
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "missing content"}), 400
    try:
        warn = make_backup(fp)
        atomic_write_text(fp, data["content"])
        key = f"{profile_name}:config.yaml"
        _watch_state[key] = get_file_signature(fp)
        resp = {"ok": True, "path": str(fp)}
        if warn:
            resp["warning"] = warn
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── .env 结构化 API ──

# 单行 .env 解析正则：缩进 / export / 可选#(禁用) / key / = / rest
_ENV_LINE_RE = re.compile(
    r'^(?P<indent>\s*)(?P<export>export\s+)?(?P<active>#?)(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(?P<rest>.*)$'
)


def _split_value_comment(rest):
    """从 = 之后的文本分离 value(含引号) 和行内注释，考虑引号包裹"""
    rest = rest.rstrip()
    if not rest:
        return "", ""
    if rest[0] in ('"', "'"):
        q = rest[0]
        end = rest.find(q, 1)
        if end == -1:
            return rest, ""  # 引号未闭合，整段当作 value
        val = rest[:end + 1]
        remainder = rest[end + 1:].strip()
        if remainder.startswith("#"):
            return val, remainder
        return val, ""
    # 无引号：以 " #" 作为行内注释起点
    idx = rest.find(" #")
    if idx != -1:
        return rest[:idx].rstrip(), rest[idx:].strip()
    return rest, ""


def _unquote(val):
    """去引号，返回 (value, quote_char)"""
    if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
        return val[1:-1], val[0]
    return val, ""


def _parse_env_line(line):
    """解析单行 .env，返回 dict；非 key=value 行返回 None"""
    m = _ENV_LINE_RE.match(line)
    if not m:
        return None
    value_raw, inline = _split_value_comment(m.group("rest"))
    value, quote_char = _unquote(value_raw)
    return {
        "indent": m.group("indent"),
        "export": m.group("export") or "",
        "active": m.group("active") != "#",
        "key": m.group("key"),
        "value": value,
        "quote_char": quote_char,
        "inline_comment": inline,
        "raw": line,
    }


def _format_env_line(p):
    """由 parsed dict 重建一行 .env"""
    indent = p.get("indent", "")
    export = p.get("export", "")
    active_marker = "" if p.get("active", True) else "#"
    key = p["key"]
    value = p.get("value", "")
    quote_char = p.get("quote_char", "")
    inline = p.get("inline_comment", "")
    needs_quote = (" " in value) or ("#" in value)
    if quote_char:
        val_str = f"{quote_char}{value}{quote_char}"
    elif needs_quote:
        val_str = f'"{value}"'
    else:
        val_str = value
    line = f"{indent}{export}{active_marker}{key}={val_str}"
    if inline:
        line += f" {inline}"
    return line


def _comment_text(comment_line):
    """从 '# foo' 提取 'foo'"""
    s = comment_line.strip()
    if s.startswith("#"):
        return s[1:].strip()
    return s


def parse_env(content):
    """解析 .env 文件，返回 [{key, value, comment, active}]。
    - #KEY=val（无空格）视为被禁用的条目（active=False）
    - # KEY=val（有空格）视为纯注释
    - 紧邻条目上方的连续注释行合并为 comment
    """
    entries = []
    pending = []  # 紧邻的注释行（自上次空行/条目起）
    for line in content.split("\n"):
        parsed = _parse_env_line(line)
        if parsed is not None:
            comment = " ".join(_comment_text(c) for c in pending).strip()
            entries.append({
                "key": parsed["key"],
                "value": parsed["value"],
                "comment": comment,
                "active": parsed["active"],
            })
            pending = []
            continue
        stripped = line.strip()
        if not stripped:
            pending = []  # 空行切断注释归属
        elif stripped.startswith("#"):
            pending.append(line)
        else:
            pending = []  # 无法识别的行，不影响后续条目注释归属
    return entries


def patch_env_text(original_text, entries):
    """在原始 .env 文本上无损打补丁：仅更新/新增/删除 key=value 行，
    保留所有空行、独立注释、export 前缀、引号风格、行内注释。
    条目的 comment 与原值一致时原样保留原注释行；被编辑时以新注释替换。
    """
    entries_by_key = {}
    for e in entries:
        if e.get("key"):
            entries_by_key[e["key"]] = e
    consumed = set()

    # 把原文切成“块”：entry 块携带其紧邻前置注释；sep 块为空行/独立注释/未识别行
    blocks = []
    pending = []
    for line in original_text.split("\n"):
        parsed = _parse_env_line(line)
        if parsed is not None:
            blocks.append({"type": "entry", "comments": pending, "parsed": parsed})
            pending = []
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            pending.append(line)
        else:
            # 未识别的非注释行：连同前置注释作为分隔块原样保留
            blocks.append({"type": "sep", "lines": pending + [line]})
            pending = []
    if pending:
        blocks.append({"type": "sep", "lines": pending})

    out_lines = []
    last_entry_end = -1  # 新增条目插入位置（最后一个保留的 entry 之后）
    for blk in blocks:
        if blk["type"] == "sep":
            out_lines.extend(blk["lines"])
            continue
        parsed = blk["parsed"]
        key = parsed["key"]
        if key in consumed:
            # 重复 key：第二次出现原样保留，避免被覆盖写入
            out_lines.append(parsed["raw"])
            continue
        if key not in entries_by_key:
            # 用户删除了此条目：条目行 + 其前置注释一并跳过
            continue
        entry = entries_by_key[key]
        consumed.add(key)
        # 决定注释：被编辑则替换为单行新注释，未编辑则原样保留
        orig_comment = " ".join(_comment_text(c) for c in blk["comments"]).strip()
        new_comment = (entry.get("comment") or "").strip()
        if new_comment != orig_comment:
            if new_comment:
                out_lines.append(f"# {new_comment}")
        else:
            out_lines.extend(blk["comments"])
        # value/active：未改动则原样保留整行（含引号/缩进/行内注释空格），完全无损
        orig_value = parsed["value"]
        orig_active = parsed["active"]
        new_value = entry.get("value", "")
        new_active = entry.get("active", True)
        if new_value == orig_value and new_active == orig_active:
            out_lines.append(parsed["raw"])
        else:
            parsed["value"] = new_value
            parsed["active"] = new_active
            out_lines.append(_format_env_line(parsed))
        last_entry_end = len(out_lines)

    # 追加用户新增的条目
    new_block = []
    for e in entries:
        key = e.get("key")
        if key and key not in consumed:
            cmt = (e.get("comment") or "").strip()
            if cmt:
                new_block.append(f"# {cmt}")
            new_block.append(_format_env_line({
                "indent": "", "export": "",
                "active": e.get("active", True),
                "key": key, "value": e.get("value", ""),
                "quote_char": "", "inline_comment": "",
            }))
    if new_block:
        insert_at = last_entry_end + 1 if last_entry_end >= 0 else len(out_lines)
        out_lines[insert_at:insert_at] = new_block

    return "\n".join(out_lines)


@app.route("/api/profile/<profile_name>/env")
def api_read_env(profile_name):
    """结构化读取 .env"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, ".env")
    content, err = read_file_safe(fp)
    if err:
        return jsonify({"error": err}), 500
    entries = parse_env(content)
    return jsonify({"entries": entries, "path": str(fp), "count": len(entries)})


@app.route("/api/profile/<profile_name>/env", methods=["PUT"])
def api_save_env(profile_name):
    """结构化保存 .env（在原文上无损打补丁，不破坏注释/格式）"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, ".env")
    body = request.get_json()
    entries = body.get("entries", [])
    # 读取原始文本作为补丁基底；不存在则视为空
    original, err = read_file_safe(fp)
    if err:
        return jsonify({"error": err}), 500
    content = patch_env_text(original, entries)
    try:
        warn = make_backup(fp)
        atomic_write_text(fp, content)
        key = f"{profile_name}:.env"
        _watch_state[key] = get_file_signature(fp)
        resp = {"ok": True, "path": str(fp)}
        if warn:
            resp["warning"] = warn
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 备份 / 恢复 / 清理 ────────────────────────────────────
BACKUP_DIR_NAME = HUB_DIR_NAME  # AAAHermesHub（兼容旧引用）
BACKUP_FILES = ["config.yaml", ".env"]  # 仅备份这两个核心配置文件


def _migrate_hub_layout():
    """迁移旧目录结构到新布局。
    1. 旧目录 AAAHermesConfigBack/AAAHermesHub 从 HERMES_HOME 移到程序所在目录（get_app_dir()）
    2. 备份时间戳目录从 AAAHermesHub/<ts>/ 移到 AAAHermesHub/backups/<ts>/
    这样 backups/ 和 shared-skills/ 在同一父目录下，互不干扰。
    注意：目标放在 get_app_dir()，重建 exe 时不会被删除。"""
    target = HUB_DIR  # get_app_dir()/AAAHermesHub
    # 来源候选：HERMES_HOME 下可能存在旧目录
    for old_name in ("AAAHermesHub", "AAAHermesConfigBack"):
        old = HERMES_HOME / old_name
        if not old.exists():
            continue
        # 如果目标已存在，只迁移内容（合并）；否则整个移动
        if target.exists():
            # 合并：把 old 下的子目录移到 target
            for d in list(old.iterdir()):
                dst = target / d.name
                if not dst.exists():
                    try:
                        d.rename(dst)
                    except Exception:
                        pass
            # 尝试删除空的旧目录
            try:
                old.rmdir()
            except Exception:
                pass
        else:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                old.rename(target)
            except Exception:
                pass
    if not target.exists():
        return
    # 把时间戳目录移到 backups/ 子目录
    backups_sub = target / "backups"
    backups_sub.mkdir(exist_ok=True)
    for d in list(target.iterdir()):
        if d.name in ("backups", "shared-skills") or d.name.startswith(".") or d.name.startswith("_"):
            continue
        if d.is_dir() and re.fullmatch(r"\d{8}_\d{6}", d.name):
            try:
                d.rename(backups_sub / d.name)
            except Exception:
                pass
        elif d.is_dir() and d.name == ".backups":
            try:
                d.rename(backups_sub / ".backups")
            except Exception:
                pass


def _fix_shared_skill_junctions():
    """修复失效的共享技能 junction，返回 (修复数, 详情列表)。
    当 AAAHermesHub 目录移动后，profile 中的 junction 可能指向旧路径导致失效。
    此函数检测所有指向 shared-skills 但目标已不存在的 junction，在新的
    SHARED_SKILLS_DIR 中查找同名技能并重新链接。"""
    if not SHARED_SKILLS_DIR.exists():
        return 0, ["共享技能库目录不存在: " + str(SHARED_SKILLS_DIR)]
    fixed = 0
    details = []
    for name in get_profile_names():
        skills_dir = get_skills_dir(name)
        if not skills_dir.exists():
            continue
        try:
            entries = list(skills_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            # 先检查 _is_junction（不依赖 is_dir()，因为失效的 junction is_dir() 可能返回 False）
            if not _is_junction(entry):
                continue
            target = _get_junction_target(entry)
            if not target:
                continue
            # 目标存在 = junction 正常，跳过
            if target.exists():
                continue
            # 目标不存在 = 失效 junction，尝试修复
            target_str = str(target)
            # 只处理 shared-skills 相关的 junction
            if "shared-skills" not in target_str.lower():
                continue
            # 提取 shared-skills 后面的相对路径（如 1password 或 category/skill_name）
            idx = target_str.lower().find("shared-skills")
            rel = target_str[idx + len("shared-skills"):].lstrip("\\/").replace("\\", "/")
            new_target = SHARED_SKILLS_DIR / rel.replace("/", os.sep) if rel else SHARED_SKILLS_DIR / entry.name
            if new_target.exists():
                try:
                    _remove_junction(entry)
                    _create_junction(entry, new_target)
                    fixed += 1
                    details.append(f"✓ {name}/{entry.name}: {target} → {new_target}")
                except Exception as e:
                    details.append(f"✗ {name}/{entry.name}: 修复失败 - {e}")
            else:
                details.append(f"! {name}/{entry.name}: 新目标不存在 - {new_target}")
    if fixed:
        print(f"  [hub] 修复了 {fixed} 个失效的共享技能 junction")
    return fixed, details


_migrate_hub_layout()
_fix_shared_skill_junctions()


def _backup_root():
    """备份根目录：程序所在目录/AAAHermesHub/backups"""
    return HUB_DIR / "backups"


def _list_backup_dirs():
    """列出所有备份日期目录（降序），排除 .backups/ 和非目录"""
    root = _backup_root()
    if not root.exists():
        return []
    dirs = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith(".") or d.name.startswith("_"):
            continue
        # 校验目录名格式：YYYYMMDD_HHMMSS
        if not re.fullmatch(r"\d{8}_\d{6}", d.name):
            continue
        dirs.append(d)
    dirs.sort(key=lambda x: x.name, reverse=True)  # 降序（最新在前）
    return dirs


def _profile_backup_source(profile_name):
    """返回 profile 的源目录（default → HERMES_HOME，其他 → profiles/<name>）"""
    return get_profile_path(profile_name)


def _profile_backup_dest(backup_dir, profile_name):
    """返回 profile 在备份目录中的目标路径"""
    return backup_dir / profile_name


@app.route("/api/backup", methods=["POST"])
def api_backup():
    """一键备份：扫描 default + 所有 profiles 的 config.yaml + .env
    到 AAAHermesConfigBack/<YYYYMMDD_HHMMSS>/<profile>/"""
    # 校验：避免同秒内重复备份
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = _backup_root()
    backup_dir = root / ts
    if backup_dir.exists():
        return jsonify({"error": f"备份目录已存在: {ts}（同秒内不可重复备份）"}), 400

    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return jsonify({"error": f"创建备份目录失败: {e}"}), 500

    profiles = get_profile_names()
    summary = []
    for pname in profiles:
        src_dir = _profile_backup_source(pname)
        dest_dir = _profile_backup_dest(backup_dir, pname)
        dest_dir.mkdir(parents=True, exist_ok=True)
        files_backed = []
        for fname in BACKUP_FILES:
            src = src_dir / fname
            if src.exists():
                try:
                    shutil.copy2(src, dest_dir / fname)
                    files_backed.append(fname)
                except Exception as e:
                    summary.append({"profile": pname, "error": f"{fname}: {e}"})
                    continue
        summary.append({"profile": pname, "files": files_backed})

    _log_operation("backup", backup_dir=str(backup_dir), profiles=len(profiles))
    return jsonify({
        "ok": True,
        "backup_dir": ts,
        "path": str(backup_dir),
        "profiles": summary,
        "message": f"已备份 {len(profiles)} 个 profile 到 {ts}/",
    })


@app.route("/api/backups")
def api_list_backups():
    """列出所有备份。返回 [{dir, profiles: [{name, files}]}] 降序"""
    backups = []
    for d in _list_backup_dirs():
        profiles = []
        for pdir in sorted(d.iterdir()):
            if not pdir.is_dir() or pdir.name.startswith("."):
                continue
            files = [f.name for f in pdir.iterdir() if f.is_file() and f.name in BACKUP_FILES]
            if files:
                profiles.append({"name": pdir.name, "files": sorted(files)})
        # 解析日期为可读格式
        name = d.name  # YYYYMMDD_HHMMSS
        readable = f"{name[:4]}-{name[4:6]}-{name[6:8]} {name[9:11]}:{name[11:13]}:{name[13:15]}" if len(name) >= 15 else name
        backups.append({
            "dir": name,
            "readable": readable,
            "profiles": profiles,
            "total_files": sum(len(p["files"]) for p in profiles),
        })
    return jsonify({"backups": backups, "root": str(_backup_root())})


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """从指定备份恢复。参数：backup_dir, profile, file（config.yaml/.env/all）。
    恢复前自动把当前文件备份到 AAAHermesConfigBack/.backups/"""
    data = request.get_json()
    backup_dir_name = (data or {}).get("backup_dir", "").strip()
    profile_name = (data or {}).get("profile", "").strip()
    file_name = (data or {}).get("file", "all").strip()

    if not backup_dir_name or not profile_name:
        return jsonify({"error": "backup_dir 和 profile 必填"}), 400
    if not re.fullmatch(r"\d{8}_\d{6}", backup_dir_name):
        return jsonify({"error": "无效的备份目录名"}), 400
    if profile_name not in get_profile_names():
        return jsonify({"error": f"无效的 profile: {profile_name}"}), 400

    # 确定要恢复的文件列表
    if file_name == "all":
        files_to_restore = BACKUP_FILES
    elif file_name in BACKUP_FILES:
        files_to_restore = [file_name]
    else:
        return jsonify({"error": f"无效的文件: {file_name}"}), 400

    # 备份源（历史备份）和恢复目标（当前 profile 目录）
    backup_dir = _backup_root() / backup_dir_name / profile_name
    if not backup_dir.exists():
        return jsonify({"error": f"备份目录不存在: {backup_dir_name}/{profile_name}"}), 404

    dest_dir = _profile_backup_source(profile_name)
    pre_backup_dir = _backup_root() / ".backups"
    pre_backup_dir.mkdir(parents=True, exist_ok=True)
    pre_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    restored = []
    warnings = []
    for fname in files_to_restore:
        src = backup_dir / fname
        if not src.exists():
            warnings.append(f"{fname}: 备份中不存在，跳过")
            continue
        dest = dest_dir / fname
        # 恢复前：把当前文件备份到 .backups/
        if dest.exists():
            pre_name = f"{pre_ts}_{profile_name}_{fname.replace('.', '_')}.bak"
            try:
                shutil.copy2(dest, pre_backup_dir / pre_name)
            except Exception as e:
                warnings.append(f"{fname}: 恢复前备份失败 ({e})，仍继续恢复")
        # 恢复（用原子写入确保完整性）
        try:
            content = src.read_text(encoding="utf-8")
            atomic_write_text(dest, content)
            restored.append(fname)
        except Exception as e:
            warnings.append(f"{fname}: 恢复失败 ({e})")

    _log_operation("restore", profile=profile_name, backup_dir=backup_dir_name,
                   files=restored, warnings=warnings)
    return jsonify({
        "ok": True,
        "restored": restored,
        "warnings": warnings,
        "message": f"已恢复 {len(restored)} 个文件到 {profile_name}/",
    })


@app.route("/api/backups/cleanup", methods=["POST"])
def api_cleanup_backups():
    """清理超过指定天数的备份，但保留最新一份。
    参数：max_age_days（整数）。.backups/ 不参与清理。"""
    data = request.get_json() or {}
    try:
        max_age_days = int(data.get("max_age_days", 30))
    except (ValueError, TypeError):
        return jsonify({"error": "max_age_days 必须是整数"}), 400
    if max_age_days < 1:
        return jsonify({"error": "max_age_days 必须 >= 1"}), 400

    dirs = _list_backup_dirs()  # 降序，最新在前
    if not dirs:
        return jsonify({"ok": True, "deleted": [], "message": "无备份可清理"})

    today = datetime.now().date()
    newest = dirs[0]  # 最新的一份，始终保留
    deleted = []
    for d in dirs:
        if d is newest:
            continue
        # 解析目录名为日期
        try:
            dt = datetime.strptime(d.name, "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        # 用日历日期差（而非 timedelta.days），避免"2天前"因不足48小时而被判为1天
        age_days = (today - dt.date()).days
        if age_days >= max_age_days:
            try:
                shutil.rmtree(d)
                deleted.append(d.name)
            except Exception as e:
                return jsonify({"error": f"删除 {d.name} 失败: {e}"}), 500

    _log_operation("cleanup_backups", deleted=deleted, kept_newest=newest.name, max_age_days=max_age_days)
    return jsonify({
        "ok": True,
        "deleted": deleted,
        "kept_newest": newest.name,
        "message": f"已删除 {len(deleted)} 个超期备份（保留最新: {newest.name}）",
    })


# ── 操作日志查看 ──────────────────────────────────────────
@app.route("/api/logs/operations")
def api_get_operations_log():
    """读取最近的操作日志（JSONL，最新在前）。?limit=200 控制返回条数。"""
    if not OPERATIONS_LOG.exists():
        return jsonify({"logs": [], "path": str(OPERATIONS_LOG)})
    try:
        limit = max(1, min(2000, int(request.args.get("limit", 200))))
        lines = OPERATIONS_LOG.read_text(encoding="utf-8").strip().split("\n")
        logs = []
        for line in reversed(lines):  # 最新的在前
            line = line.strip()
            if not line:
                continue
            try:
                logs.append(json.loads(line))
            except Exception:
                pass
        return jsonify({"logs": logs[:limit], "path": str(OPERATIONS_LOG), "total": len(logs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs/operations", methods=["DELETE"])
def api_clear_operations_log():
    """清空操作日志。"""
    try:
        if OPERATIONS_LOG.exists():
            OPERATIONS_LOG.unlink()
        return jsonify({"ok": True, "message": "操作日志已清空"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 工具集 / MCP 查看 ─────────────────────────────────────
def _parse_toolsets_registry():
    """解析 hermes-agent/toolsets.py 的 TOOLSETS dict，返回 [{name, description, tools}]"""
    ts_file = HERMES_HOME / "hermes-agent" / "toolsets.py"
    if not ts_file.exists():
        return []
    try:
        t = ts_file.read_text(encoding="utf-8", errors="replace")
        # 提取 TOOLSETS = { ... } 块（粗匹配顶层 "key": {...}）
        results = []
        for m in re.finditer(r'^    "([a-z_]+)":\s*\{\s*\n(.*?)(?=^    "[a-z_]+"|\Z)', t, re.M | re.S):
            name = m.group(1)
            body = m.group(2)
            desc = ""
            dm = re.search(r'"description":\s*(.*?)(?=,\s*\n|"tools")', body, re.S)
            if dm:
                desc = dm.group(1).strip().strip('"').strip("'")
                if desc.startswith("("): desc = desc.strip("()")
            tools = re.findall(r'"([a-z_]+)"', body.split('"tools"')[1].split("]")[0]) if '"tools"' in body else []
            results.append({"name": name, "description": desc[:120], "tools": tools})
        return results
    except Exception:
        return []


def _parse_enabled_toolsets(profile_name):
    """解析 profile config.yaml 的 platform_toolsets，返回 {platform: [toolset_names]}"""
    cfg = get_profile_path(profile_name) / "config.yaml"
    if not cfg.exists():
        return {}
    try:
        t = cfg.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'platform_toolsets:\s*\n((?:[ \t]{2,}.*\n)+)', t)
        if not m:
            return {}
        block = m.group(1)
        result = {}
        cur = None
        for line in block.splitlines():
            if not line.strip():
                continue
            # 平台名（缩进2空格，xxx:）
            if re.match(r"^  ([a-z_]+):\s*$", line):
                cur = re.match(r"^  ([a-z_]+):\s*$", line).group(1)
                result[cur] = []
            elif cur and re.match(r"^    -\s+(\S)", line):
                result[cur].append(re.match(r"^    -\s+(\S+)", line).group(1))
        return result
    except Exception:
        return {}


@app.route("/api/profile/<profile_name>/toolsets")
def api_profile_toolsets(profile_name):
    """返回该 profile 的工具集视图：所有注册工具集 + 每平台启用状态"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    registry = _parse_toolsets_registry()
    enabled = _parse_enabled_toolsets(profile_name)
    enabled_flat = set()
    for ts_list in enabled.values():
        enabled_flat.update(ts_list)
    items = [{"name": r["name"], "description": r["description"], "tools": r["tools"],
              "enabled": r["name"] in enabled_flat} for r in registry]
    return jsonify({"toolsets": items, "enabled_by_platform": enabled,
                     "total": len(items), "enabled_count": len(enabled_flat)})


def _parse_mcp_servers(profile_name):
    """解析 profile config.yaml 的 mcp_servers 段，返回 [{name, command, args, enabled}]"""
    cfg = get_profile_path(profile_name) / "config.yaml"
    if not cfg.exists():
        return []
    try:
        t = cfg.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'mcp_servers?:\s*\n((?:[ \t]{2,}.*\n)+)', t)
        if not m:
            return []
        block = m.group(1)
        result = []
        cur = None
        for line in block.splitlines():
            if not line.strip():
                continue
            # server 名（缩进2空格，xxx:）
            mm = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if mm:
                # enabled 默认 True（未写 enabled 字段视为启用）
                cur = {"name": mm.group(1), "command": "", "args": [], "enabled": True}
                result.append(cur)
            elif cur:
                cm = re.match(r"^    command:\s*(\S+)", line)
                if cm: cur["command"] = cm.group(1)
                # args 列表项缩进可能是 4 或 6 空格（args: 在 4 空格时项在 6 空格），
                # 用 \s+ 匹配任意缩进的 "- xxx" 行
                am = re.match(r"^\s+-\s+(.*)$", line)
                if am: cur["args"].append(am.group(1).strip().strip('"').strip("'"))
                em = re.match(r"^    enabled:\s*(\S+)", line)
                if em: cur["enabled"] = em.group(1).lower() in ("true", "1", "yes", "on")
        return result
    except Exception:
        return []


@app.route("/api/profile/<profile_name>/mcp")
def api_profile_mcp(profile_name):
    """返回该 profile 的 MCP server 列表"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    servers = _parse_mcp_servers(profile_name)
    return jsonify({"mcp_servers": servers, "count": len(servers)})


# ── 技能中心（多源在线搜索/下载到共享技能库） ────────────────────
# 支持多个技能来源：
#   - clawhub:   ClawHub (clawhub.ai) HTTP API（zip 包下载）
#   - skills-sh: Skills.sh (www.skills.sh) HTML 页面解析（GitHub raw 拉取 SKILL.md）
#   - custom:    用户自定义的 JSON 索引（[{name, description, download_url}]）
# 安装目标：SHARED_SKILLS_DIR（共享技能库，跨 profile 复用）
# 安全：ClawHub/custom zip 仅解压文本文件(<500KB)，跳过绝对路径/.. 和二进制；
#       skills.sh 仅下载 .md 文件(<500KB)。
CLAWHUB_BASE = "https://clawhub.ai/api/v1"

# 源配置文件路径（与 .hermes_home 同目录，便于持久化）
SOURCES_CONFIG_FILE = get_app_dir() / ".hermes_hub_sources.json"

# 默认源列表（配置文件不存在时使用）
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


def _clawhub_get(path, params=None, timeout=15):
    """调用 ClawHub API，返回 JSON 数据或 None"""
    url = CLAWHUB_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "HermesProfileManager/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_url_text(url, timeout=20, headers=None):
    """通用 HTTP 抓取，返回文本（utf-8，替换非法字节）或 None。"""
    h = {"User-Agent": "HermesProfileManager/1.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _search_clawhub(source, q, limit):
    """搜索 ClawHub 源（使用默认 CLAWHUB_BASE）。返回结果列表（含内部字段，由调用方清理）。"""
    data = _clawhub_get("/skills", {"search": q, "limit": limit})
    if data is None:
        return []
    items = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    result = []
    for item in items:
        slug = item.get("slug")
        if not slug:
            continue
        result.append({
            "slug": slug,
            "name": item.get("displayName") or item.get("name") or slug,
            "description": item.get("summary") or item.get("description") or "",
            "tags": item.get("tags", []) if isinstance(item.get("tags"), list) else [],
            "source": source["id"],
            "source_name": source["name"],
            "trust": "community",
        })
    return result


def _search_skills_sh(source, q, limit):
    """搜索 Skills.sh 源：抓取 HTML 并解析技能入口。
    Skills.sh 是 SPA（JS 渲染），?q= 搜索参数在服务端不生效，返回固定首页 HTML。
    因此改为：抓取首页所有技能链接，再用 q 做客户端关键词过滤。
    slug 用 {owner}__{repo}__{skill_name} 形式（双下划线分隔，避免与 GitHub 名中的 - 冲突）。
    内部字段 _owner_repo / _skill_name 由调用方在返回前剥离。"""
    base = (source.get("url") or "https://www.skills.sh").rstrip("/")
    # 不带 ?q= 参数（SPA 中不生效），直接抓首页
    html = _fetch_url_text(base, timeout=20)
    if not html:
        return []
    # 匹配 href="/owner/repo/skill-name"，owner/repo/skill_name 仅允许 [A-Za-z0-9._-]
    pattern = re.compile(r'href="(/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*))"')
    seen = set()
    result = []
    skip_segments = {"static", "assets", "api", "favicon.ico", "robots.txt", "css", "js", "img", "images"}
    ql = q.lower() if q else ""
    for m in pattern.finditer(html):
        owner, repo, skill_name = m.group(2), m.group(3), m.group(4)
        if owner.lower() in skip_segments or repo.lower() in skip_segments:
            continue
        key = f"{owner}/{repo}/{skill_name}"
        if key in seen:
            continue
        seen.add(key)
        # 客户端关键词过滤（skill_name 或完整路径包含 q）
        if ql and ql not in skill_name.lower() and ql not in key.lower():
            continue
        if len(result) >= limit:
            break
        # slug 用 __ 分隔，install 时再拆分回 owner/repo/skill_name
        slug = f"{owner}__{repo}__{skill_name}"
        result.append({
            "slug": slug,
            "name": skill_name,
            "description": f"{owner}/{repo}/{skill_name}",
            "tags": [],
            "source": source["id"],
            "source_name": source["name"],
            "trust": "community",
            "_owner_repo": f"{owner}/{repo}",
            "_skill_name": skill_name,
        })
    return result


def _search_custom(source, q, limit):
    """搜索自定义源：URL 指向 JSON 索引文件。
    期望格式：[{name, description, download_url, tags?, trust?, slug?}]"""
    url = source.get("url")
    if not url:
        return []
    text = _fetch_url_text(url, timeout=20, headers={"Accept": "application/json"})
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    ql = q.lower()
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        desc = item.get("description") or ""
        # 客户端关键词过滤
        if ql and ql not in name.lower() and ql not in desc.lower():
            continue
        slug = item.get("slug") or name
        if not slug:
            # 从 download_url 末段推导
            dl = item.get("download_url", "")
            slug = dl.rstrip("/").split("/")[-1] if dl else ""
        if not slug:
            continue
        result.append({
            "slug": slug,
            "name": name or slug,
            "description": desc,
            "tags": item.get("tags", []) if isinstance(item.get("tags"), list) else [],
            "source": source["id"],
            "source_name": source["name"],
            "trust": item.get("trust", "community"),
            "_download_url": item.get("download_url"),
        })
        if len(result) >= limit:
            break
    return result


@app.route("/api/skills-hub/sources")
def api_hub_sources_list():
    """列出所有技能源。"""
    return jsonify({"sources": load_sources()})


@app.route("/api/skills-hub/sources", methods=["POST"])
def api_hub_sources_add():
    """添加自定义技能源。
    body: {name, url, type}（type 为 clawhub/skills-sh/custom）"""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    src_type = (data.get("type") or "custom").strip()
    if not name or not url:
        return jsonify({"error": "name 和 url 必填"}), 400
    if src_type not in ("clawhub", "skills-sh", "custom"):
        return jsonify({"error": "type 必须为 clawhub/skills-sh/custom"}), 400
    sources = load_sources()
    # 用 type + url 哈希前缀生成唯一 id，避免重复添加同 URL
    sid = src_type + "-" + hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    if any(s["id"] == sid for s in sources):
        return jsonify({"error": "该 URL 已存在"}), 409
    sources.append({"id": sid, "name": name, "type": src_type, "url": url, "enabled": True})
    save_sources(sources)
    return jsonify({"ok": True, "source": sources[-1], "sources": sources})


@app.route("/api/skills-hub/sources/<sid>", methods=["DELETE"])
def api_hub_sources_delete(sid):
    """删除指定技能源（按 id）。"""
    sources = load_sources()
    new_list = [s for s in sources if s["id"] != sid]
    if len(new_list) == len(sources):
        return jsonify({"error": "source not found"}), 404
    save_sources(new_list)
    return jsonify({"ok": True, "sources": new_list})


@app.route("/api/skills-hub/sources/<sid>/toggle", methods=["POST"])
def api_hub_sources_toggle(sid):
    """切换技能源的启用/禁用状态。"""
    sources = load_sources()
    found = None
    for s in sources:
        if s["id"] == sid:
            s["enabled"] = not s.get("enabled", True)
            found = s
            break
    if not found:
        return jsonify({"error": "source not found"}), 404
    save_sources(sources)
    return jsonify({"ok": True, "source": found, "sources": sources})


@app.route("/api/skills-hub/search")
def api_hub_search():
    """跨多源搜索技能。?q=关键词&limit=N&source=源id(可选,缺省/all=全部启用源)
    冲突检测：与 SHARED_SKILLS_DIR 内容比对（安装目标已改为共享技能库）。
    每条结果含：slug, name, description, tags, source(源id), source_name(显示名), trust, conflict(bool)"""
    q = request.args.get("q", "").strip()
    try:
        limit = min(50, max(1, int(request.args.get("limit", "30"))))
    except ValueError:
        limit = 30
    src_filter = request.args.get("source", "").strip()
    sources = load_sources()
    # 仅搜索 enabled 的源；若指定 source 参数则进一步过滤
    active = [s for s in sources if s.get("enabled", True)]
    if src_filter and src_filter != "all":
        active = [s for s in active if s["id"] == src_filter]
    # 已安装的共享技能（用于冲突检测）
    existing = set()
    if SHARED_SKILLS_DIR.exists():
        for p in SHARED_SKILLS_DIR.iterdir():
            if p.is_dir() and not p.name.startswith(".") and not p.name.startswith("_"):
                existing.add(p.name.lower())
    # 跨源搜索（任一源失败不影响其他源）
    all_items = []
    for s in active:
        try:
            if s["type"] == "clawhub":
                items = _search_clawhub(s, q, limit)
            elif s["type"] == "skills-sh":
                items = _search_skills_sh(s, q, limit)
            elif s["type"] == "custom":
                items = _search_custom(s, q, limit)
            else:
                items = []
        except Exception:
            items = []
        all_items.extend(items)
    # 标注冲突，剥离内部字段
    result = []
    for item in all_items:
        slug = item["slug"]
        item["conflict"] = slug.lower() in existing
        item.pop("_owner_repo", None)
        item.pop("_skill_name", None)
        item.pop("_download_url", None)
        result.append(item)
    return jsonify({"items": result, "q": q, "count": len(result), "sources": sources})


def _install_clawhub(slug, source, force):
    """从 ClawHub 下载 zip 包并安装到 SHARED_SKILLS_DIR。
    安全：仅解压文本文件(<500KB)，跳过绝对路径/.. 和二进制。"""
    dst = SHARED_SKILLS_DIR / slug
    if dst.exists() and not force:
        return jsonify({"error": "conflict",
                         "message": f"技能 '{slug}' 已存在于共享技能库（{dst}）。如需覆盖请勾选'强制安装'（旧文件会备份到 .trash/）"}), 409
    # 解析最新版本号
    version = "latest"
    meta = _clawhub_get(f"/skills/{slug}")
    if meta and isinstance(meta, dict):
        latest = meta.get("latestVersion")
        if isinstance(latest, dict):
            latest = latest.get("version") or latest.get("id")
        if latest:
            version = str(latest)
    # 下载 zip
    url = CLAWHUB_BASE + "/download?" + urllib.parse.urlencode({"slug": slug, "version": version})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HermesProfileManager/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            zip_bytes = resp.read()
    except Exception as e:
        return jsonify({"error": f"下载失败: {e}"}), 502
    # 解压（仅文本文件，安全路径校验）
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > 500_000:
                    continue
                name = info.filename.replace("\\", "/")
                # 安全：跳过绝对路径、.. 穿越
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                try:
                    files[name] = zf.read(info.filename).decode("utf-8")
                except UnicodeDecodeError:
                    continue  # 跳过二进制
    except zipfile.BadZipFile:
        return jsonify({"error": "ClawHub 返回了无效的 ZIP"}), 502
    if "SKILL.md" not in files and not any(n.endswith("SKILL.md") for n in files):
        return jsonify({"error": "下载的包不含 SKILL.md，可能不是有效技能"}), 502
    # 扁平化：zip 内若有顶层目录（slug/），剥离它
    names = list(files.keys())
    if all("/" in n for n in names):
        prefix = names[0].split("/")[0]
        if all(n.startswith(prefix + "/") for n in names):
            files = {n[len(prefix) + 1:]: c for n, c in files.items()}
    # 备份已有（冲突时移入 .trash）
    if dst.exists():
        trash = SHARED_SKILLS_DIR / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{slug}_{ts}")
    # 原子写入文件
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    for fname, content in files.items():
        if not fname:
            continue
        target = dst / fname
        try:
            target.resolve().relative_to(dst.resolve())  # 路径边界校验
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_text(target, content)
            written.append(fname)
        except Exception:
            pass
    return jsonify({"ok": True, "message": f"已安装 {slug} 到共享技能库（{len(written)} 个文件）",
                     "files": written, "slug": slug, "version": version, "target": str(dst)})


def _install_skills_sh(slug, source, force, owner_repo, skill_name):
    """从 Skills.sh 拉取 SKILL.md（GitHub raw）并安装到 SHARED_SKILLS_DIR。
    尝试 main 分支，失败回退 master；同时尝试拉取同目录下其他 .md 文件。
    安全：仅下载 .md 文件，单文件 <500KB。"""
    dst = SHARED_SKILLS_DIR / slug
    if dst.exists() and not force:
        return jsonify({"error": "conflict",
                         "message": f"技能 '{slug}' 已存在于共享技能库（{dst}）。如需覆盖请勾选'强制安装'（旧文件会备份到 .trash/）"}), 409
    # 尝试 main 分支，失败回退 master
    raw_base = f"https://raw.githubusercontent.com/{owner_repo}"
    skill_md_content = None
    used_branch = None
    for branch in ("main", "master"):
        url = f"{raw_base}/{branch}/{skill_name}/SKILL.md"
        text = _fetch_url_text(url, timeout=30)
        if text:
            skill_md_content = text
            used_branch = branch
            break
    if not skill_md_content:
        return jsonify({"error": f"无法从 GitHub 拉取 SKILL.md（已尝试 main/master）：{owner_repo}/{skill_name}"}), 502
    if len(skill_md_content.encode("utf-8")) > 500_000:
        return jsonify({"error": "SKILL.md 超过 500KB 限制"}), 413
    # 尝试拉取同目录下的其他 .md 文件（README.md / NOTES.md / EXAMPLES.md）
    extra_files = {}
    for extra_name in ("README.md", "NOTES.md", "EXAMPLES.md"):
        url = f"{raw_base}/{used_branch}/{skill_name}/{extra_name}"
        text = _fetch_url_text(url, timeout=15)
        if text and len(text.encode("utf-8")) <= 500_000:
            extra_files[extra_name] = text
    # 备份已有
    if dst.exists():
        trash = SHARED_SKILLS_DIR / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{slug}_{ts}")
    # 写入
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        atomic_write_text(dst / "SKILL.md", skill_md_content)
        written.append("SKILL.md")
    except Exception as e:
        return jsonify({"error": f"写入 SKILL.md 失败: {e}"}), 500
    for fname, content in extra_files.items():
        try:
            atomic_write_text(dst / fname, content)
            written.append(fname)
        except Exception:
            pass
    return jsonify({"ok": True, "message": f"已安装 {slug} 到共享技能库（{len(written)} 个 .md 文件）",
                     "files": written, "slug": slug, "target": str(dst)})


def _install_custom(slug, source, force, download_url):
    """从自定义源下载并安装到 SHARED_SKILLS_DIR。
    download_url 可为 .md 文件 URL（直接保存为 SKILL.md）或 .zip URL（按 ClawHub 同款逻辑解压）。
    若未传 download_url，则回退到源索引重新查找。"""
    dst = SHARED_SKILLS_DIR / slug
    if dst.exists() and not force:
        return jsonify({"error": "conflict",
                         "message": f"技能 '{slug}' 已存在于共享技能库（{dst}）。如需覆盖请勾选'强制安装'（旧文件会备份到 .trash/）"}), 409
    # 回退：未传 download_url 时重新拉索引查找
    if not download_url:
        items = _search_custom(source, slug, 50)
        match = next((it for it in items if it["slug"] == slug), None)
        if match:
            download_url = match.get("_download_url")
    if not download_url:
        return jsonify({"error": "自定义源未提供 download_url，无法下载"}), 400
    # 下载内容
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "HermesProfileManager/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            content_bytes = resp.read()
    except Exception as e:
        return jsonify({"error": f"下载失败: {e}"}), 502
    # 备份已有
    if dst.exists():
        trash = SHARED_SKILLS_DIR / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{slug}_{ts}")
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    # zip 文件（按扩展名或魔术字节判断）
    is_zip = download_url.lower().endswith(".zip") or content_bytes[:4] == b"PK\x03\x04"
    if is_zip:
        files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or info.file_size > 500_000:
                        continue
                    name = info.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in name.split("/"):
                        continue
                    try:
                        files[name] = zf.read(info.filename).decode("utf-8")
                    except UnicodeDecodeError:
                        continue
        except zipfile.BadZipFile:
            return jsonify({"error": "下载内容不是有效的 ZIP"}), 502
        if "SKILL.md" not in files and not any(n.endswith("SKILL.md") for n in files):
            return jsonify({"error": "下载的包不含 SKILL.md"}), 502
        # 扁平化：剥离 zip 顶层目录
        names = list(files.keys())
        if all("/" in n for n in names):
            prefix = names[0].split("/")[0]
            if all(n.startswith(prefix + "/") for n in names):
                files = {n[len(prefix) + 1:]: c for n, c in files.items()}
        for fname, content in files.items():
            if not fname:
                continue
            target = dst / fname
            try:
                target.resolve().relative_to(dst.resolve())
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                atomic_write_text(target, content)
                written.append(fname)
            except Exception:
                pass
    else:
        # 当作 .md 文本文件保存为 SKILL.md
        if len(content_bytes) > 500_000:
            return jsonify({"error": "下载内容超过 500KB 限制"}), 413
        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "下载内容不是有效的文本"}), 502
        try:
            atomic_write_text(dst / "SKILL.md", text)
            written.append("SKILL.md")
        except Exception as e:
            return jsonify({"error": f"写入失败: {e}"}), 500
    return jsonify({"ok": True, "message": f"已安装 {slug} 到共享技能库（{len(written)} 个文件）",
                     "files": written, "slug": slug, "target": str(dst)})


@app.route("/api/skills-hub/install", methods=["POST"])
def api_hub_install():
    """从指定源下载并安装 skill 到共享技能库（SHARED_SKILLS_DIR）。
    body: {slug, source, force?}
    冲突检测：若 SHARED_SKILLS_DIR/{slug} 已存在且 force!=true，返回 409。
    安全：ClawHub/custom zip 仅解压文本文件(<500KB)，跳过绝对路径/.. 和二进制；
          skills.sh 仅下载 .md 文件(<500KB)。"""
    data = request.get_json() or {}
    slug = data.get("slug")
    source_id = data.get("source")
    force = bool(data.get("force", False))
    if not slug or not _validate_skill_name(slug):
        return jsonify({"error": "invalid skill slug"}), 400
    if not source_id:
        return jsonify({"error": "missing source"}), 400
    # 查找源定义
    sources = load_sources()
    source = next((s for s in sources if s["id"] == source_id), None)
    if not source:
        return jsonify({"error": f"source '{source_id}' not found"}), 404
    src_type = source.get("type")
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if src_type == "clawhub":
        return _install_clawhub(slug, source, force)
    elif src_type == "skills-sh":
        # slug 形如 {owner}__{repo}__{skill_name}，拆分还原
        parts = slug.split("__", 2)
        if len(parts) != 3:
            return jsonify({"error": "skills.sh slug 格式应为 owner__repo__skill_name"}), 400
        owner, repo, skill_name = parts
        return _install_skills_sh(slug, source, force, f"{owner}/{repo}", skill_name)
    elif src_type == "custom":
        return _install_custom(slug, source, force, data.get("download_url"))
    else:
        return jsonify({"error": f"unsupported source type: {src_type}"}), 400


# ── 启动 ──────────────────────────────────────────────────
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
