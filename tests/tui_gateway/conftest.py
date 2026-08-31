"""TUI unit-test isolation for intentionally non-persistent doubles."""

import pytest


@pytest.fixture(autouse=True)
def explicit_ephemeral_tui_test_mode(monkeypatch):
    monkeypatch.setenv("HERMES_TUI_TEST_EPHEMERAL", "1")
