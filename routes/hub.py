from ._base import *
import urllib.request
import urllib.parse
import zipfile
import io

bp = Blueprint("hub", __name__)


def _clawhub_get(path, params=None, timeout=15):
    """调用 ClawHub API，返回 JSON 数据或 None"""
    url = CLAWHUB_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "HermesProfileManager/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_url_text(url, timeout=20, headers=None):
    """通用 HTTP 抓取，返回文本（utf-8，替换非法字节）或 None。"""
    h = {"User-Agent": "HermesProfileManager/1.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _search_clawhub(source, q, limit):
    """搜索 ClawHub 源（使用默认 CLAWHUB_BASE）。返回结果列表（含内部字段，由调用方清理）。"""
    data = _clawhub_get("/skills", {"search": q, "limit": limit})
    if data is None:
        return []
    items = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    result = []
    for item in items:
        slug = item.get("slug")
        if not slug:
            continue
        result.append({
            "slug": slug,
            "name": item.get("displayName") or item.get("name") or slug,
            "description": item.get("summary") or item.get("description") or "",
            "tags": item.get("tags", []) if isinstance(item.get("tags"), list) else [],
            "source": source["id"],
            "source_name": source["name"],
            "trust": "community",
        })
    return result


def _search_skills_sh(source, q, limit):
    """搜索 Skills.sh 源：抓取 HTML 并解析技能入口。
    Skills.sh 是 SPA（JS 渲染），?q= 搜索参数在服务端不生效，返回固定首页 HTML。
    因此改为：抓取首页所有技能链接，再用 q 做客户端关键词过滤。
    slug 用 {owner}__{repo}__{skill_name} 形式（双下划线分隔，避免与 GitHub 名中的 - 冲突）。
    内部字段 _owner_repo / _skill_name 由调用方在返回前剥离。"""
    base = (source.get("url") or "https://www.skills.sh").rstrip("/")
    # 不带 ?q= 参数（SPA 中不生效），直接抓首页
    html = _fetch_url_text(base, timeout=20)
    if not html:
        return []
    # 匹配 href="/owner/repo/skill-name"，owner/repo/skill_name 仅允许 [A-Za-z0-9._-]
    pattern = re.compile(r'href="(/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*))"')
    seen = set()
    result = []
    skip_segments = {"static", "assets", "api", "favicon.ico", "robots.txt", "css", "js", "img", "images"}
    ql = q.lower() if q else ""
    for m in pattern.finditer(html):
        owner, repo, skill_name = m.group(2), m.group(3), m.group(4)
        if owner.lower() in skip_segments or repo.lower() in skip_segments:
            continue
        key = f"{owner}/{repo}/{skill_name}"
        if key in seen:
            continue
        seen.add(key)
        # 客户端关键词过滤（skill_name 或完整路径包含 q）
        if ql and ql not in skill_name.lower() and ql not in key.lower():
            continue
        if len(result) >= limit:
            break
        # slug 用 __ 分隔，install 时再拆分回 owner/repo/skill_name
        slug = f"{owner}__{repo}__{skill_name}"
        result.append({
            "slug": slug,
            "name": skill_name,
            "description": f"{owner}/{repo}/{skill_name}",
            "tags": [],
            "source": source["id"],
            "source_name": source["name"],
            "trust": "community",
            "_owner_repo": f"{owner}/{repo}",
            "_skill_name": skill_name,
        })
    return result


def _search_custom(source, q, limit):
    """搜索自定义源：URL 指向 JSON 索引文件。
    期望格式：[{name, description, download_url, tags?, trust?, slug?}]"""
    url = source.get("url")
    if not url:
        return []
    text = _fetch_url_text(url, timeout=20, headers={"Accept": "application/json"})
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    ql = q.lower()
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        desc = item.get("description") or ""
        # 客户端关键词过滤
        if ql and ql not in name.lower() and ql not in desc.lower():
            continue
        slug = item.get("slug") or name
        if not slug:
            # 从 download_url 末段推导
            dl = item.get("download_url", "")
            slug = dl.rstrip("/").split("/")[-1] if dl else ""
        if not slug:
            continue
        result.append({
            "slug": slug,
            "name": name or slug,
            "description": desc,
            "tags": item.get("tags", []) if isinstance(item.get("tags"), list) else [],
            "source": source["id"],
            "source_name": source["name"],
            "trust": item.get("trust", "community"),
            "_download_url": item.get("download_url"),
        })
        if len(result) >= limit:
            break
    return result


@bp.route("/api/skills-hub/sources")
def api_hub_sources_list():
    """列出所有技能源。"""
    return jsonify({"sources": load_sources()})


@bp.route("/api/skills-hub/sources", methods=["POST"])
def api_hub_sources_add():
    """添加自定义技能源。
    body: {name, url, type}（type 为 clawhub/skills-sh/custom）"""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    src_type = (data.get("type") or "custom").strip()
    if not name or not url:
        return jsonify({"error": "name 和 url 必填"}), 400
    if src_type not in ("clawhub", "skills-sh", "custom"):
        return jsonify({"error": "type 必须为 clawhub/skills-sh/custom"}), 400
    sources = load_sources()
    # 用 type + url 哈希前缀生成唯一 id，避免重复添加同 URL
    sid = src_type + "-" + hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    if any(s["id"] == sid for s in sources):
        return jsonify({"error": "该 URL 已存在"}), 409
    sources.append({"id": sid, "name": name, "type": src_type, "url": url, "enabled": True})
    save_sources(sources)
    return jsonify({"ok": True, "source": sources[-1], "sources": sources})


@bp.route("/api/skills-hub/sources/<sid>", methods=["DELETE"])
def api_hub_sources_delete(sid):
    """删除指定技能源（按 id）。"""
    sources = load_sources()
    new_list = [s for s in sources if s["id"] != sid]
    if len(new_list) == len(sources):
        return jsonify({"error": "source not found"}), 404
    save_sources(new_list)
    return jsonify({"ok": True, "sources": new_list})


@bp.route("/api/skills-hub/sources/<sid>/toggle", methods=["POST"])
def api_hub_sources_toggle(sid):
    """切换技能源的启用/禁用状态。"""
    sources = load_sources()
    found = None
    for s in sources:
        if s["id"] == sid:
            s["enabled"] = not s.get("enabled", True)
            found = s
            break
    if not found:
        return jsonify({"error": "source not found"}), 404
    save_sources(sources)
    return jsonify({"ok": True, "source": found, "sources": sources})


@bp.route("/api/skills-hub/search")
def api_hub_search():
    """跨多源搜索技能。?q=关键词&limit=N&source=源id(可选,缺省/all=全部启用源)
    冲突检测：与 SHARED_SKILLS_DIR 内容比对（安装目标已改为共享技能库）。
    每条结果含：slug, name, description, tags, source(源id), source_name(显示名), trust, conflict(bool)"""
    q = request.args.get("q", "").strip()
    try:
        limit = min(50, max(1, int(request.args.get("limit", "30"))))
    except ValueError:
        limit = 30
    src_filter = request.args.get("source", "").strip()
    sources = load_sources()
    # 仅搜索 enabled 的源；若指定 source 参数则进一步过滤
    active = [s for s in sources if s.get("enabled", True)]
    if src_filter and src_filter != "all":
        active = [s for s in active if s["id"] == src_filter]
    # 已安装的共享技能（用于冲突检测）
    existing = set()
    if SHARED_SKILLS_DIR.exists():
        for p in SHARED_SKILLS_DIR.iterdir():
            if p.is_dir() and not p.name.startswith(".") and not p.name.startswith("_"):
                existing.add(p.name.lower())
    # 跨源搜索（任一源失败不影响其他源）
    all_items = []
    for s in active:
        try:
            if s["type"] == "clawhub":
                items = _search_clawhub(s, q, limit)
            elif s["type"] == "skills-sh":
                items = _search_skills_sh(s, q, limit)
            elif s["type"] == "custom":
                items = _search_custom(s, q, limit)
            else:
                items = []
        except Exception:
            items = []
        all_items.extend(items)
    # 标注冲突，剥离内部字段
    result = []
    for item in all_items:
        slug = item["slug"]
        item["conflict"] = slug.lower() in existing
        item.pop("_owner_repo", None)
        item.pop("_skill_name", None)
        item.pop("_download_url", None)
        result.append(item)
    return jsonify({"items": result, "q": q, "count": len(result), "sources": sources})


def _install_clawhub(slug, source, force):
    """从 ClawHub 下载 zip 包并安装到 SHARED_SKILLS_DIR。
    安全：仅解压文本文件(<500KB)，跳过绝对路径/.. 和二进制。"""
    dst = SHARED_SKILLS_DIR / slug
    if dst.exists() and not force:
        return jsonify({"error": "conflict",
                         "message": f"技能 '{slug}' 已存在于共享技能库（{dst}）。如需覆盖请勾选'强制安装'（旧文件会备份到 .trash/）"}), 409
    # 解析最新版本号
    version = "latest"
    meta = _clawhub_get(f"/skills/{slug}")
    if meta and isinstance(meta, dict):
        latest = meta.get("latestVersion")
        if isinstance(latest, dict):
            latest = latest.get("version") or latest.get("id")
        if latest:
            version = str(latest)
    # 下载 zip
    url = CLAWHUB_BASE + "/download?" + urllib.parse.urlencode({"slug": slug, "version": version})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HermesProfileManager/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            zip_bytes = resp.read()
    except Exception as e:
        return jsonify({"error": f"下载失败: {e}"}), 502
    # 解压（仅文本文件，安全路径校验）
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > 500_000:
                    continue
                name = info.filename.replace("\\", "/")
                # 安全：跳过绝对路径、.. 穿越
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                try:
                    files[name] = zf.read(info.filename).decode("utf-8")
                except UnicodeDecodeError:
                    continue  # 跳过二进制
    except zipfile.BadZipFile:
        return jsonify({"error": "ClawHub 返回了无效的 ZIP"}), 502
    if "SKILL.md" not in files and not any(n.endswith("SKILL.md") for n in files):
        return jsonify({"error": "下载的包不含 SKILL.md，可能不是有效技能"}), 502
    # 扁平化：zip 内若有顶层目录（slug/），剥离它
    names = list(files.keys())
    if all("/" in n for n in names):
        prefix = names[0].split("/")[0]
        if all(n.startswith(prefix + "/") for n in names):
            files = {n[len(prefix) + 1:]: c for n, c in files.items()}
    # 备份已有（冲突时移入 .trash）
    if dst.exists():
        trash = SHARED_SKILLS_DIR / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{slug}_{ts}")
    # 原子写入文件
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    for fname, content in files.items():
        if not fname:
            continue
        target = dst / fname
        try:
            target.resolve().relative_to(dst.resolve())  # 路径边界校验
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_text(target, content)
            written.append(fname)
        except Exception:
            pass
    return jsonify({"ok": True, "message": f"已安装 {slug} 到共享技能库（{len(written)} 个文件）",
                     "files": written, "slug": slug, "version": version, "target": str(dst)})


def _install_skills_sh(slug, source, force, owner_repo, skill_name):
    """从 Skills.sh 拉取 SKILL.md（GitHub raw）并安装到 SHARED_SKILLS_DIR。
    尝试 main 分支，失败回退 master；同时尝试拉取同目录下其他 .md 文件。
    安全：仅下载 .md 文件，单文件 <500KB。"""
    dst = SHARED_SKILLS_DIR / slug
    if dst.exists() and not force:
        return jsonify({"error": "conflict",
                         "message": f"技能 '{slug}' 已存在于共享技能库（{dst}）。如需覆盖请勾选'强制安装'（旧文件会备份到 .trash/）"}), 409
    # 尝试 main 分支，失败回退 master
    raw_base = f"https://raw.githubusercontent.com/{owner_repo}"
    skill_md_content = None
    used_branch = None
    for branch in ("main", "master"):
        url = f"{raw_base}/{branch}/{skill_name}/SKILL.md"
        text = _fetch_url_text(url, timeout=30)
        if text:
            skill_md_content = text
            used_branch = branch
            break
    if not skill_md_content:
        return jsonify({"error": f"无法从 GitHub 拉取 SKILL.md（已尝试 main/master）：{owner_repo}/{skill_name}"}), 502
    if len(skill_md_content.encode("utf-8")) > 500_000:
        return jsonify({"error": "SKILL.md 超过 500KB 限制"}), 413
    # 尝试拉取同目录下的其他 .md 文件（README.md / NOTES.md / EXAMPLES.md）
    extra_files = {}
    for extra_name in ("README.md", "NOTES.md", "EXAMPLES.md"):
        url = f"{raw_base}/{used_branch}/{skill_name}/{extra_name}"
        text = _fetch_url_text(url, timeout=15)
        if text and len(text.encode("utf-8")) <= 500_000:
            extra_files[extra_name] = text
    # 备份已有
    if dst.exists():
        trash = SHARED_SKILLS_DIR / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{slug}_{ts}")
    # 写入
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        atomic_write_text(dst / "SKILL.md", skill_md_content)
        written.append("SKILL.md")
    except Exception as e:
        return jsonify({"error": f"写入 SKILL.md 失败: {e}"}), 500
    for fname, content in extra_files.items():
        try:
            atomic_write_text(dst / fname, content)
            written.append(fname)
        except Exception:
            pass
    return jsonify({"ok": True, "message": f"已安装 {slug} 到共享技能库（{len(written)} 个 .md 文件）",
                     "files": written, "slug": slug, "target": str(dst)})


def _install_custom(slug, source, force, download_url):
    """从自定义源下载并安装到 SHARED_SKILLS_DIR。
    download_url 可为 .md 文件 URL（直接保存为 SKILL.md）或 .zip URL（按 ClawHub 同款逻辑解压）。
    若未传 download_url，则回退到源索引重新查找。"""
    dst = SHARED_SKILLS_DIR / slug
    if dst.exists() and not force:
        return jsonify({"error": "conflict",
                         "message": f"技能 '{slug}' 已存在于共享技能库（{dst}）。如需覆盖请勾选'强制安装'（旧文件会备份到 .trash/）"}), 409
    # 回退：未传 download_url 时重新拉索引查找
    if not download_url:
        items = _search_custom(source, slug, 50)
        match = next((it for it in items if it["slug"] == slug), None)
        if match:
            download_url = match.get("_download_url")
    if not download_url:
        return jsonify({"error": "自定义源未提供 download_url，无法下载"}), 400
    # 下载内容
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "HermesProfileManager/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            content_bytes = resp.read()
    except Exception as e:
        return jsonify({"error": f"下载失败: {e}"}), 502
    # 备份已有
    if dst.exists():
        trash = SHARED_SKILLS_DIR / ".trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(dst), trash / f"{slug}_{ts}")
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    # zip 文件（按扩展名或魔术字节判断）
    is_zip = download_url.lower().endswith(".zip") or content_bytes[:4] == b"PK\x03\x04"
    if is_zip:
        files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or info.file_size > 500_000:
                        continue
                    name = info.filename.replace("\\", "/")
                    if name.startswith("/") or ".." in name.split("/"):
                        continue
                    try:
                        files[name] = zf.read(info.filename).decode("utf-8")
                    except UnicodeDecodeError:
                        continue
        except zipfile.BadZipFile:
            return jsonify({"error": "下载内容不是有效的 ZIP"}), 502
        if "SKILL.md" not in files and not any(n.endswith("SKILL.md") for n in files):
            return jsonify({"error": "下载的包不含 SKILL.md"}), 502
        # 扁平化：剥离 zip 顶层目录
        names = list(files.keys())
        if all("/" in n for n in names):
            prefix = names[0].split("/")[0]
            if all(n.startswith(prefix + "/") for n in names):
                files = {n[len(prefix) + 1:]: c for n, c in files.items()}
        for fname, content in files.items():
            if not fname:
                continue
            target = dst / fname
            try:
                target.resolve().relative_to(dst.resolve())
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                atomic_write_text(target, content)
                written.append(fname)
            except Exception:
                pass
    else:
        # 当作 .md 文本文件保存为 SKILL.md
        if len(content_bytes) > 500_000:
            return jsonify({"error": "下载内容超过 500KB 限制"}), 413
        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "下载内容不是有效的文本"}), 502
        try:
            atomic_write_text(dst / "SKILL.md", text)
            written.append("SKILL.md")
        except Exception as e:
            return jsonify({"error": f"写入失败: {e}"}), 500
    return jsonify({"ok": True, "message": f"已安装 {slug} 到共享技能库（{len(written)} 个文件）",
                     "files": written, "slug": slug, "target": str(dst)})


@bp.route("/api/skills-hub/install", methods=["POST"])
def api_hub_install():
    """从指定源下载并安装 skill 到共享技能库（SHARED_SKILLS_DIR）。
    body: {slug, source, force?}
    冲突检测：若 SHARED_SKILLS_DIR/{slug} 已存在且 force!=true，返回 409。
    安全：ClawHub/custom zip 仅解压文本文件(<500KB)，跳过绝对路径/.. 和二进制；
          skills.sh 仅下载 .md 文件(<500KB)。"""
    data = request.get_json() or {}
    slug = data.get("slug")
    source_id = data.get("source")
    force = bool(data.get("force", False))
    if not slug or not _validate_skill_name(slug):
        return jsonify({"error": "invalid skill slug"}), 400
    if not source_id:
        return jsonify({"error": "missing source"}), 400
    # 查找源定义
    sources = load_sources()
    source = next((s for s in sources if s["id"] == source_id), None)
    if not source:
        return jsonify({"error": f"source '{source_id}' not found"}), 404
    src_type = source.get("type")
    SHARED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if src_type == "clawhub":
        return _install_clawhub(slug, source, force)
    elif src_type == "skills-sh":
        # slug 形如 {owner}__{repo}__{skill_name}，拆分还原
        parts = slug.split("__", 2)
        if len(parts) != 3:
            return jsonify({"error": "skills.sh slug 格式应为 owner__repo__skill_name"}), 400
        owner, repo, skill_name = parts
        return _install_skills_sh(slug, source, force, f"{owner}/{repo}", skill_name)
    elif src_type == "custom":
        return _install_custom(slug, source, force, data.get("download_url"))
    else:
        return jsonify({"error": f"unsupported source type: {src_type}"}), 400
