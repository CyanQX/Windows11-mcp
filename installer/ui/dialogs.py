"""Readable maintenance sheets; raw details remain optional and previews stay immutable."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer

from PySide6.QtWidgets import QDialog, QFileDialog, QLineEdit, QPlainTextEdit, QScrollArea, QWidget, QSizePolicy, QLayout, QStyle

from installer.models import IntegrationStatus
from installer.ui.controls import GlassCheckBox as QCheckBox
from installer.ui.theme import GlassFrame, button, column, label, row, backdrop

NAMES = {"install": "安装 Skill + MCP", "uninstall": "卸载 Skill + MCP", "repair": "智能修复", "verify": "验证 MCP 连接", "backup": "备份配置", "adopt": "接管外部接入", "upgrade": "升级已安装组件"}
CLIENT_NAMES = {"claude": "Claude Code", "codex": "Codex 桌面端", "deepseek": "DeepSeek Harness", "opencode": "OpenCode", "gemini": "Gemini CLI", "zcode": "Zcode", "trae": "Trae"}
PROBLEMS = {"INCOMPLETE_INTEGRATION": "接入不完整", "MANAGED_FILE_MODIFIED": "受管文件已修改", "MCP_ENTRY_MODIFIED": "MCP 配置与记录不一致", "RUNTIME_MISSING": "运行时缺失", "RUNTIME_HASH_MISMATCH": "运行时校验不一致", "LEGACY_RECEIPT": "旧版安装记录", "VERSION_DIFFERS": "接入版本不同", "CONFIG_OR_RECEIPT_INVALID": "配置或记录无效", "SCAN_FAILED": "状态读取失败"}


class Detail(QDialog):
    def __init__(self, owner, title, size=(780, 590), *, show=True):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle(title)
        self.setWindowIcon(owner.windowIcon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        available = self.screen().availableGeometry()
        self.resize(min(size[0], available.width() - 35), min(size[1], available.height() - 65))
        self.setMinimumSize(min(540, available.width() - 35), min(320, available.height() - 65))
        self.body = column(self, 18, 12)
        self.body.addWidget(label(title, size=21, bold=True))
        owner.dialogs.append(self)
        self.finished.connect(lambda _: owner.dialogs.remove(self) if self in owner.dialogs else None)
        if show:
            self.show()
            backdrop(self, owner.theme.mode == "dark")

    def scroll(self):
        scroll, content = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        self.body.addWidget(scroll, 1)
        scroll.setWidget(content)
        return column(content, 2, 14)


def text_box(parent, value):
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    parent.addWidget(box, 1)
    return box


class DetailsText(QPlainTextEdit):
    """Keep wheel events inside this detail panel, including at either boundary."""
    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


def disclosure(layout, title, text):
    layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    control = button(title, compact=True)
    control.setProperty("detailToggle", True)
    control.setCheckable(True)
    box = DetailsText()
    box.setReadOnly(True)
    box.setPlainText(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=2))
    box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    box.ensurePolished()
    margins = box.contentsMargins()
    chrome = margins.top() + margins.bottom() + round(box.document().documentMargin() * 2)
    chrome += box.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
    box.setFixedHeight(min(220, max(64, box.document().blockCount() * box.fontMetrics().lineSpacing() + chrome)))
    box.hide()
    control.toggled.connect(box.setVisible)
    layout.addWidget(control)
    layout.addWidget(box)
    return box


def fact(layout, title, value, role=None):
    block = QWidget()
    body = column(block, 0, 3)
    body.addWidget(label(title, size=11, role="secondary"))
    body.addWidget(label(str(value), size=12, role=role, wrap=True))
    layout.addWidget(block)


def path_field(layout, title, path):
    layout.addWidget(label(title, size=11, role="secondary"))
    field = QLineEdit(str(path))
    field.setReadOnly(True)
    field.setCursorPosition(0)
    field.setAccessibleName(title)
    layout.addWidget(field)


class Preview(Detail):
    def __init__(self, owner, title, plan, apply=None):
        height = min(720, 420 + len(plan["targets"]) * 104) if isinstance(plan, dict) and isinstance(plan.get("targets"), list) else 560
        if isinstance(plan, dict) and plan.get("operation") == "undo_install":
            height = min(700, 400 + len(plan["profiles"]) * 70)
        super().__init__(owner, title, (820, height))
        self.body.setContentsMargins(24, 22, 24, 22)
        self.body.setSpacing(18)
        content = self.scroll()
        if isinstance(plan, dict) and "targets" in plan and isinstance(plan["targets"], list):
            operation = plan["operation"]
            summaries = {"install": "安装 Skill 与 MCP 连接", "upgrade": "更新 Skill、MCP 连接与共享服务", "repair": "修复 Skill、MCP 连接与服务", "uninstall": "移除本管理器安装的 Skill 与 MCP 连接", "adopt": "将现有接入纳入管理"}
            summary = GlassFrame()
            group = column(summary, 20, 10)
            group.addWidget(row(label(NAMES.get(operation, operation), size=15, bold=True), None, label("v" + plan["version"], size=12, role="secondary")))
            group.addWidget(label(summaries.get(operation, NAMES.get(operation, operation)) + f" · {len(plan['targets'])} 个编程端", size=12, role="secondary", wrap=True))
            content.addWidget(summary)
            for target in plan["targets"]:
                card = GlassFrame()
                stack = column(card, 20, 12)
                stack.addWidget(row(label(target["name"], size=15, bold=True), None, label("待卸载" if operation == "uninstall" else "待更新" if operation == "upgrade" else "待处理", size=11, role="secondary")))
                disclosure(stack, "安装位置与文件清单", {"Skill 文件夹": target["skill"], "连接配置文件": target["config"], "操作前文件校验": target["fingerprints"]})
                content.addWidget(card)
            if operation == "uninstall":
                content.addWidget(label("其他软件仍在使用的共享服务会保留。", size=12, role="secondary", wrap=True))
            disclosure(content, "共享服务与校验详情", {"服务程序": plan["runtime"], "共享服务保留规则": plan["runtime_retention"], "预览校验": plan["digest"]})
        elif isinstance(plan, dict) and plan.get("operation") == "restore_config":
            card = GlassFrame()
            group = column(card, 20, 16)
            group.addWidget(label(CLIENT_NAMES.get(plan["target"], plan["target"]), size=16, bold=True))
            before, after = QWidget(), QWidget()
            fact(column(before, 0, 6), "当前 MCP 连接", "已配置" if plan["current_entry"] is not None else "未配置")
            fact(column(after, 0, 6), "恢复后的 MCP 连接", "恢复为备份中的连接" if plan["restore_entry"] is not None else "移除 Windows-MCP 连接", role="warning" if plan["restore_entry"] is None else "success")
            group.addWidget(row(before, label("→", size=22, role="secondary"), after))
            group.addWidget(label("保留当前 Skill 和其他软件配置", size=12, role="secondary"))
            content.addWidget(card)
            path_field(content, "配置文件", plan["path"])
            disclosure(content, "查看连接内容与备份编号", {"当前连接": plan["current_entry"] or "无", "恢复后的连接": plan["restore_entry"] or "无（移除此连接）", "备份编号": plan["id"]})
        elif isinstance(plan, dict) and plan.get("operation") == "undo_install":
            content.addWidget(label("撤回这次安装，恢复安装前的文件与配置", size=15, bold=True, wrap=True))
            for key in plan["profiles"]:
                card = GlassFrame()
                column(card, 20, 12).addWidget(label(CLIENT_NAMES.get(key, key), size=15, bold=True))
                content.addWidget(card)
            content.addWidget(label("本次安装的 Skill、MCP 连接和安装记录将撤回。", size=12, role="secondary", wrap=True))
            if plan.get("conflicts"):
                content.addWidget(label("安装后部分内容已变化，无法完整撤销。原内容会保留。", role="warning", wrap=True))
                disclosure(content, "查看变化的文件", plan["conflicts"])
            disclosure(content, "查看撤销范围", plan["files"])
        elif isinstance(plan, dict):
            for name, value in plan.items():
                if name in {"digest", "current_hash", "sha256"}:
                    continue
                if isinstance(value, (dict, list)):
                    disclosure(content, str(name) + " · 展开详情", value)
                else:
                    fact(content, str(name), value)
        else:
            content.addWidget(label(str(plan), wrap=True))
        content.addStretch()
        controls = row(None)
        controls.layout().addWidget(button("取消" if apply else "关闭", self.close))
        if apply:
            confirm = button("确认撤销" if isinstance(plan, dict) and plan.get("operation") == "undo_install" else "确认恢复" if isinstance(plan, dict) and plan.get("operation") == "restore_config" else "确认执行", lambda: (self.close(), apply()), primary=True)
            if isinstance(plan, dict) and plan.get("operation") == "undo_install" and plan.get("conflicts"):
                confirm.setEnabled(False)
            controls.layout().addWidget(confirm)
        self.body.addWidget(controls)


class StatusDialog(Detail):
    def __init__(self, owner):
        super().__init__(owner, "安装状态", (850, 680))
        states = owner.selection.states
        self.body.addWidget(label(f"已安装 {sum(s.receipt_valid for s in states.values())} / {len(states)}", size=14, bold=True))
        content = self.scroll()
        for key, state in states.items():
            card = GlassFrame()
            stack = column(card, 16, 8)
            stack.addWidget(row(label(state.display_name, size=15, bold=True), None, label(state.label, role="success" if state.status == IntegrationStatus.INSTALLED_MANAGED else "secondary")))
            if state.problem_codes:
                stack.addWidget(label("\n".join(PROBLEMS.get(code, "需检查：" + code) for code in state.problem_codes), size=12, role="warning", wrap=True))
            disclosure(stack, "详情", {"安装": state.to_dict(), "连接": owner.service.health_results.get(key)})
            content.addWidget(card)
        content.addStretch()
        self.body.addWidget(row(None, button("关闭", self.close)))


class Results(Detail):
    def __init__(self, owner, title, results):
        super().__init__(owner, title, (760, 530))
        content = self.scroll()
        for result in results:
            card = GlassFrame()
            body = column(card, 16, 8)
            key = result.get("id")
            name = owner.selection.states[key].display_name if key in owner.selection.states else "操作结果"
            body.addWidget(row(label(name, size=15, bold=True), None, label("已撤销" if result.get("cancelled") else "已完成" if result.get("ok") else "未完成", role="success" if result.get("ok") or result.get("cancelled") else "error")))
            fact(body, "结论", result.get("message") or ("操作已提交，状态已核对" if result.get("ok") else "请查看日志与诊断"))
            if "tool_count" in result:
                fact(body, "真实连接核验", f"服务版本 {result.get('server_version') or '未知'} · {result['tool_count']} 个工具 · 会话 {result.get('session_status') or '未就绪'}")
                fact(body, "验证时间", result["checked_at"])
            disclosure(body, "展开技术详情", result)
            content.addWidget(card)
        content.addStretch()
        self.body.addWidget(row(None, button("关闭", self.close)))


class Targets(Detail):
    def __init__(self, owner, operation):
        self.operation, self.variables = operation, {}
        eligible = {key: state for key, state in owner.selection.states.items() if (state.status == IntegrationStatus.EXTERNAL if operation == "adopt" else state.receipt_valid if operation in {"uninstall", "repair", "verify", "upgrade"} else state.status != IntegrationStatus.UNKNOWN)}
        super().__init__(owner, NAMES[operation], (720, min(620, 190 + len(eligible) * 76)))
        body = self.scroll()
        for key, state in eligible.items():
            card = GlassFrame()
            stack = column(card, 12, 2)
            checkbox = QCheckBox(state.display_name)
            status = label(state.label, size=10, role="success" if state.receipt_valid else "secondary")
            status.setProperty("badge", True)
            status.setFixedHeight(28)
            status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stack.addWidget(row(checkbox, status))
            checkbox.toggled.connect(lambda checked, item=card: (item.setProperty("selected", checked), item.update()))
            if state.problem_codes:
                stack.addWidget(label("；".join(PROBLEMS.get(code, code) for code in state.problem_codes), size=11, role="warning", wrap=True))
            body.addWidget(card)
            self.variables[key] = checkbox
            checkbox.toggled.connect(lambda _: self.next_button.setEnabled(any(item.isChecked() for item in self.variables.values())))
        if not eligible:
            body.addWidget(label("暂无目标", role="secondary"))
        body.addStretch()
        self.next_button = button("开始验证" if operation == "verify" else "创建备份" if operation == "backup" else "开始", self.preview, primary=True)
        self.next_button.setEnabled(False)
        self.body.addWidget(row(None, button("取消", self.close), self.next_button))

    def preview(self):
        ids = [key for key, value in self.variables.items() if value.isChecked()]
        if not ids or self.owner.busy:
            return
        owner, operation = self.owner, self.operation
        self.close()
        if operation == "verify":
            def verify():
                results = []
                for index, key in enumerate(ids):
                    try:
                        result = owner.service.verify(key, progress=lambda fraction, message: owner.service.report((index + fraction) / len(ids), message)).to_dict()
                    except Exception as exc:
                        result = {"ok": False, "message": str(exc)}
                    results.append({"id": key, **result})
                    owner.service.report(len(results) / len(ids), "目标连接检查已返回")
                return results
            owner.run(verify, lambda results: Results(owner, "MCP 连接验证结果", results), rescan=False, title="等待真实 MCP 握手返回")
        elif operation == "backup":
            owner.run(lambda: owner.service.backup(ids), lambda values: Preview(owner, "备份已创建", {"备份数量": len(values), "备份记录": values}), rescan=False, title="正在创建配置快照")
        else:
            def prepare():
                if operation == "upgrade" and set(ids) != set(owner.service.upgrade_targets()):
                    raise ValueError("请勾选全部受管接入，统一升级共享服务。")
                return owner.service.preview(operation, ids)
            owner.run(prepare, lambda plan: Preview(owner, NAMES[operation] + " · 操作预览", plan, lambda: owner.run(lambda: owner.service.apply_plan(operation, ids, plan["digest"]), lambda results: Results(owner, NAMES[operation] + "结果", results), title="正在" + NAMES[operation])), rescan=False, title="正在准备逐文件操作预览")


class Diagnostics(Detail):
    def __init__(self, owner, report=False):
        super().__init__(owner, "日志与诊断", (850, 650))
        self.report_mode, self.value = report, None
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索客户端、会话、错误码或日志内容")
        self.body.addWidget(row(self.query, button("历史日志", lambda: self.mode(False)), button("错误报告预览", lambda: self.mode(True))))
        self.summary = label("", role="secondary", wrap=True)
        self.body.addWidget(self.summary)
        self.box = text_box(self.body, "")
        self.body.addWidget(row(button("复制已预览内容", self.copy), None, button("关闭", self.close), button("导出已预览内容", self.export, primary=True)))
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.refresh)
        self.query.textChanged.connect(lambda _: self.search_timer.start(250))
        self.refresh()

    def mode(self, report):
        self.report_mode = report
        self.refresh()

    def refresh(self):
        service = self.owner.service
        if self.report_mode:
            self.value = service.log.report(self.owner.selection.states, service.health_results)
            self.summary.setText("错误报告")
            self.box.setPlainText(json.dumps(self.value, ensure_ascii=False, indent=2))
        else:
            self.value = service.log.history(self.query.text())
            self.summary.setText(f"{len(self.value)} 条事件")
            self.box.setPlainText("\n".join(f"{event['time_iso']}  {event['level']}  {event['message']}  {event.get('error_code') or ''}" for event in self.value))

    def copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.box.toPlainText())

    def export(self):
        target, _ = QFileDialog.getSaveFileName(self, "导出已预览的脱敏内容", f"Windows-MCP-report-{datetime.now():%Y%m%d-%H%M%S}.json", "JSON (*.json)")
        if target:
            from installer.backend import atomic_write
            from installer.storage import check_path
            try:
                atomic_write(check_path(Path(target)), json.dumps(self.value, ensure_ascii=False, indent=2).encode("utf-8"))
                self.summary.setText("已导出")
            except Exception as exc:
                self.owner.error("导出失败", str(exc))
