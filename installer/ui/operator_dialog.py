"""Human-only Qt approval sheet; opening, refreshing and expiry never grant access."""
import json
import time
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from installer.ui.controls import ChoiceBox as QComboBox
from installer.ui.controls import GlassCheckBox as QCheckBox
from installer.ui.dialogs import Detail, text_box
from installer.ui.theme import button, label, row

from windows_mcp.infrastructure.trust import TrustStore
from windows_mcp.infrastructure.authorization import scope_applications


class OperatorCenter(Detail):
    def __init__(self, owner, store, request_id=None):
        super().__init__(owner, "本机操作审批", (850, 680), show=False)
        self.store, self.request_id, self.rows = store, request_id, {}
        self.trust = TrustStore(store.directory)
        self.defaults_enabled = request_id is None
        self._trust_active = self.trust.allows()
        self.body.addWidget(label("默认审批方式", size=14, bold=True))
        self.auto_approve = QCheckBox("全自动（仅此客户端会话和应用，最长 8 小时）")
        self.auto_approve.setChecked(self._trust_active)
        self.body.addWidget(self.auto_approve)
        self.trust_hint = label("", role="secondary", wrap=True)
        self.body.addWidget(self.trust_hint)
        self.default_notice = label("", role="warning", wrap=True)
        self.default_notice.hide()
        self.body.addWidget(self.default_notice)
        self.save_default_button = button("保存默认审批", self.save_defaults, primary=True)
        self.defaults_row = row(self.save_default_button, None, button("关闭", self.close))
        self.body.addWidget(self.defaults_row)
        self.defaults_row.setVisible(self.defaults_enabled)
        self.selector = QComboBox()
        self.body.addWidget(self.selector)
        self.details = text_box(self.body, "正在读取待审请求…")
        self.expiry = label("", role="warning")
        self.body.addWidget(self.expiry)
        self.reject_button = button("拒绝本次操作", lambda: self.decide(False))
        self.approve_button = button("批准一次", lambda: self.decide(True), primary=True)
        trust_button = button("操作授权…", lambda: TrustCenter(self.owner), icon="shield")
        self.body.addWidget(row(self.reject_button, trust_button, None, self.approve_button))
        self.auto_approve.toggled.connect(self.update_approval_mode)
        self.selector.currentIndexChanged.connect(self.select)
        self.refresh()
        from windows_mcp.infrastructure.operator_ui import mark_operator_window
        mark_operator_window(self)
        self.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.finished.connect(self.timer.stop)
        self.finished.connect(lambda: setattr(owner, "operator_window", None))

    def refresh(self):
        self.rows = {record["request_id"]: record for record in self.store.pending()}
        self.selector.blockSignals(True)
        self.selector.clear()
        for key, record in self.rows.items():
            self.selector.addItem(f"{record['tool']} · {'已批准待使用' if record['approved'] else '等待确认'} · {key[:10]}", key)
        if self.request_id is None:
            self.request_id = next(iter(self.rows), None)
        index = self.selector.findData(self.request_id)
        if index < 0:
            self.selector.addItem("指定请求已失效" if self.request_id else "暂无待审请求", self.request_id)
            index = self.selector.count() - 1
        self.selector.setCurrentIndex(index)
        self.selector.blockSignals(False)
        try:
            trust = self.trust.status()
        except (OSError, ValueError):
            trust = None
        record = self.rows.get(self.request_id)
        active = self.request_trusted(record) if record else trust is not None
        if active != self._trust_active:
            self._trust_active = active
            self.auto_approve.setChecked(active)
        if active:
            self.auto_approve.setChecked(True)
        self.render()
        if trust is None:
            self.trust_hint.setText("当前为逐次审批。先发起操作请求，再选择客户端会话和应用开启全自动；出网站点单独授权。")
        else:
            until = "此客户端会话结束或撤销前" if trust.get("expires_at") is None else "至 " + datetime.fromtimestamp(trust["expires_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
            self.trust_hint.setText(f"已有范围授权：{trust['session_id']} · {', '.join(trust['applications'])}（{until}）。其他会话、应用和出网请求仍需各自授权。")

    def request_trusted(self, record):
        if not record:
            return False
        from windows_mcp.infrastructure.authorization import action_name

        arguments = self.store.operator_arguments(record["request_id"]) or {}
        applications = scope_applications(record.get("authorization_scope") or {})
        return bool(applications) and all(self.trust.allows(record["session_id"], app, record["tool"],
                                                           action_name(record["tool"], arguments)) for app in applications)

    def enable_defaults(self):
        self.defaults_enabled = True
        self.defaults_row.show()
        self.refresh()

    def save_defaults(self):
        if not self.defaults_enabled:
            return
        record = self.rows.get(self.request_id)
        if self.request_trusted(record):
            self.refresh()
            return
        automatic = self.auto_approve.isChecked()
        if automatic and (not record or record["risk"] != 2 or not record.get("authorization_scope")):
            self.default_notice.setText("先发起并选择一个 R2 待审请求，以绑定具体客户端会话和应用。")
            self.default_notice.show()
            return
        message = (f"为会话 {record['session_id']} 的应用 {', '.join(scope_applications(record['authorization_scope']))} 保存全自动？\n"
                   "最长 8 小时；其他会话、应用、R3 动作和出网站点不包含在内。") if automatic else "保留逐次审批？后续受保护操作将逐次询问。"
        answer = QMessageBox.question(self, "保存默认审批方式", message,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if automatic:
                self.trust.grant_for_request(self.store.read(record["request_id"]), operator="approval-settings")
            # Closing the human review sheet releases the desktop-input pause.
            self.close()
        except (OSError, ValueError) as exc:
            self.default_notice.setText(f"默认审批未保存：{exc}")
            self.default_notice.show()

    def select(self, index):
        self.request_id = self.selector.itemData(index)
        self.render()

    def render(self):
        record = self.rows.get(self.request_id)
        active = bool(record and not record["approved"] and record["expires_at"] > time.time())
        self.approve_button.setEnabled(active)
        self.reject_button.setEnabled(active)
        text = "请求已失效" if self.request_id else "暂无待审请求。\n先发起操作请求，再为具体会话和应用授权；旧版全局授权需要重新确认范围。"
        if record:
            try:
                arguments = self.store.operator_arguments(self.request_id)
                text = json.dumps({"请求": self.request_id, "工具": record["tool"], "风险等级": record["risk"], "原因": record["description"], "会话": record["session_id"], "应用范围": record.get("authorization_scope"), "出网站点（批准后授权此会话最长8小时）": record.get("network_origins", []), "精确操作 SHA-256": record["action_hash"], "截止时间": datetime.fromtimestamp(record["expires_at"]).astimezone().isoformat(timespec="seconds"), "具体参数": arguments if arguments is not None else record["scope"], "参数状态": "完整" if arguments is not None else "缺失"}, ensure_ascii=False, indent=2)
                if arguments is None:
                    self.approve_button.setEnabled(False)
            except Exception as exc:
                text = f"请求校验失败：{type(exc).__name__}，未授予权限。"
                self.approve_button.setEnabled(False)
        self.details.setPlainText(text)
        self.expiry.setText(f"剩余 {max(0, int(record['expires_at'] - time.time()))} 秒" if record else "")
        self.auto_approve.setEnabled(not self._trust_active and bool(record and self.approve_button.isEnabled() and record["risk"] == 2 and record.get("authorization_scope")))
        self.save_default_button.setEnabled(not self._trust_active)
        self.update_approval_mode()

    def update_approval_mode(self, *_):
        record = self.rows.get(self.request_id)
        automatic = bool(record and record["risk"] == 2 and self.auto_approve.isChecked())
        self.approve_button.setText("批准并开启全自动" if automatic else "批准一次")

    def decide(self, approve):
        try:
            record = self.store.read(self.request_id)
            if record["approved"] or record["expires_at"] <= time.time():
                self.refresh()
                return
            if approve and self.store.operator_arguments(self.request_id) is None:
                return
            automatic = approve and record["risk"] == 2 and self.auto_approve.isChecked()
            if automatic:
                title = "确认开启全自动"
                message = (f"批准 {record['tool']} 并为以下范围开启全自动？\n\n"
                           f"会话：{record['session_id']}\n应用：{', '.join(scope_applications(record.get('authorization_scope') or {}))}\n"
                           "最长 8 小时；其他应用、会话、R3 动作和出网站点不包含在内。")
            else:
                title = "确认本次决定"
                message = f"{'批准一次' if approve else '拒绝'} {record['tool']} 请求 {self.request_id[:10]}？"
                if approve and record.get("network_origins"):
                    message += "\n同时允许此客户端会话访问以下站点，最长 8 小时：\n" + "\n".join(record["network_origins"])
            answer = QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            if approve:
                self.store.approve(self.request_id)
                if automatic and not self.request_trusted(record):
                    self.trust.grant_for_request(record, operator="approval-ui")
            else:
                self.store.reject(self.request_id)
            self.close()
        except Exception as exc:
            self.owner.error("请求已变化", f"未授予新权限：{exc}")
            self.refresh()


class TrustCenter(Detail):
    """Operator-only switch that turns off per-call approval for R2 input tools."""

    def __init__(self, owner):
        super().__init__(owner, "操作授权", (780, 650), show=False)
        from windows_mcp.infrastructure.operator_ui import mark_operator_window

        mark_operator_window(self)
        self.store = TrustStore(owner.approval_store.directory) if getattr(owner, "approval_store", None) else None
        self.body.setContentsMargins(26, 22, 26, 22)
        self.body.setSpacing(14)
        self.body.addWidget(label("完全授权", size=15, bold=True))
        self.body.addWidget(label(
            "为选中请求的客户端会话和应用开启后，对应操作无需逐次审批。"
            "授权由你本机确认并以签名文件保存，可随时撤销；撤销后立即恢复逐次审批。"
            "PowerShell、注册表写入、结束进程等 R3 能力不在此授权范围内。",
            role="secondary", wrap=True))
        self.state = label("", size=13, bold=True, wrap=True)
        self.body.addWidget(self.state)
        self.request_selector = QComboBox()
        self.request_rows = {}
        self.body.addWidget(self.request_selector)
        self.mode = QComboBox()
        self.mode.addItem("仅本次会话 · 8 小时", "session")
        self.mode.addItem("本客户端会话 · 直到撤销或会话结束", "forever")
        self.mode.setMinimumWidth(260)
        self.body.addWidget(row(label("授权范围", size=12), None, self.mode))
        self.confirm = QCheckBox("我确认仅授权所选客户端会话和应用；出网站点单独授权")
        self.body.addWidget(self.confirm)
        self.notice = label("", role="warning", wrap=True)
        self.body.addWidget(self.notice)
        self.grant_button = button("开启完全授权", self.grant, primary=True)
        self.revoke_button = button("撤销授权", self.revoke, icon="shield")
        self.body.addWidget(row(self.revoke_button, None, self.grant_button))
        self.body.addWidget(label("提示：授权文件位于本机配置目录，任何 MCP 工具都无法创建或修改它。", role="secondary", wrap=True))
        self.body.addWidget(row(None, button("关闭", self.close)))
        self.refresh()
        self.show()

    def refresh(self):
        selected = self.request_selector.currentData()
        self.request_rows = {row["request_id"]: row for row in self.owner.approval_store.pending()
                             if row["risk"] == 2 and (row.get("authorization_scope") or {}).get("application")} if self.store else {}
        self.request_selector.clear()
        for key, row_ in self.request_rows.items():
            self.request_selector.addItem(f"{row_['session_id']} · {', '.join(scope_applications(row_['authorization_scope']))} · {row_['tool']}", key)
        if not self.request_rows:
            self.request_selector.addItem("先发起一个 R2 待审请求，以选择会话和应用", None)
        elif self.request_selector.findData(selected) >= 0:
            self.request_selector.setCurrentIndex(self.request_selector.findData(selected))
        grant = self.store.status() if self.store else None
        if grant is None:
            self.state.setText("当前状态：每次操作都需要批准一次")
            self.revoke_button.setEnabled(False)
        else:
            until = "此客户端会话结束或撤销前" if grant.get("expires_at") is None else "至 " + datetime.fromtimestamp(grant["expires_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
            self.state.setText(f"范围授权：{grant['session_id']} · {', '.join(grant['applications'])} · {until}")
            self.mode.setCurrentIndex(self.mode.findData(grant.get("mode", "session")))
            self.revoke_button.setEnabled(True)

    def grant(self, *_):
        if self.store is None:
            self.notice.setText("审批组件未连接，无法授权。")
            return
        if not self.confirm.isChecked():
            self.notice.setText("请先勾选确认，再开启完全授权。")
            return
        request = self.request_rows.get(self.request_selector.currentData())
        if not request:
            self.notice.setText("先发起并选择一个 R2 待审请求，以绑定客户端会话和应用。")
            return
        if self.store.allows():
            answer = QMessageBox.question(self, "更新授权", "完全授权已开启，是否用新的授权范围覆盖？",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        answer = QMessageBox.warning(self, "确认范围授权",
                                     f"会话：{request['session_id']}\n应用：{', '.join(scope_applications(request['authorization_scope']))}\n"
                                     "仅匹配范围内的 R2 操作不再逐次询问；其他客户端、应用、R3 和出网权限不包含在内。\n\n确定开启吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            current = self.owner.approval_store.read(request["request_id"])
            self.store.grant_for_request(current, self.mode.currentData(), operator="trust-ui")
            self.notice.setText("已开启完全授权，服务会在下一次操作时直接生效。")
            self.confirm.setChecked(False)
            self.refresh()
            if getattr(self.owner, "operator_window", None):
                self.owner.operator_window.refresh()
        except Exception as exc:
            self.notice.setText(f"授权失败：{exc}")

    def revoke(self):
        if self.store is None:
            return
        answer = QMessageBox.question(self, "撤销完全授权", "撤销后，每次鼠标/键盘操作都需要重新审批。确定撤销吗？",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.revoke()
            self.notice.setText("已撤销完全授权，已恢复逐次审批。")
            self.refresh()
            if getattr(self.owner, "operator_window", None):
                self.owner.operator_window.refresh()
        except Exception as exc:
            self.notice.setText(f"撤销失败：{exc}")
