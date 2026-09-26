<div align="center">
  <h1>Windows-MCP 0.9</h1>
  <p><b>面向 AI 客户端的 Windows 桌面自动化 MCP 服务器：真实鼠标键盘输入 + UI Automation，操作后自动回读验证</b></p>

  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-blue" alt="Windows 10/11">
  <img src="https://img.shields.io/badge/MCP-stdio%20%7C%20SSE%20%7C%20HTTP-purple" alt="MCP transports">
</div>

Windows-MCP 是一个 MCP 服务器：接入 Codex、Claude Code、Claude Desktop、Gemini CLI 等 AI 客户端后，AI 可以打开程序、点按钮、填表单、选下拉框、敲快捷键、管理窗口和文件。

本仓库基于 [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 0.8.5（MIT）二次开发，版本 **0.9.0**。核心目标只有一个：**不只是“看懂屏幕”，而是在本机真实地完成操作，并证明操作确实生效。**

## 目录

- [这一版改了什么](#这一版改了什么)
- [快速开始](#快速开始)
- [工作方式](#工作方式)
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

## 这一版改了什么

| 方向 | 做了什么 | 效果（本机实测） |
|---|---|---|
| **更真实** | 新增 `Find` / `Act`：直接找到并操作真实控件。默认用**真实鼠标键盘**（应用会执行自己的事件处理，和人操作完全一样），做不到时才退回 UI Automation 模式 | 下拉框选择由应用自己收到 `CBN_SELCHANGE`、复选框收到 `BN_CLICKED`——不是“界面变了但程序没反应” |
| **更准** | 输入改用 `SendInput`：虚拟桌面绝对坐标（多显示器/负坐标）、每个快捷键一次原子发送、只给真正的扩展键加扩展标志、文本按 UTF-16 发送 | 点击落点与目标像素**完全一致**；中文、emoji、`{}` 等原样到达（系统装有中文输入法时同样成立） |
| **更准** | Snapshot 标签和 `e#` 引用在每次操作前**实时重新定位**：先滚动到可见、窗口置前，再对落点做命中测试 | 窗口移动后用旧标签点击，仍然点中原来的按钮；被别的窗口挡住时会先把目标窗口置前 |
| **更像人** | 每次操作后回读控件状态（`Verified` / `NOT verified`），并报告“效果”：新开/关闭的窗口、标题变化、前台和焦点变化 | 点“打开对话框”会明确告诉 AI：`window opened: "Fixture dialog"` |
| **更快** | 删除启动时无意义的 `sleep(1)`；遥测改为按需加载；Snapshot 默认不再把编辑器里每个单词当成节点；新增按窗口的 Snapshot 和 37 ms 级的 `Find`；开始菜单应用列表缓存 | 启动 3.2 s → 1.9 s；整桌面 Snapshot 584 ms → 304 ms，输出 30.7K → 8.9K 字符 |
| **更稳** | 所有界面操作集中到**一个专用线程**（COM 只初始化一次，两个工具调用的输入不会交错）；**键盘安全锁**：目标窗口不在前台就拒绝打字；输入被系统拦截时如实报错 | 焦点被抢走时不会把字打进别的窗口 |
| **修复** | Snapshot 纯文本结果被序列化成一行 JSON（模型看到的是 `["\n  ...\\n..."]`）；截图失败被当成“成功”返回；Scrape 阻塞事件循环；进程工具可以杀掉系统关键进程或服务器自身 | 均已修复并有回归测试 |

## 快速开始

### 1. 准备

- Windows 10 / 11
- Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)（`irm https://astral.sh/uv/install.ps1 | iex`）
- 想操作以管理员身份运行的程序时，服务器本身也要以管理员身份运行（Windows 的 UIPI 限制，见[已知限制](#已知限制)）

### 2. 安装

从本地源码安装（生成 `%USERPROFILE%\.local\bin\windows-mcp.exe`）：

```powershell
uv tool install --force E:\path\to\Windows-mcp
```

或者直接从 GitHub 安装：

```powershell
uv tool install --force git+https://github.com/CyanQX/Windows-mcp
```

> PyPI 上的 `windows-mcp` 是上游原版；本版本请用上面两种方式安装。之前装过原版的，用 `--force` 覆盖即可，客户端配置不用改。

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

AI 会依次调用 `App(mode="launch")`（返回新窗口的标题和句柄）、`Type`（返回 “Verified: … now contains the typed text”）、`App(mode="maximize")`（返回 “verified”）。每一步都能在屏幕上看到真实发生。

## 工作方式

服务器在连接时就告诉 AI 这套规程（`OBSERVE -> ACT -> VERIFY`）：

1. **观察**：`Screenshot`（最快，看画面）、`Find`（在一个窗口里按名称/类型找控件，返回 `e1`、`e2`… 引用）、`Snapshot(window="…")`（列出窗口全部可操作元素，每个带 `#N` 标签）。
2. **操作**：能被 UI Automation 看到的控件，一律用 `Act(target=…, action=…)` 操作元素而不是猜像素；画布、游戏、图片、远程桌面这类看不到控件的，才用 `Click`/`Type` 加坐标。
3. **核对**：每个操作都返回实际发生了什么——`Verified` / `NOT verified`、控件新状态、以及 `Effects`（窗口开关、标题、前台、焦点变化）。没变化或核对失败时，AI 要先重新观察再重试，不能假定成功。

几个关键机制：

- **引用不会“过期”成错误点击**：`e5`、`#12` 在每次使用前都会实时重新找到对应控件：窗口被最小化就还原，控件在列表外就滚动到可见，被别的窗口挡住就把目标窗口置前，最后对落点做命中测试。实在找不到就明确报错（`StaleTarget` / 被谁遮挡），绝不盲点。
- **三种操作通道**（`Act` 的 `via` 参数）：
  - `auto`（默认）：真实鼠标键盘优先，失败再退回 UI Automation 模式。
  - `input`：只用真实输入。
  - `uia`：只用 UI Automation 模式，不动用户的鼠标、窗口被挡住也能操作；对经典 Win32 列表、下拉框、滑块，还会补发真实操作本应产生的变更通知，让应用的处理逻辑照常执行。
- **键盘安全锁**：`Type`、`Act(type/set_value)`、带 `window` 的 `Shortcut`，发送按键前都会确认目标窗口在前台；拿不到前台就报错，一个键都不发。
- **原子快捷键**：`ctrl+shift+s` 这样的组合是一次 `SendInput` 批量发送，不会和其它输入交错，修饰键也不会卡住。

## 工具一览

共 22 个工具：7 个只读、15 个会改变系统状态。

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

- **非本机地址必须鉴权**：绑定非回环地址时，必须提供 `--auth-key` 或 OAuth（`--oauth-client-id` + `--oauth-client-secret`）才会启动；明确不要鉴权要加 `--allow-insecure-remote`（不建议）。
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
- 让 AI 在删除、发送、提交、付款、关闭未保存内容、结束进程、改系统设置之前先征得你的同意（服务器下发的操作规程里已经要求这一点）；
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
uv sync --extra dev                           # 安装依赖（含 pytest、ruff）
uv run pytest                                 # 单元测试：不发送任何真实输入，CI 里运行
uv run ruff check src tests                   # 代码检查
uv run windows-mcp serve                      # 从源码启动
```

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
├── tree/                 控件树遍历（Snapshot）
├── uia/                  UI Automation COM 封装（源自 yinkaisheng/Python-UIAutomation-for-Windows）
└── infrastructure/       鉴权、OAuth、SSRF 防护、配置、遥测
```

新增工具时：

- 在 `tools/` 下写 `register(mcp, *, get_desktop, get_analytics)`，再加进 `tools/__init__.py`；
- 标注好 `ToolAnnotations`；
- 碰界面的工具用默认的 `with_analytics(...)`（会在桌面线程运行），不碰界面的传 `offload="background"`；
- 同时更新本 README 的[工具一览](#工具一览)，`tests/test_docs.py` 会检查两边是否一致。

## 已知限制

- **管理员窗口**：普通权限运行时，无法向以管理员身份运行的程序发送输入（Windows UIPI 限制），也无法操作 UAC 弹窗、锁屏和安全桌面；这些情况下工具会报 `InputBlockedError`，而不是假装成功。
- **看不到控件的界面**：游戏、Canvas、图片、远程桌面、部分自绘界面，UI Automation 不暴露控件，只能用 `Screenshot` + 坐标 `Click`/`Type`。
- **`via="uia"`**：只对经典 Win32 列表、下拉框、滑块补发变更通知；其他框架（WPF、Qt、Electron、网页）走 UIA 模式时，应用未必会执行自己的事件处理。所以默认的 `auto` 优先使用真实输入。
- **粘贴模式**（`Type(method="paste")`）只能恢复剪贴板里原有的文字，原来复制的图片或文件会丢失。
- **快捷键里的字母**按当前键盘布局发送；输入文字请用 `Type`（Unicode 发送，不受输入法影响）。
- **本次未实测**的环境：多显示器（尤其是位于主屏左侧或上方、坐标为负的副屏）、150%/200% 缩放、Windows 10。坐标计算已按虚拟桌面处理并有单元测试，但还没在真机上验证。

## 验证情况

均为本版本在 Windows 11（26200，2560×1440，100% 缩放，已安装中文输入法）上的实测：

| 项目 | 结果 |
|---|---|
| 单元测试 | 574 项全部通过；分别在锁定依赖（fastmcp 3.4.5 / mcp 1.28.1，与 CI 相同）和新版依赖（fastmcp 4.0.10 / mcp 2.2.0）下运行 |
| 真实桌面测试 | 20 项全部通过，连续多轮稳定；两套依赖下都通过。覆盖：像素级点击、右键/中键/双击、中文+emoji+控制键打字、原生输入框回读、Find/Act、复选框、下拉框、列表外选项、滑块、模态对话框、窗口移动后的标签、被遮挡窗口、滚轮增量与位置、原子快捷键、窗口最小化/还原/关闭、完整 MCP 协议调用 |
| 性能（stdio，只读调用） | 启动 3230 → 1856 ms；整桌面 Snapshot 584 → 304 ms；Snapshot 输出 30,661 → 8,884 字符；`Find` 37 ms；Screenshot 持平（约 170 ms） |
| 代码检查 | ruff：新增和修改的代码零告警（上游遗留的告警未改动） |

## 更新日志

### 0.9.0（基于上游 0.8.5）

新增：

- `Find` 和 `Act` 工具：实时查找控件，用真实输入或 UIA 模式操作，并回读核对。`Act` 支持三种通道：`auto`、`input`、`uia`。
- `App` 新增 `minimize`、`maximize`、`restore`、`close` 模式，以及 `handle` 参数；`launch` 会等待并返回新窗口，也支持 `PATH` 上的程序。
- `Snapshot` 新增 `window`（只抓指定窗口）和 `use_words` 参数；控件树输出带 `#N` 标签。
- `Shortcut` 新增 `window`、`repeat` 参数和组合序列（`ctrl+k ctrl+s`）。
- `Type` 新增 `method` 参数，并在打完后回读内容。
- `Click`、`Shortcut`、`Act` 输出操作效果（窗口开关、标题、前台、焦点变化）；`Scroll` 报告滚动位置变化。

改进：

- 输入全部改用 `SendInput`：虚拟桌面绝对坐标、原子快捷键、正确的扩展键标志、支持 UTF-16 代理对（emoji）、输入被拦截时报错。
- 标签和引用在操作前实时重新定位，并做命中测试和遮挡处理。
- 键盘安全锁。
- 专用桌面线程：统一 COM 公寓，输入串行执行。
- 启动去掉 1 秒空等；遥测改为默认关闭、按需加载；Snapshot 默认不收集单词节点；开始菜单应用列表缓存 5 分钟。
- 系统关键进程、服务器自身和它的 MCP 客户端受保护，不能被结束。

修复：

- 纯文本 Snapshot 被序列化成一行 JSON。
- 截图或 Snapshot 失败时被当作成功返回。
- `Scrape` 在事件循环里阻塞。
- `MultiSelect` 中途出错时 Ctrl 键卡住。

依赖与文档：

- 去掉重复依赖 `fuzzywuzzy`、`python-levenshtein`（由 `thefuzz` 取代）；最低 Python 版本统一为 3.12。
- 文档合并为这一份 README（原 `SECURITY.md`、`CONTRIBUTING.md` 的内容已并入）。

## 致谢与许可证

- 上游项目：[CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP)，作者 Jeomon George，MIT 许可证。本仓库的修改同样以 MIT 发布，版权声明见 [LICENSE.md](LICENSE.md)。
- UI Automation 封装源自 [yinkaisheng/Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)（Apache-2.0）。
- MCP 框架：[FastMCP](https://gofastmcp.com)、[Model Context Protocol](https://modelcontextprotocol.io)。
