"""文件改动监听（mtime+size 轮询签名，后续可换 watchdog）。"""
from .paths import PROFILE_FILES, get_profile_names, get_file_path
from .files import get_file_signature


_watch_state = {}


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
