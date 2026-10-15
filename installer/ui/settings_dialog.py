"""One ordered settings hub; appearance updates live, maintenance remains explicit."""
from __future__ import annotations

import copy
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QColor
from shiboken6 import isValid
from PySide6.QtWidgets import QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QLineEdit, QProgressBar, QSlider, QSpinBox, QTabWidget, QTextBrowser

from installer.settings import ENV_KEYS
from installer.ui.dialogs import Detail, Diagnostics, Preview, Targets, disclosure, fact
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
            if name == "检查更新" and owner.update_release and owner.update_release["available"]:
                badge = label("new", role="error", bold=True)
                body.addWidget(row(control, badge))
            else:
                body.addWidget(control)
        self.adjustSize()
        bounds = owner.screen().availableGeometry()
        point = owner.settings_button.mapToGlobal(QPoint(owner.settings_button.width() - self.width(), owner.settings_button.height() + 5))
        self.move(max(bounds.left(), min(point.x(), bounds.right() - self.width())), max(bounds.top(), min(point.y(), bounds.bottom() - self.height())))
        owner.dialogs.append(self)
        self.finished.connect(lambda _: owner.dialogs.remove(self) if self in owner.dialogs else None)
        self.show()


class Appearance(Detail):
    def __init__(self, owner):
        super().__init__(owner, "外观", (700, 650), "外观直接生效并保存；不会重启 MCP 或重新扫描客户端。")
        body = self.scroll()
        self.controls = {}
        for key, title, values in (("theme", "全局主题", [("浅蓝玻璃", "light"), ("暖色护眼", "warm"), ("深色", "dark"), ("跟随系统", "system")]), ("log_background", "日志背景", [("浅蓝", "blue"), ("暖色", "warm"), ("深色", "dark"), ("自定义颜色", "custom")])):
            body.addWidget(label(title, size=14, bold=True))
            choice = QComboBox()
            for text, value in values:
                choice.addItem(text, value)
            current = owner.settings.value["theme"] if key == "theme" else owner.settings.value["appearance"][key]
            choice.setCurrentIndex(choice.findData(current))
            self.controls[key] = choice
            choice.currentIndexChanged.connect(self.save)
            body.addWidget(choice)
        self.color_button = button("选择日志自定义颜色", self.choose_color)
        body.addWidget(self.color_button)
        body.addWidget(label("玻璃强度", size=14, bold=True))
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setValue(owner.settings.value["appearance"]["glass_strength"])
        body.addWidget(self.strength)
        self.strength.sliderReleased.connect(self.save)
        self.strength.valueChanged.connect(self.schedule_save)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save)
        self.reduced = QCheckBox("减少动画，保持稳定、直接的反馈")
        self.reduced.setChecked(owner.settings.value["appearance"]["reduce_motion"])
        self.reduced.toggled.connect(self.save)
        body.addWidget(self.reduced)
        body.addWidget(label("拖动面板标题可自由停靠、换位或浮动；拖动分隔边可调整大小，继续向最小尺寸以内拖动可收起。", role="secondary", wrap=True))
        body.addWidget(button("恢复默认布局", owner.workspace.reset, icon="restore"))
        body.addStretch()
        self.notice = label("", role="secondary", wrap=True)
        self.body.addWidget(self.notice)
        self.body.addWidget(row(None, button("完成", self.close, primary=True)))

    def schedule_save(self):
        if hasattr(self, "save_timer"):
            self.save_timer.start(180)

    def choose_color(self):
        color = QColorDialog.getColor(QColor(self.owner.settings.value["appearance"]["log_custom_color"]), self, "日志背景颜色")
        if color.isValid():
            value = copy.deepcopy(self.owner.settings.value)
            value["appearance"]["log_custom_color"] = color.name()
            value["appearance"]["log_background"] = "custom"
            try:
                self.owner.settings.save(value)
            except Exception as exc:
                self.notice.setText(str(exc))
                return
            choice = self.controls["log_background"]
            choice.blockSignals(True)
            choice.setCurrentIndex(choice.findData("custom"))
            choice.blockSignals(False)
            self.owner.apply_appearance()

    def save(self, *_):
        if not hasattr(self, "reduced"):
            return
        try:
            value = copy.deepcopy(self.owner.settings.value)
            value["theme"] = self.controls["theme"].currentData()
            value["appearance"].update(log_background=self.controls["log_background"].currentData(), glass_strength=self.strength.value(), reduce_motion=self.reduced.isChecked())
            self.owner.settings.save(value)
            self.owner.apply_appearance()
            self.notice.setText("外观已保存")
        except Exception as exc:
            self.notice.setText(str(exc))

    def closeEvent(self, event):
        self.save_timer.stop()
        self.save()
        super().closeEvent(event)


def paths(owner):
    value = {"共享运行时": str(owner.service.backend.executable)}
    for key, details in owner.settings.path_details().items():
        state = owner.selection.states.get(key)
        value[state.display_name if state else key] = details
    return Preview(owner, "实际安装目录", value)


class Preferences(Detail):
    def __init__(self, owner, paths=False):
        self.paths_mode = paths
        super().__init__(owner, "安装目录" if paths else "高级设置", (820, 740), "目录设置仅作用于当前用户。切换受管目录前需先卸载对应接入。" if paths else "调整连接与日志选项，或为未检测到的编程端指定程序路径。")
        self.body.setContentsMargins(26, 22, 26, 22)
        self.body.setSpacing(16)
        body = self.scroll()
        body.setContentsMargins(2, 2, 14, 8)
        body.setSpacing(18)
        self.fields = {}
        value = owner.settings.value
        def section(title, description):
            card = GlassFrame()
            stack = column(card, 20, 14)
            stack.addWidget(label(title, size=15, bold=True))
            stack.addWidget(label(description, size=11, role="secondary", wrap=True))
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
                control.setPlaceholderText("自动检测；可留空")
                if key.startswith("exe:"):
                    picker = button("浏览", lambda k=key: self.browse(k), icon="folder", compact=True)
                    picker.setAccessibleName("浏览 " + title)
                    group.addWidget(row(control, picker))
                    group.addWidget(label("仅识别桌面 GUI EXE，不使用 Codex CLI。" if key == "exe:codex" else "留空使用自动检测；也可选择实际安装目录中的启动程序。", size=10, role="secondary", wrap=True))
                else:
                    group.addWidget(control)
                stack.addWidget(block)
            self.fields[key] = control
        if paths:
            field(body, "data_dir", "共享数据目录（备份、日志、收据与运行时）", value["data_dir"])
            self.fields["data_dir"].setEnabled(owner.settings.portable_data is None)
            for key in sorted(ENV_KEYS):
                field(body, key, key + "（留空沿用系统环境与官方默认目录）", value["environment_overrides"].get(key, ""))
            body.addWidget(button("查看当前实际目录", lambda: globals()["paths"](owner)))
        else:
            connection = section("连接与日志", "数值立即用于后续任务；不会修改 MCP 的操作权限。")
            for key, title, limits in (("health_timeout", "连接检查超时（秒）", (5, 180)), ("log_retention_days", "日志保留天数", (1, 365)), ("log_max_mb", "日志总容量上限（MB）", (5, 500))):
                field(connection, key, title, value[key], limits)
            clients = section("编程端程序路径", "支持自定义安装位置。DeepSeek Harness 同时检查系统注册的安装路径。")
            for key, profile in owner.service.backend.clients.items():
                name = owner.selection.states[key].display_name if key in owner.selection.states else "Codex 桌面端" if key == "codex" else profile.name
                field(clients, "exe:" + key, name, value["client_executables"].get(key, ""))
        body.addStretch()
        self.body.addWidget(row(button("恢复本页默认", lambda: self.save(True)), None, button("取消", self.close), button("保存", self.save, primary=True)))

    def browse(self, key):
        path, _ = QFileDialog.getOpenFileName(self, "选择编程端启动程序", self.fields[key].text(), "Windows 程序 (*.exe *.cmd *.bat)")
        if path:
            self.fields[key].setText(path)

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
        super().__init__(owner, "配置与恢复", (820, 620), "恢复仅处理 Windows-MCP 项，保留其他 MCP 和个人设置；先预览，再确认。")
        body = self.scroll()
        for record in owner.service.backups():
            card = GlassFrame()
            stack = column(card, 14, 8)
            stack.addWidget(label(f"{record['profile_id']} · {record['operation']} · {record['state']}", bold=True))
            stack.addWidget(label(record["id"], size=11, role="secondary"))
            choice = QComboBox()
            choice.addItems(record.get("profiles") or [record["profile_id"]])
            stack.addWidget(row(choice, None, button("恢复预览", lambda item=record, selected=choice: self.preview(item, selected.currentText()))))
            body.addWidget(card)
        if not owner.service.backups():
            body.addWidget(label("尚无备份记录。安装或修改前会自动创建快照。", role="secondary", wrap=True))
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
            Preview(owner, "崩溃恢复预览", {**item, "说明": "按文件散列恢复本次交易；保留用户后续修改并报告冲突。"}, lambda: owner.run(recover, lambda results: Results(owner, "交易恢复结果", results)))
        else:
            owner.run(lambda: owner.service.preview_restore(item["id"], key), lambda plan: Preview(owner, "配置恢复预览", plan, lambda: owner.run(lambda: owner.service.restore(item["id"], plan["digest"], key))), rescan=False)


class Updates(Detail):
    def __init__(self, owner):
        super().__init__(owner, "检查更新", (790, 650), "官方 Release 检查、下载校验和版本升级集中在这里。")
        self.updater = UpdateService(owner.service.backend.data, owner.version, portable=owner.settings.portable_data is not None)
        self.release = None
        body = self.scroll()
        fact(body, "当前管理器版本", owner.version)
        self.notice = label("尚未检查官方更新", size=14, bold=True, wrap=True)
        body.addWidget(self.notice)
        self.check_button = button("检查官方更新", self.check, icon="refresh")
        self.download_button = button("下载并校验更新", self.download, primary=True)
        self.download_button.setEnabled(False)
        body.addWidget(row(self.check_button, self.download_button, None))
        self.bar = QProgressBar()
        self.bar.setRange(0, 10000)
        self.bar.setTextVisible(False)
        body.addWidget(self.bar)
        owner.progress.valueChanged.connect(self.bar.setValue)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        self.notes.setPlainText("检查成功后显示官方版本信息与更新说明。")
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
        self.check()

    def controls(self):
        self.check_button.setEnabled(not self.owner.busy)
        self.download_button.setEnabled(bool(self.release and self.release["available"]) and not self.owner.busy)

    def check(self):
        def checked(release):
            self.owner.update_release = release
            if not isValid(self):
                return
            self.release = release
            self.notice.setText(f"发现新版本 {release['latest']}" if release["available"] else f"当前已是最新版本 · 官方稳定版 {release['latest']}")
            self.notes.setPlainText(release.get("notes", "暂无新版本。"))
            self.controls()
        self.owner.run(lambda: self.updater.check(progress=self.owner.service.report), checked, rescan=False, title="正在读取并校验官方版本信息")

    def download(self):
        if not self.release or self.owner.busy:
            return
        self.owner.run(lambda: self.updater.download(self.release, progress=self.owner.service.report), self.downloaded, rescan=False, title="正在下载官方更新")

    def downloaded(self, path):
        owner, release = self.owner, self.release
        if not getattr(sys, "frozen", False):
            Preview(owner, "更新下载已校验", {"文件": str(path), "说明": "源码运行不替换 Python，请使用 EXE 更新。"})
            return
        def install():
            if owner.busy:
                return
            self.updater.launch_installer(path, release)
            owner.close()
        if release.get("kind") == "installer":
            Preview(owner, "启动新版安装向导", {"版本": release["latest"], "安装包": str(path), "SHA-256": release["sha256"], "说明": "关闭管理器并启动已校验的安装向导，可选择原位置升级。"}, install)
        else:
            def prepared(plan):
                def apply():
                    if owner.busy:
                        return
                    location, nonce = self.updater.prepare(path, release, Path(sys.executable))
                    start_update(location, nonce)
                    owner.close()
                Preview(owner, "便携更新预览", plan, apply)
            owner.run(lambda: {"版本": release["latest"], "受管接入": owner.service.upgrade_targets(), "流程": "关闭 → 替换 → 重启 → 验证；失败保留回滚副本，保留 data。"}, prepared, rescan=False, title="正在核对便携更新计划")


class About(Detail):
    def __init__(self, owner):
        super().__init__(owner, "关于 Windows MCP", (780, 630), f"版本 {owner.version} · Windows 安装与维护管理器")
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
