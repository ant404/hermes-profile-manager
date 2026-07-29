from ._base import *

bp = Blueprint("backup", __name__)


@bp.route("/api/backup", methods=["POST"])
def api_backup():
    """一键备份：扫描 default + 所有 profiles 的 config.yaml + .env
    到 AAAHermesConfigBack/<YYYYMMDD_HHMMSS>/<profile>/"""
    # 校验：避免同秒内重复备份
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = _backup_root()
    backup_dir = root / ts
    if backup_dir.exists():
        return jsonify({"error": f"备份目录已存在: {ts}（同秒内不可重复备份）"}), 400

    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return jsonify({"error": f"创建备份目录失败: {e}"}), 500

    profiles = get_profile_names()
    summary = []
    for pname in profiles:
        src_dir = _profile_backup_source(pname)
        dest_dir = _profile_backup_dest(backup_dir, pname)
        dest_dir.mkdir(parents=True, exist_ok=True)
        files_backed = []
        for fname in BACKUP_FILES:
            src = src_dir / fname
            if src.exists():
                try:
                    shutil.copy2(src, dest_dir / fname)
                    files_backed.append(fname)
                except Exception as e:
                    summary.append({"profile": pname, "error": f"{fname}: {e}"})
                    continue
        summary.append({"profile": pname, "files": files_backed})

    _log_operation("backup", backup_dir=str(backup_dir), profiles=len(profiles))
    return jsonify({
        "ok": True,
        "backup_dir": ts,
        "path": str(backup_dir),
        "profiles": summary,
        "message": f"已备份 {len(profiles)} 个 profile 到 {ts}/",
    })


@bp.route("/api/backups")
def api_list_backups():
    """列出所有备份。返回 [{dir, profiles: [{name, files}]}] 降序"""
    backups = []
    for d in _list_backup_dirs():
        profiles = []
        for pdir in sorted(d.iterdir()):
            if not pdir.is_dir() or pdir.name.startswith("."):
                continue
            files = [f.name for f in pdir.iterdir() if f.is_file() and f.name in BACKUP_FILES]
            if files:
                profiles.append({"name": pdir.name, "files": sorted(files)})
        # 解析日期为可读格式
        name = d.name  # YYYYMMDD_HHMMSS
        readable = f"{name[:4]}-{name[4:6]}-{name[6:8]} {name[9:11]}:{name[11:13]}:{name[13:15]}" if len(name) >= 15 else name
        backups.append({
            "dir": name,
            "readable": readable,
            "profiles": profiles,
            "total_files": sum(len(p["files"]) for p in profiles),
        })
    return jsonify({"backups": backups, "root": str(_backup_root())})


@bp.route("/api/restore", methods=["POST"])
def api_restore():
    """从指定备份恢复。参数：backup_dir, profile, file（config.yaml/.env/all）。
    恢复前自动把当前文件备份到 AAAHermesConfigBack/.backups/"""
    data = request.get_json()
    backup_dir_name = (data or {}).get("backup_dir", "").strip()
    profile_name = (data or {}).get("profile", "").strip()
    file_name = (data or {}).get("file", "all").strip()

    if not backup_dir_name or not profile_name:
        return jsonify({"error": "backup_dir 和 profile 必填"}), 400
    if not re.fullmatch(r"\d{8}_\d{6}", backup_dir_name):
        return jsonify({"error": "无效的备份目录名"}), 400
    if profile_name not in get_profile_names():
        return jsonify({"error": f"无效的 profile: {profile_name}"}), 400

    # 确定要恢复的文件列表
    if file_name == "all":
        files_to_restore = BACKUP_FILES
    elif file_name in BACKUP_FILES:
        files_to_restore = [file_name]
    else:
        return jsonify({"error": f"无效的文件: {file_name}"}), 400

    # 备份源（历史备份）和恢复目标（当前 profile 目录）
    backup_dir = _backup_root() / backup_dir_name / profile_name
    if not backup_dir.exists():
        return jsonify({"error": f"备份目录不存在: {backup_dir_name}/{profile_name}"}), 404

    dest_dir = _profile_backup_source(profile_name)
    pre_backup_dir = _backup_root() / ".backups"
    pre_backup_dir.mkdir(parents=True, exist_ok=True)
    pre_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    restored = []
    warnings = []
    for fname in files_to_restore:
        src = backup_dir / fname
        if not src.exists():
            warnings.append(f"{fname}: 备份中不存在，跳过")
            continue
        dest = dest_dir / fname
        # 恢复前：把当前文件备份到 .backups/
        if dest.exists():
            pre_name = f"{pre_ts}_{profile_name}_{fname.replace('.', '_')}.bak"
            try:
                shutil.copy2(dest, pre_backup_dir / pre_name)
            except Exception as e:
                warnings.append(f"{fname}: 恢复前备份失败 ({e})，仍继续恢复")
        # 恢复（用原子写入确保完整性）
        try:
            content = src.read_text(encoding="utf-8")
            atomic_write_text(dest, content)
            restored.append(fname)
        except Exception as e:
            warnings.append(f"{fname}: 恢复失败 ({e})")

    _log_operation("restore", profile=profile_name, backup_dir=backup_dir_name,
                   files=restored, warnings=warnings)
    return jsonify({
        "ok": True,
        "restored": restored,
        "warnings": warnings,
        "message": f"已恢复 {len(restored)} 个文件到 {profile_name}/",
    })


@bp.route("/api/backups/cleanup", methods=["POST"])
def api_cleanup_backups():
    """清理超过指定天数的备份，但保留最新一份。
    参数：max_age_days（整数）。.backups/ 不参与清理。"""
    data = request.get_json() or {}
    try:
        max_age_days = int(data.get("max_age_days", 30))
    except (ValueError, TypeError):
        return jsonify({"error": "max_age_days 必须是整数"}), 400
    if max_age_days < 1:
        return jsonify({"error": "max_age_days 必须 >= 1"}), 400

    dirs = _list_backup_dirs()  # 降序，最新在前
    if not dirs:
        return jsonify({"ok": True, "deleted": [], "message": "无备份可清理"})

    today = datetime.now().date()
    newest = dirs[0]  # 最新的一份，始终保留
    deleted = []
    for d in dirs:
        if d is newest:
            continue
        # 解析目录名为日期
        try:
            dt = datetime.strptime(d.name, "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        # 用日历日期差（而非 timedelta.days），避免"2天前"因不足48小时而被判为1天
        age_days = (today - dt.date()).days
        if age_days >= max_age_days:
            try:
                shutil.rmtree(d)
                deleted.append(d.name)
            except Exception as e:
                return jsonify({"error": f"删除 {d.name} 失败: {e}"}), 500

    _log_operation("cleanup_backups", deleted=deleted, kept_newest=newest.name, max_age_days=max_age_days)
    return jsonify({
        "ok": True,
        "deleted": deleted,
        "kept_newest": newest.name,
        "message": f"已删除 {len(deleted)} 个超期备份（保留最新: {newest.name}）",
    })


# ── 操作日志查看 ──────────────────────────────────────────
@bp.route("/api/logs/operations")
def api_get_operations_log():
    """读取最近的操作日志（JSONL，最新在前）。?limit=200 控制返回条数。"""
    if not OPERATIONS_LOG.exists():
        return jsonify({"logs": [], "path": str(OPERATIONS_LOG)})
    try:
        limit = max(1, min(2000, int(request.args.get("limit", 200))))
        lines = OPERATIONS_LOG.read_text(encoding="utf-8").strip().split("\n")
        logs = []
        for line in reversed(lines):  # 最新的在前
            line = line.strip()
            if not line:
                continue
            try:
                logs.append(json.loads(line))
            except Exception:
                pass
        return jsonify({"logs": logs[:limit], "path": str(OPERATIONS_LOG), "total": len(logs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/logs/operations", methods=["DELETE"])
def api_clear_operations_log():
    """清空操作日志。"""
    try:
        if OPERATIONS_LOG.exists():
            OPERATIONS_LOG.unlink()
        return jsonify({"ok": True, "message": "操作日志已清空"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
