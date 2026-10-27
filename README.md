<div align="center">
  <h1>Windows-MCP 1.5.0</h1>
  <p>让已连接的 AI 观察、操作并核对真实 Windows 应用</p>
</div>

[![Tests](https://github.com/CyanQX/Windows11-mcp/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/CyanQX/Windows11-mcp/actions/workflows/test.yml)
[![Windows EXE setup](https://github.com/CyanQX/Windows11-mcp/actions/workflows/setup.yml/badge.svg?branch=main)](https://github.com/CyanQX/Windows11-mcp/actions/workflows/setup.yml)
[![Release](https://img.shields.io/github/v/release/CyanQX/Windows11-mcp)](https://github.com/CyanQX/Windows11-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.md)

[用途](#工具用途) · [功能与边界](#功能简介) · [下载安装](#安装指南) · [工具清单](#工具一览) · [更新记录](CHANGELOG.md) · [参数参考](skills/windows-mcp/references/advanced.md)

## 工具用途

Windows-MCP 是运行在本机的 Windows 自动化 MCP 服务。连接 Claude Code、Codex 桌面端、DeepSeek Harness、Gemini CLI、Zcode 或 Trae 后，AI 可以读取真实窗口和控件、操作应用和网页，并通过状态或文件结果核对任务。模型与 AI 客户端由你选择，本项目不提供模型账号或云端算力。

**当前稳定版：[v1.5.0](https://github.com/CyanQX/Windows11-mcp/releases/tag/v1.5.0)，文档更新于 2026-10-27。** 本版整合前三批引擎、文本效率、窗口视觉与部署配套升级；正式安装包、验证记录和校验值以 [GitHub Releases](https://github.com/CyanQX/Windows11-mcp/releases/latest) 为准。完整版本变化见 [CHANGELOG](CHANGELOG.md)。

| 组成 | 负责什么 |
|---|---|
| MCP 服务 | 提供 31 个工具，执行 UI Automation、真实鼠标键盘、受管浏览器、授权范围内文件操作与验证 |
| Skill | 告诉客户端如何选择工具、使用观察引用、处理审批和核对结果；Skill 本身不提供桌面执行能力 |
| 安装与维护管理器 | 检测客户端，接入 Skill + MCP，验证连接、修复、备份恢复、撤销安装与安全卸载 |

适合窗口控件操作、表单填写、应用内文本编辑、重复桌面流程和结果检查。网页聊天或云端模型需要能够连接本机 MCP 的客户端，打开本仓库网页不会自动获得桌面控制能力。

![Windows-MCP 1.5.0 安装与维护管理器实拍](assets/manager-v1.5.0.png)

以上为本版源码在隔离验收环境中的真实界面截图；客户端检测状态为测试夹具数据。

本项目基于 [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 二次开发，遵循 [MIT 许可证](LICENSE.md)，保留上游及第三方组件的许可声明。

## 功能简介

### 1.5.0 的三批升级

| 升级方向 | 现在可以做什么 | 使用时的边界 |
|---|---|---|
| 等待与超时 | Wait 异步等待 1–60 秒，支持取消；WaitFor 在查询间释放桌面线程；跟踪超时后仍执行的 UIA 动作 | 超时不代表动作撤回；未决动作完成后须独立核对，再由原会话 acknowledge |
| 文件与授权 | 原子覆盖文本文件；全自动绑定会话、应用、工具及期限；物理输入和 UIA 统一派发检查 | 旧版全局全自动授权须重新确认；R3 单独启用并逐次审批；出网独立授权 |
| 文本范围 | 读取 TextPattern 文本/选区，按准确文本及 occurrence 或 unit/index/count 选择、替换、删除、追加 | 依控件能力；密码、只读、不支持 TextPattern 的控件明确拒绝；局部编辑先核对选区和原文，再读回 |
| 观察与性能 | 控件子树观察、已有目标批量刷新、事件触发 Watch、每个 frame 批量浏览器元数据 | 已有目标刷新不检查新增/移除控件；事件仅唤醒回读；UIA 缓存策略需按应用测量 |
| 窗口视觉 | HWND WGC、PrintWindow 与前台桌面裁剪；ROI、缩小图片、变化比较和未变图片省略 | 后台可观察不等于后台可点击；最小化、受保护画面和特殊 provider 不保证可用 |
| OCR 与组件 | Windows OCR/Tesseract 适配、词框、语言与依赖诊断；WinGet MCP 配套发现 | OCR 仍需包标识或本地引擎及语言；微软 File Explorer/Settings 未完成原生连接验收 |
| 部署与诊断 | full/ui-core/browser profile，doctor、connectors、完整静态清单；安装后校验模式及 Session 结果 | 静态清单按 SDK/profile 生成；不代表已注册 ODR、具备 MSIX 或 Agent Workspace |

本版保留全部兼容工具。64 项升级候选并未全部实施；尚未交付完整任务队列治理、全引擎 DPI/坐标统一、OmniParser、ODR 宿主适配、Agent Workspace 等工作，不将候选能力算作现成功能。

### 观察、操作、核对

1. **观察**：优先使用 Observe、Find 或指定窗口的 Snapshot，选择当前会话返回的目标 ID。
2. **操作**：Execute 优先选择支持的 UIA 模式；需要真实输入时检查前台、窗口身份和遮挡。Browser 操作独立 Playwright 会话，NativeAPI 操作明确授权的文本文件。
3. **核对**：提供 expected，或调用 Verify 读取独立后置条件。`VERIFIED` 表示条件已得到证据支持；`DISPATCHED_UNVERIFIED` 只说明动作已派发。

```text
Observe(window="目标窗口的唯一标题")
Execute(target="本次观察返回的目标 ID", action="set_value", value="准备填写的文本")
Verify(expected={"kind":"element_state", "target":"目标 ID", "state":{"value":"准备填写的文本"}})
```

以上是 MCP 工具调用示意，不是 PowerShell 命令。实际参数以客户端发现的工具模式为准；动作须具有匹配授权。发送、提交、删除等动作若已派发但未证实，应先检查结果，避免重复执行。

### 工具 profile

| Profile | 工具数 | 适合的任务 |
|---|---|---|
| `full`（默认） | 31 | 完整桌面与兼容工具，适合现有接入 |
| `ui-core` | 12 | Observe、Execute、Verify、Watch、Session、RequestApproval、Visual、DisplayInventory、App、Wait、WaitFor、Find |
| `browser` | 5 | Browser、Observe、Session、RequestApproval、Wait；适合受管网页会话 |

profile 只决定公布哪些工具，不授予额外权限。精简 profile 的 `--tools` / `--exclude-tools` 只能进一步缩小范围。管理器默认安装 full；自定义接入示例见安装指南。

### 文本、图片与浏览器

- **文本**：`Observe(target=..., detail="text"/"selection")` 读取控件内容；`Execute` 支持 select_text、replace_text、delete_text、append_text。多处匹配须明确一基 occurrence，不静默改写整篇富文档。
- **局部观察**：`Observe(target=...)` 观察子树；`mode="refresh"` 批量回读同一窗口最多 100 个已有目标。Watch 在事件后回读相关控件，最长 1.5 秒兜底。
- **视觉**：Visual 的 region 是未缩放捕获图的像素区域，返回图的点击坐标再由服务映射回物理桌面。窗口移动、调整大小、进程生存期变化、引用过期或点击被遮挡时拒绝输入。
- **图片变化**：previous_target、only_if_changed 可省略未变图片，仍返回新视觉引用。变化仅是重观察提示，不算业务验证。
- **浏览器**：独立 Playwright profile，支持页面、iframe、表单与弹窗。可使用已安装 Edge；其他通道需要相应浏览器。服务不提供用户原始 Cookie、任意 JavaScript 或 CDP 连接。

### 操作授权与升级迁移

**从 1.4.x 升级后，旧的全局全自动授权不会继续放行。** 先发起具体待审请求，再由本机操作员核对客户端会话、应用、工具、参数和期限。勾选“全自动”并批准，为所示范围开启默认最长 8 小时的自动 R2 授权；重连生成新会话后须重新授权。通过“操作授权… → 撤销授权”撤销，AI 不能操作自己的审批窗口。

| 范围 | 行为 |
|---|---|
| 窗口与控件读取 | 按服务策略执行；读取结果可能包含截图和应用文本，应只连接信任的客户端 |
| R2 修改 | 精确一次性审批，或匹配会话/应用/工具的全自动授权 |
| R3 高风险工具及敏感目标 | 默认关闭；本地启用后仍逐次审批，不能借原始输入或 UIA 绕开 |
| HTTP(S) 出网 | 按准确 origin（协议、主机、端口）独立授权，全自动鼠标键盘授权不包含网络权限 |
| 文件 | 受可信配置的 workspace_roots 限制，服务密钥和受保护配置不在工具访问范围内 |

浏览器导航、重定向和子资源均检查 origin；顶层 GET 可逐跳检查后继续，表单和子资源的 HTTP 重定向当前会被阻止。审批窗口打开期间暂停 MCP 桌面输入。已进入 UIA provider 的动作无法由 Python 强制取消；`ACTION_IN_PROGRESS` 后用 Session 查询完成，独立核对后再 acknowledge，cancel/resume 不隐式清除未决动作。

这些措施降低本机自动化风险，不构成操作系统沙箱；普通权限无法绕过 UAC、安全桌面、锁屏或管理员窗口边界。默认关闭 PostHog 遥测。完整处理规则与离线政策见管理器“关于”和 [按需参考](skills/windows-mcp/references/advanced.md)。

### 微软组件与可选条件

| 组件 | 本版提供 | 仍需满足 |
|---|---|---|
| Windows.Graphics.Capture | HWND 捕获后端；正式 Windows x64 构建包含绑定，失败可回退 PrintWindow | 目标窗口支持捕获；不保证最小化或受保护内容 |
| Windows OCR | WinRT 绑定、词框适配及条件探测 | 真实 package identity 和系统 OCR 语言；普通 EXE 不因携带绑定就获得包标识 |
| Tesseract | 本地引擎适配 | 操作员准备引擎、PATH 与语言包；不自动下载安装 |
| WinGet MCP | 发现本机 server、输出配置片段；`--probe` 仅初始化和列工具 | 独立安装的微软 server 与其权限；已验证握手，未执行安装任务 |
| File Explorer / Settings | 原生宿主/ODR 发现指引和未就绪诊断 | 实际可用的系统组件、宿主及各自权限；当前未完成连接验收 |
| ODR / Agent Workspace | 固定 profile 静态清单准备 | 本版未提供 ODR 宿主适配、MSIX 标识或隔离会话运行模式 |

OCR 实际识别尚未在当前验收机通过：该机无包标识、无 Tesseract 引擎。不要将组件探测或握手成功理解为具体应用任务已经完成。

## 安装指南

### Windows x64 安装版与便携版

从 [最新正式 Release](https://github.com/CyanQX/Windows11-mcp/releases/latest) 下载对应版本，核对 [SHA256SUMS.txt](https://github.com/CyanQX/Windows11-mcp/releases/download/v1.5.0/SHA256SUMS.txt)。两种格式均包含管理器、Skill、MCP 服务、Python 运行时及依赖，不必另装 Python。当前页面功能对应 v1.5.0；请在发布页核对所下载资产的版本。

| 格式 | 文件与使用方法 | 数据位置 |
|---|---|---|
| [安装版下载](https://github.com/CyanQX/Windows11-mcp/releases/download/v1.5.0/Windows-MCP-Setup-v1.5.0-x64.exe) | `Windows-MCP-Setup-v1.5.0-x64.exe`；首次安装向导选择位置，后续更新显示进度页 | 服务数据在 `%LOCALAPPDATA%\WindowsMCP`；管理器设置在 `~/.windows-mcp` |
| [便携版下载](https://github.com/CyanQX/Windows11-mcp/releases/download/v1.5.0/Windows-MCP-Portable-v1.5.0-x64.zip) | `Windows-MCP-Portable-v1.5.0-x64.zip`；完整解压后运行 Windows-MCP.exe | 程序旁 data，保存配置、日志、备份、收据、运行时与审批数据 |

安装版默认路径为 `%LOCALAPPDATA%\Programs\Windows-MCP`，按用户安装，无需管理员权限；系统卸载入口位于 Windows“设置 → 应用”。**卸载管理器会保留已接入的 Skill/MCP 和个人数据，对应接入须在管理器中单独卸载。**

便携版请保留 WindowsMCP.portable.json 和整个目录，不在 ZIP 内运行。接入后保持目录位置，移动或删除前先卸载接入；加密审批数据仅供原 Windows 用户使用。

### 首次接入

1. 打开管理器，在左侧选择检测到、尚未接入的客户端，可多选。已安装项带圆点并禁用重复安装。
2. 点击“安装 Skill + MCP”，核对真实路径与操作预览，再执行安装。
3. 等待版本、31 个工具、完整模式和 Session READY 验证通过。重启目标客户端或新建会话。
4. 在目标客户端确认工具已出现，先读取一个明确窗口，再执行具有授权的操作。
5. 若需要指定客户端程序目录，进入“设置 → 高级设置”，添加真实文件夹并点击“检测”，保存后应用。

Codex 的目标是 **Codex 桌面端**，管理器不安装或调用 Codex CLI。文件写入前创建事务备份，失败按校验结果回滚，保留其他 MCP、JSONC 注释与个人配置；仍有客户端引用时保留共享服务。

| 客户端 | 默认 Skill 目录 | MCP 配置 |
|---|---|---|
| Claude Code | `~/.claude/skills/windows-mcp` | `~/.claude.json` |
| Codex 桌面端 | `~/.agents/skills/windows-mcp` | `~/.codex/config.toml` |
| DeepSeek Harness | `~/.dsh/skills/windows-mcp` | `~/.dsh/cordis.patch.yml` |
| Gemini CLI | `~/.gemini/skills/windows-mcp` | `~/.gemini/settings.json` |
| Zcode | `~/.zcode/skills/windows-mcp` | `~/.zcode/cli/config.json` 的 mcp.servers |
| Trae | `~/.trae-cn/skills/windows-mcp`，国际版为 `.trae/skills/windows-mcp` | 默认 `%APPDATA%\Trae CN\User\mcp.json`，优先沿用现有国内/国际/SOLO 配置 |

`~` 表示当前用户主目录。支持 CODEX_HOME、DSH_HOME 等实际目录设置；DeepSeek 桌面版和 CLI 共用 Harness Home。OpenCode 保留旧接入的维护与卸载，不提供新安装入口。配置格式与参数细节见 [按需参考](skills/windows-mcp/references/advanced.md)。

### 更新、修复、备份和卸载

- **更新**：设置中“检查更新”发现版本后显示红点，用户点击“更新安装”才下载，先校验 SHA-256。安装版保留原位置；便携版保留 data，由独立助手替换、重启并验证，失败保留回滚副本。
- **升级接入**：更新管理器后检查受管接入是否也完成升级和验证；已运行的 MCP 进程需由客户端重连后使用新版本。
- **验证与修复**：右侧提供连接验证和修复；健康检查明确区分版本、工具清单、模式、Session 结果及可选组件状态。OCR 缺失只作单独提示。
- **备份与撤销**：支持预览备份恢复、撤销安装批次；后续个人修改或共享引用存在时报告冲突，不强行覆盖。
- **卸载**：从独立卸载窗口选择具体客户端接入，保留其他配置与仍被引用的运行时。

管理器保留固定三面板布局、浅白/暖色护眼/深色/跟随系统主题、独立日志背景、常规/玻璃拟态视觉强度、真实任务进度及六个离线“关于”页面。界面图是实拍，版本与接入是否更新以程序内真实验证结果为准。

### 源码安装与开发

需要 Windows、Python 3.12+、Git 和 [uv](https://docs.astral.sh/uv/)。在项目目录执行：

```powershell
git clone https://github.com/CyanQX/Windows11-mcp.git
cd Windows11-mcp
uv sync --frozen --extra browser --extra capture --extra ocr
uv run --frozen --extra browser --extra capture --extra ocr windows-mcp doctor
uv run --frozen --extra browser --extra capture --extra ocr windows-mcp serve
```

`serve` 默认使用 stdio，终端等待客户端连接属于正常行为。开发管理器再添加 `--extra installer`，运行测试再添加 `--extra dev`；构建另需 `--group build`。为保持环境稳定，同一环境后续 `uv run` 使用相同 extras。WGC 可选依赖当前限 Windows x64，OCR 不会自动安装外部引擎和语言。

将上面同步完成的项目 `.venv\Scripts\windows-mcp.exe` 作为自定义客户端的 command，参数为 `["serve"]`；精简接入改为 `["serve","--profile","ui-core"]`。Skill 可从仓库 skills/windows-mcp 复制到客户端 Skill 目录。通过管理器安装的用户无需自行编辑配置。

测试使用 `uv run --frozen --extra dev --extra installer --extra browser --extra capture --extra ocr pytest`；默认不发送真实桌面输入。只有显式启用 `WINDOWS_MCP_DESKTOP_TESTS=1` 才执行专用夹具中的真实输入，具体安全边界见 [开发说明](CLAUDE.md)。CI 检查 Python 3.12/3.13 与两代 FastMCP/MCP；本地前三批在 Python 3.14.7 验证，不能据此承诺所有系统/应用组合。

### 诊断、微软配套和固定清单

以下命令用于已安装的控制台服务；打包版也可用 Windows-MCP.exe 对应子命令，并用 `--output` 写出诊断文件。

```powershell
windows-mcp doctor --output doctor.json
windows-mcp connectors --probe --output connectors.json
windows-mcp manifest --profile ui-core --output ui-core.manifest.json
windows-mcp serve --profile ui-core --static-manifest ui-core.manifest.json
```

`connectors --probe` 只核对 WinGet 初始化和 tools/list，不搜索或安装软件、不改客户端配置。各微软 server 的权限独立。静态清单须使用实际 SDK/profile 重新生成；启动检查不接受动态过滤，生成清单也不替代 ODR 注册。

可信配置可指定文件范围；只授权任务实际需要的目录：

```toml
[tools]
profile = "full"

[policy]
workspace_roots = ['E:\my-authorized-task']
allow_r3 = false
# 可选：本地操作员预设准确的 HTTP(S) origin。
allowed_network_origins = ['https://github.com']
```

默认文件范围为 `~/.windows-mcp/workspace`；便携模式为 data/config/workspace。远程 HTTP 传输需要鉴权、TLS 和 IP 白名单等条件，配置路径及完整环境变量见 [按需参考](skills/windows-mcp/references/advanced.md)。

### 常见问题与验证范围

| 现象 | 检查和处理 |
|---|---|
| 安装后客户端没有工具 | 重启客户端/新会话；管理器验证接入版本与 READY；确认客户端实际读取的用户配置 |
| 升级后需要重新授权 | 1.5.0 的会话/应用范围授权迁移是预期行为；核对当前待审请求后重新批准 |
| 返回 TARGET_STALE / FOCUS_MISMATCH | 重新观察准确窗口；确认前台、窗口位置、进程及控件仍对应目标，避免重放旧坐标 |
| 返回 ACTION_IN_PROGRESS | 先查询 Session.pending_action，等待完成并独立核对，再由原会话 acknowledge |
| 网页子资源或跳转被拒绝 | 核对实际 origin 与独立网络授权；表单/子资源 HTTP 重定向目前受限，不自动重放 POST |
| OCR 不可用 | doctor 查看真实包标识、绑定、引擎和语言；缺条件时继续使用截图或语义工具 |
| 找不到微软原生 connector | 以本机 ODR/原生宿主实际发现结果为准，不能仅依据 Windows 构建号判断可用 |
| 特殊应用看不到控件 | 可尝试窗口 Visual；不能保证游戏、自绘界面、远程桌面、管理员应用和保护内容 |

Windows 11 是当前本地验收平台。Win32 与 WPF 专用夹具、浏览器协议和两代 SDK 已有回归；Windows 10、物理混合 DPI 多屏、所有输入法及第三方客户端/Office 组合没有完整验收承诺。性能基准按应用测量，缓存减少往返不一定降低总耗时。

反馈问题时附程序版本、工具、状态码和复现步骤，先去除凭证、输入正文、业务文件和个人信息。GitHub 发布页提供校验值及构建验证记录；完整日志可能含本机路径，请核对后再共享。政策与联系信息见管理器“关于”，联系邮箱为 jingyanx2@gmail.com。

## 工具一览

共 31 个工具：11 个只读、20 个会改变系统状态。只读标记描述工具的效果，具体调用仍受服务权限策略约束。

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
| `Wait` | 异步等待 1–60 秒，支持取消并释放桌面线程 | 只读 |
| `WaitFor` | 轮询直到出现某文字、某窗口、某元素或焦点，等待间隔不占桌面线程 | 只读 |
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
| `Observe` | 窗口/子树语义观察、已有目标批量刷新与增量、文本/选区读取 | 只读 |
| `Execute` | 语义优先执行、文本范围选择和局部编辑，读回验证 | 写入 |
| `Verify` | 用有类型约束的条件独立核对文件、窗口和控件状态 | 只读 |
| `Watch` | 事件触发局部回读、1.5 秒兜底、有界取消与队列溢出报告 | 只读 |
| `Browser` | 独立 Playwright Profile、页面/iframe、导航和语义操作 | 写入 |
| `NativeAPI` | 白名单文件提供者；范围检查、独占创建、写后核对 | 写入 |
| `Session` | 会话状态、取消和显式恢复 | 写入 |
| `RequestApproval` | 生成待审请求；不能自行批准 | 写入 |
| `Visual` | HWND 捕获与回退、ROI/缩放、图片变化提示、可选 OCR、窗口身份与坐标校验 | 只读 |
