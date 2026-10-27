"""Include the installed optional capture/OCR wheels' license notices in distributions."""

from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "\n\n=== Windows-MCP optional capture and OCR components ===\n"
PACKAGES = ("windows-capture", "opencv-python", "pytesseract", "winrt-runtime",
    "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections",
    "winrt-Windows.Globalization", "winrt-Windows.Graphics.Imaging",
    "winrt-Windows.Media.Ocr", "winrt-Windows.Storage.Streams")


def main() -> None:
    path = ROOT / "installer/resources/third-party-licenses.txt"
    original = path.read_text(encoding="utf-8").split(MARKER)[0]
    notices = []
    for name in PACKAGES:
        package = distribution(name)
        files = sorted((p for p in package.files or [] if any(word in p.name.lower()
            for word in ("license", "licence", "copying", "notice"))), key=str)
        if not files:
            raise RuntimeError(f"Missing license notices: {name}")
        notices.append(f"\n{package.metadata['Name']} {package.version}\n")
        for file in files:
            notices.append(f"\n--- {file.name} ---\n" + Path(package.locate_file(file)).read_text(encoding="utf-8"))
    path.write_text((original + MARKER + "\n".join(notices)).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
