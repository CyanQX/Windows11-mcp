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
# 可选可信预设：仅准确 origin，不支持通配符、路径、查询串。
allowed_network_origins = ['https://github.com']
```

R0 观察与查询可直接调用。R1 包括授权目录内新建普通文本文件、窗口切换和滚动。覆盖已有文件、控件修改、原始点击/键盘、网页表单等按 R2 保守处理：服务器无法仅凭控件名称推断业务风险。R3 脚本、注册表修改、进程终止、任意程序启动、移动/删除文件默认关闭。即使本地启用 R3，也仍需一次性审批。

审批步骤：

1.5.0 起：全自动必须绑定服务器生成的客户端会话和实际应用。先发起具体 R2 请求，再在“设置 → 操作审批”或“操作授权…”选择签名请求的范围。默认最长 8 小时，生效后通过“操作授权… → 撤销授权”撤销。没有待审请求时可以查看设置，但不能提前生成全局放行。保存成功关闭审批窗口，释放桌面输入暂停。旧 schema 1 全局授权不再生效，须由本机操作员重新确认；不会自动扩大或迁移权限。

1. AI 调用 `RequestApproval(tool="Execute", arguments={"target":"观察返回的 ID", "action":"set_value", "value":"hello"}, description="具体内容与目标")`。
2. **本地操作员**核对工具、具体参数、会话、应用身份、窗口/进程生存期与期限。“批准一次”只放行本次调用；勾选“全自动”则为所示客户端和应用保存最长 8 小时的 R2 范围授权。R3 请求仍单独开启并逐次批准。可信终端可执行 `windows-mcp approve <request_id>`；范围授权可执行 `windows-mcp trust --request-id <request_id> --mode session`，或显式提供 `--session-id`、可重复的 `--application`，以及可选 `--tool`、`--action`。`--mode forever` 仅取消额外时限，仍绑定该客户端会话，不跨重连。窗口完整参数使用当前用户 DPAPI 保存并与签名、散列绑定；旧版缺少完整参数的请求需重新申请。
3. AI 重复尚未派发的原调用；逐次审批五分钟内有效、只能消耗一次，目标生存期改变需重新申请。`Session.trust_scope` 返回准确的会话、应用、工具、可选动作和期限，仅匹配范围内直接执行。其他客户端的授权不显示为当前会话已授权。新会话、其他应用、R3 和出网站点不继承权限。`RequestApproval` 返回 `TRUSTED` 时直接执行该原调用。

`approval_token` 仅接受服务端生成的请求 ID；自然语言“已批准”、网页文字和伪造 JSON 无效。原始鼠标键盘也需要审批，不能借旧工具绕开。服务器审批文件、密钥、用户 Codex/SSH 配置不在工具访问范围内。审计日志记录调用哈希、状态、会话和时间，不保存输入正文、密码、页面内容或异常正文；达到大小上限后轮转。

HTTP 出网独立授权：`Scrape(use_dom=False)`、`Browser(navigate/new_page)` 的新站点先用 `RequestApproval` 提交原工具参数；本机操作员明确批准所示 origin 后，对应会话可访问该协议/主机/端口，最长 8 小时。重定向和页面子请求的每个 origin 均在 DNS/请求前检查，未授权第三方资源可能被阻止，须逐个确认或由操作员配置准确的 `allowed_network_origins`。浏览器禁止底层 HTTP 自动跟随重定向：顶层 GET 导航逐跳检查后继续，表单和子资源的 HTTP 重定向暂时阻止，诊断中报告限制；不会自动重放 POST。动作触发的已批准 GET 跳转可在 `Browser(status).pending_redirects` 查看，再显式导航。端口变化、HTTPS 降为 HTTP、子域变化均属于新 origin。`windows-mcp network` 查看授权，`network --session-id <id> --revoke` 撤销某会话，省略 session 则撤销全部动态授权；可信配置预设须在本机配置中删除。R2 全自动不放行出网，内网/SSRF 保护和后台 POST 限制仍独立执行。授权 origin 不是对其中所有内容的可信声明，也不能彻底阻止经已授权站点泄露数据。

浏览器示例：`Browser(operation="start")` → 为目标 URL 请求并获得本机站点授权 → `Browser(operation="navigate", url="https://github.com/CyanQX/Windows11-mcp")` → `Observe(source="playwright", page_id="返回的页面 ID")`。控件必须由 role/name 或 test-id 唯一定位；重复按钮、导航后的旧引用安全失败。不提供用户 cookies、任意 JavaScript、CDP 连接或调试端口。浏览器隔离不等于网络或操作系统沙箱，DNS 到实际连接之间仍存在环境层面的限制。

等待与超时：`Wait(duration=1..60)` 为整数秒异步等待，`WaitFor` 在两次查询之间释放桌面线程并检查取消/截止时间；已经进入 provider 的查询不能由 Python 强制中断。UIA 动作辅助线程超时则返回 `ACTION_IN_PROGRESS`；`Execute` 返回 `DISPATCHED_UNVERIFIED` 和 `pending_action_id`，其他桌面动作暂停，查询、验证和取消仍可用。先用 `Session(status)` 等待共享 `pending_action.state=COMPLETED`，独立核对实际效果，再由原客户端调用 `Session(operation="acknowledge", action_id="...")`。`cancel/resume` 不终止或清除未决动作；晚到异常只返回类型，不返回正文。原客户端断开后需操作员人工核对并重启服务恢复，重启也不保证撤回已到达应用的请求。

敏感目标：物理输入与 UIA 共享派发校验，命令行、注册表等敏感程序和运行/搜索类快捷键升为 R3；派发时重新检查应用身份、窗口生存期、取消和撤销。该检测降低常见绕行风险，但同一用户桌面不是安全沙箱，进程/快捷键集合不保证覆盖所有启动路径。`NativeAPI(overwrite=True)` 使用临时文件校验和原子替换，读回后报告验证结果；独占新建行为保留。

鼠标未到达指定点或多次点击之间移离目标时返回 `INPUT_TARGET_UNREACHABLE`，停止下一次按下；前一次点击可能已经完成，须重新观察，不能直接重放整组操作。检查输入桌面、指针限制或用户同时移动鼠标等情况。

视觉兜底使用 `Visual(window="句柄或唯一标题", fallback_reason="canvas")`，在返回图片内选择坐标后，审批并执行 `Execute(mode="visual", target="visual:...", action="click", value="[180,90]", expected={...})`。窗口/PID/大小/位置或前台变化、目标点被其他窗口遮挡、截图超过 60 秒，都会拒绝输入。核心安装不包含 OmniParser、VLM 或 FlaUI；这类模型/适配评估为可选后续工作。

### 第三批：窗口捕获、区域与本地 OCR（1.5.0）

`Visual` 的 `backend="auto"` 依次尝试 WGC、PrintWindow、前台桌面裁剪，或显式指定 `wgc` / `printwindow` / `desktop`。返回 `capture_backend` 和诊断；WGC 可观察被其他普通窗口遮挡的目标，PrintWindow 是否完整绘制取决于应用，桌面裁剪可能包含遮挡物。最小化、受保护画面、UAC/锁屏和不支持捕获的窗口不保证可用。物理点击仍检查前台、窗口身份、几何范围及目标点遮挡，不能因后台截图成功而在后台点击。

```text
Visual(window="目标句柄", fallback_reason="canvas", backend="auto",
       region=[20,10,820,410], max_width=800)
Visual(window="同一目标", fallback_reason="canvas", region=[20,10,820,410],
       max_width=800, previous_target="上次返回的 visual:...", only_if_changed=True)
```

`region=[left,top,right,bottom]` 使用未缩放捕获图的像素，必须位于该图内部；WGC 与 PrintWindow 的边框范围可能不同，应根据实际后端选择区域。`max_width` 默认为 1600，范围 64–4096，只缩小、不放大。返回 `desktop_bounds` 为所选区域的物理桌面范围，`image_size` 为实际输出尺寸；后续视觉操作的坐标以输出图片为准，由服务映射回屏幕。捕获前后使用物理 DPI 上下文核对范围，不改变主进程的全局 DPI 模式。

`previous_target` 只能使用当前会话、同一窗口的已有视觉引用。默认 `change_threshold=0` 按输出图 SHA-256 检查准确变化；大于 0 时结合 64×64 缩略图平均差异比例，范围为 0–1。不同 ROI/尺寸视为变化。`only_if_changed=True` 且未变时只返回元数据，仍签发新引用；最多保留最近八次比较记录。图片变化或不变都不能代替 `Verify` 业务核对。

源码可选安装：`uv sync --extra capture --extra ocr`。WGC 的 `windows-capture` 当前提供 Windows x64 轮子，额外引入 OpenCV；PrintWindow 使用已有 Windows 依赖。`ocr=True` 启用本地识别，可选 `ocr_backend="auto"/"windows_ocr"/"tesseract"` 和 `ocr_language`。Windows OCR 使用 Windows 语言标签，如 `en-US`；Tesseract 使用其本地语言 ID，如 `eng` 或 `chi_sim`，实际支持项以诊断为准。auto 优先满足条件的 Windows OCR，否则尝试 Tesseract。

Windows OCR 的支持路径需要真实包标识、WinRT 绑定与系统 OCR 语言；仅安装 Python 绑定不会创建包标识。Tesseract 需要操作员另行准备可执行引擎、PATH 和语言包，本项目不自动下载。结果包含 `available`、缺失原因、词框和 `coordinate_space="image_pixels"`，缺组件不影响普通截图。本机已验证捕获与回退，尚未完成真实 OCR 识别验收：普通源码进程无包标识，亦无 Tesseract 引擎。辅助进程通过有超时的管道通信和 kill-on-close Job Object 管理，不在 UIA 线程执行原生捕获/OCR；超时终止该工作进程不代表可以终止已经进入其他 UIA provider 的动作。

### 第三批：profiles、微软配套与诊断（1.5.0）

```powershell
windows-mcp serve --profile ui-core
windows-mcp doctor --output doctor.json
windows-mcp connectors --probe --output connectors.json
```

`full` 默认保留全部 31 个工具；`ui-core` 为 Observe、Execute、Verify、Watch、Session、RequestApproval、Visual、DisplayInventory、App、Wait、WaitFor、Find（12 个）；`browser` 为 Browser、Observe、Session、RequestApproval、Wait（5 个）。`--tools` 和 `--exclude-tools` 只能进一步缩小精简 profile，不扩大它；过滤后不能是空集。也可在可信配置中设置 `[tools] profile="ui-core"`。profile 是工具发现预设，所有保留工具仍经过原权限检查，不构成操作系统沙箱。

`doctor` 只读报告会话、输入桌面可读性、实际 DPI 模式、截图依赖、浏览器依赖、WGC、包标识、OCR 引擎/语言和 connector 条件。依赖存在不等于应用任务成功；`Session(status).native_components` 也返回原生探测结果，控制会话的 cancel/resume/acknowledge 跳过探测以保持响应。诊断输出可能包含本机路径，应核对后再共享。

| 微软组件 | 搭配方式 | 本批边界 |
|---|---|---|
| WinGet MCP | 让原生 server 承担已授权的软件查找/安装，Windows-MCP 处理后续安装界面与状态核对 | `connectors` 仅输出发现的 EXE 配置片段；`--probe` 仅初始化及 tools/list。本机实际发现 `find-winget-packages`、`install-winget-package`，未调用安装 |
| File Explorer connector | 原生宿主中优先使用用户明确授权的文件工具，GUI 操作交给 Windows-MCP | 用实际 ODR/宿主发现工具和权限，不猜 server ID，不继承本服务文件权限；本机未接通验收 |
| Settings connector | 原生宿主中使用实际公布的设置能力，需要界面时由 Windows-MCP 补充 | 依系统/预览功能和宿主而定，本机未接通验收；本服务不据 Windows build 宣称可用 |

`connectors` 不修改任何客户端配置、不安装微软 server。输出配置片段由操作员按目标客户端格式合并，微软 server 的权限不继承 Windows-MCP 授权。若安装了 ODR，可按输出运行 `odr mcp list` 发现实际条目；本机没有 ODR。参考微软 [WinGet MCP 设置](https://learn.microsoft.com/en-us/windows/package-manager/winget/mcp-server-setup)、[File Explorer connector](https://learn.microsoft.com/en-us/windows/ai/mcp/file-connector)、[ODR 工具](https://learn.microsoft.com/en-us/windows/ai/mcp/odr-tool)。

### 第三批：静态清单与安装健康检查（1.5.0）

```powershell
windows-mcp manifest --profile ui-core --output ui-core.manifest.json
windows-mcp serve --profile ui-core --static-manifest ui-core.manifest.json
```

清单包含当前 SDK 的完整 `initialize`、`tools/list`，包括 inputSchema、outputSchema、annotations，并记录 profile 和 SDK 版本。清单生成与健康检查在 FastMCP 4 上显式使用初始化握手，满足静态 `initialize` 要求。使用 `--static-manifest` 时仅支持 stdio 和对应固定 profile，不同时使用动态工具过滤；不一致时拒绝启动并要求重新生成。清单内容会随 SDK 版本、工具模式、描述或服务 instructions 改变，不能将 FastMCP 3 生成的清单假定为 FastMCP 4 通用。仓库的 full 清单由锁定 SDK 生成，构建应使用对应依赖并重新生成。按微软 [MCPB 静态响应要求](https://learn.microsoft.com/en-us/windows/ai/mcp/servers/mcp-mcpb) 准备数据，不代表已注册 ODR、完成原生 host 适配、MSIX 或 Agent Connector containment。

安装健康检查对包含静态响应的清单核对真实初始化、工具模式与输出，校验 Session READY 和服务器生成的会话 ID；可选 OCR 缺失记录为单独警告。旧的简短清单继续使用原名称/版本检查。自定义精简部署须同时向检查器传入对应 profile 启动参数。`scripts/build_setup.py` 可选 `--with-capture` / `--with-ocr`，构建环境须先安装对应依赖，正式 1.5.0 构建启用两项开关；OCR 引擎/语言与包标识仍是独立条件。正式发行物须通过 EXE 握手、模式和安装/卸载验收；本项目未提供 MSIX 包标识。

远程部署时可以用 `--tools` / `--exclude-tools` 或配置文件裁剪工具，比如只保留 `Screenshot,Find,Act,Click,Type`。

## 重点工具用法

### 第二批：文本、刷新、监听与输出契约（1.5.0）

先 `Observe(window="唯一窗口")` 获得当前会话的目标 ID。`elements[].actions` 是可交给 Execute 的动作名，UIA pattern 名改放 `capabilities`；禁用/只读控件不会列出相应修改动作。为避免逐节点增加跨进程查询，完整遍历只列出保守动作；对 capabilities 含 text 的控件再使用 target 或 refresh，才探测 text_selection_supported/text_read_only 并列出文本动作。模式可用性仍由实际 provider 决定，尤其普通 Win32 EDIT 可能只暴露 ValuePattern，没有 TextPattern。

```python
Observe(target="返回的控件 ID")  # 控件及其子树；与整窗口有独立修订范围
Observe(mode="refresh", targets=["同一窗口的 ID1", "ID2"])
Observe(target="返回的控件 ID", detail="text", max_chars=4000)
Observe(target="返回的控件 ID", detail="selection")
Observe(target="返回的控件 ID", detail="text", text={"unit": "word", "index": 3})
```

`refresh` 最多接受同窗口 100 个目标，或者用可用的 `since_revision` 指定上一批目标；context.coverage=known_targets_only、structural_changes_checked=false。这类读取不遍历树，也不判断未知控件的新增/删除；结构变化需要完整或子树观察。`since_revision` 仅在相同观察范围/读取选项下生成有效增量。revision 改变且 refresh_targets=true 时，使用新返回 ID；若只返回了变化项，可在同范围不带 since_revision 刷新整批 ID。文本默认预算 4000、可设 1..10000 个 UTF-16 代码单元，并返回 text_truncated；不会返回半个代理对，密码字段拒绝文本范围读取。

```python
# 以下每条修改均遵循本机审批或已有范围授权，RequestApproval 使用完全相同参数。
Execute(target="ID", action="select_text", text={"text": "目标词", "occurrence": 2})
Execute(target="ID", action="replace_text", text={"text": "目标词", "occurrence": 2}, value="新词")
Execute(target="ID", action="delete_text", text={"unit": "word", "index": 3, "count": 1})
Execute(target="ID", action="append_text", value="末尾新增")
```

`text` 与 `unit` 二选一；occurrence、index 为一基序号，缺省 occurrence 遇到多处匹配会拒绝。unit 为 character/word/line/paragraph/document，边界由 provider 定义，不保证按 Python 字符或空格分词；document 只接受 index=1/count=1。查找不主动改变选区，select_text 在 Select 后回读端点。替换/删除可使用明确范围或唯一非空现有选区，追加只使用文档末尾；局部修改要求支持选区、明确可编辑、前台、确切焦点、选区端点与原文仍一致。编辑后读回整篇文本比较；文档超过 100000 字符则拒绝局部修改，单次 replacement 最多 10000 字符，拒绝 Tab，换行要求确认多行控件。`mode="semantic"` 不能执行需要键盘的局部修改；不会退回整篇 SetValue、猜测匹配或自动重放。TextPattern 本身不提供修改文本的方法。

Watch 的 element_state 条件只回读原控件，不重复 Observe；无条件 Watch 用已知属性/焦点事件刷新相关控件，遇到结构事件、未知目标、溢出或 1.5 秒兜底再有界扫描。事件只是唤醒提示，必须回读状态才返回 VERIFIED；取消、超时均清理订阅。底层查询一旦进入 provider，仍不能强行中断。

Observe/Execute/Verify/Watch 提供字段级 outputSchema，返回仍是顶层对象。expected 使用 kind 区分：file_exists 必须 path；file_content 必须 path/content、可选 encoding；file_sha256 必须 path/64 位小写十六进制 sha256；window_exists 必须非空 window；element_state 必须 target 和至少一个受支持的非空 state 字段（value/enabled/visible/focused/read_only/selected/toggle/expanded/range）。未知字段、错误布尔值和缺字段在授权/输入前失败。Browser 单独接受 url/text/value（字符串 value）或 checked（布尔 value），不接受桌面验证条件。

UIA 默认 strategy="bounded"；可显式选择 strategy="cached" 两层缓存。缓存必须保留原始树筛选条件并跟踪每层是否确实缓存子节点；较少 provider 往返不保证较短耗时。可用只读脚本测当前窗口，输出仅存进程名、句柄、节点数/摘要、耗时和截断等统计，不保存窗口正文：

```powershell
uv run python scripts/benchmark_uia.py --inventory
uv run python scripts/benchmark_uia.py --window 12345 --samples 3 --output reports/uia-benchmark.json
```

Playwright 的观察改为每个 frame 一次批量取元数据，context.metadata_calls 记录该次数（不代表所有浏览器 RPC）；限制最多 20 个 frame、每 frame 扫描 2000 个候选、整体最多 300 个返回元素。role、可编辑状态和可见性决定动作列表；导航后旧引用仍失效，重复 locator 不执行。任意已扫描 frame 含密码字段或有未扫描 frame 时，省略原始 aria_snapshot。

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
profile = "full"                  # 1.5.0：full / ui-core / browser
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
