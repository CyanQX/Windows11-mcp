<div align="center">
  <h1>Windows-MCP 1.2.1</h1>
  <p>给编程 AI 接入真实 Windows 桌面操作能力</p>
</div>

## 工具用途

Windows-MCP 是面向 Claude Code、Codex、DeepSeek Harness 等 AI 编程工具的 Windows 自动化服务。连接后，AI 可以读取窗口和控件，操作真实应用、浏览器与文件，并核对实际结果。

配套的 **Windows 安装与维护管理器**提供浅蓝色原生界面：自动检测编程端，一次安装 Skill 和 MCP，随后可验证连接、修复、备份、恢复与安全卸载。Skill 提供操作说明，MCP 服务提供真正的 31 个工具。Codex 的安装目标为 **Codex 桌面端**，不安装或调用 Codex CLI。

本项目基于 [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) 二次开发，以 [MIT 许可证](LICENSE.md)发布。

## 功能简介

- **读取与操作 Windows**：识别窗口、控件和状态，点击、输入、快捷键、滚动、拖放及窗口管理。
- **语义执行与结果核对**：`Observe → Execute → Verify`，先定位目标，再操作并读回状态；支持真实鼠标键盘输入。
- **浏览器自动化**：独立 Playwright 浏览器会话，读取页面、填表、操作 iframe 和弹窗。
- **原生文件与状态监听**：授权目录内的文件操作、多个窗口的事件监听、有界等待和取消。
- **视觉兜底与权限控制**：自绘界面可按窗口截图定位；高风险操作遵循一次性本地审批。普通权限不能绕过 UAC、锁屏或管理员窗口限制。
- **一键接入客户端**：支持 Codex 桌面端、Claude Code、官方 DeepSeek Harness、OpenCode 和 Gemini CLI。已安装项禁用重复安装，卸载目标在独立窗口选择；保留其他 MCP、个人配置与仍被使用的共享服务。
- **维护与诊断**：真实连接验证、事务备份与回滚、智能修复、外部接管预览、脱敏错误报告、主题和自定义安装目录。
- **可视化审批**：受保护操作自动打开本机审批窗口，由你核对参数并批准一次或拒绝，无需反复输入 PowerShell 命令。
- **校验后更新**：从官方 Release 检查和下载，核对 SHA-256 后确认升级；独立助手替换、重启并验证，失败保留回滚副本。更新记录见 [CHANGELOG](CHANGELOG.md)。

Windows 11 已实测；Windows 10、混合 DPI 多显示器和特殊应用的验证范围见 Skill 的[按需参考](skills/windows-mcp/references/advanced.md)。

## 安装指南

### EXE 安装与维护管理器（推荐）

1. 从 [1.2.1 Releases](https://github.com/CyanQX/Windows11-mcp/releases/tag/v1.2.1) 下载 **Windows-MCP-Setup-1.2.1.exe**。
2. 双击打开，在左侧下拉框选择已检测到、尚未接入的编程端，可多选。Codex 桌面端可安装时首次默认选中。
3. 点击底部“安装 Skill + MCP”，核对路径与文件预览后确认。右侧显示实际执行日志，安装完成自动核对版本、31 个工具和 Session READY。
4. 重启对应客户端或新建会话，使用 Windows-MCP。需要再次验证时，点击“验证 MCP 连接”，选择受管目标。
5. 卸载从底部“卸载 Skill + MCP”进入独立窗口；修复从右侧进入，备份、恢复、日志、路径与版本管理从“设置”进入。

EXE 内置 Python、MCP 服务和依赖，默认安装在 `%LOCALAPPDATA%\WindowsMCP`，**无需管理员权限、无需另装 Python**。普通桌面与已安装的 Edge 无需额外下载；其他 Playwright 浏览器通道需提供相应浏览器。MCP 由客户端按需启动，界面只展示最近一次真实验证结果及时间。

修改前创建私有备份；失败执行基于散列的回滚。JSONC 注释及其他配置会保留。手动修改过的接入卸载时保留冲突并提示部分完成；没有收据的外部接入需先预览并确认接管。多个客户端共享运行时，统一升级；存在其他或未知引用时保留运行时。修改数据目录前需卸载原目录的受管接入，旧日志与备份保留。

| 编程工具 | 默认 Skill 目录 | MCP 配置 |
|---|---|---|
| Claude Code | `~/.claude/skills/windows-mcp` | `~/.claude.json` |
| Codex 桌面端 | `~/.agents/skills/windows-mcp` | `~/.codex/config.toml` |
| DeepSeek Harness | `~/.dsh/skills/windows-mcp` | `~/.dsh/cordis.patch.yml` |
| OpenCode | `~/.config/opencode/skills/windows-mcp` | `~/.config/opencode/opencode.json` 或已有的 `opencode.jsonc` |
| Gemini CLI | `~/.gemini/skills/windows-mcp` | `~/.gemini/settings.json` |

`~` 表示当前用户主目录。界面显示实际目录，支持 `CODEX_HOME`、`DSH_HOME` 等环境变量。DeepSeek 桌面版和 CLI 使用同一个 Harness Home。目录规则参照 [Claude Code](https://code.claude.com/docs/en/skills)、[Codex](https://learn.chatgpt.com/docs/build-skills)、[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.md)、[OpenCode](https://opencode.ai/docs/skills/) 和 [Gemini CLI](https://geminicli.com/docs/cli/using-agent-skills/) 的官方文档。

接入的是当前用户的本地编程工具；网页聊天页面和云端会话需提供能连接本机的客户端。Codex 桌面端使用用户级 `config.toml`；同一用户的其他 Codex 客户端也会读取这份配置。服务请求审批时会打开“本机操作审批”，核对原会话、参数、期限后批准一次，再让 AI 重试相同调用。审批窗口打开期间暂停 MCP 桌面输入，避免 AI 操作自己的审批界面。旧请求缺少完整参数时需重新申请。

### Python 服务安装（可选）

已有 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/) 时，可继续安装固定版本：

```powershell
uv tool install --force --with "playwright>=1.51,<2" "git+https://github.com/CyanQX/Windows11-mcp.git@v1.2.1"
```

将 `%USERPROFILE%\.local\bin\windows-mcp.exe` 作为客户端 MCP 命令，参数为 `["serve"]`。使用 Skill 可从本仓库 `main` 的 `skills/windows-mcp` 复制到上表相应目录。工具参数与可选设置见 [Skill 参考](skills/windows-mcp/references/advanced.md)。

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
