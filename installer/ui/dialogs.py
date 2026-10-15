"""Readable maintenance sheets; raw details remain optional and previews stay immutable."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer

from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QLineEdit, QPlainTextEdit, QScrollArea, QWidget

from installer.models import IntegrationStatus, Presence
from installer.ui.theme import GlassFrame, button, column, label, row, backdrop

NAMES = {"install": "安装 Skill + MCP", "uninstall": "卸载 Skill + MCP", "repair": "智能修复", "verify": "验证 MCP 连接", "backup": "备份配置", "adopt": "接管外部接入", "upgrade": "统一升级受管接入"}
PROBLEMS = {"INCOMPLETE_INTEGRATION": "接入文件不完整，请使用智能修复", "MANAGED_FILE_MODIFIED": "受管文件已被修改，请先核对修改内容", "MCP_ENTRY_MODIFIED": "MCP 配置与安装记录不一致", "RUNTIME_MISSING": "MCP 运行时缺失", "RUNTIME_HASH_MISMATCH": "运行时校验不一致，请修复后再验证", "LEGACY_RECEIPT": "旧版安装记录，升级后可补全版本信息", "VERSION_DIFFERS": "接入版本与管理器不同，请在检查更新中升级", "CONFIG_OR_RECEIPT_INVALID": "配置或安装记录无法解析", "SCAN_FAILED": "无法读取状态，请查看诊断"}


class Detail(QDialog):
    def __init__(self, owner, title, size=(780, 590), subtitle=None, *, show=True):
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
        if subtitle:
            self.body.addWidget(label(subtitle, role="secondary", wrap=True))
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
        return column(content, 0, 10)


def text_box(parent, value):
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    parent.addWidget(box, 1)
    return box


def disclosure(layout, title, text):
    control = button(title, compact=True)
    control.setCheckable(True)
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setPlainText(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=2))
    box.setMinimumHeight(140)
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


class Preview(Detail):
    def __init__(self, owner, title, plan, apply=None, *, warning=None):
        super().__init__(owner, title, subtitle=warning or ("核对目标与文件后确认。文件在预览后发生变化时会停止操作。" if apply else "以下为本次读取的结果。"))
        content = self.scroll()
        if isinstance(plan, dict) and "targets" in plan and isinstance(plan["targets"], list):
            for target in plan["targets"]:
                card = GlassFrame()
                stack = column(card, 16, 9)
                stack.addWidget(label(target["name"], size=15, bold=True))
                fact(stack, "Skill 目录", target["skill"])
                fact(stack, "MCP 配置", target["config"])
                disclosure(stack, f"查看逐文件预览 · {len(target['fingerprints'])} 个文件", "\n\n".join(f"{path}\n当前 SHA-256：{digest or '不存在，将按计划创建'}" for path, digest in target["fingerprints"].items()))
                content.addWidget(card)
            fact(content, "共享 MCP 运行时", plan["runtime"])
            fact(content, "操作与版本", f"{NAMES.get(plan['operation'], plan['operation'])} · {plan['version']}")
            fact(content, "共享服务处理", plan["runtime_retention"])
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
            controls.layout().addWidget(button("确认执行", lambda: (self.close(), apply()), primary=True))
        self.body.addWidget(controls)


class StatusDialog(Detail):
    def __init__(self, owner):
        super().__init__(owner, "安装状态", (850, 680), "分别查看客户端、Skill 文件与 MCP 配置；连接结果来自最近一次真实验证。")
        states = owner.selection.states
        self.body.addWidget(label(f"{sum(s.presence == Presence.DETECTED for s in states.values())} 个客户端已检测   ·   {sum(s.receipt_valid for s in states.values())} 个受管接入", size=14, bold=True))
        content = self.scroll()
        for key, state in states.items():
            card = GlassFrame()
            stack = column(card, 16, 8)
            stack.addWidget(row(label(state.display_name, size=15, bold=True), None, label(state.label, role="success" if state.status == IntegrationStatus.INSTALLED_MANAGED else "secondary")))
            stack.addWidget(label(f"客户端  {'已检测' if state.presence == Presence.DETECTED else '未检测或不可用'}     Skill  {'已存在' if state.skill_present else '未安装'}     MCP 配置  {'已存在' if state.mcp_entry_present else '未配置'}", wrap=True))
            checked = owner.service.health_results.get(key)
            stack.addWidget(label(f"接入版本 {state.detected_version or '尚无版本记录'}  ·  " + (f"最近验证 {'通过' if checked['ok'] else '未通过'}，{checked['checked_at']}" if checked else "尚未执行连接验证"), size=11, role="secondary", wrap=True))
            if state.problem_codes:
                stack.addWidget(label("\n".join(PROBLEMS.get(code, "需检查：" + code) for code in state.problem_codes), size=12, role="warning", wrap=True))
            disclosure(stack, "路径与技术详情", state.to_dict())
            content.addWidget(card)
        content.addStretch()
        self.body.addWidget(row(None, button("关闭", self.close)))


class Results(Detail):
    def __init__(self, owner, title, results):
        super().__init__(owner, title, (760, 530), "结果来自本次实际操作；下方可展开技术详情。")
        content = self.scroll()
        for result in results:
            card = GlassFrame()
            body = column(card, 16, 8)
            key = result.get("id")
            name = owner.selection.states[key].display_name if key in owner.selection.states else "操作结果"
            body.addWidget(row(label(name, size=15, bold=True), None, label("已完成" if result.get("ok") else "未完成", role="success" if result.get("ok") else "error")))
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
        subtitle = "选择接入后执行真实握手，核对服务版本、全部工具和 Session READY。" if operation == "verify" else "选择目标 → 核对逐文件预览 → 确认执行与核验。"
        eligible = {key: state for key, state in owner.selection.states.items() if (state.status == IntegrationStatus.EXTERNAL if operation == "adopt" else state.receipt_valid if operation in {"uninstall", "repair", "verify", "upgrade"} else state.status != IntegrationStatus.UNKNOWN)}
        super().__init__(owner, NAMES[operation], (720, min(620, 230 + len(eligible) * 90)), subtitle)
        body = self.scroll()
        for key, state in eligible.items():
            card = GlassFrame()
            stack = column(card, 12, 2)
            checkbox = QCheckBox(state.display_name)
            stack.addWidget(checkbox)
            stack.addWidget(label(f"{state.label} · 接入版本 {state.detected_version or '未记录'}", size=11, role="secondary"))
            if state.problem_codes:
                stack.addWidget(label("；".join(PROBLEMS.get(code, code) for code in state.problem_codes), size=11, role="warning", wrap=True))
            body.addWidget(card)
            self.variables[key] = checkbox
            checkbox.toggled.connect(lambda _: self.next_button.setEnabled(any(item.isChecked() for item in self.variables.values())))
        if not eligible:
            body.addWidget(label("没有符合条件的目标。请先检查安装状态。", role="secondary", wrap=True))
        body.addStretch()
        self.next_button = button("开始验证" if operation == "verify" else "创建备份" if operation == "backup" else "查看操作预览", self.preview, primary=True)
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
        super().__init__(owner, "日志与诊断", (850, 650), "先查看日志或预览脱敏报告，再选择复制、导出。")
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
            self.summary.setText("报告包含版本、脱敏检测结论、连接结果与最近事件；不含完整配置、凭证和私人文件。")
            self.box.setPlainText(json.dumps(self.value, ensure_ascii=False, indent=2))
        else:
            self.value = service.log.history(self.query.text())
            self.summary.setText(f"{len(self.value)} 条匹配事件 · 内容已脱敏")
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
                self.summary.setText("导出完成：已保存预览中的脱敏内容。")
            except Exception as exc:
                self.owner.error("导出失败", str(exc))
