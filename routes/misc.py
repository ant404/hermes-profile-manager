from ._base import *
from urllib.parse import urlparse
import urllib.request as _urlreq
import urllib.error as _urlerr

bp = Blueprint("misc", __name__)


@bp.route("/")
def index():
    # 用绝对路径，避免 PyInstaller exe 运行时 cwd 不在项目目录导致 404
    return send_from_directory(get_app_dir(), "index.html")


@bp.route("/api/info")
def api_info():
    """返回 HERMES_HOME 信息"""
    return jsonify({
        "hermes_home": str(HERMES_HOME),
        "source": HERMES_HOME_SOURCE,
        "detected": HERMES_HOME_SOURCE != "fallback",
        "profiles_dir": str(PROFILES_DIR),
    })


@bp.route("/api/hermes-home", methods=["PUT"])
def api_set_hermes_home():
    """手动设置 HERMES_HOME"""
    data = request.get_json()
    path = data.get("path", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"error": "path does not exist"}), 400
    if not (p / "config.yaml").exists() and not (p / "profiles").exists():
        return jsonify({"error": "not a valid hermes home (no config.yaml or profiles/)"}), 400
    # 保存到配置文件（写在 exe 所在目录，onefile 模式下才能持久化）
    config_file = get_app_dir() / ".hermes_home"
    try:
        atomic_write_text(config_file, str(p))
    except Exception as e:
        return jsonify({"error": f"cannot save config: {e}"}), 500
    return jsonify({"ok": True, "message": "Please restart the server to apply the new HERMES_HOME"})


# ── 主题持久化（.hermes_theme，跨重启保活） ───────────────
# 说明：pywebview 的 localStorage 在某些环境下重启后丢失，
# 改用后端文件持久化，前端启动时读取 API 应用主题。
THEME_FILE = ".hermes_theme"


@bp.route("/api/theme")
def api_get_theme():
    f = get_app_dir() / THEME_FILE
    theme = "dark"
    if f.exists():
        try:
            v = f.read_text(encoding="utf-8-sig").strip().lower()
            if v in ("dark", "light"):
                theme = v
        except Exception:
            pass
    return jsonify({"theme": theme})


@bp.route("/api/theme", methods=["PUT"])
def api_set_theme():
    data = request.get_json() or {}
    theme = (data.get("theme") or "").strip().lower()
    if theme not in ("dark", "light"):
        return jsonify({"error": "invalid theme"}), 400
    try:
        atomic_write_text(get_app_dir() / THEME_FILE, theme)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "theme": theme})


@bp.route("/api/poll")
def api_poll():
    changes = check_changes()
    return jsonify({"changes": changes})


@bp.route("/api/profile/<name>/open-dir", methods=["POST"])
def api_open_profile_dir(name):
    """在文件管理器中打开 profile 根目录"""
    if name not in get_profile_names():
        return jsonify({"error": "invalid profile"}), 400
    path = get_profile_path(name)
    try:
        os.startfile(str(path))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": f"打开目录失败: {e}"}), 500


@bp.route("/api/profile/<profile_name>/discover-models/<int:cp_index>", methods=["POST"])
def api_discover_models(profile_name, cp_index):
    """请求 provider 的 /v1/models 端点获取模型列表。
    支持前端直接传入 provider 配置（未保存时也能探测）。"""
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    body = request.get_json(silent=True) or {}
    cp = body.get("provider")
    if not cp:
        # 回退：从磁盘文件读取
        fp = get_file_path(profile_name, "config.yaml")
        data, err = _load_yaml_file(fp)
        if err or data is None:
            return jsonify({"error": "cannot load config"}), 500
        cp_list = data.get("custom_providers", [])
        if cp_index < 0 or cp_index >= len(cp_list):
            return jsonify({"error": "invalid provider index"}), 400
        cp = cp_list[cp_index]
    base_url = str(cp.get("base_url", "")).rstrip("/")
    api_key = str(cp.get("api_key", ""))
    if not base_url:
        return jsonify({"error": "base_url is empty"}), 400
    # 协议白名单：仅允许 http/https，阻止 file:// / gopher:// 等危险协议
    try:
        parsed = urlparse(base_url)
    except Exception:
        return jsonify({"error": "invalid base_url"}), 400
    if parsed.scheme not in ("http", "https"):
        return jsonify({"error": f"scheme not allowed: {parsed.scheme or '(missing)'}"}), 400
    if not parsed.hostname:
        return jsonify({"error": "base_url missing host"}), 400

    # 构造 /v1/models 或 /models 请求
    models_url = f"{base_url}/models"
    if not models_url.endswith("/v1/models") and not "/v1/models" in models_url:
        if "/v1" not in models_url:
            models_url = f"{base_url}/v1/models"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = _urlreq.Request(models_url, headers=headers, method="GET")
        with _urlreq.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        # OpenAI 格式: {data: [{id: "model-name"}, ...]}
        models = []
        if isinstance(body, dict) and "data" in body:
            for m in body["data"]:
                mid = m.get("id") or m.get("name")
                if mid:
                    models.append(mid)
        elif isinstance(body, list):
            for m in body:
                mid = m.get("id") or m.get("name") if isinstance(m, dict) else str(m)
                if mid:
                    models.append(mid)
        models.sort()
        return jsonify({"models": models, "count": len(models), "url": models_url})
    except _urlerr.HTTPError as e:
        return jsonify({"error": f"HTTP {e.code}: {e.reason}", "url": models_url}), 502
    except _urlerr.URLError as e:
        return jsonify({"error": f"连接失败: {e.reason}", "url": models_url}), 502
    except Exception as e:
        return jsonify({"error": str(e), "url": models_url}), 500


@bp.route("/api/profile/<profile_name>/diff/<file_key>", methods=["POST"])
def api_file_diff(profile_name, file_key):
    """保存前 diff 预览：对比提交内容与磁盘内容，返回 unified diff。
    body: {content: str}
    支持两种 file_key:
      1. 标准 profile 文件: config.yaml / .env / SOUL.md / MEMORY.md / USER.md
      2. skill 文件: skills/<skill_name>/<file_name>（如 skills/my-skill/SKILL.md）"""
    import difflib
    if profile_name not in get_profile_names():
        return jsonify({"error": "profile not found"}), 404
    # 解析 file_key：标准文件 或 skill 文件
    if file_key.startswith("skills/"):
        parts = file_key.split("/", 2)  # ["skills", "<skill_name>", "<file_name>"]
        if len(parts) < 3 or not parts[1] or not parts[2]:
            return jsonify({"error": "invalid skill file key (format: skills/<skill>/<file>)"}), 400
        skill_name, file_name = parts[1], parts[2]
        # 安全检查：skill_name 中不应含路径穿越
        if ".." in skill_name or "/" in skill_name or "\\" in skill_name:
            return jsonify({"error": "invalid skill name"}), 400
        if ".." in file_name or "/" in file_name or "\\" in file_name:
            return jsonify({"error": "invalid file name"}), 400
        skills_dir = get_skills_dir(profile_name)
        file_path = skills_dir / skill_name / file_name
    else:
        file_path = get_file_path(profile_name, file_key)
        if not file_path:
            return jsonify({"error": "invalid file key"}), 400
    data = request.get_json() or {}
    new_content = data.get("content", "")
    # 磁盘现状（可能不存在 → 视为空）
    disk_content = ""
    if file_path.exists():
        try:
            disk_content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return jsonify({"error": f"读取磁盘文件失败: {e}"}), 500
    old_lines = disk_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"磁盘/{file_key}", tofile=f"当前编辑/{file_key}", lineterm="",
    ))
    # 统计增删行数（排除 diff 头 +++/---/@@）
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return jsonify({
        "ok": True,
        "changed": added > 0 or removed > 0,
        "added": added,
        "removed": removed,
        "diff": "".join(l if l.endswith("\n") else l + "\n" for l in diff),
        "disk_signature": get_file_signature(file_path),
    })
