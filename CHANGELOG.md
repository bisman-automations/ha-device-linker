# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.2.0]: https://github.com/bisman-automations/ha-device-linker/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/bisman-automations/ha-device-linker/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bisman-automations/ha-device-linker/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bisman-automations/ha-device-linker/releases/tag/v1.0.0
