"""Fixtures for tests that run inside Home Assistant."""

from pathlib import Path

import pytest

# Repo root is tests/ha/../../ .
REPO_COMPONENTS = str(Path(__file__).resolve().parents[2] / "custom_components")


def _add_repo_components() -> None:
    """Let Home Assistant load this repo's integration.

    The test harness ships its own ``custom_components`` package. Add ours so
    imports and config-entry setup find ``device_linker``. Called at import
    time as well as from the fixture: test modules import the integration
    while they are collected, before fixtures run.
    """
    import custom_components

    if REPO_COMPONENTS not in custom_components.__path__:
        custom_components.__path__.append(REPO_COMPONENTS)


_add_repo_components()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations and keep this repo on the package path."""
    _add_repo_components()
    yield
