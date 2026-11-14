"""Per-user/version atomic acknowledgement, recorded only by a visible Qt sheet."""
from __future__ import annotations

import json
import time

from installer.backend import atomic_write
from installer.storage import ProcessLock, private_directory

TITLE = "本次更新"
CONTENT = """Actilo v1.9.11｜修复安装完成后的启动时序。

🔄 安装验证完成后再启动
修复更新时提前打开管理面板，误弹“安装／恢复尚未完成”的问题。安装器统一完成 Skill、MCP 与主程序同步和验证后，才启动一次管理面板，无须再次同步组件。

此前重大更新的功能继续保留：

🚀 启动体验全面优化
重点修复旧版启动缓慢、点击无响应、窗口重复打开与无限重载等问题，优化初始化流程和单实例运行机制。

🔄 安装与更新机制重构
新版安装器自动处理本项目旧版残留、配置迁移及受管组件同步。MCP、Skill 与启动器随新版本统一安装和升级，无须手动执行组件升级。

🔐 AI 控制与授权升级
支持 AI 在普通用户权限边界内操作 PowerShell、CMD 和 Windows Terminal；保留“持续全自动 · 完全访问”与“批准一次”模式，禁止自行获取更高级系统权限。

✨ 全新 Actilo 管理界面
统一品牌名称、简化页面结构、移除冗余设置，优化响应式布局、过渡动画及操作反馈。

⚡ 稳定性与资源使用优化
改进进程生命周期、依赖加载、错误处理与后台任务，降低无效重载和重复检查。

感谢使用 Actilo！"""


class ReleaseNotesState:
    def __init__(self, home, version):
        self.directory, self.version = home / ".windows-mcp", version
        self.path = self.directory / "release-notes-shown.json"

    def shown(self):
        if not self.path.exists():
            return False
        if self.path.stat().st_size > 65536:
            raise ValueError("更新日志展示记录过大，原记录已保留")
        row = json.loads(self.path.read_text(encoding="utf-8"))
        return self.version in row.get("versions", {})

    def record_visible(self):
        private_directory(self.directory)
        with ProcessLock(self.directory / "release-notes.lock"):
            if self.shown():
                return False
            row = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"schema": 1, "versions": {}}
            row["versions"][self.version] = {"displayed_at": time.time()}
            atomic_write(self.path, json.dumps(row, ensure_ascii=False).encode())
            return True


def show_release_notes(owner, *, automatic=False):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QTextBrowser
    from installer.ui.dialogs import Detail
    from installer.ui.theme import button, row
    import webbrowser
    from windows_mcp.product import REPOSITORY

    state = ReleaseNotesState(owner.settings.home, owner.version)
    display_lock = None
    def release_display_lock(*_):
        nonlocal display_lock
        if display_lock is not None:
            display_lock.__exit__()
            display_lock = None
    if automatic:
        try:
            private_directory(state.directory)
            display_lock = ProcessLock(state.directory / "release-notes-display.lock")
            display_lock.__enter__()
        except Exception as exc:
            display_lock = None
            if getattr(exc, "code", None) == "INSTALLER_BUSY":
                return None  # Another session is already displaying this sheet.
            raise
        try:
            if state.shown():
                release_display_lock()
                return None
        except Exception as exc:
            release_display_lock()
            owner.error("更新日志记录不可读取", str(exc))
            return None
    if getattr(owner, "release_notes_window", None) is not None:
        release_display_lock()
        owner.release_notes_window.raise_()
        return owner.release_notes_window

    class Notes(Detail):
        def showEvent(self, event):
            super().showEvent(event)
            if automatic:
                QTimer.singleShot(0, self.confirm_visible)

        def confirm_visible(self):
            if not self.isVisible():
                return
            try:
                state.record_visible()
            except Exception as exc:
                owner.error("更新日志记录失败", str(exc))
            finally:
                release_display_lock()

    sheet = Notes(owner, TITLE, (720, 680), show=False)
    owner.release_notes_window = sheet
    sheet.finished.connect(lambda _: setattr(owner, "release_notes_window", None))
    sheet.finished.connect(release_display_lock)
    body = QTextBrowser()
    body.setPlainText(CONTENT)
    body.setAccessibleName("Actilo 本次重大更新内容")
    sheet.body.addWidget(body, 1)
    sheet.body.addWidget(row(button("查看完整更新日志", lambda: webbrowser.open(REPOSITORY + "/releases/tag/v" + owner.version)), None, button("开始使用", sheet.close, primary=True)))
    sheet.show()
    return sheet
