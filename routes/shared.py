from ._base import *

bp = Blueprint("shared", __name__)


@bp.route("/api/skills/shared/list")
def api_shared_list():
    """列出共享技能库（AAAHermesHub/shared-skills/）里的所有 skill，
    并统计每个 skill 被多少 profile 引用（junction）。
    同时返回分类目录信息，支持分类级引用。"""
    if not SHARED_SKILLS_DIR.exists():
        return jsonify({"skills": [], "categories": []})
    skills = []
    # 收集所有 profile 的 junction 引用
    # skill_ref_counts: skill 名 → 引用数（skill 级 junction）
    # cat_ref_counts: 分类名 → 引用数（分类级 junction，目录本身是 junction 但无 SKILL.md）
    skill_ref_counts = {}
    cat_ref_counts = {}
    for pname in get_profile_names():
        sdir = get_skills_dir(pname)
        if not sdir.exists():
            continue
        for d in sdir.rglob("*"):
            if not (d.is_dir() and _is_junction(d)):
                continue
            if (d / "SKILL.md").exists():
                skill_ref_counts[d.name] = skill_ref_counts.get(d.name, 0) + 1
            else:
                # 分类级 junction：目录是 junction 但无 SKILL.md，子目录有 SKILL.md
                try:
                    has_sub = any((d / sub / "SKILL.md").exists() for sub in os.listdir(d))
                except Exception:
                    has_sub = False
                if has_sub:
                    cat_ref_counts[d.name] = cat_ref_counts.get(d.name, 0) + 1
    # 扫描共享库（支持 category/skill_name 嵌套）
    categories = {}  # category → info
    for skill_md in sorted(SHARED_SKILLS_DIR.rglob("SKILL.md")):
        d = skill_md.parent
        rel = d.relative_to(SHARED_SKILLS_DIR)
        if any(part.startswith(".") or part.startswith("_") for part in rel.parts):
            continue
        category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
        content, err = read_file_safe(skill_md)
        if err:
            continue
        meta, _ = parse_skill_frontmatter(content)
        skills.append({
            "name": d.name,
            "category": category,
            "description": meta.get("description", ""),
            "modified": datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "ref_count": skill_ref_counts.get(d.name, 0),
        })
        if category:
            if category not in categories:
                categories[category] = {"name": category, "skills": [], "ref_count": 0}
            categories[category]["skills"].append(d.name)
    # 填充分类引用计数和子 skill 数
    for cat_info in categories.values():
        cat_info["ref_count"] = cat_ref_counts.get(cat_info["name"], 0)
        cat_info["skill_count"] = len(cat_info["skills"])
    return jsonify({
        "skills": skills,
        "categories": sorted(categories.values(), key=lambda c: c["name"]),
    })


@bp.route("/api/skills/shared/extract", methods=["POST"])
def api_shared_extract():
    """抽取 profile 的 skill 到共享库，原位置替换为 junction（共同进化）。
    body: {profile, skill_name, force?}
    - 用户 skill：移到 .trash，原位置建 junction
    - 内置 skill：复制到共享库，在 profile/skills/ 下建 junction 遮蔽内置（hermes-agent 不动）
    - 冲突处理（共享库已存在同名同分类）：
      * 内容相同 → 自动替换本地（移到 .trash + 建 junction）
      * 内容不同 + force=false → 返回 409 differs=true，由前端提示
      * 内容不同 + force=true → 用户确认放弃本地版本，本地移到 .trash + 建 junction（共享库版本保留）
    保留分类结构：skills/security/X → shared-skills/security/X"""
    data = request.get_json() or {}
    profile = data.get("profile")
    skill_name = data.get("skill_name")
    force = bool(data.get("force", False))
    if not profile or profile not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    skill_dir, source = _resolve_skill_dir(profile, skill_name)
    if not skill_dir:
        return jsonify({"error": "skill not found"}), 404
    # 已是共享技能（skill_dir 自身是 junction）
    if _is_junction(skill_dir):
        return jsonify({"error": "该技能已是共享 junction"}), 400
    # 检测分类级 junction：skill_dir 的祖先目录是 junction → 已通过分类级 junction 共享
    skills_dir = get_skills_dir(profile)
    current = skill_dir.parent
    while current != skills_dir and current.parent != current:
        if _is_junction(current):
            return jsonify({"error": "该技能已通过分类级 junction 共享", "category_junction": str(current)}), 400
        current = current.parent
    # 获取分类：用户 skill 相对 profile/skills/，内置 skill 相对 hermes-agent/skills/
    if source == "builtin":
        try:
            rel = skill_dir.relative_to(BUILTIN_SKILLS_DIR)
            category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
        except ValueError:
            category = ""
    else:
        try:
            rel = skill_dir.relative_to(skills_dir)
            category = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else ""
        except ValueError:
            category = ""
    # 共享库目标（保留分类结构）
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    shared_dir = SHARED_SKILLS_DIR / category / skill_name if category else SHARED_SKILLS_DIR / skill_name
    try:
        if shared_dir.exists():
            # 内容比对决定是自动合并还是需要用户确认
            local_hash = _skill_content_hash(skill_dir)
            shared_hash = _skill_content_hash(shared_dir)
            if local_hash == shared_hash:
                # 内容相同：直接替换本地为 junction（共享库不动，本地备份到 .trash）
                _extract_install_junction(profile, skill_name, category, source, shared_dir, skill_dir)
                _log_operation("extract", profile=profile, skill=skill_name, source=source,
                               category=category, shared_dir=str(shared_dir),
                               conflict="same_content", detail="内容相同，本地移到 .trash + 建 junction")
                return jsonify({"ok": True, "message": f"'{skill_name}' 内容与共享库一致，已替换为 junction（原本地版本备份到 .trash/）"})
            # 内容不同：需用户确认是否放弃本地版本
            if not force:
                return jsonify({"error": "conflict", "differs": True,
                                "message": f"共享库已存在 '{skill_name}' 且内容不同。force=true 表示确认放弃本地版本（移到 .trash），用共享库版本建立 junction"}), 409
            # force=true：用户已确认放弃本地版本（共享库保留原内容，本地移到 .trash + 建 junction）
            _extract_install_junction(profile, skill_name, category, source, shared_dir, skill_dir)
            _log_operation("extract", profile=profile, skill=skill_name, source=source,
                           category=category, shared_dir=str(shared_dir),
                           conflict="diff_content", force=True,
                           detail="内容不同，用户确认放弃本地版本，本地移到 .trash + 建 junction")
            return jsonify({"ok": True, "message": f"'{skill_name}' 已替换为共享库版本（本地原版本已移动到 .trash/，可恢复；共享库内容未改动）"})
        # 无冲突：复制本地到共享库，替换本地为 junction
        shared_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(skill_dir), str(shared_dir))
        _extract_install_junction(profile, skill_name, category, source, shared_dir, skill_dir)
        _log_operation("extract", profile=profile, skill=skill_name, source=source,
                       category=category, shared_dir=str(shared_dir),
                       detail="无冲突，复制本地到共享库 + 本地建 junction")
    except Exception as e:
        _log_operation("extract", result="error", profile=profile, skill=skill_name,
                       error=str(e))
        return jsonify({"error": f"抽取失败: {e}"}), 500
    return jsonify({"ok": True, "message": f"已抽取 '{skill_name}' 到共享库，原位置已替换为 junction（所有引用此共享库的 profile 将共同进化）"})


@bp.route("/api/skills/shared/link", methods=["POST"])
def api_shared_link():
    """从共享库引用 skill 到 profile（创建 junction）。
    body: {profile, skill_name, link_category?}
    - link_category=true: 引用整个分类目录（分类级 junction），所有子 skill 同步可用
    - 默认: 引用单个 skill（skill 级 junction）
    保留分类结构：shared-skills/security/X → skills/security/X"""
    data = request.get_json() or {}
    profile = data.get("profile")
    skill_name = data.get("skill_name")
    link_category = data.get("link_category", False)
    if not profile or profile not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    if link_category:
        # 分类级引用：shared-skills/<category>/ → profile/skills/<category>/
        shared_dir = SHARED_SKILLS_DIR / skill_name
        if not shared_dir.exists() or not shared_dir.is_dir():
            return jsonify({"error": f"共享库中不存在分类 '{skill_name}'"}), 404
        try:
            has_sub = any((shared_dir / sub / "SKILL.md").exists() for sub in os.listdir(shared_dir))
        except Exception:
            has_sub = False
        if not has_sub:
            return jsonify({"error": f"'{skill_name}' 不是分类目录（无子 skill）"}), 400
        target = get_skills_dir(profile) / skill_name
        if target.exists():
            return jsonify({"error": "conflict", "message": f"分类 '{skill_name}' 已存在于 {profile}，请先删除或重命名"}), 409
        try:
            _create_junction(str(target), str(shared_dir))
        except Exception as e:
            _log_operation("link_shared", result="error", profile=profile, skill=skill_name, error=str(e))
            return jsonify({"error": f"引用分类失败: {e}"}), 500
        sub_count = sum(1 for sub in os.listdir(shared_dir) if (shared_dir / sub / "SKILL.md").exists())
        # 为分类下所有子 skill 添加 .usage.json 条目
        for sub in os.listdir(shared_dir):
            if (shared_dir / sub / "SKILL.md").exists():
                _add_skill_usage(profile, sub, skill_name)
        _log_operation("link_shared", profile=profile, skill=skill_name,
                       shared_dir=str(shared_dir), target=str(target), category_level=True)
        return jsonify({"ok": True, "message": f"已引用分类 '{skill_name}'（{sub_count} 个子 skill）到 {profile}"})
    # 单个 skill 引用（原有逻辑）
    shared_dir, category = _find_skill_dir_in(SHARED_SKILLS_DIR, skill_name)
    if not shared_dir:
        return jsonify({"error": "共享库中不存在此 skill"}), 404
    target = get_skills_dir(profile) / category / skill_name if category else get_skills_dir(profile) / skill_name
    if target.exists():
        return jsonify({"error": "conflict", "message": f"'{skill_name}' 已存在于 {profile}，请先删除或重命名"}), 409
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _create_junction(str(target), str(shared_dir))
    except Exception as e:
        _log_operation("link_shared", result="error", profile=profile, skill=skill_name, error=str(e))
        return jsonify({"error": f"引用失败: {e}"}), 500
    _add_skill_usage(profile, skill_name, category)
    _log_operation("link_shared", profile=profile, skill=skill_name,
                   shared_dir=str(shared_dir), target=str(target))
    return jsonify({"ok": True, "message": f"已从共享库引用 '{skill_name}' 到 {profile}（修改共享库内容时同步生效）"})


@bp.route("/api/skills/shared/unlink", methods=["POST"])
def api_shared_unlink():
    """解除共享：删除 junction，复制独立副本回 profile（断开共同进化）。
    body: {profile, skill_name}"""
    data = request.get_json() or {}
    profile = data.get("profile")
    skill_name = data.get("skill_name")
    if not profile or profile not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    # 在 profile 中查找 junction（支持嵌套分类）
    target, _ = _find_skill_dir_in(get_skills_dir(profile), skill_name)
    if not target or not _is_junction(target):
        return jsonify({"error": "该技能不是共享 junction"}), 400
    # 在共享库中查找源
    shared_dir, _ = _find_skill_dir_in(SHARED_SKILLS_DIR, skill_name)
    if not shared_dir:
        return jsonify({"error": "共享源不存在，无法复制独立副本"}), 404
    try:
        # 删除 junction（不删除共享库内容）
        _remove_junction(str(target))
        # 复制独立副本到原位置（保留分类目录）
        shutil.copytree(str(shared_dir), str(target))
    except Exception as e:
        _log_operation("unlink_shared", result="error", profile=profile, skill=skill_name, error=str(e))
        return jsonify({"error": f"解除共享失败: {e}"}), 500
    _log_operation("unlink_shared", profile=profile, skill=skill_name,
                   shared_dir=str(shared_dir), target=str(target),
                   detail="删除 junction，复制独立副本到 profile")
    return jsonify({"ok": True, "message": f"已解除 '{skill_name}' 的共享，现为独立副本（修改不再影响其他 profile）"})


@bp.route("/api/skills/shared/unlink-batch", methods=["POST"])
def api_shared_unlink_batch():
    """批量解除共享：对指定 profile 的多个 junction skill 批量执行 unlink。
    每项独立容错（单个失败不中断整批），返回逐项成功/失败明细。
    body: {profile, skill_names: [str, ...]}"""
    data = request.get_json() or {}
    profile = data.get("profile")
    skill_names = data.get("skill_names") or []
    if not profile or profile not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    if not isinstance(skill_names, list) or not skill_names:
        return jsonify({"error": "skill_names 必须是非空数组"}), 400

    succeeded, failed = [], []
    for skill_name in skill_names:
        if not _validate_skill_name(skill_name):
            failed.append({"skill": skill_name, "error": "invalid skill name"})
            continue
        # 在 profile 中查找 junction（支持嵌套分类）
        target, _ = _find_skill_dir_in(get_skills_dir(profile), skill_name)
        if not target or not _is_junction(target):
            failed.append({"skill": skill_name, "error": "该技能不是共享 junction"})
            continue
        # 在共享库中查找源
        shared_dir, _ = _find_skill_dir_in(SHARED_SKILLS_DIR, skill_name)
        if not shared_dir:
            failed.append({"skill": skill_name, "error": "共享源不存在，无法复制独立副本"})
            continue
        try:
            _remove_junction(str(target))
            shutil.copytree(str(shared_dir), str(target))
            succeeded.append(skill_name)
        except Exception as e:
            failed.append({"skill": skill_name, "error": str(e)})

    _log_operation("unlink_shared_batch", profile=profile,
                   succeeded=succeeded, succeeded_count=len(succeeded),
                   failed_count=len(failed),
                   detail=f"批量解除共享 {len(succeeded)}/{len(skill_names)} 成功")
    return jsonify({
        "ok": True,
        "succeeded": succeeded,
        "failed": failed,
        "succeeded_count": len(succeeded),
        "failed_count": len(failed),
        "message": f"已批量解除 {len(succeeded)} 个技能的共享"
                   + (f"，{len(failed)} 个失败" if failed else ""),
    })


@bp.route("/api/skills/shared/delete", methods=["POST"])
def api_shared_delete():
    """从共享库删除 skill：移到 .trash（可恢复），并为所有引用的 profile 复制独立副本。
    body: {skill_name}"""
    data = request.get_json() or {}
    skill_name = data.get("skill_name")
    if not _validate_skill_name(skill_name):
        return jsonify({"error": "invalid skill name"}), 400
    # 在共享库中查找（支持嵌套分类）
    shared_dir, _ = _find_skill_dir_in(SHARED_SKILLS_DIR, skill_name)
    if not shared_dir:
        return jsonify({"error": "共享库中不存在此 skill"}), 404
    # 解除所有 profile 的 junction，并复制独立副本回 profile
    unlinked = []
    for pname in get_profile_names():
        target, _ = _find_skill_dir_in(get_skills_dir(pname), skill_name)
        if target and _is_junction(target):
            try:
                _remove_junction(str(target))
                # 复制独立副本到原位置（profile 不丢失 skill，变为独立版本）
                shutil.copytree(str(shared_dir), str(target))
                unlinked.append(pname)
            except Exception:
                pass
    # 移到 shared-skills/.trash/（可恢复，不直接删除）
    trash = SHARED_SKILLS_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = trash / f"{skill_name}_{ts}"
    try:
        shutil.move(str(shared_dir), str(dest))
    except Exception as e:
        _log_operation("delete_shared", result="error", skill=skill_name, error=str(e))
        return jsonify({"error": f"删除共享 skill 失败: {e}"}), 500
    _log_operation("delete_shared", skill=skill_name, shared_dir=str(dest),
                   unlinked_profiles=unlinked,
                   detail=f"移到 .trash/，{len(unlinked)} 个 profile 获得独立副本")
    return jsonify({"ok": True, "message": f"已从共享库删除 '{skill_name}'（移到 .trash/，可恢复），并为 {len(unlinked)} 个 profile 复制了独立副本: {', '.join(unlinked) if unlinked else '无'}"})


@bp.route("/api/skills/fix-junctions", methods=["POST"])
def api_fix_junctions():
    """一键修复失效的共享技能 junction。
    当 AAAHermesHub 目录移动后，profile 中的 junction 可能指向旧路径导致失效。
    此接口检测所有指向 shared-skills 但目标已不存在的 junction，在新的
    SHARED_SKILLS_DIR 中查找同名技能并重新链接。"""
    fixed, details = _fix_shared_skill_junctions()
    _log_operation("fix_junctions", fixed=fixed, details=details)
    return jsonify({
        "ok": True,
        "fixed": fixed,
        "details": details,
        "message": f"修复了 {fixed} 个失效的 junction" if fixed
                   else "没有需要修复的 junction（所有链接正常）"
    })
