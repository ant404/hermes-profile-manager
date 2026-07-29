"""备份 / 恢复 / 清理 的目录逻辑 + hub 布局迁移。"""
import re
import shutil
from pathlib import Path
from datetime import datetime

from .paths import (HUB_DIR, SHARED_SKILLS_DIR, BACKUP_FILES,
    get_profile_names, get_profile_path, get_skills_dir)
from .files import atomic_write_text
from .junctions import is_junction, get_junction_target, create_junction


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
