"""文件安全读写、原子写入、备份、操作日志。"""
import os
import json
import shutil
from pathlib import Path
from datetime import datetime

from .paths import LOGS_DIR, OPERATIONS_LOG


def read_file_safe(path):
    """安全读取文本文件。返回 (content, error)。"""
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
        with open(tmp, "wb") as f:
            f.write(content.encode(encoding))
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def make_backup(path, profile_hint=None):
    """备份文件到 AAAHermesHub/backups/incremental/<profile>/。
    profile_hint: 可选，用于确定 backup 子目录名（如 "default" / "coder"）。
    备份失败不阻止保存：原子写入本身已防止文件损坏，备份只是"可撤销"的便利。"""
    if not path.exists():
        return None
    try:
        from .paths import get_profile_names, get_profile_path
        # 推断 profile 名：按路径长度降序排列（避免 default/HERMES_HOME 先于 profiles/xxx 匹配）
        if profile_hint is None:
            profile_hint = "unknown"
            pns = sorted(get_profile_names(), key=lambda n: len(str(get_profile_path(n))), reverse=True)
            for pn in pns:
                pp = str(get_profile_path(pn))
                if str(path).startswith(pp):
                    profile_hint = pn
                    break
        # 备份目标：AAAHermesHub/backups/incremental/<profile>/<relpath>/
        from .paths import HUB_DIR
        incremental_root = HUB_DIR / "backups" / "incremental" / profile_hint
        # 保持源文件相对 profile 目录的路径结构
        pp = get_profile_path(profile_hint) if profile_hint != "unknown" else Path(path).parent
        try:
            rel = Path(path).resolve().relative_to(pp.resolve())
        except ValueError:
            rel = Path(path).name  # fallback: 只用文件名
        backup_dir = incremental_root / rel.parent
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{path.stem}_{ts}{path.suffix if path.suffix else '.bak'}"
        shutil.copy2(path, backup_dir / backup_name)
        return None
    except Exception as e:
        return f"backup failed: {e}"


def get_file_signature(path):
    """返回文件的 (mtime_ns, size) 签名，用于保存前冲突检测（外部改动）。
    文件不存在返回 None。"""
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def log_operation(action, result="success", **kwargs):
    """记录操作日志到 AAAHermesHub/logs/operations.log（JSONL 格式）。
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


# 原代码使用带下划线的名字，导出别名保持一致
_log_operation = log_operation
