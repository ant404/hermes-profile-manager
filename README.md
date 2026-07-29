# Hermes Profile Manager

实时编辑 Hermes Agent 各 Profile 配置的桌面 Web 应用。

## 功能

- **多 Profile 管理**：列出 `default` / `coder` / `toy` 等所有 profile，一键切换
- **5 种文件编辑**：每个 profile 下的 `config.yaml` / `.env` / `SOUL.md` / `MEMORY.md` / `USER.md`
- **实时同步**：3 秒轮询外部文件变化，未修改时自动刷新
- **保存前备份**：每次保存自动在 `.backups/` 下留时间戳备份
- **Profile 增删**：新建 profile（自动创建目录和空文件）、删除 profile（移入 `.trash/`）
- **跨 Profile 复制**：一键把某个文件从其他 profile 复制过来
- **模型配置**：可视化管理 config.yaml 中的 models（增删改、Provider 选择、跨 profile 复制 Provider 配置）
- **技能管理**：用户技能 + 内置技能合并展示，支持编辑、跨 profile 复制/移动、抽取到共享库
- **一键抽取**：按来源类型（内置 / 内置→用户 / 自定义）勾选，批量抽取到共享库
- **技能中心**：搜索、浏览、安装技能（含冲突检测和二次确认）
- **工具集 / MCP 查看**：分别展示 profile 的 toolsets（启用/总数）和 mcp_servers
- **操作日志**：记录所有关键操作（抽取、复制、删除、保存、备份等），支持查看和恢复
- **暗色主题**：精细色阶 + 圆角 + 阴影层次 + 等宽字体栈，切换后跨重启持久化
- **快捷键**：`Ctrl+S` 保存 · `Ctrl+B` 折叠侧栏 · `Ctrl+R`/`F5` 刷新 · `Esc` 关闭弹窗
- **加载反馈**：全局加载条 + 按钮加载态 + 阻塞遮罩（批量操作）+ Toast 提示（6s，悬停保留）
- **可折叠侧栏**：宽屏/窄屏自适应，配置组折叠状态记忆到 localStorage

## 安全特性

本工具用于本地编辑 Hermes 配置文件，对「文件完整性」和「接口边界」做了多层防护：

### 文件写入安全
- **原子写入**：所有保存操作先写入同目录临时文件（`<name>.<pid>.<rand>.tmp`），再用 `os.replace` 原子替换目标文件。即使保存过程中崩溃、断电、磁盘满，原文件不会被截断或损坏。
- **保存前备份**：每次写入前复制原文件到 `.backups/<name>_<timestamp>.<ext>`。备份失败不会阻止保存（原子写入已防损坏），但会以 `warning` 字段透传给前端，不再静默吞错。
- **临时文件清理**：写入失败时主动 `unlink` 临时文件，避免残留。

### .env 无损编辑
- 结构化读写 `.env` 时采用「**解析 → 补丁 → 序列化**」流程，而非整体重写。
- 保留原文件的空行、独立注释、`export` 前缀、引号风格（`"` / `'` / 无引号）、行内注释。
- 仅修改值或启用状态的条目会重写该行；未改动的条目原样保留，保证文件 diff 最小化。
- 重复 key 第二次出现时原样保留，避免被意外覆盖。

### 路径穿越防护
- Skill 名严格校验：仅允许 `[A-Za-z0-9][A-Za-z0-9._-]*`，禁止 `..` 和路径分隔符。
- Skill 内文件路径用 `Path.resolve()` + `relative_to()` 做边界判断（非字符串前缀匹配），彻底阻止 `../../etc/passwd` 等穿越攻击。

### API 边界防护
- **CORS 限制**：仅允许 `localhost` / `127.0.0.1` / `::1`（任意端口）跨域访问 `/api/*`，防止任意网页调用本地 API 窃取或篡改配置。
- **Origin 守卫**：`@before_request` 拦截非本机 Origin 的跨域请求，防 DNS rebinding 攻击。
- **SSRF 防护**：discover-models 接口对 `base_url` 做协议白名单校验，仅允许 `http` / `https`，阻止 `file://` / `gopher://` 等危险协议。

## 文件位置对照

| Profile | 根目录 |
|---------|--------|
| `default` | `D:\hermes\` |
| `coder` | `D:\hermes\profiles\coder\` |
| `toy` | `D:\hermes\profiles\toy\` |

每个 profile 下的可编辑文件：

| 文件 | 路径（相对于 profile 根） | 语言 |
|------|--------------------------|------|
| `config.yaml` | `config.yaml` | YAML |
| `.env` | `.env` | INI |
| `SOUL.md` | `SOUL.md` | Markdown |
| `MEMORY.md` | `memories/MEMORY.md` | Markdown |
| `USER.md` | `memories/USER.md` | Markdown |

## 使用

### 方式一：直接运行 exe（推荐）

下载或构建 `dist/HermesProfileManager.exe`，双击即可启动。无需安装 Python 或依赖，启动后自动打开桌面窗口。

### 方式二：从源码运行

#### Windows

双击 `start.bat`（会自动创建 `.venv/` 并安装依赖）

#### Git Bash

```bash
./start.sh
```

#### 任意终端

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

启动后浏览器访问 http://127.0.0.1:18520

### 从源码构建 exe

```bash
.venv\Scripts\pip install pyinstaller packaging
.venv\Scripts\python -m PyInstaller build.spec --noconfirm
```

构建产物：`dist/HermesProfileManager.exe`（约 15 MB，单文件）

> **onefile 模式说明**：所有依赖打包进单个 exe，启动时解压到 `%TEMP%/_MEIxxxx/`，启动比 onedir 慢 1-3 秒，但分发更方便。如需更快启动，可将 [build.spec](build.spec) 中的 `onefile=True` 改为 `False` 并恢复 `COLLECT` 段。

## 环境隔离

- 所有依赖安装在项目内 `.venv/`，不污染系统环境
- 不修改任何 Hermes 运行时文件结构，只读写配置文件
- 备份文件放在各 profile 的 `.backups/` 目录（详见「安全特性 → 文件写入安全」）
- 删除的 profile 移入 `profiles/.trash/`，可手动恢复

## 共享技能库（共同进化）

多个 profile 需要用同一个 skill 时，无需各自复制一份。通过 **NTFS junction（目录连接）** 实现共享：

- **共享库位置**：`<程序所在目录>/AAAHermesHub/shared-skills/<category>/<skill_name>/`
- **抽取到共享库**：将 skill 复制到共享库，原位置替换为 junction（本地版本移到 `.trash/` 可恢复）
- **一键抽取**：技能子标签工具栏「📦 一键抽取」→ 按来源类型勾选（内置 / 内置→用户 / 自定义）→ 批量抽取
- **从共享库引用**：点击"🔗 共享库"按钮，搜索并选择 skill 引用到当前 profile（创建 junction）
- **解除共享**：在共享技能详情页点击"解除共享"，删除 junction 并复制独立副本回 profile
- **从共享库删除**：共享库弹窗中点击「删除」→ 移到 `shared-skills/.trash/`（可恢复），各引用 profile 获得独立副本
- **共同进化**：修改共享库中的 skill 时，所有通过 junction 引用的 profile 同步生效

### 共享库弹窗

- **搜索**：实时过滤技能名称、描述、分类（不区分大小写）
- **引用计数**：每个 skill 显示蓝色「N 引用」徽章
- **冲突检测**：当前 profile 已有的 skill 显示黄色「已存在」标签
- **删除**：每条右侧「删除」按钮，弹窗确认后执行

### 抽取时的冲突处理

| 情况 | 行为 |
|------|------|
| 共享库无同名 | 复制本地→共享库，本地移到 `.trash/`，建 junction |
| 同名 + 内容**相同** | 自动合并：本地移到 `.trash/`，建 junction（共享库不动） |
| 同名 + 内容**不同** | 弹窗提示「跳过 / 替换为共享库版本」；替换时本地移到 `.trash/`，建 junction |
| 该技能已是 junction | 跳过（包括分类级 junction 下的技能） |

### 删除行为

| 操作 | skill 类型 | 行为 |
|------|-----------|------|
| 技能详情页「删除」 | **共享 skill** (junction) | 仅删除当前 profile 的 junction，**共享库内容和其他 profile 不受影响** |
| 技能详情页「删除」 | **非共享 skill** | 移到 `<profile>/skills/.trash/`（可恢复） |
| 技能详情页「删除」 | **内置 skill** | 禁止（返回 403） |
| 共享库弹窗「删除」 | — | 共享库 skill 移到 `shared-skills/.trash/`，各引用 profile 获得独立副本 |

### 分类级 junction

整个分类目录（如 `ui-ux-pro-max/`）也可以是 junction。系统会自动检测：
- 分类级 junction 下的技能标记为 `source="shared"`，不参与批量抽取
- 抽取单个技能时检测祖先 junction，避免误操作共享库内容

> junction 无需管理员权限，对 hermes 完全透明（hermes 用 `os.walk(followlinks=True)` 加载 skill）。
>
> **目录位置说明**：`AAAHermesHub` 位于**程序所在目录**（exe 同级目录，dev 模式下为脚本目录），而非 `HERMES_HOME`。这样重建 exe 时不会被删除（PyInstaller onefile 只覆盖 exe 本身，不清理同目录其他文件），且与 hermes 主目录分离，hermes 更新不影响备份和共享技能。下设 `backups/`（配置备份）、`shared-skills/`（共享技能）、`logs/`（操作日志）三个子目录。首次启动时会自动从旧的 `HERMES_HOME/AAAHermesHub` 或 `HERMES_HOME/AAAHermesConfigBack` 迁移内容。

## 操作日志

所有关键操作自动记录到 `<程序所在目录>/AAAHermesHub/logs/operations.log`（JSONL 格式），用于出问题后查询和恢复：

- **记录的操作**：抽取、引用/解除共享、删除共享/技能、复制/跨 profile 复制、保存技能/配置文件、备份/恢复/清理、修复链接
- **每条记录**：时间、动作、相关 profile/skill、结果（成功/失败）、详情（含 `.trash/` 路径用于恢复）
- **查看**：顶栏「📋 日志」按钮，弹窗显示最近 200 条（最新在前），含恢复提示
- **清空**：日志弹窗底部「清空」按钮

## 技术栈

- 后端：Python + Flask（核心逻辑见 [app.py](app.py)）
- 前端：原生 HTML/CSS/JS（无框架依赖，[index.html](index.html)）
- 桌面壳：pywebview（WebView2 后端）
- 打包：PyInstaller（onefile 模式，见 [build.spec](build.spec)）
- 无需 Node.js，无需编译

## 故障恢复

如果保存后发现问题，可从以下位置恢复：

1. **时间戳备份**：`<profile>/.backups/<file>_<YYYYMMDD_HHMMSS>.<ext>`
2. **一键备份**：`<程序所在目录>/AAAHermesHub/backups/<YYYYMMDD_HHMMSS>/<profile>/`（含 config.yaml + .env）
3. **删除的 profile**：`profiles/.trash/<profile_name>/`
4. **抽取/删除的 profile 技能**：`<profile>/skills/.trash/<skill_name>_<YYYYMMDD_HHMMSS>/`（抽取到共享库或删除时移到这里）
5. **从共享库删除的技能**：`<程序所在目录>/AAAHermesHub/shared-skills/.trash/<skill_name>_<YYYYMMDD_HHMMSS>/`
6. **操作日志**：`<程序所在目录>/AAAHermesHub/logs/operations.log`（JSONL，含每条操作的 `.trash/` 路径，可用于定位恢复）
