"""Setup copy has to describe automatic linking as it works.

Automatic linking is off unless Matching settings turns it on, and then it
links only a shared MAC address, a hardware identifier, or an exact name of
two or more words. The setup step is the sentence shown before any of that.
Home Assistant reads ``translations/en.json``; ``strings.json`` is the same
text and the two files have to stay identical.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "device_linker"
STRINGS = COMPONENT / "strings.json"
ENGLISH = COMPONENT / "translations" / "en.json"

# Said as what Device Linker does, with no mention that the setting is off.
# The setup step used to end on the first of these. Older changelog sections
# still describe the releases where it was true, and those stay as history.
_UNCONDITIONAL = (
    "links strong matches on its own",
    "Strong matches are linked automatically",
)
# Current docs and the integration. CHANGELOG.md is the history of older
# releases, and this test file quotes the phrases it is looking for.
_COPY_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".md"}
_SKIP_PARTS = {".git", "__pycache__", ".venv", "tests"}


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_strings_and_english_are_the_same_file() -> None:
    """Home Assistant shows translations/en.json, so the two cannot drift."""
    assert STRINGS.read_text(encoding="utf-8") == ENGLISH.read_text(encoding="utf-8")
    assert _load(STRINGS) == _load(ENGLISH)


def test_setup_step_says_automatic_linking_is_off() -> None:
    """The setup step no longer says strong matches are linked on their own."""
    description = _load(ENGLISH)["config"]["step"]["user"]["description"]
    assert isinstance(description, str)
    folded = description.casefold()
    assert "links strong matches on its own" not in folded
    assert "automatic linking is off" in folded
    assert "matching settings" in folded
    # What turning it on is allowed to link. The same three are in the
    # Matching settings description, so the two screens agree.
    assert "mac address" in folded
    assert "hardware identifier" in folded
    assert "exact name of two or more words" in folded


def test_current_copy_does_not_say_automatic_linking_is_unconditional() -> None:
    """README, agent notes, and the integration do not describe the old default.

    A sentence that says Device Linker links strong matches, with no
    condition, is the wording from before automatic linking defaulted off.
    """
    hits: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in _COPY_SUFFIXES:
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.name == "CHANGELOG.md":
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for phrase in _UNCONDITIONAL:
                if phrase in line:
                    hits.append(f"{path.relative_to(ROOT)}:{line_number}: {phrase}")
    assert not hits, "copy still says automatic linking just happens:\n" + "\n".join(
        hits
    )
