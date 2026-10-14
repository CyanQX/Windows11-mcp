<div align="center">
  <h1>Windows-MCP 1.1.1</h1>
  <p><b>面向 AI 客户端的 Windows 桌面自动化 MCP 服务器：真实鼠标键盘输入 + UI Automation，操作后自动回读验证</b></p>

  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT">
  <img src="https://img.shields.io/badge/version-1.1.1-blue" alt="Version: 1.1.1">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-blue" alt="Windows 10/11">
  <img src="https://img.shields.io/badge/MCP-stdio%20%7C%20SSE%20%7C%20HTTP-purple" alt="MCP transports">
</div>

Windows-MCP 是一个 MCP 服务器：接入 Codex、Claude Code、Claude Desktop、Gemini CLI 等 AI 客户端后，AI 可以打开程序、点按钮、填表单、选下拉框、敲快捷键、管理窗口和文件。

当前正式版本为 **1.1.1**，提供 **31 个 MCP 工具**。版本的完整源码位于 `main`，固定发布快照为 [`v1.1.1`](https://github.com/CyanQX/Windows11-mcp/tree/v1.1.1)，安装包和校验文件见 [GitHub Releases](https://github.com/CyanQX/Windows11-mcp/releases/tag/v1.1.1)。

1.1.1 已实现统一的 `Observe → Execute → Verify` 语义执行、会话隔离、多订阅 Watch、受管 Playwright 浏览器、限定目录的原生文件 API、窗口绑定的视觉兜底及本地一次性审批。原有 22 个工具保留，所有调用经过同一权限网关。Windows 10、混合 DPI 多显示器及特殊应用的验证范围见[已知限制](#已知限制)。

本项目基于 [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP)（MIT）二次开发，保留上游版权与许可证。

## 目录

- [快速开始](#快速开始)
- [工作方式](#工作方式)
- [升级与审批](#升级与审批)
- [工具一览](#工具一览)
- [重点工具用法](#重点工具用法)
- [运行方式与远程访问](#运行方式与远程访问)
- [环境变量](#环境变量)
- [安全须知](#安全须知)
- [开发与测试](#开发与测试)
- [已知限制](#已知限制)
- [验证情况](#验证情况)
- [更新日志](#更新日志)
- [致谢与许可证](#致谢与许可证)

## 快速开始

### 1. 准备

- Windows 10 / 11
- Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)（`irm https://astral.sh/uv/install.ps1 | iex`）
- 想操作以管理员身份运行的程序时，服务器本身也要以管理员身份运行（Windows 的 UIPI 限制，见[已知限制](#已知限制)）

### 2. 安装

推荐安装固定的 **1.1.1** 发布版本（生成 `%USERPROFILE%\.local\bin\windows-mcp.exe`）：

```powershell
uv tool install --force "git+https://github.com/CyanQX/Windows11-mcp.git@v1.1.1"
```

也可以下载 Releases 中的 wheel，离线保存安装包后安装（首次安装依赖仍需网络）：

```powershell
uv tool install --force E:\Downloads\windows_mcp-1.1.1-py3-none-any.whl
```

> PyPI 上的 `windows-mcp` 由上游项目发布；本仓库的 1.1.1 请从固定 Git tag、GitHub Release 安装包或本仓库源码安装。之前装过原版的，用 `--force` 覆盖即可，客户端可继续使用同一可执行文件路径；权限策略的变化见[升级与审批](#升级与审批)。

浏览器能力是可选依赖：

```powershell
uv tool install --force --with "playwright>=1.51,<2" "git+https://github.com/CyanQX/Windows11-mcp.git@v1.1.1"
```

源码运行用 `uv sync --frozen --extra dev --extra browser`。受管浏览器默认启动已安装的 Edge，使用独立临时 Profile；选择 Chromium 时需先运行 `uv run --extra browser playwright install chromium`。普通桌面工具不依赖 Playwright。

### 3. 接入 AI 客户端

**Codex**（`%USERPROFILE%\.codex\config.toml`）：

```toml
[mcp_servers.windows-mcp]
command = 'C:\Users\<用户名>\.local\bin\windows-mcp.exe'
args = ["serve"]
startup_timeout_sec = 60
```

**Claude Code**：

```powershell
claude mcp add --scope user windows-mcp -- "C:\Users\<用户名>\.local\bin\windows-mcp.exe" serve
```

**Claude Desktop / Cursor / Gemini CLI / Qwen Code 等**（各自的 `mcpServers` 配置）：

```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "C:\\Users\\<用户名>\\.local\\bin\\windows-mcp.exe",
      "args": ["serve"]
    }
  }
}
```

不想安装、直接从源码目录运行：把 `command` 换成 `uv`，`args` 换成 `["--directory", "E:\\path\\to\\Windows-mcp", "run", "windows-mcp", "serve"]`。

<details>
<summary>特殊环境：Microsoft Store 版 Claude Desktop、WSL</summary>

- **Store（MSIX）版 Claude Desktop**：配置文件在 `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`，而且不继承系统 `PATH`，`command` 必须写 `windows-mcp.exe` 的完整路径。改完要从托盘彻底退出再重开。
- **WSL 里的 Claude Code**：服务器必须跑在 Windows 侧：
  `claude mcp add windows-mcp --transport stdio -s user -- powershell.exe -Command "C:\Users\<用户名>\.local\bin\windows-mcp.exe serve"`

</details>

### 4. 试一下

重启客户端后，对 AI 说：“打开记事本，输入‘你好，世界 🌍’，然后把窗口最大化。”

AI 先观察，再提出具体操作。需要审批的调用会被服务器拦截；本地操作员批准后，AI 重复相同调用。默认关闭 R3 任意应用启动、脚本、注册表写入和结束进程。可以先手动打开记事本，使用 `Observe` → `Execute` → `Verify`；窗口切换、调整大小仍可直接执行。

## 工作方式

服务器在连接时就告诉 AI 这套规程（`OBSERVE -> ACT -> VERIFY`）：

1. **观察**：默认 `Observe`、`Find` 或 `Snapshot(use_vision=False, window="…")`。观察包含来源、会话、修订号、输出截断信息；截图用于语义缺失时的兜底。
2. **操作**：新 `Execute` 的 `auto` 先选支持的 UIA Pattern，缺失时才使用物理输入；浏览器通过受管 Playwright 的唯一 locator 操作。旧 `Act` 的 `via` 签名和输入优先行为保留。每条工具入口都经过同一权限网关。
3. **核对**：新动作使用 `VERIFIED`、`DISPATCHED_UNVERIFIED`、`TARGET_STALE`、`AMBIGUOUS_TARGET`、`FOCUS_MISMATCH` 等状态。传入 `expected` 可核对文件内容或控件状态；发出输入、改变焦点或打开窗口不等于业务目标成功。未证实的发送、付款、删除不能自动重试。

几个关键机制：

- **引用有作用域和生存期**：新元素 ID 绑定会话和观察修订；窗口/PID 变化或修订失效后须重新观察。旧 `e#` 和 Snapshot 状态也按客户端隔离；重定位遇到多个同名候选会拒绝，避免按距离猜选。
- **三种操作通道**（`Act` 的 `via` 参数）：
  - `auto`（默认）：真实鼠标键盘优先，失败再退回 UI Automation 模式。
  - `input`：只用真实输入。
  - `uia`：只用 UI Automation 模式，不动用户的鼠标、窗口被挡住也能操作；对经典 Win32 列表、下拉框、滑块，还会补发真实操作本应产生的变更通知，让应用的处理逻辑照常执行。
- **键盘安全锁**：`Type`、`Act(type/set_value)`、带 `window` 的 `Shortcut`，发送按键前都会确认目标窗口在前台；拿不到前台就报错，一个键都不发。
- **原子快捷键**：`ctrl+shift+s` 这样的组合是一次 `SendInput` 批量发送，不会和其它输入交错，修饰键也不会卡住。

## 工具一览

共 31 个工具：11 个只读、20 个会改变系统状态。

| 工具 | 作用 | 类型 |
|---|---|---|
| `Screenshot` | 快速截图 + 光标位置 + 窗口列表；最快的“看一眼” | 只读 |
| `Snapshot` | 窗口的完整控件树，每个元素带 `#N` 标签；支持只抓指定窗口、浏览器 DOM、附带截图 | 只读 |
| `Find` | 在真实窗口里按名称/类型/AutomationId 搜索控件，返回 `e#` 引用、坐标、支持的操作和当前状态 | 只读 |
| `Act` | 对控件做语义操作（点击、切换、选择、展开、填值、追加、滑块、聚焦…）并回读核对 | 写入 |
| `Click` | 真实鼠标点击（坐标或 `#N` 标签），左/右/中键，单击/双击/三击/悬停 | 写入 |
| `Type` | 点击输入框后真实打字；清空、回车、光标位置；打完回读内容 | 写入 |
| `Scroll` | 真实滚轮（含横向），并报告滚动位置前后变化 | 写入 |
| `Move` | 移动鼠标（平滑移动，悬停菜单能响应）或拖放 | 写入 |
| `Shortcut` | 快捷键和组合序列（如 `ctrl+k ctrl+s`），可指定窗口、重复次数 | 写入 |
| `Wait` | 等待若干秒 | 只读 |
| `WaitFor` | 轮询直到出现某文字、某窗口、某元素或焦点 | 只读 |
| `App` | 启动程序（等待并返回新窗口）、切换、调整大小、最小化/最大化/还原/关闭窗口 | 写入 |
| `MultiSelect` | 按住 Ctrl 连续多选（文件、复选项） | 写入 |
| `MultiEdit` | 一次填写多个输入框 | 写入 |
| `Clipboard` | 读取或设置剪贴板文本 | 写入 |
| `PowerShell` | 执行 PowerShell 命令 | 写入 |
| `FileSystem` | 读、写、复制、移动、删除、列目录、搜索、查看文件信息 | 写入 |
| `Registry` | 读、写、删、列注册表 | 写入 |
| `Process` | 列出进程或结束进程（系统关键进程和服务器自身受保护） | 写入 |
| `Notification` | 发送 Windows 通知 | 写入 |
| `DisplayInventory` | 显示器布局、工作区、DPI 和缩放比例 | 只读 |
| `Scrape` | 抓取网页内容，或读取当前浏览器标签页的 DOM 文本 | 只读 |
| `Observe` | 有界语义观察、会话/修订/来源、增量状态 | 只读 |
| `Execute` | 语义优先执行，明确物理输入和业务验证结果 | 写入 |
| `Verify` | 独立核对文件、窗口和控件状态 | 只读 |
| `Watch` | 多订阅、窗口过滤、有界事件等待和状态回读 | 只读 |
| `Browser` | 独立 Playwright Profile、页面/iframe、导航和语义操作 | 写入 |
| `NativeAPI` | 白名单文件提供者；范围检查、独占创建、写后核对 | 写入 |
| `Session` | 会话状态、取消和显式恢复 | 写入 |
| `RequestApproval` | 生成待审请求；不能自行批准 | 写入 |
| `Visual` | 目标窗口截图、负坐标/缩放映射、窗口身份和位置校验 | 只读 |

## 升级与审批

配置文件的 `[policy]` 是本地操作员维护的可信配置，MCP 不能修改。默认文件范围为 `%USERPROFILE%\.windows-mcp\workspace`；配置授权任务目录示例：

```toml
[policy]
workspace_roots = ['E:\my-authorized-task']
allow_r3 = false
allow_private_browser = false
```

R0 观察与查询可直接调用。R1 包括授权目录内新建普通文本文件、窗口切换和滚动。覆盖已有文件、控件修改、原始点击/键盘、网页表单等按 R2 保守处理：服务器无法仅凭控件名称推断业务风险。R3 脚本、注册表修改、进程终止、任意程序启动、移动/删除文件默认关闭。即使本地启用 R3，也仍需一次性审批。

审批步骤：

1. AI 调用 `RequestApproval(tool="Execute", arguments={"target":"观察返回的 ID", "action":"set_value", "value":"hello"}, description="具体内容与目标")`。
2. **本地操作员**在可信终端执行返回的 `windows-mcp approve <request_id>`，查看具体调用的作用域与参数摘要后确认。文本/命令内容在摘要中脱敏，调用摘要和审批哈希绑定原参数。
3. AI 重复完全相同的调用；批准在五分钟内有效，只能消耗一次，换会话、换参数或重放均拒绝。旧工具不加新参数，也可以消耗对应的已批准调用。

`approval_token` 仅接受服务端生成的请求 ID；自然语言“已批准”、网页文字和伪造 JSON 无效。原始鼠标键盘也需要审批，不能借旧工具绕开。服务器审批文件、密钥、用户 Codex/SSH 配置不在工具访问范围内。审计日志记录调用哈希、状态、会话和时间，不保存输入正文、密码、页面内容或异常正文；达到大小上限后轮转。

浏览器示例：`Browser(operation="start")` → `Browser(operation="navigate", url="https://github.com/CyanQX/Windows11-mcp")` → `Observe(source="playwright", page_id="返回的页面 ID")`。控件必须由 role/name 或 test-id 唯一定位；重复按钮、导航后的旧引用安全失败。默认阻止内网地址、未授权的后台 POST 等请求和 WebSocket；不提供用户 cookies、任意 JavaScript、CDP 连接或调试端口。浏览器隔离不等于网络或操作系统沙箱，DNS 到实际连接之间仍存在环境层面的限制。

视觉兜底使用 `Visual(window="句柄或唯一标题", fallback_reason="canvas")`，在返回图片内选择坐标后，审批并执行 `Execute(mode="visual", target="visual:...", action="click", value="[180,90]", expected={...})`。窗口/PID/大小/位置或前台变化、目标点被其他窗口遮挡、截图超过 60 秒，都会拒绝输入。核心安装不包含 OmniParser、VLM 或 FlaUI；这类模型/适配评估为可选后续工作。

远程部署时可以用 `--tools` / `--exclude-tools` 或配置文件裁剪工具，比如只保留 `Screenshot,Find,Act,Click,Type`。

## 重点工具用法

### Find + Act：操作真实控件

```text
Find(name="保存", window="记事本")
  -> e3    Button "保存"  at (812,430)  [invoke]
Act(target="e3", action="click")
  -> click -> Button "保存" (e3) in "无标题 - 记事本": real left click on it at (812,430).
     Effects: window opened: "另存为"; keyboard focus moved to Edit "文件名:".
```

- `target` 可以是 `e#` 引用、Snapshot 的标签号（`12` 或 `"#12"`），也可以直接写控件名称（`"保存"`）。名称有多个同样好的匹配时会返回候选列表，由 AI 挑选，不会随便选一个。
- `window`：窗口标题（包含即可）、进程名（`notepad`、`notepad.exe`）、句柄（`0x1A2B`）、`taskbar`、`desktop`、`*`（全部窗口）；不写就是当前前台窗口。
- 常用动作：
  - `click`、`double_click`、`right_click`、`hover`：真实鼠标。
  - `toggle`：`value` 写 `on`/`off`，已经是目标状态就不再点。
  - `select`：`value` 写选项名；下拉框会先打开再真实点击选项，列表会先滚动到选项。
  - `expand` / `collapse`。
  - `set_value`：替换全部文本。
  - `type`：在末尾追加文本。
  - `set_range`：滑块或数值框。
  - `invoke`、`focus`、`scroll_into_view`。

### Click / Type：坐标或标签

- 坐标是**物理屏幕像素**（虚拟桌面坐标，多显示器可为负）。如果 `Screenshot` 返回的图被缩小了，按输出里的 `Screenshot Coordinate Scale` 换算。
- `Click(label=12)` 会先实时找到 `#12` 对应的控件再点；输出会写明鼠标下面实际是哪个控件。
- `Type` 的 `method`：
  - `auto`：真实按键，超过 2000 字改用粘贴。
  - `keys`：只用按键。
  - `paste`：走剪贴板，之后恢复原来的文本内容。
  - `value`：用 UI Automation 直接设值，需要 `label` 和 `clear=True`。
- 回车（`\n`）会按一次 Enter 键，在单行输入框里可能直接提交表单；多行内容想原样写入，用 `Act(set_value)`。

### Snapshot：只抓需要的窗口

- `Snapshot(window="记事本")` 只遍历这个窗口，比整桌面快得多，也不会因元素上限（默认 500）截断掉真正的按钮。
- `use_words=True` 会把文本框、文档里的每个单词也列成可点击的 `word` 节点（用于点击或选中某个词）；默认关闭。
- `use_dom=True` 读取浏览器页面内容（Chrome、Edge；Firefox 通过 IAccessible2）。

### App：程序与窗口

- `launch`：从开始菜单名称或 `PATH` 上的程序名（`notepad`、`calc`、`mspaint`）启动，等待并返回它的窗口标题和句柄。
- `launch_executable`：精确启动一个 exe，参数分开传，不经过 shell。
- `switch`：切换到窗口，并核对它确实到了前台。
- `resize`：调整窗口位置和大小。
- `minimize` / `maximize` / `restore`：结果都会核对。
- `close`：发送 `WM_CLOSE`，和点右上角 X 一样；如果程序弹出“是否保存”，会如实告诉 AI 窗口还开着。
- 用 `handle` 可以精确指定窗口。

### Shortcut / Scroll

- `Shortcut`：
  - 支持 `ctrl+c`、`alt+f4`、`win+r`、`win`、`f5`、`ctrl++`（Ctrl 加 + 键）、`ctrl+k ctrl+s`（先后两个组合）。
  - `repeat=5` 可重复按。
  - `window="…"` 会先把该窗口置前，失败就不发送。
- `Scroll`：滚完会报告滚动容器的位置变化（例如 `List "Items" scrolled 0.0% -> 21.7%`）；已经到底时会明确说没动。

## 运行方式与远程访问

```powershell
windows-mcp serve                                                    # stdio（本地客户端，默认）
windows-mcp serve --transport streamable-http --host 127.0.0.1 --port 8000
windows-mcp install --transport streamable-http --port 8000          # 注册为登录自启的计划任务
windows-mcp uninstall                                                # 删除计划任务
windows-mcp auth --transport streamable-http --with-tls             # 生成访问密钥（和自签证书），写入配置文件
```

`windows-mcp install` 会创建名为 `windows-mcp-server` 的计划任务和 `~/.windows-mcp/start-server.cmd`；日志写在 `~/.windows-mcp/server.log` 和 `server.error.log`。

HTTP 传输（`sse` / `streamable-http`）的安全设置：

- **非本机地址必须鉴权、TLS 和明确 IP 白名单**：缺少任意一项就拒绝启动。旧 `--allow-insecure-remote` 参数保留解析兼容性，不能绕过此要求。
- `--auth-key`：所有请求都要带 `Authorization: Bearer <key>`。
- `--ip-allowlist "10.0.0.0/8,192.168.1.5"`：只允许这些 IP 或网段连接。
- `--ssl-certfile` / `--ssl-keyfile`：启用 HTTPS。
- OAuth 2.0 + PKCE：客户端必须预先配置，不开放动态注册，回调地址只允许本机。
- 默认**不发任何 CORS 头**，浏览器网页无法跨域访问；确需时用 `--cors-origins` 精确放行。Host 头校验（防 DNS 重绑定）会自动开启。
- `--stateless-http`：streamable-http 无会话模式，适合服务器重启或负载均衡后客户端重连。

也可以写进 `~/.windows-mcp/config.toml`（命令行参数优先；`--config` 可指定其他路径）：

```toml
[server]
transport    = "streamable-http"
host         = "0.0.0.0"
port         = 8000
auth_key     = "换成你的密钥"
ssl_certfile = "cert.pem"        # 相对于配置文件所在目录
ssl_keyfile  = "key.pem"

[security]
ip_allowlist = ["192.168.1.0/24"]

[tools]
exclude = ["PowerShell", "Registry"]
```

## 环境变量

都是可选的，可以写在客户端配置的 `env` 里。

| 变量 | 默认 | 说明 |
|---|---|---|
| `WINDOWS_MCP_SCREENSHOT_SCALE` | `1.0` | 截图缩放（0.1–1.0）；2K/4K 屏幕截图太大时调小 |
| `WINDOWS_MCP_SCREENSHOT_BACKEND` | `auto` | 截图后端：`auto`（dxcam → mss → pillow）、`dxcam`、`mss`、`pillow` |
| `WINDOWS_MCP_MAX_TREE_ELEMENTS` | `500` | 单次 Snapshot 最多收集的元素数，防止超大列表卡死 |
| `WINDOWS_MCP_PROFILE_SNAPSHOT` | 关 | `1` 时输出截图、Snapshot 各阶段耗时 |
| `WINDOWS_MCP_DISABLE_FLASH` | 关 | `1` 时不显示截图后的橙色边框提示 |
| `WINDOWS_MCP_WATCHDOG` | 开 | `off` 关闭焦点监视线程（部分环境长时间运行后不稳定时使用） |
| `WINDOWS_MCP_DEBUG` | 关 | `1` 打开调试日志（等同 `--debug`） |
| `ANONYMIZED_TELEMETRY` | `false` | 匿名使用统计，**默认关闭**；设为 `true` 才开启 |
| `POSTHOG_API_KEY` / `POSTHOG_HOST` | 上游默认 | 开启遥测时使用的 PostHog 项目和地址 |
| `WINDOWS_MCP_AUTH_KEY` | 无 | 同 `--auth-key` |
| `WINDOWS_MCP_IP_ALLOWLIST` | 无 | 同 `--ip-allowlist` |
| `WINDOWS_MCP_CORS_ORIGINS` | 无 | 同 `--cors-origins` |
| `WINDOWS_MCP_TOOLS` / `WINDOWS_MCP_EXCLUDE_TOOLS` | 全部 | 同 `--tools` / `--exclude-tools` |
| `WINDOWS_MCP_SSL_CERTFILE` / `WINDOWS_MCP_SSL_KEYFILE` | 无 | 同 `--ssl-certfile` / `--ssl-keyfile` |
| `WINDOWS_MCP_OAUTH_CLIENT_ID` / `WINDOWS_MCP_OAUTH_CLIENT_SECRET` | 无 | 同 `--oauth-client-id` / `--oauth-client-secret` |
| `WINDOWS_MCP_STATELESS_HTTP` | 关 | 同 `--stateless-http` |
| `WINDOWS_MCP_DESKTOP_TESTS` | 关 | 仅开发用：`1` 时运行真实桌面测试（见[开发与测试](#开发与测试)） |

## 安全须知

**Windows-MCP 不是沙箱。** 它以当前用户的权限直接操作真实系统，每一次调用都是真实动作；删除文件、覆盖文本、确认对话框、修改注册表等操作通常无法撤销。

不建议部署在生产服务器、存有无法恢复数据的机器、受监管环境（医疗、金融、政务）或多人共用的电脑上。更稳妥的做法：

- 在虚拟机或 Windows Sandbox 里使用，操作前打快照；
- 用普通权限、甚至专用账户运行，不要默认以管理员运行；
- 用 `--exclude-tools` 去掉用不到的高风险工具（如 `PowerShell`、`Registry`、`FileSystem`）；
- 所有修改都先经过服务器权限网关；需要审批的动作必须由本地操作员对具体调用批准，提示词不能放行；
- 远程访问务必开鉴权和 TLS，并限制 IP。

各工具风险：

| 风险 | 工具 |
|---|---|
| 极高 | `PowerShell`（任意命令）、`Registry`（系统配置）、`FileSystem`（删除、覆盖） |
| 高 | `Click`、`Act`、`Type`、`Shortcut`、`MultiSelect`、`MultiEdit`（可能点到或确认破坏性操作）、`Process`（结束进程）、`App`（关闭窗口） |
| 中 | `Move`（拖放可能移动文件）、`Clipboard`、`Notification` |
| 低 | `Scroll`、`Wait` |
| 只读 | `Screenshot`、`Snapshot`、`Find`、`WaitFor`、`DisplayInventory`、`Scrape`（截图可能包含敏感信息；Scrape 会访问外部网站） |

内置的防护：

- 键盘安全锁（目标窗口不在前台就不打字）；
- 点击前的命中测试和遮挡检测；
- 输入被系统拦截时如实报错；
- 系统关键进程（`csrss`、`lsass`、`winlogon`、`svchost` 等）、服务器自身和它的 MCP 客户端不能被结束；
- `Scrape` 拦截内网、回环、链路本地地址和带账号密码的 URL（防 SSRF）；
- HTTP 传输的鉴权、CORS、Host 校验（见[运行方式与远程访问](#运行方式与远程访问)）。

**遥测**：本版本默认**关闭**。设 `ANONYMIZED_TELEMETRY=true` 才会开启。开启后，只上报工具名、成功或失败、耗时、客户端名称和版本，以及一个本地随机 ID；**不上报**工具参数（输入的文字、坐标、路径）和结果（截图、命令输出）。发生错误时，错误信息里偶尔可能带有本地路径。

发现安全问题请不要公开提 Issue，请通过 GitHub 的 Security Advisory 私下报告。

## 开发与测试

```powershell
git clone --branch v1.1.1 https://github.com/CyanQX/Windows11-mcp.git
Set-Location Windows11-mcp
uv sync --frozen --extra dev --extra browser
uv run --frozen --extra dev --extra browser pytest --junitxml=reports/test-results/tests.xml
uv run --frozen --extra dev --extra browser ruff check src tests scripts
uv run --frozen windows-mcp serve
```

发布构建使用 `uv build`，生成 wheel 和源代码分发包；`uv run python scripts/verify_distribution.py --dist dist --tag v1.1.1` 检查版本、工具清单及包内执行源码是否与仓库一致。`scripts/smoke_installed.py <windows-mcp.exe 完整路径>` 使用真实 stdio 客户端核对安装后的工具注册、会话与权限拦截。CI 在 Windows 上检查锁定依赖和 FastMCP 4 / MCP 2 两代依赖。

**真实桌面测试**（`tests/desktop`）会启动一个真正的 Win32 测试程序（`tests/fixtures/win32_fixture.py`，含文本框、按钮、复选框、下拉框、列表、滑块、模态对话框和一块画布）。测试用真实工具去操作它，再根据**程序自己收到的事件**来判断：鼠标消息的屏幕坐标、`WM_CHAR` 字符、控件通知。因为会真的移动鼠标、敲键盘，默认不运行，需要手动打开：

```powershell
$env:WINDOWS_MCP_DESKTOP_TESTS = "1"
uv run pytest tests/desktop -v                # 约 30 秒，期间请勿操作鼠标键盘
```

测试的安全措施：

- 测试窗口置顶，两分钟后自动关闭；
- 每个坐标在发送前都要确认属于测试进程，否则不发送；
- 键盘输入受安全锁保护。

代码结构：

```text
src/windows_mcp/
├── __main__.py           命令行、服务器组装、给 AI 的操作规程
├── runtime.py            专用桌面线程（所有 UIA 与输入都在这里串行执行）
├── desktop/
│   ├── native_input.py   SendInput 真实鼠标键盘
│   ├── elements.py       查找 / 引用 / 命中测试 / 语义操作 / 效果观察 / 键盘安全锁
│   └── service.py        Desktop：截图、状态采集、窗口与应用管理
├── tools/                每个 MCP 工具一个模块（element.py 是 Find/Act）
├── engine/               统一观察、元素身份、路由、会话与业务验证
├── browser/              受管 Playwright 与语义定位
├── native_api/           白名单原生文件提供者
├── vision/               绑定窗口身份和有效期的视觉兜底
├── tree/                 控件树遍历（Snapshot）
├── uia/                  UI Automation COM 封装（源自 yinkaisheng/Python-UIAutomation-for-Windows）
├── watchdog/             MTA 事件、多订阅队列、过期清理与状态回读
└── infrastructure/       权限、一次性审批、脱敏审计、鉴权、配置与遥测
```

新增工具时：

- 在 `tools/` 下写 `register(mcp, *, get_desktop, get_analytics)`，再加进 `tools/__init__.py`；
- 标注好 `ToolAnnotations`；
- 碰界面的工具用默认的 `with_analytics(...)`（会在桌面线程运行），不碰界面的传 `offload="background"`；
- 同时更新本 README 的[工具一览](#工具一览)，`tests/test_docs.py` 会检查两边是否一致。

## 已知限制

- **管理员窗口**：普通权限运行时，无法向以管理员身份运行的程序发送输入（Windows UIPI 限制），也无法操作 UAC 弹窗、锁屏和安全桌面；这些情况下工具会报 `InputBlockedError`，而不是假装成功。
- **看不到控件的界面**：游戏、Canvas、图片、远程桌面、部分自绘界面，UI Automation 可能不暴露控件，需要窗口绑定的 `Visual` 兜底；视觉候选仍须验证，不保证自动识别全部软件。
- **`via="uia"`**：只对经典 Win32 列表、下拉框、滑块补发变更通知；其他框架（WPF、Qt、Electron、网页）未必执行相同业务回调。旧 `Act(auto)` 保留真实输入优先；新 `Execute(auto)` 根据 Pattern 支持选择通道并核对结果。
- **粘贴模式**（`Type(method="paste")`）只能恢复剪贴板里原有的文字，原来复制的图片或文件会丢失。
- **快捷键里的字母**按当前键盘布局发送；输入文字请用 `Type`（Unicode 发送，不受输入法影响）。
- **本次未实测**的环境：多显示器（尤其是位于主屏左侧或上方、坐标为负的副屏）、150%/200% 缩放、Windows 10。坐标计算已按虚拟桌面处理并有单元测试，但还没在真机上验证。
- 已发出的 COM 调用不能强制撤回；慢提供者虽然不会阻塞 MCP 主事件循环，仍可能占用桌面工作线程。取消会拦截尚未执行的调用与下一批输入，并允许释放已经按下的鼠标/修饰键。
- 客户端空闲 15 分钟后回收会话和浏览器，断线后不保证立即回收。浏览器能力仅验证本机已安装 Edge；Chrome/Chromium 通道及 Office COM/模型适配仍待单独验收。

## 验证情况

本地功能验收环境为 Windows 11（26200，2560×1440，系统 DPI 96）、Python 3.14.7，锁定依赖 FastMCP 3.4.5 / MCP 1.28.1，浏览器额外依赖 Playwright 1.64.0。发布构建重新校验正式源码、安装包版本和实际 stdio 连接。

| 项目 | 验证范围与结果 |
|---|---|
| 单元与可选浏览器测试 | 本地 656 通过、1 跳过（账户未启用创建符号链接的权限），无失败；包含真实隔离浏览器的表单、iframe、弹窗、重渲染和导航失效测试 |
| 真实桌面功能验收 | 发布前实现的 26 项全部通过；覆盖 Win32 实际输入、MTA 多订阅事件、丢事件回读、取消、语义写入、窗口移动拒绝和旧工具回归 |
| 两代依赖 | 锁定的 FastMCP 3 / MCP 1 与 FastMCP 4 / MCP 2 均通过本地功能验收；CI 持续检查 Python 3.12 / 3.13 与当前兼容依赖 |
| 安装包 | wheel 与源代码分发包的版本均为 1.1.1；包内全部 Python 执行源码与发布目录逐文件校验 |
| 实际 MCP 协议 | 从独立安装环境启动 stdio 服务，核对 31 个工具、可用会话及未授权高风险命令的拒绝结果 |
| 隔离浏览器已有测量 | 简单表单重复任务 10/10 成功；p50 95.50 ms、p95 324.74 ms；该本机测量不代表任意网站的性能 |
| 静态检查 | `ruff check src tests scripts` 通过 |

最新自动检查见 [GitHub Actions](https://github.com/CyanQX/Windows11-mcp/actions/workflows/test.yml)；正式版本的测试记录、安装验证及 SHA256 校验文件随 [1.1.1 Release](https://github.com/CyanQX/Windows11-mcp/releases/tag/v1.1.1) 提供。真实桌面测试需要交互会话，默认不会在无交互 CI 中发送物理输入。

本机备份、缓存、临时兼容环境和历史升级报告不进入发布源码。版本依赖以 `uv.lock` 为准；本地测试输出统一写入 `reports/test-results/`。需要重新导出依赖清单时，可用 `uv export --frozen --no-dev --no-emit-workspace --extra browser --output-file <输出路径>`。

## 更新日志

### 1.1.1

- 发布完整的 31 工具实现：原 22 个兼容工具，加上 `Observe`、`Execute`、`Verify`、`Watch`、`Browser`、`NativeAPI`、`Session`、`RequestApproval`、`Visual`。
- 统一观察与动作结果契约，按会话隔离元素注册表和 Snapshot 状态；窗口、PID、修订、同名歧义与过期引用在执行前检查。
- 新执行通道优先 UIA Pattern；旧 `Act(auto)` 保留真实输入优先行为，维持原工具参数兼容。
- Watch 使用 MTA 事件后端、有界多订阅队列、溢出标记、到期清理及缺事件时的条件回读。
- 增加可选 Playwright 受管浏览器，独立 Profile，支持 iframe、弹窗、SPA 重渲染与导航后的引用失效处理。
- 所有工具统一经过权限网关；限定文件目录、写后核对、会话绑定的一次性本地审批、取消输入与脱敏轮转审计。
- 非回环 HTTP 强制鉴权、TLS 和 IP 白名单；视觉候选绑定窗口身份、位置、缩放及有效期。
- 包版本、manifest、依赖锁、README、Git tag 和发行包统一为 1.1.1，发布前检查分发包完整性并验证独立安装。
- Office COM、OmniParser/VLM 和 FlaUI 为后续可选集成；当前支持范围与未完成的环境验收见“已知限制”。

旧版本实现与说明可在 [历史提交](https://github.com/CyanQX/Windows11-mcp/commits/main) 中追溯。恢复旧版本时，请从已核对的旧 tag 或完整 commit 安装，并重新检查权限配置；重启 MCP 客户端以重新加载工具。发布源码不依赖任何开发者机器上的旧版本备份 ZIP。

## 致谢与许可证

- 上游项目：[CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP)，作者 Jeomon George，MIT 许可证。本仓库的修改同样以 MIT 发布，版权声明见 [LICENSE.md](LICENSE.md)。
- UI Automation 封装源自 [yinkaisheng/Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)（Apache-2.0）。
- MCP 框架：[FastMCP](https://gofastmcp.com)、[Model Context Protocol](https://modelcontextprotocol.io)。
