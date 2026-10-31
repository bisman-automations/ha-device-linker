# Device Linker: notes for Claude and other coding agents

Device Linker is a HACS custom integration (`custom_components/device_linker`, domain `device_linker`). It adds one shared MAC address connection to devices that are already in Home Assistant, so Home Assistant can show them together under **Linked devices**. It links registry entries. It does not merge devices or copy their entities.

Since 2.0.0 there is one **hub** config entry (`data: {"hub": true}`, unique id `device_linker`), and each link is a config subentry of type `link` under it. It also links devices that look like the same hardware: a shared MAC or hardware identifier first (across areas), then the same or a similar name. A missing area is compatible. Strong matches are linked automatically. Read this before changing anything.

## Layout

| Path | What it is |
| --- | --- |
| `__init__.py` | Setup, unload, removal, and migration. The hub creates `LinkRuntime` (`entry.runtime_data`) and the suggestion manager. `LinkRuntime.async_reconcile` runs from the hub's update listener (fired by subentry add, update and remove) and compares the subentries with the last applied snapshot in the `device_linker.links` Store: a deleted link or a dropped device has its owned connections removed and its pairs remembered, and every link's MAC is put back. A device-registry listener restores a connection a refresh dropped. Unload stops the listeners and leaves the connections in place. Removing the hub unlinks every link. `async_migrate_entry` (to version 3) turns the first old link entry into the hub and adds every other one as a subentry; those become `{merged_into: hub_id}` and remove themselves on setup. Ignored discoveries are config entries with no link; setup returns immediately. |
| `config_flow.py` | `DeviceLinkerConfigFlow` (version 3): the user step creates the hub once; the discovery steps (`discovery_mac`, `discovery_identifier`, `discovery_name`) add a link to the hub and abort `linked`; the ignore step. `LinkSubentryFlow`: add link (main device, linked devices, MAC) and reconfigure (edit link). `DeviceLinkerOptionsFlow`: the suggestion settings, on the hub only. `MANUAL_ENTRY` is the manual-MAC path and must stay available. |
| `matching.py` | Pure suggestion engine. No Home Assistant imports. Name normalization, hardware keys, ranking, and grouping automatic links. |
| `manager.py` | Loads preferences, debounces registry updates and the started event, links strong matches, publishes discovery flows for the rest, raises the repair, and owns the sensor. |
| `sensor.py` | Diagnostic **Suggestions** sensor. |
| `diagnostics.py` | Config-entry diagnostics. MAC addresses are redacted. |
| `registry.py` | `get_device`, `link_settings` / `get_entry_settings`, and the hub helpers `is_hub`, `hub_entry`, `hub_links`, `iter_links`. No flow logic. |
| `const.py` | Domain, storage keys, and `MAC_PATTERN`. No Home Assistant imports. |
| `strings.json`, `translations/en.json` | The same keys. Home Assistant loads `translations/en.json`. |
| `services.yaml` | `rescan_suggestions` and `reset_dismissed`. |
| `brand/` | `icon.png` (256) and `icon@2x.png` (512), the same artwork as `logo.png` and `logo@2x.png`, plus dark variants. `python scripts/make_brand.py` rebuilds the dark variants from the light icons. |
| `scripts/make_brand.py` | Knocks the white background out of the light icons to make `dark_icon` and `dark_logo`. Needs Pillow, which is not a dependency of the integration (hassfest rejects Pillow in `requirements`). |
| `tests/test_mac.py`, `tests/test_matching.py` | Pure tests, loaded without the integration package. |
| `tests/ha/` | Config flow, options, legacy settings, the registry link, and suggestions, inside Home Assistant. |

Paths under `custom_components/device_linker/` unless noted.

## Conventions

- **Storage (links).** Each `link` subentry's data holds `main_device_id` (or null for manual MAC entry), `linked_device_ids`, `mac_address` (lowercase, colon-separated, from `format_mac`), `added_connections`: a map of linked device id to whether this entry added that MAC, and `origin` (`manual` or `auto`). Removal and unlinking delete a connection only when the map says this entry added it. Missing `origin` means manual.
- **Old entries are migrated.** Before 2.0.0 every link was its own config entry. `async_migrate_entry` moves them under the hub, through `link_settings`, which still reads both older shapes:
  - 1.0.0 stored one pair: `device_id` is the device that receives the MAC, `peer_device_id` is the device that already has it (the main device), and `added_connection` is the single ownership flag.
  - The earlier multi-select build stored `device_id` as the main device and `target_devices` as the linked ids. It did not record ownership, so `added_connections` is false for each of them and a later edit will not remove a MAC it cannot prove it added.
- **What a link shares.** `mac_address` holds the link's value. A MAC address (`registry.normalize_link_value` turns colon, hyphen or bare-hex forms into lower-case colons) becomes a `("mac", value)` connection. Anything else (a serial number, a VIN) becomes a `("device_linker", value)` identifier: Home Assistant links devices that share any identifier or connection. Something shaped like part of a MAC is refused. `registry.link_item` / `device_has_link` hide the difference; `_add_owned` / `_remove_owned` update whichever field it is. When the main device doesn't have the value (a shared ID, or a MAC typed by hand), the flow folds it into the linked devices and the link has no main device.
- **Already linked.** Two devices that share any identifier or connection are already linked (`registry.already_linked`, `matching._already_share_connection`). They are never suggested, and the add-link devices step refuses them with `already_linked`, naming them.
- **Only devices, never services.** A device with `entry_type` service, or any device of an integration whose `integration_type` is `service` (Music Assistant's players), is a service (`registry.is_service(hass, device, include_services=...)`) and is never a candidate unless the **Include services** setting (`include_services`, default off, stored with the suggestion settings) is on: `_device_views` and the add-link pickers skip it. Integrations' services (Daily Routine routines, Ford Assistant, TV Mgmt) link themselves to their device. On start, `_drop_services_from_automatic_links` takes services out of automatic links (removing a link whose main device is a service or that would be left empty); manual links are not touched.
- **Odd identifiers.** Integrations can register a number as an identifier value, or a tuple of more than two parts, and Home Assistant stores it. Always read identifiers and connections through `registry.text_pairs` (text pairs, extra parts joined with `:`), and keep `matching.normalize_mac` / `_add_hardware_value` tolerant of non-text values.
- **Cleanup.** The options flow is a menu: `settings` (the suggestion settings) and `cleanup`. `cleanup.find_leftovers` lists values no current link uses on that device: any `("device_linker", value)` identifier, and network MACs on devices in a remembered unlinked pair that another device still shares. Iterate the registry with the mapping-or-iterable pattern (never `.values()` or `hasattr`, which trips the 2027.9 deprecation). Removing an integration's own MAC is harmless; it re-registers it. Not offered: a value that is all the device has (Home Assistant raises "A device must have at least one of identifiers or connections"; ESPHome and UniFi devices have only their MAC), a MAC on a service device, or one only services share. `remove_leftovers` catches `HomeAssistantError` per value and returns `(removed, failed names)`; the step aborts `cleaned` or `cleaned_some`. Each refusal is logged as a warning, each removal at info; any other exception in finding or removing is logged with its traceback (`_LOGGER.exception`) and the step aborts `cleanup_failed`, never "Unknown error".
- **No devices for links.** Links have no device of their own; Device Linker's only device is the Suggestions service device. 2.2.0 to 2.4.0 gave each link a service device (`("device_linker", "link_<subentry id>")`), which cluttered Linked devices; `_remove_link_devices` removes those on every reconcile.
- **Already linked devices aren't offered.** `registry.linked_device_ids`: devices in a link, plus devices sharing an identifier or connection with another non-service device (a service linking itself to its device doesn't count). They are left out of the add-link pickers (editing a link keeps its own devices), and `config_flow._hidden_note` names them in the `{hidden}` placeholder of the user and devices steps (link title, or "linked by Home Assistant"; `HIDDEN_NAMES` = 5, then "and N more"). In suggestions, `manager._for_links` drops pairs where both are linked. A pair where one device is linked only by Home Assistant (it shares a network MAC with another device) is kept, shown and automatic, only when that device is the match's anchor on that MAC and is the group's best device by `preferred_mac_sources` (`_mac_holders`), once per new device and MAC, and the new device's config entry isn't already in the group; it becomes an ordinary link on the group's MAC. The add-link main picker excludes only `devices_in_links` (a Home-Assistant-linked device can share its MAC); the devices step still excludes `linked_device_ids`. Otherwise, a pair with one device in a link and the other unlinked becomes a **join** suggestion (`join_link_id`, `join_link_title`, `join_device_id`), once per device and link, skipped when the link already has a device from the new device's config entry. Discovery opens `discovery_join` for those (no fields; adds the device with `added_connections` true) and `_async_sync_flows` reopens a flow whose kind changed. Automatic linking only extends a link through its main device and MAC, as before. `flow_title` is `{name}`.
- **Never-suggest list.** `SuggestionSettings.ignored_devices` (stored as `ignored_devices`): those devices are left out of `_device_views` for suggestions and automatic links, not out of Add link. Every discovery form (and `discovery_join`) has an optional `never_suggest` multi-select of its devices; ticking any aborts `devices_ignored` without linking (`_async_never_suggest`, `manager.async_ignore_devices`, which rescans). The discovery MAC field is optional (`_mac_schema(required=False)`) so declining needs no address. The settings form edits the list with a multiple `SelectSelector` of `_ignorable_device_options` (the same devices as Add link: no services unless Include services is on; devices already listed stay so they can be removed). Home Assistant's `DeviceSelector` can't filter out services, so don't go back to it.
- **Preferred MAC sources and link titles.** `SuggestionSettings.preferred_mac_sources` (None until saved: `DEFAULT_PREFERRED_MAC_SOURCES`, network integrations such as unifi; read through `manager.preferred_mac_sources`) is an ordered list of integration domains, edited as a multiple `SelectSelector` with `custom_value` and `sort=False` (the frontend can't reorder; selection order is the priority). `matching.integration_rank` orders by it: `_name_anchor` picks the better-ranked side when both devices have one MAC, and `_rank_macs` orders and preselects MAC choices in discovery and confirm steps (Edit link orders but keeps the current value). Every link's title is `registry.link_title` "<name> (N Devices Linked)", N = every device; `registry.retitle_links` renames each link after its best-ranked device (else the main, else the first), dropping a connection tag like "(BLE)", on every reconcile and when the settings change.
- **Different brands never match by name.** `_name_score` returns 0 when both folded names have `_BRAND_TOKENS` and they don't overlap (Apple vs Google). A brand on only one side can still match.
- **Name scores.** Only the same name scores 1: equal folded names, or equal after removing area words and connection tags (`_core_name`, which also counts as exact); any other match is capped at `_BEST_FUZZY_SCORE` (0.99), so a threshold of 1 means the same name. Names whose device-type words (`_kinds`, `_GENERIC_TOKENS`, singular) both exist and don't overlap score 0. A brand on one name only caps the score at `_ONE_BRAND_SCORE` (0.9). `normalize_name` drops single-letter tokens (the "s" of "'s").
- **Leftover words in a name match.** In `_token_set_ratio`, one name inside the other scores as a full match only when the names share two or more words or the other name's leftovers are filler (`_filler_word`): "hub" inside "family hub" is not a match.
- **Shared IDs from names.** With `shared_id_links` (default on) and automatic linking on, `manager._async_link_shared_ids` links exact name matches (`auto_eligible`) where neither device `has_address` (no network MAC or Bluetooth connection) through `matching.shared_id_for(name)` (folded, connection tags dropped, hyphen-joined); a link already using that value takes the new devices. With automatic linking off, the discovery form prefills that ID (`_mac_schema(suggested_id=...)`).
- **Nearby addresses.** `matching._match_nearby_macs` (`match_nearby_macs`, default on) suggests devices whose Wi-Fi/Bluetooth addresses share the first five bytes and differ by 1 to `NEARBY_MAC_SPAN` (3) in the last, reason `nearby_mac` (`NEARBY_MAC_SCORE` 0.9), ranked below names and never auto-eligible. The anchor is the network-MAC device best ranked by preferred sources; a tie has no anchor. Discovery step `discovery_nearby_mac`.
- **Same-entry refusal.** `_check_devices` returns `same_config_entry` when any two chosen devices (main included) come from one config entry; `_same_entry_names` names them in the `{same_entry}` placeholder of the devices, join_devices and reconfigure forms.
- **Confirmed links.** Each link's data has `confirmed` (`CONF_CONFIRMED`); `link_settings` defaults it to origin manual. `link_payload(..., confirmed=None)` defaults the same way; automatic plans pass False for a new link and keep the existing value when extending; joins and accepted suggestions keep the link's value. Edit link has a `confirmed` boolean; while it stays ticked, dropping a device or changing the address errors `confirmed_locked`. `_drop_services_from_automatic_links` skips confirmed links. Home Assistant's own Delete on a link can't be blocked.
- **Recheck automatic links** (`recheck.py`, options step `recheck`, field `unlink`): for each unconfirmed automatic link, each linked device minus the value the link copied onto it is matched against the main device with `suggest` and `plan_auto_links` under the current settings; devices no plan links are listed. `unlink_mismatches` updates the subentry (or removes it when no linked device is left), and the reconcile removes owned values and remembers the pairs. `manager.device_views` is the shared view builder.
- **Settings changes rescan.** Saving the settings, Reset dismissed and Rescan all scan again, and so does every link added, changed or deleted (the hub's reconcile). A discovery confirm checks `manager.may_link` first and aborts `already_linked` when a device was linked since; turning **Include services** off also takes services out of automatic links straight away.
- **The flow.** User step: pick the main device, or **Manual MAC entry**, or (when a link exists) **Add devices to an existing link** (`JOIN_LINK`): the `join` step picks the link (`link_id`), `join_devices` adds unlinked devices from config entries not already in it (`added_connections` true) and aborts `joined`. `async_update_and_abort` can't take a custom reason on 2026.8, so it saves with `save_link` and aborts. Devices step: pick one or more devices to link. Confirm step: pick a MAC registered on the main device, or type one. Every MAC choice (confirm and discovery steps) is labelled with its source device and integration through `_mac_sources` (connections, plus identifiers that normalize to a MAC). Colons and hyphens are both accepted; the stored value is normalized.
- **Who can share a MAC.** Home Assistant requires a connection to be unique inside one config entry. The main device and every linked device must come from different config entries, and the linked devices must not share a config entry with each other. The confirm step names the device that already has the MAC.
- **Editing a link** lists every device in it, the main device included, and offers the MAC addresses those devices registered themselves (not ones this link copied), each labelled with its source device. The device that owns the chosen MAC becomes the main device (the old main wins a tie); none owning it makes the link manual (`main_device_id` null). Changing the selection unlinks what was removed and remembers those pairs. Clearing the selection unlinks every device. Only owned connections are removed. Deleting a link, or the hub, remembers every pair in it.
- **Unload keeps the link.** The listener is stopped and the MAC stays. Removal of the config entry is what deletes owned connections.
- **Skip composite devices.** `get_device` returns none when `is_composite_device` is set, so a synthetic device is not offered as a link target.
- **Suggestions and automatic links.** `matching.suggest` ranks MAC matches, then other hardware identifiers, then names. A missing area is compatible with any area; two different assigned areas are not a name match. Area names are removed as whole tokens (`area_tokens`), not substrings. Connection words (`_CONNECTION_TOKENS`: ble, cloud, matter, zigbee, wifi…) are removed too, so a name left with only one of them doesn't score 1 against every name containing it. An exact folded name scores 1 even when distinctive-word normalization is empty. Default suggestion threshold is 0.8 (`DEFAULT_SUGGESTION_THRESHOLD`). Automatic linking defaults on (`auto_link`). Hardware matches link automatically. Exact names link automatically, except a one-word exact name when either device has no area. Fuzzy names link only when both devices share an area, they share at least two distinctive tokens, and the score reaches `DEFAULT_AUTO_LINK_THRESHOLD` (0.95). One shared word stays a suggestion. With `auto_link_similar` (default on), a fuzzy name at the automatic threshold also links when `names_contained` holds: every word of the shorter name is in the longer one (`_SHORT_FORMS` such as cam/camera count) and the leftovers are filler (generic words, `_FILLER_TOKENS`, numbers, letter-and-digit model codes). This applies with a missing area too, never across two different areas. Keep the filler list narrow: a leftover brand or place word ("Apple", "Door") must keep the pair a suggestion. Fuzzy pairs where neither device has a network MAC are compared only inside one area. A MAC in `added_connections` (copied by this integration) is passed as `copied_macs` and is not an anchor. Both thresholds are number selectors, 0.5 to 1, step 0.05, stored for the whole integration in `device_linker.suggestions` (storage version 1; 1.3.0 keys default when an older store omits them) along with the signal switches, dismissed unique ids, unlinked pair keys, and announced pair keys. Dismissal and unlinking are permanent until `reset_dismissed`. One anchor and MAC become one link, capped at 10 devices. The anchor is the device that already had the single network MAC. Discovery flows for everything else are capped at 10; the sensor and diagnostics list up to 25. Scans run after setup, after `EVENT_HOMEASSISTANT_STARTED`, and on device create, update, and remove, debounced for 2 seconds. A device that already shares a network-MAC or Bluetooth connection is already linked and is not suggested. Devices from `device_linker` itself are not candidates.
- **Support Home Assistant 2026.8 and newer** (`hacs.json`). Do not lower that minimum without retesting device-registry connections on the older release. `requirements_test_min.txt` pins the newest pytest-homeassistant-custom-component release of 2026.8.
- `strings.json` and `translations/en.json` must be identical.
- `manifest.json` keys: `domain`, `name`, then alphabetical (hassfest). `issue_tracker` is the GitHub issues URL. Do not put `homeassistant` in the manifest: hassfest rejects that key. The minimum version lives in `hacs.json`.

## Checks

The shared checks at the end of this file. There is no mypy and there are no Node tests. Run pytest on the newest Home Assistant and, when registry behavior could differ, on `requirements_test_min.txt` as well. Both need Python 3.14.

- Treat any `Detected that custom integration 'device_linker' ...` warning as a bug to fix, even if tests pass.
- `tests/test_mac.py` and `tests/test_matching.py` cover the pure helpers. `tests/ha/` runs inside Home Assistant: the config and options flows, legacy entries, setup, unload, and removal, suggestion discovery, dismissal, debounce, and options, and automatic linking including the UniFi same-name fixture.

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
