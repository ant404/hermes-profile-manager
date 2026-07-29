"""Skill 解析、目录扫描、读写、usage 追踪。"""
import os
import re
import json
import shutil
import hashlib
from pathlib import Path
from datetime import datetime

from .paths import (HERMES_HOME, BUILTIN_SKILLS_DIR,
    get_profile_path, get_skills_dir, get_profile_names)
from .files import read_file_safe, atomic_write_text, make_backup
from .junctions import is_junction as _is_junction, create_junction as _create_junction


_builtin_skills_cache = None


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
