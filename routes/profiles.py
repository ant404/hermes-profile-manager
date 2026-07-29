from ._base import *

bp = Blueprint("profiles", __name__)


@bp.route("/api/profiles")
def api_profiles():
    profiles = []
    for name in get_profile_names():
        files = {}
        for fk, info in PROFILE_FILES.items():
            fp = get_file_path(name, fk)
            sig = get_file_signature(fp)
            files[fk] = {
                "exists": fp.exists(),
                "size": sig[1] if sig else 0,
                "mtime": sig[0] if sig else None,
                "lang": info["lang"],
                "sub": info["sub"],
            }
        # skill 数量（user + builtin，与 skills 列表口径一致）
        skill_count = len(list_skills(name))
        # 工具集 + MCP 统计
        toolset_total, toolset_enabled = _count_toolsets(name)
        mcp_count = _count_mcp(name)
        profiles.append({"name": name, "files": files, "skill_count": skill_count,
                          "toolset_total": toolset_total, "toolset_enabled": toolset_enabled,
                          "mcp_count": mcp_count})
    return jsonify({"profiles": profiles})


@bp.route("/api/profile/<profile_name>/<file_key>")
def api_read_file(profile_name, file_key):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, file_key)
    if not fp:
        return jsonify({"error": "invalid file key"}), 400
    content, err = read_file_safe(fp)
    if err:
        return jsonify({"error": err}), 500
    return jsonify({
        "content": content,
        "path": str(fp),
        "exists": fp.exists(),
        "lang": PROFILE_FILES[file_key]["lang"],
    })


@bp.route("/api/profile/<profile_name>/<file_key>", methods=["PUT"])
def api_save_file(profile_name, file_key):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    fp = get_file_path(profile_name, file_key)
    if not fp:
        return jsonify({"error": "invalid file key"}), 400
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "missing content"}), 400
    content = data["content"]
    try:
        warn = make_backup(fp)
        atomic_write_text(fp, content)
        key = f"{profile_name}:{file_key}"
        _watch_state[key] = get_file_signature(fp)
        _log_operation("save_file", profile=profile_name, file=file_key, path=str(fp))
        resp = {"ok": True, "path": str(fp)}
        if warn:
            resp["warning"] = warn
        return jsonify(resp)
    except Exception as e:
        _log_operation("save_file", result="error", profile=profile_name, file=file_key, error=str(e))
        return jsonify({"error": str(e)}), 500


@bp.route("/api/profile/<profile_name>", methods=["POST"])
def api_create_profile(profile_name):
    """新建 profile 已禁用：profile 创建涉及目录结构/配置模板初始化，
    手动创建易出问题。如需新建请用 hermes CLI 或手动操作 profiles/ 目录。"""
    return jsonify({"error": "已禁用 Profile 新建功能。如需新建请用 hermes CLI 或手动操作 profiles/ 目录。"}), 403


@bp.route("/api/profile/<profile_name>", methods=["DELETE"])
def api_delete_profile(profile_name):
    """删除 profile 已禁用：hermes 运行时可能正在使用某 profile，
    且 sessions 表无可靠激活 profile 标记，误删风险高。
    如需删除请手动操作 profiles/ 目录。"""
    return jsonify({"error": "为安全起见已禁用 Profile 删除功能。如需删除请手动操作 profiles/ 目录。"}), 403


@bp.route("/api/profile/<profile_name>/<file_key>/copy", methods=["POST"])
def api_copy_file(profile_name, file_key):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    data = request.get_json()
    source_profile = data.get("source_profile")
    if not source_profile or source_profile not in get_profile_names():
        return jsonify({"error": "invalid source profile"}), 400
    src_fp = get_file_path(source_profile, file_key)
    dst_fp = get_file_path(profile_name, file_key)
    if not src_fp.exists():
        return jsonify({"error": "source file not found"}), 404
    warn = make_backup(dst_fp)
    dst_fp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_fp, dst_fp)
    key = f"{profile_name}:{file_key}"
    _watch_state[key] = get_file_signature(dst_fp)
    resp = {"ok": True}
    if warn:
        resp["warning"] = warn
    return jsonify(resp)
