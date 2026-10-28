# Device Linker: notes for Claude and other coding agents

Device Linker is a HACS custom integration (`custom_components/device_linker`, domain `device_linker`). It adds one shared MAC address connection to devices that are already in Home Assistant, so Home Assistant can show them together under **Linked devices**. It links registry entries. It does not merge devices or copy their entities.

It also suggests devices to link: a shared MAC or hardware identifier first (across areas), then similar names in the same area. Read this before changing anything.

## Layout

| Path | What it is |
| --- | --- |
| `__init__.py` | Setup, unload, and removal. On setup, puts the shared MAC on each linked device this entry owns, listens for device-registry updates so a refresh does not drop that connection, and starts the suggestion manager. Unload stops the listener and leaves the connections in place. Removal deletes only connections this entry added. Ignored discoveries are config entries with no link; setup returns immediately. |
| `config_flow.py` | Config flow (main device, linked devices, MAC), discovery flows (`discovery_mac`, `discovery_identifier`, `discovery_name`), the ignore step, and the options flow. `MANUAL_ENTRY` is the manual-MAC path and must stay available. |
| `matching.py` | Pure suggestion engine. No Home Assistant imports. Name normalization, hardware keys, ranking. |
| `manager.py` | Loads preferences, debounces registry updates, publishes discovery flows, and owns the sensor. |
| `sensor.py` | Diagnostic **Suggestions** sensor. |
| `diagnostics.py` | Config-entry diagnostics. MAC addresses are redacted. |
| `registry.py` | `get_device` and `get_entry_settings`. No flow logic. |
| `const.py` | Domain, storage keys, and `MAC_PATTERN`. No Home Assistant imports. |
| `strings.json`, `translations/en.json` | The same keys. Home Assistant loads `translations/en.json`. |
| `services.yaml` | `rescan_suggestions`. |
| `brand/` | `icon.png` (256) and `icon@2x.png` (512), the same artwork as `logo.png` and `logo@2x.png`, plus dark variants. `python scripts/make_brand.py` rebuilds the dark variants from the light icons. |
| `scripts/make_brand.py` | Knocks the white background out of the light icons to make `dark_icon` and `dark_logo`. Needs Pillow, which is not a dependency of the integration (hassfest rejects Pillow in `requirements`). |
| `tests/test_mac.py`, `tests/test_matching.py` | Pure tests, loaded without the integration package. |
| `tests/ha/` | Config flow, options, legacy settings, the registry link, and suggestions, inside Home Assistant. |

Paths under `custom_components/device_linker/` unless noted.

## Conventions

- **Storage (current entries).** `main_device_id` (or null for manual MAC entry), `linked_device_ids`, `mac_address` (lowercase, colon-separated, from `format_mac`), and `added_connections`: a map of linked device id to whether this entry added that MAC. Removal and unlinking delete a connection only when the map says this entry added it.
- **Old entries stay readable.** `get_entry_settings` accepts them, and the options flow can save them in the current shape.
  - 1.0.0 stored one pair: `device_id` is the device that receives the MAC, `peer_device_id` is the device that already has it (the main device), and `added_connection` is the single ownership flag.
  - The earlier multi-select build stored `device_id` as the main device and `target_devices` as the linked ids. It did not record ownership, so `added_connections` is false for each of them and a later edit will not remove a MAC it cannot prove it added.
- **The flow.** User step: pick the main device, or **Manual MAC entry**. Devices step: pick one or more devices to link. Confirm step: pick a MAC registered on the main device, or type one. Colons and hyphens are both accepted; the stored value is normalized.
- **Who can share a MAC.** Home Assistant requires a connection to be unique inside one config entry. The main device and every linked device must come from different config entries, and the linked devices must not share a config entry with each other. The confirm step names the device that already has the MAC.
- **The main device stays fixed** in the options flow. Changing the selection unlinks what was removed. Clearing the selection unlinks every device. Only owned connections are removed.
- **Unload keeps the link.** The listener is stopped and the MAC stays. Removal of the config entry is what deletes owned connections.
- **Skip composite devices.** `get_device` returns none when `is_composite_device` is set, so a synthetic device is not offered as a link target.
- **Suggestions.** `matching.suggest` ranks MAC matches, then other hardware identifiers, then same-area name similarity. Default threshold is 0.8 (`DEFAULT_SUGGESTION_THRESHOLD`), edited in the options flow (number selector, 0.5 to 1, step 0.05) and stored for the whole integration in `device_linker.suggestions`, along with the two signal switches and dismissed unique ids. Dismissal is permanent. Discovery flows are capped at 10; the sensor and diagnostics list up to 25. Registry updates debounce for 2 seconds. A device that already shares a network-MAC or Bluetooth connection is already linked and is not suggested. Devices from `device_linker` itself are not candidates.
- **Support Home Assistant 2026.8 and newer** (`hacs.json`). Do not lower that minimum without retesting device-registry connections on the older release. `requirements_test_min.txt` pins the newest pytest-homeassistant-custom-component release of 2026.8.
- `strings.json` and `translations/en.json` must be identical.
- `manifest.json` keys: `domain`, `name`, then alphabetical (hassfest). `issue_tracker` is the GitHub issues URL.

## Checks

The shared checks at the end of this file. There is no mypy and there are no Node tests. Run pytest on the newest Home Assistant and, when registry behavior could differ, on `requirements_test_min.txt` as well. Both need Python 3.14.

- Treat any `Detected that custom integration 'device_linker' ...` warning as a bug to fix, even if tests pass.
- `tests/test_mac.py` and `tests/test_matching.py` cover the pure helpers. `tests/ha/` runs inside Home Assistant: the config and options flows, legacy entries, setup, unload, and removal, and suggestion discovery, dismissal, debounce, and options.

## Open decisions

- None.


<!-- BEGIN bisman-automations shared rules -->
<!-- This block is the same in every bisman-automations repo. Edit it in
     bisman-automations/.github (templates/CLAUDE.shared.md) and copy it to
     every repo; the Validate workflow's "standards" job checks it matches. -->

## Shared rules (every bisman-automations repo)

### Releases

- The version lives in `custom_components/<domain>/manifest.json`. Anything
  else that repeats it is called out in the repo-specific notes below and is
  bumped in the same commit.
- Versions follow [Semantic Versioning](https://semver.org): patch for fixes,
  translations, docs and CI; minor for new features, options or entities;
  major when something users rely on is removed or renamed (entities,
  services, events, options) or setup has to be redone.
- Check what is released before choosing a version:
  `git ls-remote --tags origin | awk -F/ '{print $3}' | sort -V | tail -1`
  (`sort -V`: plain sorting puts `v1.10.0` before `v1.9.0`). Never add
  changes to a released version. If the manifest version has no tag yet,
  add to its CHANGELOG section instead of bumping again.
- Every push to `main` ships as a release. In that same commit, named
  `Release X.Y.Z`, bump the manifest version and write the notes directly
  under a new `## [X.Y.Z] - YYYY-MM-DD` heading in `CHANGELOG.md` (Keep a
  Changelog: Added, Changed, Deprecated, Removed, Fixed, Security), with
  that version's compare link at the bottom. There is no `## [Unreleased]`
  section and no `[Unreleased]:` compare link. New work is not collected
  under a holding heading and moved later; the release commit is where the
  heading and the entries are written. The standards check fails if a
  `## [Unreleased]` heading is present.
- Ask Donavan before every push. Push fast-forward only: never force-push,
  rebase or amend anything already pushed, or rewrite history in any other
  way. Rewriting history orphans the release tags (it broke drafting in
  ha-button-actions).
- Pushing to `main` runs **Draft release**, which creates or refreshes a
  draft `vX.Y.Z` with that version's CHANGELOG section as the notes, the
  commits since the last release and `<domain>.zip`. **Donavan publishes
  the draft himself**, and publishing creates the tag. Never create or push
  tags, publish releases, or edit published releases.
- Publishing runs **Release check**: the tag must match the manifest version
  and the CHANGELOG, and be on `main`. If it fails, fix it with a new patch
  release, not by moving the tag.

### Commits

- Author commits as `Donavan Becker <donavan.becker@icloud.com>` (set
  `git config user.name` / `user.email` in the clone). HACS's
  default-repository check needs the submitter to have a fair share of the
  commits. Credit an AI assistant with a `Co-Authored-By:` trailer if you
  like, never as the author.
- Subject lines are plain, imperative and say what changed for the user
  ("Show Pico remotes as unavailable when Lutron isn't running"). No
  `fix:`/`feat:` prefixes. Release commits are "Release X.Y.Z".
- Commit and push only when asked.

### CI (shared workflows)

- `.github/workflows/` holds four thin callers of the reusable workflows in
  [bisman-automations/.github](https://github.com/bisman-automations/.github):
  `test.yml` (lint, frontend, pytest on the oldest supported and the newest
  Home Assistant, plus the upcoming one as information only), `validate.yml`
  (hassfest, HACS, standards), `draft-release.yml` and `release-check.yml`.
  Don't add jobs to the callers. Use the shared workflow's inputs, or change
  the shared workflow so every repo gets the improvement.
- `requirements_test.txt` leaves `pytest-homeassistant-custom-component`
  unpinned (newest Home Assistant) and pins ruff (and mypy where used).
  `requirements_test_min.txt` pins the harness release for the minimum in
  `hacs.json`: the newest harness release of that month. CI fails if they
  drift apart.
- Raising the minimum Home Assistant means changing, together: `hacs.json`,
  `requirements_test_min.txt`, `python-min` in `test.yml` (3.13 up to
  2026.2, 3.14 from 2026.3), the README's Home Assistant badge, and a
  CHANGELOG entry.
- Home Assistant 2026.3 and newer need Python 3.14 (3.14.2+).
- The published hassfest rejects `categories` in `manifest.json` and Pillow
  in `requirements`. Leave both out.
- Dependabot (`.github/dependabot.yml`) proposes action and pinned-tool
  updates weekly. It ignores the test harness on purpose.
- Test and Validate also run every Thursday, the day after Home Assistant's
  betas come out. The **next** test job (newest pre-release harness) is
  information only: when it fails, fix the cause before that release ships.

### Before saying work is done

Run from the repo root, in a Python 3.14 venv with `requirements_test.txt`
installed:

```
ruff check .
ruff format --check .
pytest
```

Also run `node --test tests/*.mjs` when the repo has Node tests, mypy when
the repo-specific notes list it, and the tests in a second venv with
`requirements_test_min.txt` when a change could behave differently on the
oldest supported Home Assistant. The standards check (also in CI) is
`python <path to bisman-automations/.github>/scripts/check_repo.py .`.

### Code and docs

- Support every Home Assistant from the `hacs.json` minimum to the newest
  release. Check for deprecations in the newest release and the beta.
- Keep entity IDs, unique IDs, service names and event names stable. When
  one has to change, migrate it and say so under Changed in the CHANGELOG.
- `strings.json` and `translations/en.json` hold the same keys. Home
  Assistant reads only `translations/`, so change both in the same commit.
- Never put personal details in the repo (README, CHANGELOG, code, tests,
  screenshots): no names, addresses, account or device identifiers, or real
  entity IDs. Use placeholders such as `light.living_room`.
- `README.md` is user documentation with the standard header (badges, HACS
  and "add integration" My buttons) and sections Installation, Setup, ...,
  Troubleshooting, Development, License. Use plain language and show names,
  not entity IDs, wherever the UI does.
<!-- END bisman-automations shared rules -->
