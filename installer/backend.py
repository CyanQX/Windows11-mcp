"""Install only owned Skill files and one MCP entry, preserving other client settings."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import json5
import tomlkit
from ruamel.yaml import YAML

SERVER = "windows-mcp"
ROW = "windows-mcp-setup"


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    skill_root: Path
    config: Path
    kind: str


def profiles(home: Path, env: dict | None = None) -> list[Profile]:
    env = os.environ if env is None else env

    def root(variable: str, fallback: Path) -> Path:
        value = env.get(variable, "").strip()
        return Path(value).expanduser().absolute() if value else fallback

    codex = root("CODEX_HOME", home / ".codex")
    claude = root("CLAUDE_CONFIG_DIR", home / ".claude")
    dsh = root("DSH_HOME", home / ".dsh")
    opencode = root("XDG_CONFIG_HOME", home / ".config") / "opencode"
    opencode_config = root("OPENCODE_CONFIG", opencode / "opencode.json")
    if not env.get("OPENCODE_CONFIG") and (opencode / "opencode.jsonc").exists():
        opencode_config = opencode / "opencode.jsonc"
    return [
        Profile("claude", "Claude Code", claude / "skills", home / ".claude.json", "json"),
        Profile("codex", "Codex", home / ".agents" / "skills", codex / "config.toml", "toml"),
        Profile("deepseek", "DeepSeek Harness", dsh / "skills", dsh / "cordis.patch.yml", "yaml"),
        Profile("opencode", "OpenCode", opencode / "skills", opencode_config, "opencode"),
        Profile("gemini", "Gemini CLI", home / ".gemini" / "skills", home / ".gemini" / "settings.json", "json"),
    ]


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".windows-mcp-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


class Configuration:
    def __init__(self, profile: Profile, content: bytes | None):
        self.profile = profile
        self.yaml = YAML(typ="rt")
        self.yaml.preserve_quotes = True
        text = (content or b"").decode("utf-8-sig")
        self.original_text = text
        self.had_bom = bool(content and content.startswith(b"\xef\xbb\xbf"))
        try:
            if profile.kind == "toml":
                self.document = tomlkit.parse(text)
            elif profile.kind == "yaml":
                self.document = self.yaml.load(text) if text.strip() else []
                if not isinstance(self.document, list):
                    raise ValueError("Cordis patch must be a list")
            else:
                self.document = json5.loads(text, allow_duplicate_keys=False) if text.strip() else {}
                if not isinstance(self.document, dict):
                    raise ValueError("Client configuration must be an object")
            if profile.kind != "yaml":
                section = "mcp_servers" if profile.kind == "toml" else "mcp" if profile.kind == "opencode" else "mcpServers"
                existing = self.document.get(section, {})
                if not isinstance(existing, dict):
                    raise ValueError("MCP section must be an object")
                self.section = section
                if SERVER in existing and not isinstance(existing[SERVER], dict):
                    raise ValueError("Windows-MCP entry must be an object")
        except Exception as exc:
            raise ValueError(f"无法读取 {profile.config.name}，原配置未修改。请先修复配置格式。") from exc

    def get(self):
        if self.profile.kind != "yaml":
            return self.document.get(self.section, {}).get(SERVER)
        matches = [row for patch in self.document if isinstance(patch, dict) for row in patch.get("insert", []) if isinstance(row, dict) and (row.get("id") == ROW or (row.get("name") == "@deepseek-ai/dsh-mcp-client" and row.get("config", {}).get("serverName") == SERVER))]
        if len(matches) > 1:
            raise ValueError("Harness 中有多个 Windows-MCP 配置，请先合并重复项。")
        return matches[0] if matches else None

    def set(self, value) -> None:
        if self.profile.kind != "yaml":
            if value is None:
                self.document.get(self.section, {}).pop(SERVER, None)
            else:
                if self.section not in self.document:
                    self.document[self.section] = {}
                self.document[self.section][SERVER] = value
            return
        old = self.get()
        for patch in self.document:
            if isinstance(patch, dict) and old in patch.get("insert", []) and old is not None:
                rows = patch["insert"]
                index = rows.index(old)
                if value is None:
                    rows.pop(index)
                    if not rows and len(patch) == 1:
                        self.document.remove(patch)
                else:
                    rows[index] = value
                return
        if value is not None:
            self.document.append({"insert": [value]})

    def encode(self) -> bytes:
        if self.profile.kind == "toml":
            return tomlkit.dumps(self.document).encode("utf-8")
        if self.profile.kind == "yaml":
            stream = io.StringIO()
            self.yaml.dump(self.document, stream)
            return stream.getvalue().encode("utf-8")
        from installer.jsonpatch import patch_member

        text = patch_member(self.original_text, [self.section, SERVER], self.get())
        if json5.loads(text, allow_duplicate_keys=False) != self.document:
            raise ValueError("配置保真校验失败，原文件未修改")
        return (("\ufeff" if self.had_bom else "") + text).encode("utf-8")


def mcp_entry(profile: Profile, executable: Path) -> dict:
    command = str(executable)
    env = {"ANONYMIZED_TELEMETRY": "false"}
    if profile.kind == "toml":
        return {"command": command, "args": ["--serve"], "startup_timeout_sec": 90, "env": env}
    if profile.kind == "opencode":
        return {"type": "local", "command": [command, "--serve"], "enabled": True, "timeout": 90000, "environment": env}
    if profile.kind == "yaml":
        return {"id": ROW, "name": "@deepseek-ai/dsh-mcp-client", "config": {"serverName": SERVER, "transport": "stdio", "command": command, "args": ["--serve"], "env": env, "cwd": str(executable.parent), "toolCallTimeoutMs": 60000, "failOnStartupError": False}}
    entry = {"command": command, "args": ["--serve"], "env": env}
    if profile.id == "claude":
        entry["type"] = "stdio"
    return entry


class Installer:
    def __init__(self, home: Path, data: Path, skill: Path, executable: Path, env: dict | None = None):
        self.home = home.absolute()
        self.data = data.absolute()
        self.skill = skill
        self.source_executable = executable.absolute()
        self.executable = self.data / "Windows-MCP.exe"
        self.clients = {profile.id: profile for profile in profiles(self.home, env)}
        self.on_write = None
        self.emit = None
        self.version = None

    def _write(self, path: Path, content: bytes | None) -> None:
        if self.on_write:
            self.on_write(path, digest(content) if content is not None else None)
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, content)

    def _event(self, phase: str, message: str):
        if self.emit:
            self.emit(phase, message)

    def receipt_path(self, profile: Profile) -> Path:
        return self.data / "receipts" / f"{profile.id}.json"

    def inspect(self, profile: Profile) -> dict:
        skill_file = profile.skill_root / SERVER / "SKILL.md"
        configured = Configuration(profile, read_file(profile.config)).get() is not None
        return {"skill_installed": skill_file.is_file(), "mcp_configured": configured, "skill_path": str(skill_file.parent), "config_path": str(profile.config)}

    def _runtime(self) -> None:
        if not self.source_executable.is_file():
            raise ValueError("服务程序不存在，请使用构建好的 Windows EXE 安装器。")
        self.data.mkdir(parents=True, exist_ok=True)
        if self.executable.exists() and digest(self.executable.read_bytes()) == digest(self.source_executable.read_bytes()):
            return
        temporary = self.data / (".runtime-" + uuid.uuid4().hex + ".exe")
        try:
            shutil.copyfile(self.source_executable, temporary)
            expected = digest(self.source_executable.read_bytes())
            if digest(temporary.read_bytes()) != expected:
                raise ValueError("运行时复制校验失败")
            if self.on_write:
                self.on_write(self.executable, expected)
            try:
                os.replace(temporary, self.executable)
            except PermissionError:
                if not self.executable.exists():
                    raise
                # Preserve a loaded image; never terminate clients or delete it in place.
                retired = self.data / "retired"
                retired.mkdir(exist_ok=True)
                previous = retired / (uuid.uuid4().hex + ".exe")
                os.rename(self.executable, previous)
                try:
                    os.replace(temporary, self.executable)
                except Exception:
                    os.rename(previous, self.executable)
                    raise
            self._event("runtime", "MCP 运行时已复制并校验")
        finally:
            temporary.unlink(missing_ok=True)

    def install(self, profile: Profile, skill: bool = True, mcp: bool = True) -> dict:
        if not skill and not mcp:
            raise ValueError("至少选择一项安装内容。")
        before_config = read_file(profile.config)
        config = Configuration(profile, before_config) if mcp else None
        expected = mcp_entry(profile, self.executable)
        old_receipt = read_file(self.receipt_path(profile))
        previous = json.loads(old_receipt) if old_receipt else None
        if previous and previous["config_path"] != str(profile.config):
            raise ValueError("安装目录与已有记录不同，请先卸载原目录的接入。")
        if mcp:
            self._runtime()
            config.set(expected)
        transaction = self.data / "backups" / uuid.uuid4().hex
        transaction.mkdir(parents=True, exist_ok=False)
        self._event("backup", "原配置与受管理 Skill 的备份位置已创建")
        receipt = previous or {"profile": profile.id, "config_path": str(profile.config), "skill_root": str(profile.skill_root), "files": {}, "config_backup": None, "config_originally_exists": before_config is not None}
        changes = []
        if mcp:
            if not receipt.get("mcp"):
                backup = transaction / "client-config"
                if before_config is not None:
                    backup.write_bytes(before_config)
                    receipt["config_backup"] = str(backup)
                receipt["config_originally_exists"] = before_config is not None
            changes.append((profile.config, before_config, config.encode()))
            receipt["mcp"] = True
            receipt["entry"] = expected
        if skill:
            for source in self.skill.rglob("*"):
                if not source.is_file():
                    continue
                relative = source.relative_to(self.skill).as_posix()
                destination = profile.skill_root / SERVER / relative
                if destination.is_symlink() or any(parent.is_symlink() or parent.is_junction() for parent in destination.parents if parent != parent.parent):
                    raise ValueError("Skill 安装路径含符号链接，请改为普通目录。")
                content = source.read_bytes()
                before = read_file(destination)
                if relative not in receipt["files"]:
                    backup = transaction / "skill" / relative
                    if before is not None:
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        backup.write_bytes(before)
                    receipt["files"][relative] = {"backup": str(backup) if before is not None else None}
                receipt["files"][relative]["hash"] = digest(content)
                changes.append((destination, before, content))
        applied = []
        try:
            for path, before, after in changes:
                if read_file(path) != before:
                    raise ValueError(f"{path.name} 在安装时被其他程序修改，请重试。")
                self._write(path, after)
                applied.append((path, before, after))
                self._event("config" if path == profile.config else "skill", "MCP 配置已合并" if path == profile.config else "Skill 文件已写入并复读校验")
                if read_file(path) != after:
                    raise ValueError("落盘校验失败")
            if self.version:
                receipt.update(schema=2, version=self.version, runtime_sha256=digest(self.executable.read_bytes()))
            self._write(self.receipt_path(profile), json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8"))
        except Exception:
            for path, before, after in reversed(applied):
                if read_file(path) == after:
                    self._write(path, before)
            raise
        return {"profile": profile.name, "skill": skill, "mcp": mcp, "backup": str(transaction)}

    def uninstall(self, profile: Profile) -> dict:
        receipt_file = self.receipt_path(profile)
        if not receipt_file.is_file():
            raise ValueError("未找到本安装器的安装记录，未修改已有配置。")
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        if receipt["config_path"] != str(profile.config) or receipt["skill_root"] != str(profile.skill_root):
            raise ValueError("目录与安装记录不同，未修改配置。")
        retained = []

        def backup_content(name: str | None):
            if name is None:
                return None
            path = Path(name).resolve()
            if not path.is_relative_to((self.data / "backups").resolve()):
                raise ValueError("备份路径超出安装器目录。")
            return path.read_bytes()

        if receipt.get("mcp"):
            current_content = read_file(profile.config)
            config = Configuration(profile, current_content)
            if config.get() == receipt["entry"]:
                original = Configuration(profile, backup_content(receipt["config_backup"]))
                config.set(original.get())
                if read_file(profile.config) != current_content:
                    raise ValueError("配置被其他程序修改，请重试。")
                self._write(profile.config, config.encode())
                receipt.pop("mcp", None)
            else:
                retained.append("MCP 配置已被修改，已保留")
        for relative, record in list(receipt["files"].items()):
            destination = profile.skill_root / SERVER / relative
            if not destination.resolve().is_relative_to((profile.skill_root / SERVER).resolve()):
                raise ValueError("Skill 文件路径超出安装目录。")
            content = read_file(destination)
            if content is None or digest(content) == record["hash"]:
                before = backup_content(record["backup"])
                self._write(destination, before)
                receipt["files"].pop(relative)
            else:
                retained.append(f"{relative} 已被修改，已保留")
        if retained:
            self._write(receipt_file, json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8"))
        else:
            self._write(receipt_file, None)
        return {"profile": profile.name, "retained": retained}
