"""Ordered settings sheets with explicit commits and readable recovery history."""
from __future__ import annotations

import copy
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QColor
from shiboken6 import isValid
from PySide6.QtWidgets import QColorDialog, QDialog, QFileDialog, QLineEdit, QProgressBar, QSpinBox, QTabWidget, QTextBrowser

from installer.settings import ENV_KEYS
from installer.ui.controls import GlassCheckBox as QCheckBox
from installer.ui.controls import ChoiceBox as QComboBox
from installer.ui.dialogs import CLIENT_NAMES, Detail, Diagnostics, Preview, Targets, disclosure, fact
from installer.ui.theme import GlassFrame, button, column, label, row
from installer.updates import UpdateService, start_update


class SettingsHub(QDialog):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("设置")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        frame = GlassFrame(self, popup=True)
        column(self, 5, 0).addWidget(frame)
        body = column(frame, 12, 4)
        body.addWidget(label("设置", size=14, bold=True))
        actions = [("外观", "settings", lambda: Appearance(owner)), ("高级设置", "settings", lambda: Preferences(owner)), ("配置与恢复", "restore", lambda: Backups(owner)), ("安装目录", "folder", lambda: Preferences(owner, paths=True)), ("检查更新", "update", lambda: Updates(owner)), ("日志与诊断", "document", lambda: Diagnostics(owner)), ("关于", "info", lambda: About(owner))]
        self.names = []
        for name, icon, action in actions:
            self.names.append(name)
            control = button(name, lambda invoke=action: (self.close(), invoke()), icon=icon, compact=True)
            control.setMinimumWidth(210)
            if name == "检查更新":
                self.update_badge = label("new", role="error", bold=True)
                self.update_badge.setVisible(bool(owner.update_release and owner.update_release["available"]))
                body.addWidget(row(control, self.update_badge))
                owner.release_checked.connect(self.release_hint)
            else:
                body.addWidget(control)
        self.adjustSize()
        bounds = owner.screen().availableGeometry()
        point = owner.settings_button.mapToGlobal(QPoint(owner.settings_button.width() - self.width(), owner.settings_button.height() + 5))
        self.move(max(bounds.left(), min(point.x(), bounds.right() - self.width())), max(bounds.top(), min(point.y(), bounds.bottom() - self.height())))
        owner.dialogs.append(self)
        self.finished.connect(lambda _: owner.dialogs.remove(self) if self in owner.dialogs else None)
        self.show()

    def release_hint(self, release):
        self.update_badge.setVisible(bool(release and release.get("available")))
        self.adjustSize()


class Appearance(Detail):
    def __init__(self, owner):
        super().__init__(owner, "外观", (700, 650))
        self.draft = copy.deepcopy(owner.settings.value)
        self.reset_layout = False
        self.body.setContentsMargins(24, 22, 24, 22)
        body = self.scroll()
        card = GlassFrame()
        group = column(card, 20, 16)
        body.addWidget(card)
        self.controls = {}
        for key, title, values in (("theme", "全局主题", [("浅蓝玻璃", "light"), ("暖色护眼", "warm"), ("深色", "dark"), ("跟随系统", "system")]), ("log_background", "日志背景", [("浅蓝", "blue"), ("暖色", "warm"), ("深色", "dark"), ("自定义颜色", "custom")])):
            choice = QComboBox()
            for text, value in values:
                choice.addItem(text, value)
            current = owner.settings.value["theme"] if key == "theme" else owner.settings.value["appearance"][key]
            choice.setCurrentIndex(choice.findData(current))
            self.controls[key] = choice
            group.addWidget(row(label(title, size=13, bold=True), None, choice))
            choice.setMinimumWidth(290)
        self.color_button = button("选择日志自定义颜色", self.choose_color)
        group.addWidget(row(None, self.color_button))
        self.strength = QComboBox()
        self.strength.addItem("常规", 0)
        self.strength.addItem("玻璃拟态", 65)
        self.strength.setCurrentIndex(0 if owner.settings.value["appearance"]["glass_strength"] == 0 else 1)
        self.strength.setMinimumWidth(290)
        group.addWidget(row(label("视觉强度", size=13, bold=True), None, self.strength))
        def demo():
            from installer.ui.glass_demo import open_demo
            open_demo(owner)
        group.addWidget(row(None, button("玻璃按钮交互预览", demo)))
        self.reduced = QCheckBox("减少动画")
        self.reduced.setChecked(owner.settings.value["appearance"]["reduce_motion"])
        group.addWidget(self.reduced)
        body.addStretch()
        self.layout_button = button("恢复默认布局", self.request_layout_reset, icon="restore")
        body.addWidget(row(self.layout_button, None))
        self.notice = label("", role="secondary", wrap=True)
        self.body.addWidget(self.notice)
        self.body.addWidget(row(None, button("取消", self.close), button("完成", self.save, primary=True)))

    def request_layout_reset(self):
        self.reset_layout = True
        self.layout_button.setText("完成后恢复默认布局")

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.draft["appearance"]["log_custom_color"]), self, "日志背景颜色")
        if color.isValid():
            self.draft["appearance"]["log_custom_color"] = color.name()
            self.color_button.setText("自定义颜色 · " + color.name().upper())
            choice = self.controls["log_background"]
            choice.blockSignals(True)
            choice.setCurrentIndex(choice.findData("custom"))
            choice.blockSignals(False)

    def save(self, *_):
        if not hasattr(self, "reduced"):
            return
        try:
            value = copy.deepcopy(self.owner.settings.value)
            value["theme"] = self.controls["theme"].currentData()
            value["appearance"].update(log_background=self.controls["log_background"].currentData(), log_custom_color=self.draft["appearance"]["log_custom_color"], glass_strength=self.strength.currentData(), reduce_motion=self.reduced.isChecked())
            self.owner.settings.save(value)
            self.owner.apply_appearance()
            if self.reset_layout:
                self.owner.workspace.reset()
            self.close()
        except Exception as exc:
            self.notice.setText(str(exc))

def paths(owner):
    value = {"共享运行时": str(owner.service.backend.executable)}
    for key, details in owner.settings.path_details().items():
        state = owner.selection.states.get(key)
        value[state.display_name if state else key] = details
    return Preview(owner, "实际安装目录", value)


class Preferences(Detail):
    def __init__(self, owner, paths=False):
        self.paths_mode = paths
        super().__init__(owner, "安装目录" if paths else "高级设置", (820, 740))
        self.body.setContentsMargins(26, 22, 26, 22)
        self.body.setSpacing(16)
        body = self.scroll()
        body.setContentsMargins(2, 2, 14, 8)
        body.setSpacing(18)
        self.fields = {}
        self.folder_notices = {}
        value = owner.settings.value
        def section(title):
            card = GlassFrame()
            stack = column(card, 20, 14)
            stack.addWidget(label(title, size=15, bold=True))
            body.addWidget(card)
            return stack
        def field(stack, key, title, initial, limits=None):
            control = QSpinBox() if limits else QLineEdit(str(initial))
            control.setAccessibleName(title)
            if limits:
                control.setRange(*limits)
                control.setValue(initial)
                control.setFixedWidth(108)
                control.setAlignment(Qt.AlignmentFlag.AlignCenter)
                minus = button("−", control.stepDown, compact=True)
                plus = button("+", control.stepUp, compact=True)
                for item, verb in ((minus, "减少"), (plus, "增加")):
                    item.setFixedSize(36, 42)
                    item.setAccessibleName(verb + title)
                stack.addWidget(row(label(title, size=12), None, minus, control, plus))
            else:
                block = GlassFrame()
                group = column(block, 14, 8)
                group.addWidget(label(title, size=12, bold=True))
                control.setPlaceholderText("自动检测" if key.startswith("exe:") else "默认目录")
                if key.startswith("exe:"):
                    picker = button("添加文件夹", lambda k=key: self.browse(k), icon="folder", compact=True)
                    picker.setAccessibleName("添加文件夹 · " + title)
                    group.addWidget(row(control, picker))
                    notice = label("", size=11, role="secondary", wrap=True)
                    notice.hide()
                    group.addWidget(notice)
                    self.folder_notices[key] = notice
                else:
                    group.addWidget(control)
                stack.addWidget(block)
            self.fields[key] = control
        if paths:
            field(body, "data_dir", "共享数据目录（备份、日志、收据与运行时）", value["data_dir"])
            self.fields["data_dir"].setEnabled(owner.settings.portable_data is None)
            for key in sorted(ENV_KEYS):
                field(body, key, key, value["environment_overrides"].get(key, ""))
            body.addWidget(button("查看当前实际目录", lambda: globals()["paths"](owner)))
        else:
            connection = section("连接与日志")
            for key, title, limits in (("health_timeout", "连接检查超时（秒）", (5, 180)), ("log_retention_days", "日志保留天数", (1, 365)), ("log_max_mb", "日志总容量上限（MB）", (5, 500))):
                field(connection, key, title, value[key], limits)
            clients = section("编程端程序路径")
            for key, profile in owner.service.backend.clients.items():
                name = owner.selection.states[key].display_name if key in owner.selection.states else "Codex 桌面端" if key == "codex" else profile.name
                field(clients, "exe:" + key, name, value["client_executables"].get(key, ""))
        body.addStretch()
        self.body.addWidget(row(button("恢复本页默认", lambda: self.save(True)), None, button("取消", self.close), button("保存", self.save, primary=True)))

    def browse(self, key):
        if self.owner.busy:
            return
        current = Path(self.fields[key].text()) if self.fields[key].text() else self.owner.settings.home
        path = QFileDialog.getExistingDirectory(self, "选择编程端安装文件夹", str(current.parent if current.is_file() else current))
        if path:
            self.fields[key].setText(path)
            notice = self.folder_notices[key]
            notice.setText("正在查找程序…")
            notice.show()
            from installer.detection import find_client_in_folder
            def scan():
                try:
                    return True, str(find_client_in_folder(Path(path), key[4:]))
                except Exception as exc:
                    return False, str(exc)
            def found(result):
                if isValid(self):
                    notice.setText("已找到 · " + Path(result[1]).name if result[0] else result[1])
            self.owner.run(scan, found, rescan=False, title="正在扫描所选文件夹")

    def save(self, reset=False):
        owner = self.owner
        if owner.busy:
            owner.error("任务进行中", "请在当前维护任务结束后保存高级或目录设置。")
            return
        try:
            previous = owner.settings.value
            updated = copy.deepcopy(previous)
            for key, control in self.fields.items():
                if key in ENV_KEYS:
                    updated["environment_overrides"][key] = "" if reset else control.text().strip()
                elif key.startswith("exe:"):
                    updated["client_executables"][key[4:]] = "" if reset else control.text().strip()
                else:
                    updated[key] = owner.settings.defaults[key] if reset else control.value() if isinstance(control, QSpinBox) else control.text().strip()
            owner.settings.save(updated)
            structural = any(updated[key] != previous[key] for key in ("data_dir", "environment_overrides"))
            discovery_changed = updated["client_executables"] != previous["client_executables"]
            if structural:
                owner.reload_service()
            else:
                owner.service.health.timeout = updated["health_timeout"]
                owner.service.log.retention_days = updated["log_retention_days"]
                owner.service.log.max_bytes = updated["log_max_mb"] * 1048576
                if discovery_changed:
                    owner.service.detector.discovery = owner.settings.discovery()
            self.close()
            if structural or discovery_changed:
                owner.scan()
        except Exception as exc:
            owner.error("设置未保存", str(exc))


class Backups(Detail):
    def __init__(self, owner):
        super().__init__(owner, "配置与恢复", (820, 620))
        self.body.setContentsMargins(24, 22, 24, 22)
        body = self.scroll()
        records = [item for item in owner.service.backups() if not item.get("batch_id")]
        operations = {"install": "安装前备份", "install_batch": "安装前备份", "backup": "手动备份", "upgrade": "升级前备份", "uninstall": "卸载前备份", "repair": "修复前备份", "restore": "恢复前备份", "adopt": "接入前备份"}
        states = {"committed": "可恢复", "prepared": "待恢复", "writing": "待恢复", "recovery_required": "需检查", "rolled_back": "已撤销"}
        for record in records[:100]:
            card = GlassFrame()
            stack = column(card, 20, 12)
            keys = record.get("profiles") or [record["profile_id"]]
            name = "、".join(CLIENT_NAMES.get(key, key) for key in keys)
            status = label(states.get(record["state"], "需检查"), size=11, role="success" if record["state"] == "committed" else "secondary")
            status.setProperty("badge", True)
            stack.addWidget(row(label(name, size=14, bold=True, wrap=True), None, status))
            when = datetime.fromtimestamp(record.get("time") or 0).strftime("%Y-%m-%d  %H:%M")
            metadata = label(operations.get(record["operation"], "配置备份") + "  ·  " + when, size=11, role="secondary")
            if len(keys) > 1:
                stack.addWidget(metadata)
                choice = QComboBox()
                for key in keys:
                    choice.addItem(CLIENT_NAMES.get(key, key), key)
                stack.addWidget(row(choice, None, button("恢复配置…", lambda item=record, selected=choice: self.preview(item, selected.currentData()))))
            else:
                stack.addWidget(row(metadata, None, button("恢复配置…", lambda item=record, key=keys[0]: self.preview(item, key))))
            body.addWidget(card)
        if not records:
            body.addWidget(label("暂无备份", role="secondary"))
        body.addStretch()
        self.body.addWidget(row(button("创建配置备份", lambda: Targets(owner, "backup"), icon="backup"), None, button("关闭", self.close)))

    def preview(self, item, key):
        owner = self.owner
        if owner.busy:
            return
        if item["state"] in {"prepared", "writing", "recovery_required"}:
            def recover():
                conflicts = owner.service.recover(item["id"])
                return [{"id": item["profile_id"], "ok": not conflicts, "message": "交易已恢复" if not conflicts else "存在用户修改或文件占用，请查看技术详情", "conflicts": conflicts}]
            from installer.ui.dialogs import Results
            view = {"恢复目标": "、".join(CLIENT_NAMES.get(target, target) for target in (item.get("profiles") or [key])), "恢复内容": "撤回中断操作已写入的文件与配置", "处理方式": "安装后的个人修改会保留，无法恢复的文件会列出"}
            Preview(owner, "恢复中断的操作", view, lambda: owner.run(recover, lambda results: Results(owner, "恢复结果", results)))
        else:
            owner.run(lambda: owner.service.preview_restore(item["id"], key), lambda plan: Preview(owner, "配置恢复预览", plan, lambda: owner.run(lambda: owner.service.restore(item["id"], plan["digest"], key))), rescan=False)


class Updates(Detail):
    def __init__(self, owner):
        super().__init__(owner, "检查更新", (790, 650))
        self.updater = UpdateService(owner.service.backend.data, owner.version, portable=owner.settings.portable_data is not None)
        self.release = None
        self.pending_release = None
        body = self.scroll()
        fact(body, "当前管理器版本", owner.version)
        self.notice = label("尚未检查官方更新", size=14, bold=True, wrap=True)
        body.addWidget(self.notice)
        self.check_button = button("检查官方更新", self.check, icon="refresh")
        self.download_button = button("更新安装", self.download, primary=True)
        self.download_button.setEnabled(False)
        body.addWidget(row(self.check_button, self.download_button, None))
        self.bar = QProgressBar()
        self.bar.setRange(0, 10000)
        self.bar.setTextVisible(False)
        body.addWidget(self.bar)
        owner.progress.valueChanged.connect(self.bar.setValue)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        body.addWidget(self.notes, 1)
        body.addWidget(label("版本与升级", size=16, bold=True))
        managed = {key: {"version": state.detected_version or "旧版记录", "status": state.label} for key, state in owner.selection.states.items() if state.receipt_valid}
        disclosure(body, f"查看受管接入版本 · {len(managed)} 个", managed)
        body.addWidget(button("升级现有受管接入至此版本", lambda: Targets(owner, "upgrade"), icon="update"))
        self.body.addWidget(row(None, button("关闭", self.close)))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.controls)
        self.timer.start(150)
        self.finished.connect(self.timer.stop)
        owner.release_checked.connect(self.checked)
        if owner.update_release:
            self.checked(owner.update_release)
        else:
            self.check()

    def controls(self):
        self.check_button.setEnabled(not self.owner.busy)
        self.download_button.setEnabled(bool(self.release and self.release["available"]) and not self.owner.busy)

    def checked(self, release):
        if not isValid(self):
            return
        self.release = release
        self.notice.setText(f"发现新版本 {release['latest']}" if release["available"] else f"当前已是最新版本 · 官方稳定版 {release['latest']}")
        self.notes.setPlainText(release.get("notes", "暂无新版本。"))
        self.controls()

    def check(self):
        def checked(release):
            self.owner.update_release = release
            self.owner.release_checked.emit(release)
        self.owner.run(lambda: self.updater.check(progress=self.owner.service.report), checked, rescan=False, title="正在读取并校验官方版本信息")

    def download(self):
        if not self.release or not self.release.get("available") or self.owner.busy:
            return
        self.pending_release = copy.deepcopy(self.release)
        release = self.pending_release
        self.owner.run(lambda: self.updater.download(release, progress=self.owner.service.report), self.downloaded, rescan=False, title="正在下载官方更新")

    def downloaded(self, path):
        owner, release = self.owner, self.pending_release or self.release
        if not getattr(sys, "frozen", False):
            Preview(owner, "更新下载已校验", {"文件": str(path), "模式": "源码运行"})
            return
        if owner.busy:
            return
        if release.get("kind") == "installer":
            self.updater.launch_installer(path, release, Path(sys.executable))
            owner.close()
        else:
            location, nonce = self.updater.prepare(path, release, Path(sys.executable))
            start_update(location, nonce)
            owner.close()


class About(Detail):
    def __init__(self, owner):
        super().__init__(owner, "关于 Windows MCP", (780, 630))
        self.body.addWidget(label(owner.version, role="secondary"))
        tabs = QTabWidget()
        self.body.addWidget(tabs, 1)
        texts = [("简介", "Windows MCP 为编程 AI 提供真实 Windows 操作能力。管理器负责 Skill 与 MCP 的安装、验证、修复、备份和更新。\n\nCodex 的安装目标为桌面端。\n\n本项目基于 CursorTouch/Windows-MCP，保留原项目许可与贡献者声明。"), ("使用政策", "请仅在有权操作的设备、应用、账号和文件范围内使用。\n\n禁止利用本工具实施恶意代码注入、进程劫持、窃取凭证、破坏数据、绕过权限或操控审批等危险行为。\n\n受保护操作须由本机用户核对具体参数后批准一次。不得让 AI 自行授予审批或通过注入绕过审批。\n\n以上为使用政策；权限边界由实际审批和服务策略执行。\n\n本政策不限制开源许可证允许的库替换、修改、调试及为调试该修改进行的逆向工程。"), ("许可证", (owner.base / "LICENSE.md").read_text(encoding="utf-8"))]
        texts.append(("Qt 许可与重建", (owner.base / "installer/resources/Qt-LICENSES.txt").read_text(encoding="utf-8")))
        notices = owner.base / "installer/resources/third-party-licenses.txt"
        if notices.exists():
            texts.append(("第三方声明", notices.read_text(encoding="utf-8")))
        for title, text in texts:
            box = QTextBrowser()
            box.setPlainText(text)
            tabs.addTab(box, title)
        self.body.addWidget(row(button("项目主页", lambda: webbrowser.open("https://github.com/CyanQX/Windows11-mcp")), None, button("关闭", self.close)))
