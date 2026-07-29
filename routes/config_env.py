from ._base import *

bp = Blueprint("config_env", __name__)


@bp.route("/api/profile/<profile_name>/config-schema")
def api_config_schema(profile_name):
    """返回 config.yaml 的字段 schema"""
    return jsonify({"schema": CONFIG_SCHEMA})


@bp.route("/api/profile/<profile_name>/config")
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


@bp.route("/api/profile/<profile_name>/config", methods=["PUT"])
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


@bp.route("/api/profile/<profile_name>/config/raw")
def api_read_config_raw(profile_name):
    """读取 config.yaml 原始文本 (高级模式用)"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, "config.yaml")
    content, err = read_file_safe(fp)
    if err:
        return jsonify({"error": err}), 500
    return jsonify({"content": content, "path": str(fp), "lang": "yaml"})


@bp.route("/api/profile/<profile_name>/config/raw", methods=["PUT"])
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


@bp.route("/api/profile/<profile_name>/env")
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


@bp.route("/api/profile/<profile_name>/env", methods=["PUT"])
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
