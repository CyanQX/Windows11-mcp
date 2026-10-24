"""Acceptance error handling recognizes both supported option-value spellings."""
import pytest

from installer.app import argument_value


@pytest.mark.parametrize("arguments", [
    ["--ui-smoke", "C:/test with spaces/page.png", "--ui-smoke-case", "ready"],
    ["--ui-smoke=C:/test with spaces/page.png", "--ui-smoke-case=ready"],
])
def test_acceptance_error_path_uses_the_full_option_value(arguments):
    assert argument_value(arguments, "--ui-smoke") == "C:/test with spaces/page.png"
    assert argument_value(arguments, "--self-test") is None
