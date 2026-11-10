"""Repair and doc links must land on a heading that is still in the README.

GitHub builds a heading's anchor from its visible text: lowercase, punctuation
and symbols removed, spaces turned into hyphens. A heading that repeats an
earlier one is ``name``, then ``name-1``, then ``name-2``. The automatic-linking
repair's ``learn_more_url`` is one of those anchors. 2.13.0 renamed the heading
and left the old fragment in place, so Learn more opened the top of the README.

Two searches cover that:

* Every ``learn_more_url`` is parsed from the call (the URL is split across
  adjacent string literals, so a text search would not see it as one link)
  and its fragment has to be a current README heading.
* Every other link aimed at this repository's README, in code or in docs, is
  checked the same way. A link to a source line (``#L1145``) is not one of
  these: only the README itself, and a ``README.md`` path, count.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path
import re
import tokenize
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
COMPONENT = ROOT / "custom_components" / "device_linker"
# The repository whose README GitHub serves at the root URL. A fragment on
# that URL, or on a blob/tree URL that ends in README.md, is a heading anchor.
REPO = "bisman-automations/ha-device-linker"
# Text files that can hold a link. Brand art and caches cannot.
_TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".md"}
# A fenced code block can contain a line that looks like a heading. GitHub
# does not anchor those, so the heading scan has to step over the fence.
_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<mark>`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(?P<marks>#{1,6})[ \t]+(?P<text>.+?)\s*$")
# Characters GitHub keeps in an anchor: letters, digits, underscore, space,
# and hyphen. Everything else (punctuation, emoji, the parentheses and the
# period in "2026.11") is dropped, then each space becomes a hyphen. This is
# the ASCII result of github-slugger, which is what github.com uses, and it
# drops emoji because an emoji is none of those kept characters.
_KEPT = re.compile(r"[^\w -]", re.UNICODE)
_MD_LINK = re.compile(r"(?<!!)\[[^\[\]]*\]\(([^)\s]+)\)")


def github_slug(text: str) -> str:
    """Return the anchor GitHub would give this heading text, before duplicates.

    ``github-slugger`` lowercases, strips punctuation, and replaces spaces.
    Hyphens stay, so a heading that already says ``auto-linking`` keeps that
    hyphen. The caller numbers duplicates.
    """
    cleaned = _KEPT.sub("", text.lower())
    return cleaned.replace(" ", "-")


def _rendered_heading(raw: str) -> str:
    """The words a reader sees, which is what GitHub puts in the anchor.

    Markup is not part of the heading. A link contributes its label, not its
    URL, or ``[Setup](#setup)`` would slug to ``setupsetup``. Emphasis
    asterisks are punctuation and fall out in :func:`github_slug`.
    """
    text = raw.strip()
    text = re.sub(r"[ \t]+#+\s*$", "", text).strip()
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "")
    return re.sub(r"<[^>]+>", "", text).strip()


def heading_texts(markdown: str) -> list[str]:
    """Visible text of every ATX and setext heading, fences excluded.

    A line of dashes under a title is a setext heading. The same dashes after
    a blank line are a thematic break, so only a non-blank preceding line
    counts. This README uses ATX headings; setext is read so a later edit
    does not silently drop a heading from the check.
    """
    texts: list[str] = []
    fence: str | None = None
    fence_length = 0
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = _FENCE.match(line)
        if fence is None and marker is not None:
            fence = marker.group("mark")[0]
            fence_length = len(marker.group("mark"))
            index += 1
            continue
        if fence is not None:
            # The closing fence is the same character, at least as long, and
            # the rest of the line is empty. A shorter run is just text.
            closing = _FENCE.match(line)
            if (
                closing is not None
                and closing.group("mark")[0] == fence
                and len(closing.group("mark")) >= fence_length
                and line[closing.end() :].strip() == ""
            ):
                fence = None
            index += 1
            continue
        atx = _ATX.match(line)
        if atx is not None:
            texts.append(_rendered_heading(atx.group("text")))
            index += 1
            continue
        underline = lines[index + 1] if index + 1 < len(lines) else ""
        if line.strip() and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", underline):
            texts.append(_rendered_heading(line.strip()))
            index += 2
            continue
        index += 1
    return [text for text in texts if text]


def github_anchors(markdown: str) -> list[str]:
    """Anchors GitHub generates for ``markdown``, in heading order.

    The first heading wins the plain slug. The next heading that would reuse
    it becomes ``slug-1``, then ``slug-2``, which is what GitHub and
    ``github-slugger`` both do. Numbering looks at the slug actually issued,
    so a heading whose own text is already ``slug-1`` pushes the next
    duplicate of ``slug`` on to ``slug-2``.
    """
    issued: dict[str, int] = {}
    anchors: list[str] = []
    for text in heading_texts(markdown):
        slug = github_slug(text)
        original = slug
        while slug in issued:
            issued[original] += 1
            slug = f"{original}-{issued[original]}"
        issued[slug] = 0
        anchors.append(slug)
    return anchors


def _constant_strings(tree: ast.Module) -> dict[str, str]:
    """Module-level string constants, including ones built by concatenation."""
    found: dict[str, str] = {}
    for node in tree.body:
        value: ast.expr | None = None
        name: str | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = node.value
        if name is None or value is None:
            continue
        try:
            found[name] = _literal(value, {})
        except ValueError:
            continue
    return found


def _literal(node: ast.expr, constants: dict[str, str]) -> str:
    """A string built only from literals and names already resolved."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal(node.left, constants) + _literal(node.right, constants)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            raise ValueError(
                f"learn_more_url f-string is not constant: {ast.dump(node)}"
            )
        return "".join(parts)
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    raise ValueError(f"learn_more_url is not a constant string: {ast.dump(node)}")


def _imported_names(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Local name to ``(module, original name)`` for ``from x import y``."""
    found: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        # Only relative imports inside the integration. An absolute import is
        # Home Assistant or the standard library, which does not hold this URL.
        if node.level < 1:
            continue
        for alias in node.names:
            found[alias.asname or alias.name] = (node.module, alias.name)
    return found


def _module_path(module: str) -> Path | None:
    """``const`` to ``custom_components/device_linker/const.py``."""
    candidate = COMPONENT.joinpath(*module.split("."))
    file_path = candidate.with_suffix(".py")
    if file_path.is_file():
        return file_path
    init = candidate / "__init__.py"
    if init.is_file():
        return init
    return None


def learn_more_urls() -> list[tuple[Path, int, str]]:
    """Every ``learn_more_url`` argument in the integration, as a full URL.

    The repair passes the URL as two adjacent string literals. Parsing the
    call joins them, and a name is followed back to the module-level constant
    it was imported from, so moving the URL into ``const.py`` does not hide
    it from the check.
    """
    files = sorted(COMPONENT.rglob("*.py"))
    parsed: dict[Path, tuple[ast.Module, dict[str, str]]] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parsed[path] = (tree, _constant_strings(tree))

    def resolve(path: Path, node: ast.expr, seen: set[tuple[Path, str]]) -> str:
        constants = parsed[path][1]
        if isinstance(node, ast.Name) and node.id not in constants:
            imported = _imported_names(parsed[path][0]).get(node.id)
            if imported is None:
                raise ValueError(
                    f"{path.relative_to(ROOT)} uses {node.id} for learn_more_url, "
                    "and it is not a string in that module"
                )
            module, original = imported
            other = _module_path(module)
            if other is None or other not in parsed:
                raise ValueError(
                    f"{path.relative_to(ROOT)} imports {node.id} from {module}, "
                    "which this check cannot read"
                )
            key = (other, original)
            if key in seen:
                raise ValueError(f"{node.id} imports itself")
            seen.add(key)
            assignment = parsed[other][1].get(original)
            if assignment is None:
                raise ValueError(
                    f"{other.relative_to(ROOT)} does not define {original} as a string"
                )
            return assignment
        return _literal(node, constants)

    found: list[tuple[Path, int, str]] = []
    for path, (tree, _constants) in parsed.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "learn_more_url":
                continue
            found.append((path, node.lineno, resolve(path, node.value, set())))
    return found


def _fragment_of_readme_url(url: str, source: Path) -> str | None:
    """The heading fragment when ``url`` points at this README, else None.

    The repository root URL is the README. So is any ``README.md`` path on
    that repository, a relative link to ``README.md``, and (inside the README
    itself) a same-page ``#heading`` link. A fragment on a source file, such
    as a line number, is left alone.
    """
    if url.startswith("#") and source.resolve() == README.resolve():
        fragment = unquote(url[1:])
        return fragment or None
    if " #" in url or url.startswith("<"):
        return None
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        if parsed.netloc != "github.com":
            return None
        path = parsed.path.strip("/")
        root = path == REPO
        readme = path.startswith(f"{REPO}/") and path.endswith("/README.md")
        if not (root or readme):
            return None
        return unquote(parsed.fragment) or None
    if "#" not in url:
        return None
    relative, _hash, fragment = url.partition("#")
    if not relative or not fragment:
        return None
    target = (source.parent / relative).resolve()
    if target != README.resolve():
        return None
    return unquote(fragment)


def _python_strings(text: str) -> list[tuple[int, str]]:
    """String values in a Python file, with adjacent literals joined.

    Python joins ``"https://example" "#anchor"`` into one string. A search
    that looks at each line would see a URL with no fragment and a fragment
    with no URL, and miss the link. Whitespace and comments between the
    literals do not break the join, matching the language.
    """
    groups: list[tuple[int, list[str]]] = []
    current: list[str] | None = None
    start = 1
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    ignored = {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.COMMENT,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
    for token in tokens:
        if token.type == tokenize.STRING:
            try:
                value = ast.literal_eval(token.string)
            except SyntaxError, ValueError:
                value = None
            if isinstance(value, str):
                if current is None:
                    current = []
                    start = token.start[0]
                current.append(value)
                continue
        if token.type in ignored:
            continue
        if current is not None:
            groups.append((start, current))
            current = None
    if current is not None:
        groups.append((start, current))
    return [(line, "".join(parts)) for line, parts in groups]


def _record(
    found: list[tuple[Path, int, str]],
    seen: set[tuple[int, str]],
    path: Path,
    line: int,
    url: str,
) -> None:
    fragment = _fragment_of_readme_url(url, path)
    if fragment is None:
        return
    key = (line, fragment)
    if key in seen:
        return
    seen.add(key)
    found.append((path, line, fragment))


def readme_fragment_links() -> list[tuple[Path, int, str]]:
    """Every README heading fragment linked from the repo, with its location."""
    found: list[tuple[Path, int, str]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if any(part in {".git", "__pycache__", ".venv"} for part in path.parts):
            continue
        # This file documents the check and embeds sample fragments that are
        # not links. The integration, the docs, and the other tests are in.
        if path.resolve() == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8")
        seen: set[tuple[int, str]] = set()
        for match in _MD_LINK.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            _record(found, seen, path, line, match.group(1))
        if path.suffix == ".py":
            for line, value in _python_strings(text):
                _record(found, seen, path, line, value)
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for token in re.findall(r"https://[^\s\"')<>]+", line):
                _record(found, seen, path, line_number, token)
    return found


def _where(path: Path, line: int) -> str:
    return f"{path.relative_to(ROOT)}:{line}"


def test_github_anchor_rules() -> None:
    """Punctuation, fences, duplicates, and a setext heading slug like GitHub."""
    sample = "\n".join(
        [
            "# Device Linker",
            "## Marketplace (Home Assistant 2026.11 and newer)",
            "```markdown",
            "## not a heading",
            "```",
            "## Same name",
            "## Same name",
            "Setext title",
            "------------",
            "",
            "---",
            "## [Setup](#setup)",
        ]
    )
    assert github_anchors(sample) == [
        "device-linker",
        "marketplace-home-assistant-202611-and-newer",
        "same-name",
        "same-name-1",
        "setext-title",
        "setup",
    ]


def test_readme_headings_match_github_anchors() -> None:
    """The anchors this README actually has, including the repair's section.

    These are the fragments GitHub serves today. A heading rename changes
    the fragment, and the repair link has to change with it.
    """
    anchors = set(github_anchors(README.read_text(encoding="utf-8")))
    assert "how-suggestions-and-automatic-linking-work" in anchors
    assert "marketplace-home-assistant-202611-and-newer-recommended" in anchors
    assert "hacs-home-assistant-202610-and-older" in anchors
    assert "custom-repository" in anchors
    assert "cleaning-up-leftover-addresses" in anchors
    # The heading 2.13.0 renamed away. It must not be the one we link, and
    # it is not a heading any more, which is why the old repair URL 404'd
    # into the top of the page.
    assert "how-auto-linking-works" not in anchors


def test_learn_more_urls_point_at_readme_headings() -> None:
    """Each repair Learn more link names a heading that is in the README."""
    anchors = set(github_anchors(README.read_text(encoding="utf-8")))
    urls = learn_more_urls()
    assert urls, "the integration has no learn_more_url; the repair link was dropped"
    missing: list[str] = []
    for path, line, url in urls:
        fragment = _fragment_of_readme_url(url, README)
        if fragment is None:
            missing.append(f"{_where(path, line)} does not point at the README: {url}")
            continue
        if fragment not in anchors:
            missing.append(
                f"{_where(path, line)} -> #{fragment} is not a README heading"
            )
    assert not missing, "learn_more_url anchors:\n" + "\n".join(missing)
    # The one repair we ship is the suggestions section, not the old name.
    fragments = {_fragment_of_readme_url(url, README) for _path, _line, url in urls}
    assert fragments == {"how-suggestions-and-automatic-linking-work"}


def test_every_readme_fragment_link_names_a_heading() -> None:
    """A README anchor linked from code or docs is still a heading."""
    anchors = set(github_anchors(README.read_text(encoding="utf-8")))
    missing = [
        f"{_where(path, line)} -> #{fragment}"
        for path, line, fragment in readme_fragment_links()
        if fragment not in anchors
    ]
    assert not missing, "README links with no heading:\n" + "\n".join(missing)
    # The repair URL is one of them, so this is not an empty pass. Python
    # joins the two adjacent literals before the fragment is read.
    fragments = {fragment for _path, _line, fragment in readme_fragment_links()}
    assert "how-suggestions-and-automatic-linking-work" in fragments
