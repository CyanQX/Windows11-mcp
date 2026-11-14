"""One local full-access switch, with exact one-use decisions when it is off."""
import json
import time
from datetime import datetime

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect
from installer.ui.controls import ChoiceBox as QComboBox
from installer.ui.controls import GlassCheckBox as QCheckBox
from installer.ui.dialogs import Detail, text_box
from installer.ui.theme import GlassFrame, button, label, row, column
from windows_mcp.infrastructure.trust import TrustStore


class OperatorCenter(Detail):
    def __init__(self, owner, store, request_id=None):
        super().__init__(owner, "本机操作审批", (760, 430), show=False)
        self.store, self.request_id, self.rows = store, request_id, {}
        self.trust = TrustStore(store.directory)
        self.body.setContentsMargins(24, 20, 24, 20)
        self.body.setSpacing(16)
        card = GlassFrame()
        content = column(card, 18, 8)
        self.auto_approve = QCheckBox("持续全自动 · 完全访问")
        self.auto_approve.setAccessibleDescription("点击立即保存。在当前用户非提升权限内访问文件、网络和已开放操作，直到手动关闭；不能提权。")
        content.addWidget(self.auto_approve)
        self.trust_hint = label("", role="secondary", wrap=True)
        content.addWidget(self.trust_hint)
        self.body.addWidget(card)
        self.default_notice = label("", role="warning", wrap=True)
        self.default_notice.hide()
        self.body.addWidget(self.default_notice)
        self.state = label("", size=14, bold=True, wrap=True)
        self.body.addWidget(self.state)
        self.selector = QComboBox()
        self.body.addWidget(self.selector)
        self.summary = label("", role="secondary", wrap=True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.summary)
        self.detail_button = button("查看请求详情", self.toggle_details, compact=True)
        self.body.addWidget(self.detail_button)
        self.details = text_box(self.body, "")
        self.details.setMinimumHeight(100)
        self.details.hide()
        self.expiry = label("", role="warning")
        self.body.addWidget(self.expiry)
        self.body.addStretch()
        self.reject_button = button("拒绝本次操作", lambda: self.decide(False))
        self.approve_button = button("批准一次", lambda: self.decide(True), primary=True)
        self.body.addWidget(row(self.reject_button, None, button("关闭", self.close), self.approve_button))
        self.effect = QGraphicsOpacityEffect(self.state)
        self.state.setGraphicsEffect(self.effect)
        self.animation = QPropertyAnimation(self.effect, b"opacity", self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # clicked expresses human activation; restoring or refreshing checked
        # state never creates a grant. There is no separate Save/Trust dialog.
        self.auto_approve.clicked.connect(self.save_defaults)
        self.selector.currentIndexChanged.connect(self.select)
        self.refresh()
        from windows_mcp.infrastructure.operator_ui import mark_operator_window
        mark_operator_window(self)
        self.update_review_pause()
        self.show()
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.finished.connect(self.timer.stop)
        self.finished.connect(lambda: setattr(owner, "operator_window", None))

    def refresh(self):
        if self.trust.automatic():
            self.store.dismiss_automatic()
        self.rows = {record["request_id"]: record for record in self.store.pending()}
        self.selector.blockSignals(True)
        self.selector.clear()
        for key, record in self.rows.items():
            self.selector.addItem(f"{record['tool']} · {'已批准待使用' if record['approved'] else '等待批准'}", key)
        if self.request_id is None:
            self.request_id = next(iter(self.rows), None)
        index = self.selector.findData(self.request_id)
        if index < 0 and self.request_id:
            self.selector.addItem("指定请求已失效", self.request_id)
            index = self.selector.count() - 1
        if index >= 0:
            self.selector.setCurrentIndex(index)
        self.selector.blockSignals(False)
        full = self.trust.full_access()
        self.auto_approve.setChecked(full)
        self.trust_hint.setText("允许 AI 在普通用户权限内持续访问文件、网络与已开放操作。点击即生效，重连、重启后仍有效；不能提权。")
        self.state.setText("完全访问已开启" if full else "等待你批准" if self.rows else "暂无待审请求")
        if not full and self.trust.automatic():
            self.state.setText("旧版自动审批已保留 · 开启上方开关可切换为完全访问")
        self.render()
        self.update_review_pause()

    def enable_defaults(self):
        self.refresh()

    def update_review_pause(self):
        from windows_mcp.infrastructure.operator_ui import set_operator_pause
        set_operator_pause(self, any(not item["approved"] for item in self.rows.values()) and not self.trust.automatic())

    def save_defaults(self, checked=None):
        checked = self.auto_approve.isChecked() if checked is None else checked
        try:
            if checked:
                self.trust.grant_automatic(operator="approval-switch", full_access=True)
                self.store.dismiss_automatic()
            else:
                self.trust.revoke()
            self.default_notice.hide()
            self.refresh()
            self.animation.stop()
            if not self.owner.theme.appearance["reduce_motion"]:
                self.animation.setStartValue(0.35)
                self.animation.setEndValue(1.0)
                self.animation.start()
            else:
                self.effect.setOpacity(1.0)
        except (OSError, ValueError) as exc:
            self.auto_approve.setChecked(self.trust.full_access())
            self.default_notice.setText(f"设置未保存：{exc}")
            self.default_notice.show()

    def select(self, index):
        self.request_id = self.selector.itemData(index)
        self.render()

    def toggle_details(self):
        self.details.setVisible(not self.details.isVisible())
        self.detail_button.setText("收起请求详情" if self.details.isVisible() else "查看请求详情")

    def render(self):
        record = self.rows.get(self.request_id)
        active = bool(record and not record["approved"] and record["expires_at"] > time.time())
        try:
            arguments = self.store.operator_arguments(self.request_id) if record else None
        except (OSError, ValueError, KeyError, PermissionError):
            arguments = None
        self.approve_button.setEnabled(active and arguments is not None)
        self.reject_button.setEnabled(active)
        self.approve_button.setVisible(record is not None)
        self.reject_button.setVisible(record is not None)
        self.auto_approve.setEnabled(True)
        self.selector.setVisible(len(self.rows) > 1)
        self.detail_button.setVisible(record is not None)
        if record:
            exact = " · ".join(f"{name}: {arguments[name]}" for name in ("operation", "action", "mode", "path", "destination", "url", "command", "text", "loc") if arguments is not None and name in arguments)
            self.summary.setText(f"{record['tool']} · {record['description']}\n{exact[:240]}")
            self.details.setPlainText(json.dumps({"工具": record["tool"], "原因": record["description"], "风险等级": record["risk"], "会话": record["session_id"], "应用范围": record.get("authorization_scope"), "出网站点": record.get("network_origins", []), "截止时间": datetime.fromtimestamp(record["expires_at"]).astimezone().isoformat(timespec="seconds"), "具体参数": arguments, "精确操作 SHA-256": record["action_hash"]}, ensure_ascii=False, indent=2))
            self.expiry.setText(f"剩余 {max(0, int(record['expires_at'] - time.time()))} 秒")
        else:
            self.summary.setText("AI 将自主执行任务。可随时关闭持续全自动。" if self.trust.full_access() else "需要权限时，请求会显示在这里。你可以批准一次，或开启持续全自动。")
            self.details.clear()
            self.details.hide()
            self.expiry.clear()

    def decide(self, approve):
        try:
            record = self.store.read(self.request_id)
            if record["approved"] or record["expires_at"] <= time.time():
                self.refresh()
                return
            if approve:
                if self.store.operator_arguments(self.request_id) is None:
                    return
                self.store.approve(self.request_id)
            else:
                self.store.reject(self.request_id)
            self.close()
        except Exception as exc:
            self.owner.error("请求已变化", f"未授予新权限：{exc}")
            self.refresh()
