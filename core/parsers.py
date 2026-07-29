"""工具集 / MCP / 计数 解析。"""
import re
from pathlib import Path

from .paths import HERMES_HOME, get_profile_path


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
