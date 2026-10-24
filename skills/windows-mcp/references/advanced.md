# Windows-MCP 按需参考

安装器的服务命令为 `%LOCALAPPDATA%\WindowsMCP\Windows-MCP.exe --serve`。1.2.2 打包版在请求审批时自动打开本机审批窗口；同一 EXE 的 `approve <request_id>` 也会打开窗口。由用户核对具体参数后批准一次或拒绝，AI 不得操作自己的审批界面。便携版完整解压后通过 `WindowsMCP.portable.json` 启用程序旁 `data`，服务配置位于 `data/config/config.toml`，默认文件范围为 `data/config/workspace`；安装版仍使用用户目录。下文的 `windows-mcp` 示例适用于独立控制台服务。

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

可提前授权：在管理器左上角“设置 → 操作审批”打开默认审批设置，无需任何待审请求。勾选“全自动”并点击“保存默认审批”、由本机用户确认后，即可开始连续自动化。生效后勾选框保持选中并锁定，只能通过审批窗口下方“操作授权… → 撤销授权”恢复逐次审批。保存成功会关闭审批窗口，释放桌面输入暂停。仅打开或勾选、不保存或取消确认，不会授予权限。

1. AI 调用 `RequestApproval(tool="Execute", arguments={"target":"观察返回的 ID", "action":"set_value", "value":"hello"}, description="具体内容与目标")`。
2. **本地操作员**在自动打开的审批窗口核对工具、具体参数、原会话和期限。默认不勾选“全自动”，点击“批准一次”只放行本次调用；勾选“全自动”并点击“批准并开启全自动”，确认后保存永久的 R2 持续授权，直到在“本机操作审批 → 操作授权… → 撤销授权”中撤销。仅勾选、关闭窗口、取消确认、拒绝或请求过期均不会启用全自动。R3 请求不提供此选项，仍须单独开启并逐次审批。控制台服务仍可在可信终端执行 `windows-mcp approve <request_id>`。窗口完整参数通过当前用户 DPAPI 加密保存且与签名、操作散列绑定，不向 MCP 返回原文。没有完整参数的旧版请求需重新申请。
3. AI 重复原调用；逐次审批在五分钟内有效，只能消耗一次，换会话、换参数或重放均拒绝。开启全自动后，`Session(operation="status")` 的 `trust_mode` 为 `forever`（8 小时授权为 `session`），后续 R2 操作应直接调用，不再申请审批；`RequestApproval` 返回 `TRUSTED` 时也直接执行。旧工具同样遵守这两种授权模式。

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

## 已知限制

- **管理员窗口**：普通权限运行时，无法向以管理员身份运行的程序发送输入（Windows UIPI 限制），也无法操作 UAC 弹窗、锁屏和安全桌面；这些情况下工具会报 `InputBlockedError`，而不是假装成功。
- **看不到控件的界面**：游戏、Canvas、图片、远程桌面、部分自绘界面，UI Automation 可能不暴露控件，需要窗口绑定的 `Visual` 兜底；视觉候选仍须验证，不保证自动识别全部软件。
- **`via="uia"`**：只对经典 Win32 列表、下拉框、滑块补发变更通知；其他框架（WPF、Qt、Electron、网页）未必执行相同业务回调。旧 `Act(auto)` 保留真实输入优先；新 `Execute(auto)` 根据 Pattern 支持选择通道并核对结果。
- **粘贴模式**（`Type(method="paste")`）只能恢复剪贴板里原有的文字，原来复制的图片或文件会丢失。
- **快捷键里的字母**按当前键盘布局发送；输入文字请用 `Type`（Unicode 发送，不受输入法影响）。
- **本次未实测**的环境：多显示器（尤其是位于主屏左侧或上方、坐标为负的副屏）、150%/200% 缩放、Windows 10。坐标计算已按虚拟桌面处理并有单元测试，但还没在真机上验证。
- 已发出的 COM 调用不能强制撤回；慢提供者虽然不会阻塞 MCP 主事件循环，仍可能占用桌面工作线程。取消会拦截尚未执行的调用与下一批输入，并允许释放已经按下的鼠标/修饰键。
- 客户端空闲 15 分钟后回收会话和浏览器，断线后不保证立即回收。浏览器能力仅验证本机已安装 Edge；Chrome/Chromium 通道及 Office COM/模型适配仍待单独验收。
