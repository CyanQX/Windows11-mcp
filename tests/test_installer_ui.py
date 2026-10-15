"""Native Tk integration: installed target disabling and independent maintenance."""

from pathlib import Path

import pytest

from installer.settings import SettingsService
from installer.models import SelectionModel
from installer.ui.dialogs import Targets
from installer.ui.main_window import MainWindow
from tests.installer_helpers import make_service

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_window(tmp_path_factory):
    # A single Tcl interpreter matches the product's one-root GUI lifecycle.
    # Repeated Tk initialization in one Windows process is not a supported driver.
    tmp_path = tmp_path_factory.mktemp("native-manager")
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    settings = SettingsService(tmp_path / "ui-home", env={"LOCALAPPDATA": str(tmp_path / "ui-home" / "appdata")})
    root = MainWindow(ROOT, settings, service.backend.source_executable, service=service)
    root.withdraw()
    root.after_cancel(root.scan_after_id)
    yield root
    root.destroy()


@pytest.fixture
def window(native_window, tmp_path):
    import customtkinter as ctk
    for child in list(native_window.winfo_children()):
        if isinstance(child, ctk.CTkToplevel):
            child.destroy()
    service = make_service(tmp_path)
    service.install_targets(["codex"])
    native_window.service = service
    native_window.selection = SelectionModel()
    native_window.selector.model = native_window.selection
    native_window.selector.popup = None
    native_window.accept_states(service.inspect_all())
    return native_window


def test_installed_checkbox_keyboard_select_all_and_programmatic_denied(window):
    window.selector.toggle()
    checkbox, variable = window.selector.checks["codex"]
    assert checkbox.cget("state") == "disabled"
    checkbox.toggle()
    assert not variable.get()
    window.selector.choose("codex", True)
    assert not variable.get() and "codex" not in window.selection.selected
    window.selector.select_all()
    assert "codex" not in window.selection.selected
    assert window.selector.checks["claude"][1].get()


def test_uninstall_chooses_installed_target_independently(window):
    assert "codex" not in window.selection.selected
    assert window.uninstall_button.cget("state") == "normal"
    dialog = Targets(window, "uninstall")
    assert "codex" in dialog.variables
    assert not any(variable.get() for variable in dialog.variables.values())
    dialog.destroy()


def test_refresh_clears_installed_selection_without_switching_client(window):
    window.selection.choose("claude")
    assert "claude" in window.selection.selected
    window.service.install_targets(["claude"])
    window.accept_states(window.service.inspect_all())
    assert not window.selection.selected
    assert window.install_button.cget("state") == "disabled"
