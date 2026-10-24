"""Package the verified GUI/service as a portable, adjacent-data bundle."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def package(executable: Path, output: Path, version: str):
    from installer.distribution import portable_name, portable_root, verify_portable
    from installer.storage import check_path
    from windows_mcp.infrastructure.runtime_paths import PORTABLE_MARKER, PORTABLE_MODE

    executable, output = check_path(executable), check_path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / portable_name(version)
    root = portable_root(version)
    readme = f"""Windows-MCP {version} x64 便携版

解压整个文件夹后，双击 Windows-MCP.exe 打开安装与维护管理器。
无需安装管理器、无需另装 Python。不要在 ZIP 内直接运行。
请保留 WindowsMCP.portable.json；设置、日志、备份、收据、运行时和审批数据位于旁边的 data 文件夹。
Skill 与 MCP 接入仍写入 Codex 桌面端等编程工具的官方用户目录，需在页面中预览并确认安装。
接入后请保持解压目录的位置；移动或删除便携目录前，先从管理器卸载对应接入。
未接入时可整体移动文件夹。审批凭证由 Windows 当前用户加密，不跨用户或电脑迁移。
维护编程工具配置时，会使用用户目录中的全局维护锁协调安装版与便携版。
安装版与便携版管理器请分别使用各自的受管收据；既有外部接入不会自动覆盖。

项目与完整工具说明：https://github.com/CyanQX/Windows11-mcp
"""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(executable, f"{root}/Windows-MCP.exe")
        archive.writestr(f"{root}/{PORTABLE_MARKER}", json.dumps(PORTABLE_MODE) + "\n")
        archive.writestr(f"{root}/README.txt", readme.encode("utf-8-sig"))
        archive.write(ROOT / "LICENSE.md", f"{root}/LICENSE.txt")
        archive.write(ROOT / "installer/resources/third-party-licenses.txt", f"{root}/THIRD-PARTY-LICENSES.txt")
        archive.writestr(f"{root}/data/", b"")
    result = verify_portable(target, version)
    (output / "portable-structure-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "dist/setup")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    print(json.dumps(package(args.executable, args.out, version), indent=2))


if __name__ == "__main__":
    main()
