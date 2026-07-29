from ._base import *

bp = Blueprint("profiles", __name__)


@bp.route("/api/profiles")
def api_profiles():
    thin = request.args.get("thin", "").lower() in ("1", "true", "yes")
    # 基于所有 profile 文件的签名计算 ETag（纯 mtime+size，极快）
    etag_parts = []
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
            if sig:
                etag_parts.append(f"{name}:{fk}:{sig[0]}:{sig[1]}")
        if not thin:
            skill_count = len(list_skills(name))
            toolset_total, toolset_enabled = _count_toolsets(name)
            mcp_count = _count_mcp(name)
        else:
            skill_count = toolset_total = toolset_enabled = mcp_count = 0
        profiles.append({"name": name, "files": files, "skill_count": skill_count,
                          "toolset_total": toolset_total, "toolset_enabled": toolset_enabled,
                          "mcp_count": mcp_count})
    # ETag: 哈希所有文件的 mtime+size，文件不变则 ETag 不变
    etag = hashlib.md5("|".join(sorted(etag_parts)).encode()).hexdigest()
    if request.headers.get("If-None-Match") == etag:
        return "", 304
    resp = jsonify({"profiles": profiles})
    resp.headers["ETag"] = etag
    return resp


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
        "signature": list(get_file_signature(fp)) if fp.exists() else None,
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
    # 冲突检测：客户端可传 signature 防止覆盖外部改动
    client_sig = data.get("signature")
    if client_sig is not None:
        disk_sig = get_file_signature(fp)
        if client_sig != (list(disk_sig) if disk_sig else None):
            return jsonify({
                "error": "conflict: file modified externally since last read",
                "code": "conflict",
                "disk_signature": list(disk_sig) if disk_sig else None,
                "client_signature": client_sig,
            }), 409
    try:
        warn = make_backup(fp)
        atomic_write_text(fp, content)
        key = f"{profile_name}:{file_key}"
        new_sig = get_file_signature(fp)
        _watch_state[key] = new_sig
        _log_operation("save_file", profile=profile_name, file=file_key, path=str(fp))
        resp = {"ok": True, "path": str(fp), "signature": list(new_sig) if new_sig else None}
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
