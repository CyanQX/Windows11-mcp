"""One executable: setup window by default, packaged MCP server with --serve."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import io
import json
import os
import queue
import sys
import threading
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer.backend import Configuration, Installer, mcp_entry


def resources() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def restore_stdio() -> None:
    """The windowed Windows bootloader sets Python streams to None; MCP inherits pipes."""
    if os.name != "nt":
        return
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    process = kernel.GetCurrentProcess()
    for name, number, mode, flag in [("stdin", -10, "rb", os.O_RDONLY), ("stdout", -11, "wb", os.O_WRONLY), ("stderr", -12, "wb", os.O_WRONLY)]:
        if getattr(sys, name) is not None:
            continue
        handle = kernel.GetStdHandle(number)
        if not handle or handle == ctypes.c_void_p(-1).value:
            kernel.AttachConsole(wintypes.DWORD(-1))
            handle = kernel.GetStdHandle(number)
        if not handle or handle == ctypes.c_void_p(-1).value:
            stream = open(os.devnull, "r" if mode == "rb" else "w", encoding="utf-8")
        else:
            duplicate = wintypes.HANDLE()
            if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            fd = msvcrt.open_osfhandle(duplicate.value, flag | os.O_BINARY)
            stream = io.TextIOWrapper(os.fdopen(fd, mode, buffering=0), encoding="utf-8", newline="\n", write_through=True)
        setattr(sys, name, stream)


async def check_service(command: Path) -> dict:
    if not command.is_file():
        raise RuntimeError("请先安装 MCP 服务，然后检查连接。")
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    transport = StdioTransport(command=str(command), args=["--serve"], env={"ANONYMIZED_TELEMETRY": "false", "WINDOWS_MCP_WATCHDOG": "off", "WINDOWS_MCP_DISABLE_FLASH": "1"})
    async with Client(transport, timeout=90) as client:
        tools = await client.list_tools()
        expected = {tool["name"] for tool in json.loads((resources() / "manifest.json").read_text(encoding="utf-8"))["tools"]}
        if {tool.name for tool in tools} != expected:
            raise RuntimeError("服务工具清单与版本不一致。")
        status = await client.call_tool("Session", {"operation": "status"})
        if status.is_error:
            raise RuntimeError("服务会话未就绪。")
        return {"tool_count": len(tools), "session_status": "READY"}


def isolated_test(root: Path) -> None:
    if root.exists() and any(root.iterdir()):
        raise ValueError("自测只能使用空的临时目录。")
    root.mkdir(parents=True, exist_ok=True)
    backend = Installer(root / "home", root / "app", resources() / "skills" / "windows-mcp", Path(sys.executable), {})
    for profile in backend.clients.values():
        backend.install(profile)
        assert (profile.skill_root / "windows-mcp" / "SKILL.md").is_file()
        assert Configuration(profile, profile.config.read_bytes()).get() == mcp_entry(profile, backend.executable)
    installed = list(backend.clients)
    for profile in backend.clients.values():
        result = backend.uninstall(profile)
        assert not result["retained"]
        assert Configuration(profile, profile.config.read_bytes()).get() is None
    (root / "result.json").write_text(json.dumps({"profiles": installed, "install_and_uninstall": "PASS", "isolated": True}, indent=2), encoding="utf-8")


def gui(screenshot: Path | None = None) -> None:
    # Libraries used by the GUI/client may log, but a windowed process has no console.
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))
    import customtkinter as ctk
    from tkinter import messagebox

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    base = resources()
    version = json.loads((base / "manifest.json").read_text(encoding="utf-8"))["version"]
    data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "WindowsMCP"
    backend = Installer(Path.home(), data, base / "skills" / "windows-mcp", Path(sys.executable))

    class Window(ctk.CTk):
        def __init__(self):
            super().__init__()
            self.title(f"Windows-MCP 安装助手 · {version}")
            self.geometry("1080x790")
            self.minsize(940, 740)
            self.configure(fg_color="#0c121a")
            self.events = queue.Queue()
            self.busy = False
            self.variables = {}
            self.cards = {}
            self.skill = ctk.BooleanVar(value=True)
            self.mcp = ctk.BooleanVar(value=True)
            header = ctk.CTkFrame(self, fg_color="transparent")
            header.pack(fill="x", padx=34, pady=(25, 16))
            ctk.CTkLabel(header, text="W", width=42, height=42, corner_radius=12, fg_color="#1cbaa6", text_color="#071c19", font=("Segoe UI", 24, "bold")).pack(side="left")
            ctk.CTkLabel(header, text="Windows MCP", font=("Segoe UI", 22, "bold")).pack(side="left", padx=13)
            ctk.CTkLabel(header, text=f"v{version}  /  安装助手", text_color="#8293a8").pack(side="left", padx=8)
            ctk.CTkLabel(header, text="31 个工具 · 当前用户安装", text_color="#8fbfb6").pack(side="right")
            ctk.CTkLabel(self, text="把 Windows 操作能力接入你的 AI", font=("Microsoft YaHei UI", 27, "bold"), anchor="w").pack(fill="x", padx=34)
            ctk.CTkLabel(self, text="选择编程工具，一次安装使用 Skill 与本地 MCP 服务。", text_color="#91a1b5", anchor="w", font=("Microsoft YaHei UI", 13)).pack(fill="x", padx=35, pady=(8, 20))
            content = ctk.CTkFrame(self, fg_color="transparent")
            content.pack(fill="both", expand=True, padx=34)
            content.grid_columnconfigure(0, weight=6)
            content.grid_columnconfigure(1, weight=5)
            content.grid_rowconfigure(0, weight=1)
            clients = ctk.CTkFrame(content, fg_color="#141e2a", corner_radius=14)
            clients.grid(row=0, column=0, sticky="nsew", padx=(0, 17))
            ctk.CTkLabel(clients, text="01  选择编程工具", font=("Microsoft YaHei UI", 16, "bold"), anchor="w").pack(fill="x", padx=20, pady=(18, 12))
            badges = {"claude": ("C", "#bd8b66"), "codex": ("O", "#65b9a3"), "deepseek": ("D", "#739dff"), "opencode": ("o", "#d0d7e2"), "gemini": ("G", "#b49cf2")}
            for profile in backend.clients.values():
                card = ctk.CTkFrame(clients, fg_color="#1b2837", corner_radius=10)
                card.pack(fill="x", padx=16, pady=5)
                letter, color = badges[profile.id]
                ctk.CTkLabel(card, text=letter, text_color=color, font=("Segoe UI", 21, "bold"), width=44).pack(side="left", padx=(10, 0), pady=10)
                self.variables[profile.id] = ctk.BooleanVar(value=False)
                checkbox = ctk.CTkCheckBox(card, text=profile.name, variable=self.variables[profile.id], command=self.refresh, font=("Segoe UI", 16, "bold"), fg_color="#16a993", hover_color="#198b7d", border_color="#4b6078", checkbox_width=21, checkbox_height=21)
                checkbox.pack(side="left", fill="x", expand=True, padx=8, pady=14)
                self.cards[profile.id] = checkbox
            ctk.CTkLabel(clients, text="可多选 · 按各工具官方目录安装\n支持 CODEX_HOME / DSH_HOME 等自定义环境目录", text_color="#8192a7", justify="left", font=("Microsoft YaHei UI", 11)).pack(anchor="w", padx=22, pady=(16, 18))
            options = ctk.CTkFrame(content, fg_color="#141e2a", corner_radius=14)
            options.grid(row=0, column=1, sticky="nsew")
            ctk.CTkLabel(options, text="02  安装内容与目录", font=("Microsoft YaHei UI", 16, "bold"), anchor="w").pack(fill="x", padx=20, pady=(18, 16))
            self.skill_option = ctk.CTkCheckBox(options, text="使用 Skill", variable=self.skill, command=self.refresh, fg_color="#16a993", font=("Microsoft YaHei UI", 14))
            self.skill_option.pack(anchor="w", padx=22)
            ctk.CTkLabel(options, text="让 AI 知道怎样观察、操作与核对 Windows", text_color="#8798ad", font=("Microsoft YaHei UI", 11)).pack(anchor="w", padx=23, pady=(5, 16))
            self.mcp_option = ctk.CTkCheckBox(options, text="MCP 服务 · 内置运行环境", variable=self.mcp, command=self.refresh, fg_color="#16a993", font=("Microsoft YaHei UI", 14))
            self.mcp_option.pack(anchor="w", padx=22)
            ctk.CTkLabel(options, text="无需另外安装 Python · 提供 31 个真实工具", text_color="#8798ad", font=("Microsoft YaHei UI", 11)).pack(anchor="w", padx=23, pady=(5, 17))
            self.paths = ctk.CTkTextbox(options, height=180, fg_color="#0f1823", border_width=1, border_color="#263649", font=("Consolas", 11), wrap="word")
            self.paths.pack(fill="both", expand=True, padx=19, pady=(0, 12))
            ctk.CTkLabel(options, text="修改前备份配置 · 保留其他 MCP 与个人设置\n高风险操作继续使用服务内置的本地审批", text_color="#8192a7", justify="left", font=("Microsoft YaHei UI", 11)).pack(anchor="w", padx=21, pady=(0, 18))
            self.progress = ctk.CTkProgressBar(self, height=3, progress_color="#1cbaa6", fg_color="#263649", mode="indeterminate")
            self.progress.pack(fill="x", padx=36, pady=(20, 10))
            self.progress.set(0)
            self.status = ctk.CTkLabel(self, text="请选择至少一个工具", anchor="w", text_color="#91a1b5", font=("Microsoft YaHei UI", 12))
            self.status.pack(fill="x", padx=35)
            controls = ctk.CTkFrame(self, fg_color="transparent")
            controls.pack(fill="x", padx=34, pady=(14, 24))
            self.install_button = ctk.CTkButton(controls, text="安装 Skill + MCP", width=230, height=45, font=("Microsoft YaHei UI", 15, "bold"), fg_color="#18ae99", hover_color="#149884", text_color="#081a17", command=lambda: self.perform("install"))
            self.install_button.pack(side="right")
            self.remove_button = ctk.CTkButton(controls, text="卸载所选接入", width=145, height=43, fg_color="#223144", hover_color="#34475e", command=lambda: self.perform("uninstall"))
            self.remove_button.pack(side="right", padx=12)
            self.check_button = ctk.CTkButton(controls, text="检查服务连接", width=130, height=43, fg_color="transparent", border_width=1, border_color="#375369", command=lambda: self.perform("check"))
            self.check_button.pack(side="left")
            self.refresh()
            self.after(100, self.poll)
            self.protocol("WM_DELETE_WINDOW", self.close_window)

        def refresh(self):
            selected = [backend.clients[key] for key, variable in self.variables.items() if variable.get()]
            self.paths.configure(state="normal")
            self.paths.delete("1.0", "end")
            for profile in selected:
                try:
                    current = backend.inspect(profile)
                    state = "已安装" if current["skill_installed"] and current["mcp_configured"] else "准备安装"
                except ValueError:
                    state = "配置格式需要检查"
                self.paths.insert("end", f"{profile.name} · {state}\nSkill: {profile.skill_root / 'windows-mcp'}\nMCP: {profile.config}\n\n")
            if not selected:
                self.paths.insert("end", "选择左侧工具后，这里显示实际安装目录。\n\n安装在当前用户目录，无需管理员权限。")
            self.paths.configure(state="disabled")
            self.install_button.configure(state="normal" if selected and (self.skill.get() or self.mcp.get()) and not self.busy else "disabled")
            self.remove_button.configure(state="normal" if selected and not self.busy else "disabled")

        def perform(self, operation):
            selected = [backend.clients[key] for key, variable in self.variables.items() if variable.get()]
            if self.busy or (not selected and operation != "check"):
                return
            if operation == "uninstall" and not messagebox.askyesno("卸载接入", "恢复所选工具之前的 Windows-MCP 配置，并移除本安装器写入的 Skill？\n\n其他工具和个人修改将保留。", parent=self):
                return
            self.busy = True
            self.progress.start()
            for control in [*self.cards.values(), self.skill_option, self.mcp_option, self.check_button]:
                control.configure(state="disabled")
            self.refresh()
            skill, mcp = self.skill.get(), self.mcp.get()

            def work():
                messages = []
                failures = []
                try:
                    if operation == "check":
                        result = asyncio.run(check_service(backend.executable))
                        messages.append(f"服务连接正常：{result['tool_count']} 个工具，会话 READY")
                    else:
                        for profile in selected:
                            self.events.put(("status", f"正在处理 {profile.name}…"))
                            try:
                                result = backend.install(profile, skill, mcp) if operation == "install" else backend.uninstall(profile)
                                retained = result.get("retained", [])
                                if retained:
                                    failures.append(f"{profile.name}：部分卸载，" + "；".join(retained))
                                else:
                                    messages.append(f"{profile.name}：{'已安装' if operation == 'install' else '已卸载'}")
                            except Exception as exc:
                                failures.append(f"{profile.name}：{exc}")
                except Exception as exc:
                    failures.append(str(exc))
                self.events.put(("done", (messages, failures)))

            threading.Thread(target=work, daemon=True).start()

        def poll(self):
            while not self.events.empty():
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.configure(text=value)
                else:
                    messages, failures = value
                    self.busy = False
                    self.progress.stop()
                    self.progress.set(0)
                    for control in [*self.cards.values(), self.skill_option, self.mcp_option, self.check_button]:
                        control.configure(state="normal")
                    self.status.configure(text="处理完成 · 重启客户端或新建会话后使用" if not failures else "部分步骤未完成，请查看提示", text_color="#72cabb" if not failures else "#eaa27f")
                    self.refresh()
                    (messagebox.showwarning if failures else messagebox.showinfo)("安装结果", "\n".join(messages + failures), parent=self)
            self.after(100, self.poll)

        def close_window(self):
            if self.busy:
                messagebox.showinfo("正在处理", "请等待当前步骤结束后关闭窗口。", parent=self)
            else:
                self.destroy()

    window = Window()
    if screenshot is not None:
        def capture():
            from PIL import ImageGrab
            window.update_idletasks()
            rectangle = (window.winfo_rootx(), window.winfo_rooty(), window.winfo_rootx() + window.winfo_width(), window.winfo_rooty() + window.winfo_height())
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab(bbox=rectangle).save(screenshot)
            window.destroy()
        window.after(1800, capture)
    window.mainloop()


def main():
    if len(sys.argv) > 1 and (sys.argv[1] == "--serve" or sys.argv[1] in {"serve", "approve", "auth"}):
        restore_stdio()
        from windows_mcp.__main__ import main as server_main
        arguments = ["serve", *sys.argv[2:]] if sys.argv[1] == "--serve" else sys.argv[1:]
        server_main(args=arguments)
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--ui-smoke", type=Path)
    args = parser.parse_args()
    if args.self_test:
        isolated_test(args.self_test)
    else:
        gui(args.ui_smoke)


if __name__ == "__main__":
    main()
