"""Human-only Qt approval sheet; opening, refreshing and expiry never grant access."""
import json
import time
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QMessageBox
from installer.ui.dialogs import Detail, text_box
from installer.ui.theme import button, label, row


class OperatorCenter(Detail):
    def __init__(self, owner, store, request_id=None):
        super().__init__(owner, "本机操作审批", (850, 680), "请核对具体参数后批准一次；仅限原会话、原参数与有效期。", show=False)
        self.store, self.request_id, self.rows = store, request_id, {}
        self.selector = QComboBox()
        self.body.addWidget(self.selector)
        self.details = text_box(self.body, "正在读取待审请求…")
        self.expiry = label("", role="warning")
        self.body.addWidget(self.expiry)
        self.reject_button = button("拒绝本次操作", lambda: self.decide(False))
        self.approve_button = button("批准一次", lambda: self.decide(True), primary=True)
        self.body.addWidget(row(self.reject_button, None, self.approve_button))
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
        self.render()

    def select(self, index):
        self.request_id = self.selector.itemData(index)
        self.render()

    def render(self):
        record = self.rows.get(self.request_id)
        active = bool(record and not record["approved"] and record["expires_at"] > time.time())
        self.approve_button.setEnabled(active)
        self.reject_button.setEnabled(active)
        text = "指定请求已过期、已使用或不存在；请重新申请。其他请求须手动选择并核对。" if self.request_id else "暂无待审请求。受保护操作的请求会自动打开此窗口。"
        if record:
            try:
                arguments = self.store.operator_arguments(self.request_id)
                text = json.dumps({"请求": self.request_id, "工具": record["tool"], "风险等级": record["risk"], "原因（来自请求方，请自行核对）": record["description"], "会话": record["session_id"], "精确操作 SHA-256": record["action_hash"], "截止时间": datetime.fromtimestamp(record["expires_at"]).astimezone().isoformat(timespec="seconds"), "具体参数（本机查看）": arguments if arguments is not None else record["scope"], "说明": "批准只授权以上原始参数。" if arguments is not None else "旧请求缺少完整参数，请拒绝并重新申请。"}, ensure_ascii=False, indent=2)
                if arguments is None:
                    self.approve_button.setEnabled(False)
            except Exception as exc:
                text = f"请求校验失败：{type(exc).__name__}，未授予权限。"
                self.approve_button.setEnabled(False)
        self.details.setPlainText(text)
        self.expiry.setText(f"剩余 {max(0, int(record['expires_at'] - time.time()))} 秒" if record else "")

    def decide(self, approve):
        try:
            record = self.store.read(self.request_id)
            if record["approved"] or record["expires_at"] <= time.time():
                self.refresh()
                return
            if approve and self.store.operator_arguments(self.request_id) is None:
                return
            answer = QMessageBox.question(self, "确认本次决定", f"{'批准一次' if approve else '拒绝'} {record['tool']} 请求 {self.request_id[:10]}？\n请确认你已核对具体参数。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            if approve:
                self.store.approve(self.request_id)
            else:
                self.store.reject(self.request_id)
            self.close()
        except Exception as exc:
            self.owner.error("请求已变化", f"未授予新权限：{exc}")
            self.refresh()
