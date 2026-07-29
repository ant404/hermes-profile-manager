from ._base import *
from core.skills_lib import _usage_key

bp = Blueprint("skills", __name__)


@bp.route("/api/profile/<profile_name>/skills")
def api_list_skills(profile_name):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    skills = list_skills(profile_name)
    return jsonify({"skills": skills, "count": len(skills)})


@bp.route("/api/profile/<profile_name>/skills/<skill_name>/<path:file_path>")
def api_read_skill_file(profile_name, skill_name, file_path):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    result, err = read_skill_file(profile_name, skill_name, file_path)
    if err:
        return jsonify({"error": err}), 404
    # 推断语言
    ext = Path(file_path).suffix.lstrip(".")
    lang_map = {"md": "markdown", "py": "python", "sh": "bash", "js": "javascript",
                "ts": "typescript", "json": "json", "yaml": "yaml", "yml": "yaml"}
    lang = lang_map.get(ext, "text")
    result["lang"] = lang
    result["skill_name"] = skill_name
    result["file_path"] = file_path
    return jsonify(result)


@bp.route("/api/profile/<profile_name>/skills/<skill_name>/<path:file_path>", methods=["PUT"])
def api_save_skill_file(profile_name, skill_name, file_path):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "missing content"}), 400
    # 冲突检测
    client_sig = data.get("signature")
    if client_sig is not None:
        disk_path = _resolve_skill_dir(profile_name, skill_name)[0]
        if disk_path:
            disk_sig = get_file_signature(disk_path / file_path)
            if client_sig != (list(disk_sig) if disk_sig else None):
                return jsonify({
                    "error": "conflict: file modified externally since last read",
                    "code": "conflict",
                    "disk_signature": list(disk_sig) if disk_sig else None,
                }), 409
    err, warn = save_skill_file(profile_name, skill_name, file_path, data["content"])
    if err:
        _log_operation("save_skill", result="error", profile=profile_name, skill=skill_name, file=file_path, error=err)
        return jsonify({"error": err}), 500
    _log_operation("save_skill", profile=profile_name, skill=skill_name, file=file_path)
    resp = {"ok": True}
    if warn:
        resp["warning"] = warn
    return jsonify(resp)


@bp.route("/api/profile/<profile_name>/skills/<skill_name>", methods=["DELETE"])
def api_delete_skill(profile_name, skill_name):
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    skill_dir, source = _resolve_skill_dir(profile_name, skill_name)
    if not skill_dir:
        return jsonify({"error": "skill not found"}), 404
    if source == "builtin":
        return jsonify({"error": "内置 skill 只读，不可删除"}), 403
    skills_dir = get_skills_dir(profile_name)
    # 检查祖先是否为 junction（分类级共享引用）：
    # 此时 skill_dir 实际在共享库内，直接 rmtree 会删共享库内容。
    # 删除整个分类 junction（不影响共享库），而非单个子 skill。
    current = Path(skill_dir).parent
    while current != skills_dir and current.parent != current:
        if _is_junction(current):
            try:
                cat_name = current.name
                _remove_junction(str(current))
            except Exception as e:
                _log_operation("delete_skill", result="error", profile=profile_name, skill=skill_name, error=str(e))
                return jsonify({"error": f"删除分类共享引用失败: {e}"}), 500
            _log_operation("delete_skill", profile=profile_name, skill=skill_name, shared=True,
                           detail=f"删除分类级 junction '{cat_name}'（共享库内容未动）")
            # 同步 .usage.json：移除该分类下所有子 skill 的条目
            _remove_skill_usage(profile_name, skill_name, cat_name)
            return jsonify({"ok": True, "shared": True, "category_junction": cat_name,
                            "message": f"'{skill_name}' 属于分类级共享引用 '{cat_name}'，已删除整个分类引用（共享库内容未删除）"})
        current = current.parent
    # 获取 skill 的分类（用于 .usage.json 键）
    try:
        rel = Path(skill_dir).relative_to(skills_dir)
        del_category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
    except ValueError:
        del_category = ""
    # 共享 skill（junction）：只删除 junction，不影响共享库内容
    if _is_junction(skill_dir):
        try:
            _remove_junction(str(skill_dir))
        except Exception as e:
            _log_operation("delete_skill", result="error", profile=profile_name, skill=skill_name, error=str(e))
            return jsonify({"error": f"删除共享引用失败: {e}"}), 500
        _cleanup_empty_parent(skill_dir, skills_dir)
        _remove_skill_usage(profile_name, skill_name, del_category)
        _log_operation("delete_skill", profile=profile_name, skill=skill_name, shared=True,
                       detail="仅删除 junction，共享库内容未动")
        return jsonify({"ok": True, "shared": True,
                        "message": f"已删除共享引用 '{skill_name}'（共享库内容未删除，其他 profile 不受影响）"})
    # 非共享 skill：直接删除
    shutil.rmtree(str(skill_dir))
    _cleanup_empty_parent(skill_dir, skills_dir)
    _remove_skill_usage(profile_name, skill_name, del_category)
    _log_operation("delete_skill", profile=profile_name, skill=skill_name)
    return jsonify({"ok": True})


@bp.route("/api/profile/<profile_name>/skills/<skill_name>/copy", methods=["POST"])
def api_copy_skill(profile_name, skill_name):
    """从其他 profile（或内置库）复制 skill 到当前 profile。
    body: {source_profile} — 源 profile 名；若源 profile 无此 skill 则回退查内置库。"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    data = request.get_json() or {}
    source_profile = data.get("source_profile")
    if not source_profile or source_profile not in get_profile_names():
        return jsonify({"error": "invalid source profile"}), 400
    # 源：先查源 profile 的用户 skills，再查内置库（支持嵌套）
    src, src_source = _resolve_skill_dir(source_profile, skill_name)
    if not src:
        return jsonify({"error": "source skill not found"}), 404
    dst = get_skills_dir(profile_name) / skill_name
    if dst.exists():
        # 备份
        trash = dst.parent / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{skill_name}_{ts}")
    shutil.copytree(src, dst)
    _add_skill_usage(profile_name, skill_name)
    _log_operation("copy_skill", profile=profile_name, skill=skill_name, source_profile=source_profile, source=src_source)
    return jsonify({"ok": True, "source": src_source})


@bp.route("/api/profile/<profile_name>/skills/<skill_name>/copy-to", methods=["POST"])
def api_copy_skill_to(profile_name, skill_name):
    """把当前 profile 的 skill 复制或移动到目标 profile。
    body: {target_profile, move} — move=true 时移动（仅用户 skill 可移动，builtin 只复制）。"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    data = request.get_json() or {}
    target_profile = data.get("target_profile")
    move = bool(data.get("move", False))
    if not target_profile or target_profile not in get_profile_names():
        return jsonify({"error": "invalid target profile"}), 400
    if target_profile == profile_name:
        return jsonify({"error": "目标 profile 与源相同"}), 400
    # 源：先用户再内置（支持 builtin 嵌套结构）
    src_user = get_skills_dir(profile_name) / skill_name
    src, src_source = _resolve_skill_dir(profile_name, skill_name)
    if not src:
        return jsonify({"error": "source skill not found"}), 404
    dst = get_skills_dir(target_profile) / skill_name
    if dst.exists():
        trash = dst.parent / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{skill_name}_{ts}")
    shutil.copytree(src, dst)
    moved = False
    # 移动：仅当源是用户 skill 时删除源；内置 skill 不删（只复制）
    if move and src_source == "user" and src_user.exists():
        trash = src_user.parent / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(src_user), trash / f"{skill_name}_{ts}")
        moved = True
    _add_skill_usage(target_profile, skill_name)
    if moved:
        _remove_skill_usage(profile_name, skill_name)
    _log_operation("copy_skill_to", profile=profile_name, skill=skill_name,
                   target_profile=target_profile, move=moved, source=src_source)
    return jsonify({"ok": True, "moved": moved, "target_profile": target_profile,
                     "source": src_source})


@bp.route("/api/profile/<profile_name>/skills/<skill_name>/state", methods=["PUT"])
def api_set_skill_state(profile_name, skill_name):
    """切换 skill 的启用/禁用状态（写入 .usage.json 的 state 字段）。
    body: {enabled: true/false}"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", True))
    # 查找 skill 目录以获取分类
    skill_dir, source = _resolve_skill_dir(profile_name, skill_name)
    if not skill_dir:
        return jsonify({"error": "skill not found"}), 404
    skills_dir = get_skills_dir(profile_name)
    category = ""
    try:
        rel = Path(skill_dir).relative_to(skills_dir)
        category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
    except ValueError:
        pass
    # 更新 .usage.json
    usage = _read_usage_json(skills_dir)
    key = _usage_key(skill_name, category)
    now = datetime.now().astimezone().isoformat()
    if key not in usage:
        # 兼容旧数据：尝试纯 skill_name
        if skill_name in usage:
            key = skill_name
        else:
            usage[key] = {
                "archived_at": None,
                "created_at": now,
                "created_by": None,
                "last_patched_at": None,
                "last_used_at": now,
                "last_viewed_at": now,
                "patch_count": 0,
                "pinned": False,
                "state": "active",
                "use_count": 0,
                "view_count": 0,
            }
    entry = usage[key]
    if enabled:
        entry["state"] = "active"
        entry["archived_at"] = None
    else:
        entry["state"] = "archived"
        entry["archived_at"] = now
    _write_usage_json(skills_dir, usage)
    _log_operation("set_skill_state", profile=profile_name, skill=skill_name,
                   enabled=enabled, category=category)
    return jsonify({"ok": True, "enabled": enabled})
