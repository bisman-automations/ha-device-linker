<p align="center">
  <img src="https://raw.githubusercontent.com/bisman-automations/ha-device-linker/main/custom_components/device_linker/brand/icon.png" alt="Device Linker icon" width="128">
</p>

# Device Linker

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8%2B-blue.svg)](https://www.home-assistant.io/)
[![GitHub Release](https://img.shields.io/github/v/release/bisman-automations/ha-device-linker)](https://github.com/bisman-automations/ha-device-linker/releases)
[![Test](https://github.com/bisman-automations/ha-device-linker/actions/workflows/test.yml/badge.svg)](https://github.com/bisman-automations/ha-device-linker/actions/workflows/test.yml)
[![Validate](https://github.com/bisman-automations/ha-device-linker/actions/workflows/validate.yml/badge.svg)](https://github.com/bisman-automations/ha-device-linker/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/bisman-automations/ha-device-linker)](LICENSE)

Device Linker adds a shared MAC address connection to devices that are already in Home Assistant, so Home Assistant can show them together under **Linked devices**. It links registry entries. It does not merge devices, and it does not copy or synchronize their entities.

One device is the main device: the one that already has the MAC address, or a MAC you type in. Every other device you pick gets that same MAC address, and Home Assistant treats the shared connection as a link.

## Requirements

- Home Assistant 2026.8 or newer.
- The main device and each linked device must already be registered, and they must belong to different config entries. Home Assistant allows a connection on only one device inside a single config entry.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-device-linker&category=integration)

1. Select the button above, or in HACS open the menu, choose **Custom repositories**, and add `https://github.com/bisman-automations/ha-device-linker` as an **Integration**.
2. Download **Device Linker** and restart Home Assistant.

Requires **Home Assistant 2026.8 or newer**.

### Manual

1. Download `device_linker.zip` from the [latest release](https://github.com/bisman-automations/ha-device-linker/releases/latest).
2. Unzip it into `config/custom_components/device_linker/` and restart Home Assistant.

The zip is the integration folder itself (`manifest.json` is at the top of the archive), which is the same layout HACS uses.

## Setup

[![Open your Home Assistant instance and start setting up Device Linker.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=device_linker)

Select the button above, or go to **Settings > Devices & services > Add integration** and search for **Device Linker**.

1. Choose the main device, the one that already has the MAC address. Each device is listed with its integration, and any MAC addresses already registered on it. Or choose **Manual MAC entry** when the address is not registered on a device yet.
2. Select one or more devices to link. You can pick several. Each must come from a different config entry than the main device and than each other.
3. Confirm the MAC address. When the main device has registered addresses, they are listed and a single address is preselected. You can still type a different address. Colons and hyphens both work (`AA:BB:CC:DD:EE:FF` or `AA-BB-CC-DD-EE-FF`).

The confirmation screen lists the selected device names. If the address is already in use, the error names the device that has it. Choose a different MAC or a different device.

## Editing and unlinking

Open the Device Linker entry under **Settings > Devices & services**, then choose **Configure**.

The main device stays as it is. You can change the MAC address or the linked devices. Removing a device from the selection unlinks it. Clearing the selection unlinks all of them. Device Linker removes a MAC connection only when it was the integration that added it, so an address another integration registered is left alone.

An entry created by an older version of Device Linker can still be opened and saved. Saving stores it in the current shape.

## How suggestions work

Once Device Linker is set up, it watches the device registry and suggests other devices that look like the same hardware.

Suggestions are checked in this order:

1. **MAC address.** The strongest match. Device Linker compares MAC addresses on every device, from every integration, whether the address is stored as a connection or as an identifier. A UniFi client and the Shelly, ESPHome, or TV device for the same hardware are suggested even when they are in different areas. The suggestion names both areas when they differ. Bluetooth addresses are included, and a Zigbee address that was made by extending a MAC (with `fffe` in the middle) matches that MAC.
2. **Other hardware identifiers.** A shared Zigbee address or serial number is next. It is also allowed across areas.
3. **Similar names in the same area.** This is the fallback. Names are compared without regard to case or punctuation. The area name, words such as light, switch, sensor, and plug, and common brand names are set aside, and the remaining words are scored. The default minimum score is **0.8**. Devices in different areas are not matched on name alone.

A device is not suggested when it is disabled, when it belongs to the same config entry (Home Assistant cannot put one MAC on two of that entry's devices), when the two devices already share a MAC connection, or when you have already ignored that pair.

Each suggestion appears under **Settings > Devices & services** as a discovered integration: **Link X with Y?** Confirming it adds the MAC connection. **Ignore** dismisses that pair and does not offer it again, including after a restart.

The **Suggestions** sensor on the Device Linker device shows how many suggestions are waiting. Its attributes, and **Download diagnostics** on the integration, list the devices, the reason (`mac`, `identifier`, or `name`), and the score. MAC addresses are left out of both.

**Configure** on any Device Linker entry sets the name-similarity minimum (from 0.5 to 1) and can turn hardware matching or name matching off. Those settings apply to every entry. The **Rescan suggestions** action looks again immediately instead of waiting for the next registry update. Registry updates are batched for a couple of seconds so a large UniFi refresh is scanned once.

## Troubleshooting

- **A device is not listed.** It has to be registered by another integration, and it has to belong to a different config entry than the main device and the other devices you selected. A device with no config entry is not offered.
- **The devices are still separate.** Device Linker only adds a registry connection. Home Assistant shows **Linked devices** for devices that share one. It does not combine their entities, states, or controls.
- **The MAC is already in use.** The confirm screen names the device that has it. Two devices from the same config entry cannot share a MAC address. Pick another address, or link a device from a different entry.
- **A suggestion came back, or never appeared.** Ignored pairs stay ignored. Raise or lower **Name similarity** under **Configure**, or turn the hardware and name switches on. Then run **Rescan suggestions**. Devices that already share a MAC, or that sit on the same integration entry, are not suggested.
- **Diagnostics.** On the integration's page, open the menu and choose **Download diagnostics**. The file lists suggestions and how many connections this entry added. The MAC address is removed. Review device names before you share the file.
- **Debug logs.** On the integration's page choose **Enable debug logging**, reproduce the problem, then **Disable debug logging** to download the log. Or add this to `configuration.yaml` and restart:

  ```yaml
  logger:
    logs:
      custom_components.device_linker: debug
  ```

- Report problems at [GitHub Issues](https://github.com/bisman-automations/ha-device-linker/issues). Check a log before you attach it.

## Development

Tests cover MAC address checking on its own, and the integration running inside Home Assistant: the config and options flows, entries saved by older versions, and adding, keeping, and removing the registry connection.

The dark brand icons are rebuilt from the light icons with `python scripts/make_brand.py` (install Pillow first; it is not a dependency of the integration).

Tests run inside a real Home Assistant through [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component). You need Python 3.14.

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

To test against the oldest supported Home Assistant (2026.8), use Python 3.14 and `pip install -r requirements_test_min.txt` instead.

CI runs the same checks on every push and pull request, on both Home Assistant versions, plus hassfest, HACS validation, and the shared repo standards, using the workflows in [bisman-automations/.github](https://github.com/bisman-automations/.github). Releases are drafted from [CHANGELOG.md](CHANGELOG.md); see [CLAUDE.md](CLAUDE.md).

## License

MIT. See [LICENSE](LICENSE).
