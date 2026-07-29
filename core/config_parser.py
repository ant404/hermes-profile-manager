"""config.yaml 结构化解析（schema 驱动 + raw 读写 + custom_providers + MCP）。"""
import io
import re
from pathlib import Path

from ruamel.yaml import YAML

from .paths import get_profile_path
from .files import read_file_safe, atomic_write_text, make_backup

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

