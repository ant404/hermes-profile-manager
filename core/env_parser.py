""".env 无损解析与回写（保留注释、空行、行内注释、引号）。"""
import re
from pathlib import Path

from .paths import get_profile_path
from .files import read_file_safe, atomic_write_text, make_backup

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

