# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-10-30

### Added

- **Link similar names automatically** under **Configure** (on by default). Names that are the same device with an extra word are linked without asking, even when one side has no area: Front Driveway and Front Driveway Camera, Deck and G4 Deck Camera, Mamaroo and 4moms Mamaroo. The extra words must be device words like camera or light, numbers, or model codes. Names that differ by a brand or a place, like Living Room TV and Living Room Apple TV, or Garage Door and Garage Camera, still ask first.

### Changed

- Device Linker is now one entry with every link listed under it, instead of one entry per link. Add a link with **Add link** on the Device Linker entry, change one with **Edit link**, and delete one from its menu. Automatic links and confirmed suggestions appear in the same list.
- Your existing links move under the one entry the first time Home Assistant starts with 2.0.0. Devices, MAC addresses and titles are kept, and nothing is unlinked. The separate entries disappear. Going back to 1.x after this needs the links set up again.
- **Configure** is on the Device Linker entry and holds only the matching settings. Changing a link's devices or MAC address is now **Edit link** on that link.
- Confirming a suggestion adds a link under the Device Linker entry instead of creating a new entry.
- **Install from the Marketplace.** The README now explains how to install from the Marketplace that replaces HACS in Home Assistant 2026.11, with its own button. The HACS steps stay for Home Assistant 2026.10 and older, and an install you already have moves over by itself.

## [1.3.0] - 2026-10-28

### Added

- Strong matches are linked automatically. A shared MAC address or hardware identifier, and an exact device name of more than one word, become a normal Device Linker entry (`Link <device> to N devices`) instead of a discovered integration you have to confirm. Several devices that share one anchor are grouped onto that one entry. A fuzzy name is linked when both devices are in the same area, they share at least two distinctive words, and the score reaches the automatic threshold. A single shared word stays a suggestion.
- **Configure** has **Link strong matches automatically** (on by default) and **Automatic name similarity** (default 0.95, from 0.5 to 1). Name similarity for suggestions is still there. Both sliders apply to every entry.
- Each entry records whether it was created automatically or by hand. Automatic entries show up in the **Suggestions** sensor and in diagnostics.
- A repair lists the devices that were linked automatically.
- **Reset dismissed and unlinked** forgets ignored suggestions and pairs you separated, then looks again.
- The **Suggestions** sensor records when the last scan finished, including when the count stays at 0.

### Changed

- Name matching no longer requires both devices to be in the same area. A device with no area, which is common for a UniFi client, can match a device in a room or another device that also has no area. Two devices that are each assigned to a different area are still not matched on name alone. Automatic linking is stricter: a missing area is linked only when the full name matches and the name is more than one word, so an unassigned device called TV is not linked to every TV.
- An exact name always matches, including when the name is only an area plus a generic word such as "light".
- Area names are ignored as whole words. An area called Room does not change a device named bedroom.
- Devices in the same area are still suggested when neither has a MAC address. Other name pairs are compared when at least one of them has a MAC, so a large list of devices without an address is not compared with itself.

### Fixed

- Suggestions stayed at 0 when the devices that should be linked were the same name and only one of them had a MAC, because UniFi devices often have no area and name matching skipped any device without one.
- A MAC address Device Linker copied onto a device is not used as the anchor for a later match. The device that already had the address stays the anchor.
- The integration scans again after Home Assistant has finished starting, so devices registered while other integrations are still setting up are included. Device registry creates, updates, and removals still schedule one scan after a short delay.

## [1.2.1] - 2026-10-28

### Fixed

- hassfest accepts the integration manifest again. The minimum Home Assistant version stays 2026.8, recorded in hacs.json.

## [1.2.0] - 2026-10-28

### Added

- Device Linker can suggest devices to link. A shared MAC address or hardware identifier (a UniFi client and the Shelly, ESPHome, or TV device for the same hardware, including a Zigbee address or serial number) is suggested first, even when the devices are in different areas. The suggestion says so when the areas differ.
- Devices in the same area with similar names are suggested when they are not a hardware match. Generic words such as light, switch, sensor, and plug, the area name, and common brand names are ignored. Name similarity defaults to 0.8 and can be changed under **Configure**.
- Suggestions show up as discovered integrations (**Link X with Y?**). Confirm adds the MAC connection. **Ignore** dismisses that pair permanently.
- A **Suggestions** sensor and diagnostics list each suggestion with its reason (MAC address, hardware identifier, or name) and score. The **Rescan suggestions** action looks again.
- **Configure** can turn MAC and identifier matching, and name matching, on or off.

### Changed

- The README follows the same layout as the other bisman-automations integrations: the icon, the badges, a button that opens this repository in HACS, a button that starts setup, and troubleshooting steps for diagnostics and debug logs.
- Manual installation matches the release archive. Unzip `device_linker.zip` into `config/custom_components/device_linker/`.
- The integration icon has a dark-theme version, so the chain stays visible on a dark background.
- The oldest Home Assistant this integration tests is 2026.8.3, the newest 2026.8 release, matching the Home Assistant 2026.8 minimum.

## [1.1.1] - 2026-10-28

### Fixed

- Fix the Configure flow crashing while building device options.
- Show selected device names on the MAC confirmation and edit screens.
- Name the device that already owns a conflicting MAC address.
- Include integration brand images in the release archive.

## [1.1.0] - 2026-10-28

### Added

- Link multiple device registry entries to one main device and shared MAC address.
- Edit the MAC address and linked device selection from the integration options.
- Unlink deselected devices while preserving MAC connections Device Linker did not add.
- Keep existing 1.0.0 entries editable through a backward-compatible settings reader.

## [1.0.0] - 2026-10-27

### Added

- Initial HACS release.
- Link two existing Home Assistant devices by adding their shared MAC connection.
- Select a target device, then choose a peer device or manual entry.
- Offer a selected peer’s registered MAC address for confirmation, with manual entry as a fallback.
- Restore the link when the owning integration refreshes the device registry entry.
- Preserve links during config-entry unloads and remove links when the Device Linker entry is deleted.

[2.0.0]: https://github.com/bisman-automations/ha-device-linker/compare/v1.3.0...v2.0.0
[1.3.0]: https://github.com/bisman-automations/ha-device-linker/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/bisman-automations/ha-device-linker/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/bisman-automations/ha-device-linker/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/bisman-automations/ha-device-linker/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bisman-automations/ha-device-linker/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bisman-automations/ha-device-linker/releases/tag/v1.0.0
