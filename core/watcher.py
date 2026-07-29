"""文件改动监听（mtime+size 轮询签名 + profile 列表缓存）。"""
import time
from .paths import PROFILE_FILES, get_profile_names, get_file_path
from .files import get_file_signature


_watch_state = {}
_profiles_cache = None
_profiles_cache_ts = 0


def _cached_profile_names():
    """缓存 get_profile_names() 结果 2 秒，避免高频扫描目录。"""
    global _profiles_cache, _profiles_cache_ts
    now = time.time()
    if _profiles_cache is not None and (now - _profiles_cache_ts) < 2:
        return _profiles_cache
    _profiles_cache = get_profile_names()
    _profiles_cache_ts = now
    return _profiles_cache


def init_watch():
    for profile in _cached_profile_names():
        for file_key in PROFILE_FILES:
            fp = get_file_path(profile, file_key)
            key = f"{profile}:{file_key}"
            _watch_state[key] = get_file_signature(fp)


def check_changes():
    changes = []
    for profile in _cached_profile_names():
        for file_key in PROFILE_FILES:
            fp = get_file_path(profile, file_key)
            key = f"{profile}:{file_key}"
            current = get_file_signature(fp)
            if _watch_state.get(key) != current:
                changes.append({"profile": profile, "file": file_key})
                _watch_state[key] = current
    return changes
