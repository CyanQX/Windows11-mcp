"""Settings are details, not a dense permanent grid on the main window."""

import json
import sys
import webbrowser
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from installer.settings import ENV_KEYS
from installer.ui.dialogs import Detail, Diagnostics, Preview, Targets, text_box
from installer.ui.theme import COLORS, FONT, button, label
from installer.updates import UpdateService, start_update


class SettingsHub(Detail):
    def __init__(self, owner):
        super().__init__(owner, "设置", "660x650")
        label(self, "设置", size=23, bold=True).pack(anchor="w", padx=26, pady=(22, 12))
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        for name, icon, action in [
            ("检查更新", "refresh", lambda: check_updates(owner)),
            ("版本与升级", "update", lambda: versions(owner)),
            ("日志与诊断", "document", lambda: Diagnostics(owner)),
            ("错误报告导出", "shield", lambda: Diagnostics(owner, True)),
            ("配置与恢复", "restore", lambda: backups(owner)),
            ("接管外部接入", "link", lambda: Targets(owner, "adopt")),
            ("安装目录", "folder", lambda: preferences(owner, paths=True)),
            ("高级设置", "settings", lambda: preferences(owner)),
            ("许可证与关于", "info", lambda: about(owner)),
        ]:
            button(body, name, lambda a=action: (self.destroy(), a()), icon=owner.icons.get(icon), anchor="w").pack(fill="x", padx=10, pady=5)


def paths(owner):
    value = {key: {**details, "状态": owner.selection.states[key].label, "客户端": owner.selection.states[key].client_path, "收据": str(owner.service.backend.receipt_path(owner.service.backend.clients[key]))} for key, details in owner.settings.path_details().items()}
    Preview(owner, "实际安装路径", {"共享运行时": str(owner.service.backend.executable), "客户端": value, "说明": "Codex 仅检测桌面端；其用户 MCP 配置与同一用户的其他 Codex 客户端共享。"})


def preferences(owner, paths=False):
    window = Detail(owner, "安装目录" if paths else "高级设置", "820x690")
    label(window, "安装目录" if paths else "高级设置", size=22, bold=True).pack(anchor="w", padx=22, pady=(18, 6))
    label(window, "仅修改管理器与客户端目录设置；现有受管接入需先卸载，才能切换数据目录。" if paths else "目录环境变量和客户端程序路径仅用于当前用户；不更改 MCP 权限策略。", size=12, color="secondary", wraplength=760).pack(anchor="w", padx=22)
    body = ctk.CTkScrollableFrame(window, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=12)
    fields = {}
    value = owner.settings.value

    def field(key, title, default):
        label(body, title, size=12).pack(anchor="w", padx=5, pady=(8, 3))
        entry = ctk.CTkEntry(body, height=36, font=(FONT, 12))
        entry.pack(fill="x", padx=5)
        entry.insert(0, str(default))
        fields[key] = entry

    if paths:
        field("data_dir", "MCP 共享数据目录（备份、收据、日志、运行时）", value["data_dir"])
        for key in sorted(ENV_KEYS):
            field(key, f"{key}（留空沿用系统环境 / 官方默认）", value["environment_overrides"].get(key, ""))
        button(body, "查看当前实际路径", lambda: globals()["paths"](owner), icon=owner.icons.get("folder")).pack(anchor="w", padx=5, pady=14)
    else:
        label(body, "主题", size=13).pack(anchor="w", padx=5, pady=6)
        theme = ctk.CTkOptionMenu(body, values=["light", "dark", "system"])
        theme.set(value["theme"])
        theme.pack(anchor="w", padx=5)
        fields["theme"] = theme
        for key, title in (("health_timeout", "连接检查超时（5～180 秒）"), ("log_retention_days", "日志保留天数（1～365）"), ("log_max_mb", "日志总容量上限 MB（5～500）")):
            field(key, title, value[key])
        for key in owner.service.backend.clients:
            field(f"exe:{key}", f"{owner.selection.states[key].display_name} 程序路径（可选；Codex 必须为桌面 GUI EXE）", value["client_executables"].get(key, ""))
    controls = ctk.CTkFrame(window, fg_color="transparent")
    controls.pack(fill="x", padx=22, pady=18)

    def save(reset=False):
        try:
            if owner.busy:
                raise ValueError("维护任务进行中，请完成后再修改设置。")
            updated = json.loads(json.dumps(owner.settings.defaults if reset else value))
            if not reset:
                for key, entry in fields.items():
                    raw = entry.get().strip()
                    if key in ENV_KEYS:
                        updated["environment_overrides"][key] = raw
                    elif key.startswith("exe:"):
                        updated["client_executables"][key[4:]] = raw
                    else:
                        updated[key] = int(raw) if key in {"health_timeout", "log_retention_days", "log_max_mb"} else raw
            owner.settings.save(updated)
            owner.reload_service()
            window.destroy()
            owner.scan()
        except Exception as exc:
            messagebox.showerror("设置未保存", str(exc), parent=window)

    button(controls, "恢复默认", lambda: save(True), width=120).pack(side="left")
    button(controls, "保存并重新检测", save, primary=True, width=180).pack(side="right")


def backups(owner):
    window = Detail(owner, "备份管理与崩溃恢复", "820x650")
    label(window, "备份管理与崩溃恢复", size=22, bold=True).pack(anchor="w", padx=22, pady=(20, 6))
    label(window, "配置恢复只替换 Windows-MCP 项；保留后来添加的其他 MCP 和个人设置。", size=12, color="secondary").pack(anchor="w", padx=22)
    body = ctk.CTkScrollableFrame(window, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=16, pady=12)
    rows = owner.service.backups()
    for row in rows:
        frame = ctk.CTkFrame(body, fg_color=COLORS["panel"])
        frame.pack(fill="x", pady=5)
        label(frame, f"{row['profile_id']} · {row['operation']} · {row['state']}\n{row['id']}", size=12, justify="left").pack(side="left", padx=12, pady=12)
        choice = ctk.CTkOptionMenu(frame, values=row.get("profiles") or [row["profile_id"]], width=110)
        if row.get("profiles"):
            choice.pack(side="right", padx=6)

        def preview(item=row, selector=choice):
            try:
                if item["state"] in {"prepared", "writing", "recovery_required"}:
                    plan = {**item, "说明": "对本次交易文件执行基于散列的回滚；用户后续修改会保留并报告冲突。"}
                    Preview(owner, "崩溃恢复预览", plan, lambda: owner.run(lambda: owner.service.recover(item["id"])))
                else:
                    key = selector.get()
                    plan = owner.service.preview_restore(item["id"], key)
                    Preview(owner, "配置恢复预览", plan, lambda: owner.run(lambda: owner.service.restore(item["id"], plan["digest"], key)))
            except Exception as exc:
                messagebox.showerror("无法预览", str(exc), parent=window)
        button(frame, "恢复预览", preview, width=120).pack(side="right", padx=12)
    if not rows:
        label(body, "尚无备份记录。安装前会自动创建快照。", color="secondary").pack(pady=40)
    button(window, "手动创建备份", lambda: Targets(owner, "backup"), icon=owner.icons.get("backup")).pack(anchor="w", padx=22, pady=(0, 18))


def versions(owner):
    states = owner.selection.states
    value = {"管理器版本": owner.version, "程序": str(Path(sys.executable)), "受管接入": {key: {"version": state.detected_version or "未知 / 旧版收据", "state": state.label, "problems": state.problem_codes} for key, state in states.items() if state.receipt_valid}, "升级规则": "共享运行时统一升级；先备份、预览、确认、核验，失败一起回滚。旧版收据仅在明确升级或修复后补充版本信息。"}
    window = Preview(owner, "版本与升级", value)
    controls = ctk.CTkFrame(window, fg_color="transparent")
    controls.pack(fill="x", padx=20, pady=(0, 18))
    button(controls, "检查官方更新", lambda: check_updates(owner), width=160).pack(side="left")
    button(controls, "升级现有接入至此版本", lambda: Targets(owner, "upgrade"), primary=True, width=215).pack(side="right")


def check_updates(owner):
    updater = UpdateService(owner.service.backend.data, owner.version)

    def checked(release):
        if not release["available"]:
            messagebox.showinfo("检查更新", f"当前 {owner.version}；官方最新稳定版 {release['latest']}。暂无更新。", parent=owner)
            return

        def download():
            owner.run(lambda: updater.download(release), downloaded)

        def downloaded(path):
            if not getattr(sys, "frozen", False):
                messagebox.showinfo("下载完成", f"已校验：{path}\n源码运行模式不替换 Python，请使用 EXE 更新。", parent=owner)
                return
            try:
                managed = owner.service.upgrade_targets()
                plan = {"版本": release["latest"], "当前程序": str(Path(sys.executable)), "校验": release["sha256"], "接入升级": managed, "流程": "关闭此程序 → 独立助手替换 → 重启 → 升级全部受管接入并验证；失败保留回滚副本。"}

                def apply():
                    path_plan, nonce = updater.prepare(path, release, Path(sys.executable))
                    start_update(path_plan, nonce)
                    owner.destroy()
                Preview(owner, "确认升级与重启", plan, apply)
            except Exception as exc:
                messagebox.showerror("更新未执行", str(exc), parent=owner)
        Preview(owner, "发现新版本 · 下载预览", release, download, warning="下载并验证官方资产后，还需确认升级与重启。")

    owner.run(updater.check, checked)


def about(owner):
    window = Detail(owner, "许可证与关于", "850x670")
    label(window, f"Windows MCP 安装与维护管理器  {owner.version}", size=22, bold=True).pack(anchor="w", padx=22, pady=(20, 8))
    label(window, "本地运行 · 当前用户安装 · 31 个 MCP 工具 · 无需另装 Python", size=13, color="secondary").pack(anchor="w", padx=22)
    text = (owner.base / "LICENSE.md").read_text(encoding="utf-8")
    notices = owner.base / "installer" / "resources" / "third-party-licenses.txt"
    if notices.is_file():
        text += "\n\n第三方许可\n" + notices.read_text(encoding="utf-8")
    text_box(window, text)
    button(window, "打开项目主页", lambda: webbrowser.open("https://github.com/CyanQX/Windows11-mcp"), width=180).pack(anchor="w", padx=22, pady=(0, 18))
