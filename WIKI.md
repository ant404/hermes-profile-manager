# Hermes Profile Manager — 架构文档 & 开发日志

> 最后更新：2026-07-29 | v2.0（模块化重构完成）

---

## 一、项目概述

基于 Flask + pywebview 的 Hermes Agent 配置管理桌面工具。通过 Web UI 编辑 profile 的 `config.yaml`、`.env`、`SOUL.md`/`MEMORY.md`/`USER.md`、skill 文件，支持共享技能库（junction）、备份/恢复、skill hub 搜索安装。

### 技术栈

| 层 | 技术 |
|---|------|
| 后端 | Python 3.11 + Flask + CORS |
| 前端 | 原生 HTML/CSS/JS（零框架，~2700 行） |
| 桌面壳 | pywebview（WinForms 后端） |
| 打包 | PyInstaller onefile → 20MB exe |
| YAML | ruamel.yaml |
| 静态分析 | pygount / flake8 |

### 目录结构（拆分后）

```
hermes-profile-manager/
├── app.py              # 薄工厂：Flask() + CORS + 蓝图注册（~62行）
├── main.py             # pywebview 入口
├── build.spec          # PyInstaller 打包配置
├── index.html          # HTML 骨架（~2KB）
├── static/
│   ├── css/style.css   # 样式（~29KB）
│   └── js/main.js      # 逻辑（~160KB, ~2800行）
├── core/               # 纯逻辑层（零 Flask 依赖）
│   ├── paths.py        # HERMES_HOME 探测、profile 路径解析
│   ├── files.py        # 安全读写、原子写入、备份、签名
│   ├── junctions.py    # NTFS junction/符号链接
│   ├── skills_lib.py   # 技能发现、解析、保存、校验
│   ├── config_parser.py# config.yaml 结构化解析
│   ├── env_parser.py   # .env 行级解析
│   ├── backup_lib.py   # 备份/恢复/清理
│   ├── watcher.py      # mtime+size 轮询 + profile 缓存
│   ├── sources.py      # hub 源配置
│   └── parsers.py      # 工具集/MCP 计数
├── routes/             # Flask Blueprint（每个文件一个蓝图）
│   ├── _base.py        # 公共导入基座（含 __all__）
│   ├── misc.py         # /, info, theme, models, diff
│   ├── profiles.py     # profiles CRUD + ETag/冲突检测
│   ├── skills.py       # skill 读写删/复制/移动
│   ├── shared.py       # 共享库 + unlink/unlink-batch
│   ├── config_env.py   # config.yaml/.env 读写
│   ├── backup.py       # 备份/恢复/清理
│   ├── inspect.py      # toolsets/MCP
│   └── hub.py          # skill hub 搜索/安装
└── AAAHermesHub/        # 运行时数据（gitignore）
    ├── backups/
    │   ├── incremental/ # 每次保存前的增量备份（新）
    │   └── <ts>/        # 全量快照备份
    ├── shared-skills/   # 共享技能库
    └── logs/            # 操作日志
```

---

## 二、设计决策（为什么这样做）

### 2.1 模块化拆分（B 系列）

**问题**：原 `app.py` 单体 3223 行，路由逻辑、核心逻辑、前端全部混在一起。

**方案**：三层拆分——
1. `core/`：纯 Python 逻辑，零 Flask import，可单独测试
2. `routes/`：薄路由层，只做参数解析/校验，调用 core 函数
3. `static/`：前端 CSS/JS 从 HTML 内联抽离

**关键决策**：
- `_base.py` 用 `from ._base import *` 模式避免每个路由文件重复 import
- `**__all__**` 显式导出带下划线名字（`_log_operation`、`_count_toolsets` 等），因为 Python 的 `import *` 默认跳过 `_` 前缀
- 子代理并行生成 8 个 routes 文件（delegate_task 3-way fan-out），每个文件 AST 验证通过后整合

### 2.2 前端零框架

**决策**：不使用 React/Vue，保持原生 JS。理由：工具类应用逻辑固定，原生 JS 足够；避免构建步骤；URL 路由不冲突。

**踩坑**：内联 `onclick="toggleTheme()"` 依赖全局函数——拆分后 JS 放 `<script src>` 末尾加载，时序安全（函数在点击时才求值）。

### 2.3 保存前 diff 预览

**设计**：保存 raw 文本前调 `/api/profile/<p>/diff/<file_key>`，对比磁盘内容与编辑器内容。有差异 → 弹窗展示 unified diff → 确认保存。

**为什么不用 WebSocket**：diff 是保存时的快照对比，不需要实时同步。用 POST 接口简单可靠。

**坑**：skill 文件路径包含 `/`（如 `skills/my-skill/SKILL.md`），Flask 默认路由 `<file_key>` 不匹配。解决方案：在 diff handler 中手动解析 `skills/<name>/<file>` 前缀。

### 2.4 冲突检测

**设计**：保存时接受可选 `signature` 字段（`[mtime_ns, size]`），与磁盘当前签名比对。不匹配 → 409 Conflict。

**关键**：签名用 tuple `(mtime_ns, size)` 而非内容 hash——文件不变时 mtime+size 足够快（纯 stat()），且文件变了必然触发。

### 2.5 `/api/profiles` 瘦身

**问题**：每次调用遍历所有 skill 并解析 frontmatter + 解析 config.yaml 计数 toolsets/MCP，耗时长。

**方案**：
1. **ETag**：基于所有 profile 文件的 mtime+size 计算哈希，浏览器可带 `If-None-Match: <etag>` 获取 304 空响应
2. **?thin=1**：跳过 `list_skills()`/`_count_toolsets()`/`_count_mcp()`，只返回文件元数据

### 2.6 共享技能（junction）

**设计**：NTFS junction 链接 profile 的 `skills/<name>` → `AAAHermesHub/shared-skills/<name>`。修改共享库内容 → 所有引用 profile 自动同步。

**批量 unlink**：`POST /api/skills/shared/unlink-batch` 一次请求处理 N 个 junction 解除，逐项容错（单个失败不中断整批）。

### 2.7 备份策略

**两种备份**：
1. **增量备份**（`make_backup`）：每次保存前自动备份到 `AAAHermesHub/backups/incremental/<profile>/`，格式 `file_<ts>.ext`
2. **全量快照**（备份管理界面）：手动/定时创建完整 profile 快照到 `AAAHermesHub/backups/<YYYYMMDD_HHMMSS>/`

**v2.1 修复**：增量备份原写 `<profile_dir>/.backups/`，污染 hermes 目录。改为统一存储在 `AAAHermesHub/backups/incremental/`。

---

## 三、踩坑记录

### 3.1 Python `import *` 不导出下划线名字

`from ._base import *` 默认不导出 `_xxx` 开头的名字。routes 文件里用到的大量 `_log_operation`、`_count_toolsets`、`OPERATIONS_LOG` 全部 `NameError`。

**解决**：在 `_base.py` 末尾加 `__all__ = [name for name in globals() if not name.startswith("__")]`，显式导出所有非 dunder 名字。

### 3.2 Flask `static_folder="."` 与拆分后路径不匹配

原 `app.py` 用 `static_folder="."`，拆分后 `static/css/style.css` 对应磁盘 `./css/style.css`（少一层 `static/`）。

**解决**：新 `app.py` 用 `static_folder="static"`，`/` 路由用 `send_from_directory(current_app.root_path, "index.html")`。

### 3.3 PyInstaller onefile 下的 `get_app_dir()` vs `_MEIPASS`

`get_app_dir()` 在 frozen 模式返回 exe 所在目录，但打包的 `index.html`/`static/` 在 `_MEIPASS`（临时解压目录）。

**解决**：`index()` 路由改用 `current_app.root_path`（Flask 自动解析到模块所在目录 = `_MEIPASS`）。

### 3.4 `_ENV_LINE_RE` 正则常量切分遗漏

原 `app.py:1953` 定义的 `_ENV_LINE_RE` 在 `env_parser.py` 中被使用但未定义。

**解决**：补回 `env_parser.py` 顶部。

### 3.5 批量 unlink 的 Flask 路由 `/` 重复

`misc.py` 和 `app_new.py` 都注册了 `/`。Flask 会警告重复路由。

**解决**：删掉 `app.py` 工厂中的 index，保留 `misc.py` 中的版本。

### 3.6 路径穿越在 diff API 中的双重防护

`/api/profile/coder/diff/../../etc/passwd` → Flask 路由层先拦截返回 404（Werkzeug URL 规范化）。

`/api/profile/coder/diff/__nonexistent__` → 应用层 `get_file_path()` 返回 None → 400。

### 3.7 Ctrl+S 全局快捷键

`document.addEventListener("keydown", ...)` 在 `init()` 中注册。`contenteditable` 或 `<textarea>` 聚焦时最好不拦截（当前实现所有场景都拦截，适合工具类应用）。

### 3.8 watch 不需要 `/api/watch` 端点

原设计可能计划暴露 watch 状态给前端。实际上 `startPolling` 前端自己定时调 `/api/profiles` + ETag 即可。不需要独立端点。

---

## 四、保存操作对 Hermes 的副作用审查

### 4.1 config.yaml 编辑

**直接影响**：
- 🔴 **修改 `model.*` 会立即影响 LLM 调用**（provider/model/fallback 配置）
- 🔴 **修改 `tools.*` 会改变可用工具集**
- 🟡 YAML 格式错误 → Hermes 启动失败或配置回退
- 🟡 修改 `gateway.*` 会影响消息渠道连接

**防护**：备份 + diff 预览 + 冲突检测三重保护

### 4.2 .env 编辑

**直接影响**：
- 🔴 **修改 API key** 会立即影响认证（如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`）
- 🟡 修改 `HERMES_HOME` 会导致路径错误
- 🟡 修改 `OBSIDIAN_VAULT_PATH`、`NOTION_TOKEN` 等会影响集成

### 4.3 共享技能 (junction) 编辑

**直接影响**：
- 🔴 **修改共享库中的 skill → 所有引用此 skill 的 profile 同步生效**（设计意图：共同进化）
- 🟡 删除共享 junction → 仅移除当前 profile 的引用，其他 profile 不受影响
- 🟢 创建 junction → 无副作用

### 4.4 文件保存流程

```
编辑器修改 → Ctrl+S / 点击保存
  ↓
confirmDiffSave(file_key, content, callback)
  ├─ POST /api/profile/<p>/diff/<file_key>  ← 对比磁盘
  ├─ 无变化 → 直接保存（跳过弹窗）
  └─ 有变化 → 弹窗展示 diff → 确认
       ↓
PUT /api/profile/<p>/<file_key> {content, signature?}
  ├─ signature 匹配 → 调用 make_backup() → atomic_write_text() → 200
  ├─ signature 不匹配 → 409 Conflict
  └─ 无 signature(向后兼容) → 直接写入
```

---

## 五、验证基准

每次改动后运行：

```bash
# 后端
cd hermes-profile-manager && HERMES_HOME= .venv/Scripts/python.exe _regression.py

# 前端语法
node --check static/js/main.js

# 打包
.venv/Scripts/python.exe -m PyInstaller build.spec --noconfirm
```
