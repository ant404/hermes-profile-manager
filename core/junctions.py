"""NTFS junction（目录连接点）管理：创建、删除、检测、目标解析。

Windows junction 无需管理员权限，是共享技能库跨 profile 复用的核心机制。
"""
import subprocess
from pathlib import Path


def is_junction(path):
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


def create_junction(link_path, target_path):
    """创建 NTFS junction（目录连接点），无需管理员权限。
    link_path: junction 路径（如 profile/skills/my_skill）
    target_path: 目标路径（如 shared-skills/my_skill）"""
    link_path = Path(link_path)
    target_path = Path(target_path).resolve()
    if link_path.exists():
        raise FileExistsError(f"目标位置已存在: {link_path}")
    if not target_path.exists():
        raise FileNotFoundError(f"共享技能目录不存在: {target_path}")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"New-Item -ItemType Junction -Path '{link_path}' -Target '{target_path}'"],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise RuntimeError(f"创建 junction 失败: {result.stderr.strip() or result.stdout.strip()}")
    return True


def remove_junction(link_path):
    """删除 junction 本身（不删除目标内容）。
    用 Win32 RemoveDirectoryW 直接删除 reparse point，安全且不递归目标。"""
    link_path = Path(link_path)
    if not is_junction(link_path):
        raise ValueError(f"不是 junction: {link_path}")
    import ctypes
    RemoveDirectory = ctypes.windll.kernel32.RemoveDirectoryW
    RemoveDirectory.argtypes = [ctypes.c_wchar_p]
    RemoveDirectory.restype = ctypes.c_bool
    if not RemoveDirectory(str(link_path)):
        err = ctypes.windll.kernel32.GetLastError()
        raise RuntimeError(f"删除 junction 失败 (Win32 error {err}): {link_path}")
    return True


def get_junction_target(path):
    """获取 junction 的目标路径（用 fsutil 解析 reparse point）"""
    try:
        result = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(path)],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        for line in result.stdout.splitlines():
            if "Substitute Name:" in line:
                target = line.split("Substitute Name:", 1)[1].strip()
                if target.startswith("\\??\\"):
                    target = target[4:]
                return Path(target)
    except Exception:
        pass
    return None
