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

The main device stays as it is. You can change the MAC address or the linked devices. Removing a device from the selection unlinks it. Clearing the selection unlinks all of them. Deleting the entry unlinks every device on it. Device Linker removes a MAC connection only when it was the integration that added it, so an address another integration registered is left alone.

A device you remove, and every device on an entry you delete, is remembered. Device Linker will not link that pair again, including after a restart. The **Reset dismissed and unlinked** action forgets those pairs and any suggestion you ignored, then looks again.

An entry created by an older version of Device Linker can still be opened and saved. Saving stores it in the current shape. Those entries stay marked as ones you created.

## How auto-linking works

Once Device Linker is set up, it watches the device registry. A strong match is linked for you. You do not have to confirm it. The result is a normal Device Linker entry, with the same title as one you would create by hand: **Link Living Room TV to 2 devices**.

The anchor is the device that already has the MAC address, for example the UniFi client. Every other device in the match gets that address. Devices that share one anchor and one MAC are grouped onto one entry rather than one entry per pair. At most 10 devices are added automatically; any further matches stay as suggestions.

Matches are checked in this order:

1. **MAC address.** The strongest match, and it is linked automatically. Device Linker compares MAC addresses on every device, from every integration, whether the address is stored as a connection or as an identifier. A UniFi client and the Shelly, ESPHome, or TV device for the same hardware are linked even when they are in different areas. Bluetooth addresses are included, and a Zigbee address that was made by extending a MAC (with `fffe` in the middle) matches that MAC.
2. **Other hardware identifiers.** A shared Zigbee address or serial number is next, also across areas. It is linked automatically when Device Linker can turn it into one MAC address and one of the devices already has that address as a connection. Otherwise it stays a suggestion and asks you for the address.
3. **Names.** This is the match that matters when only one of the devices has a MAC. Names are compared without regard to case or punctuation. The area name, words such as light, switch, sensor, and plug, and common brand names (including UniFi, Apple, and Sonos) are set aside as whole words for the fuzzy score. An area called Room does not change a device named bedroom. Those words are not set aside for an exact name, so two devices that are both called the same thing still match when those words were the whole name.

   An exact name of more than one word is linked automatically. **Living Room TV** qualifies. A device named only **TV** does not, when either device has no area: that pair stays a suggestion, so an unassigned UniFi client called TV is not linked to every TV in the house. A fuzzy name is linked automatically only when both devices are in the same area, they share at least two distinctive words, and the score reaches **Automatic name similarity** (default **0.95**). **North window** and **North porch** share one word, so they stay a suggestion even when the score is high. Between the suggestion minimum (default **0.8**) and that automatic threshold, a name stays a suggestion.

A missing area does not block a name match. UniFi often leaves a client unassigned while the Apple TV, Sonos, or Shelly integration puts the same device in a room. Those two still match when the full name matches. So do two devices that both have no area. Two devices that are each assigned to a different area are not matched on name alone.

Devices in the same area are still suggested when neither has a MAC address. Other name pairs are compared when at least one of them has a MAC. That keeps a large list of devices without an address from being compared with itself, and it keeps a ceiling light and a ceiling in the same room visible as a suggestion.

A name is linked automatically only when exactly one of the devices has exactly one network MAC that Device Linker did not copy onto it. That device is the anchor. After **Living Room TV** is linked, the Apple TV has the UniFi address because Device Linker added it. A later device with the same name is added to the UniFi entry. The Apple TV does not start a second link. If both devices already have different addresses, or neither has one, Device Linker asks you instead of guessing.

**UniFi example.** UniFi Network registers **Living Room TV** with a MAC connection and no area. The Apple TV integration registers **Living Room TV** in the Living Room and does not have a MAC. HomeKit or Shelly might register the same name again. Device Linker creates one entry, **Link Living Room TV to N devices**, anchored on the UniFi device, and copies the UniFi MAC onto the others. Home Assistant then shows them under **Linked devices**. The **Suggestions** sensor can still read 0, because there is nothing left to confirm. The new entry and the repair are the result.

A device is not linked or suggested when it is disabled, when it belongs to the same config entry (Home Assistant cannot put one MAC on two of that entry's devices), when the two devices already share a MAC connection, when it is Device Linker's own service device, or when you have already ignored or unlinked that pair. An existing link does not hide a different device: a third **Living Room TV** is added to the same entry.

Weaker matches still appear under **Settings > Devices & services** as a discovered integration: **Link X with Y?** Confirming it adds the MAC connection. **Ignore** dismisses that pair and does not offer it again, including after a restart.

Turn **Link strong matches automatically** off under **Configure** to go back to confirm-each-one. The area rule above still applies.

The **Suggestions** sensor on the Device Linker device shows how many suggestions are still waiting. Its attributes list those suggestions (devices, reason, score, and whether the name was exact), the automatic entries, and the time of the last scan. **Download diagnostics** on the integration shows the same, and whether the entry was automatic or manual. MAC addresses are left out of both. The count stays at 0 when everything strong was linked, and Home Assistant does not move "last changed" while the count stays 0. The **last scan** attribute is the time to look at.

**Configure** on any Device Linker entry sets both similarity minimums (from 0.5 to 1) and can turn hardware matching, name matching, or automatic linking off. Those settings apply to every entry. **Rescan suggestions** looks again immediately. Registry updates are batched for a couple of seconds so a large UniFi refresh is scanned once. Device Linker also looks again after Home Assistant has finished starting, because other integrations may still be registering devices when Device Linker itself starts.

## Troubleshooting

- **A device is not listed.** It has to be registered by another integration, and it has to belong to a different config entry than the main device and the other devices you selected. A device with no config entry is not offered.
- **The devices are still separate.** Device Linker only adds a registry connection. Home Assistant shows **Linked devices** for devices that share one. It does not combine their entities, states, or controls.
- **The MAC is already in use.** The confirm screen names the device that has it. Two devices from the same config entry cannot share a MAC address. Pick another address, or link a device from a different entry.
- **The Suggestions sensor stays at 0.** That is expected after automatic linking: the matches became Device Linker entries, and a repair lists them. Check **Settings > Devices & services** for entries named **Link … to N devices**, and the **last scan** attribute on the sensor. A UniFi client with no area is included. Two devices assigned to different areas are not matched on name alone.
- **A device was linked and you do not want it.** Open that entry, choose **Configure**, and remove the device, or delete the entry. It will not be linked again. Run **Reset dismissed and unlinked** only when you want those pairs to be eligible again.
- **A suggestion came back, or never appeared.** Ignored pairs and unlinked pairs stay that way until you reset them. Raise or lower the two similarity sliders under **Configure**, or turn the hardware, name, and automatic switches on. Then run **Rescan suggestions**. Devices that already share a MAC, or that sit on the same integration entry, are not suggested.
- **Diagnostics.** On the integration's page, open the menu and choose **Download diagnostics**. The file lists suggestions and how many connections this entry added. The MAC address is removed. Review device names before you share the file.
- **Debug logs.** On the integration's page choose **Enable debug logging**, reproduce the problem, then **Disable debug logging** to download the log. Or add this to `configuration.yaml` and restart:

  ```yaml
  logger:
    logs:
      custom_components.device_linker: debug
  ```

- Report problems at [GitHub Issues](https://github.com/bisman-automations/ha-device-linker/issues). Check a log before you attach it.

## Development

Tests cover MAC address checking on its own, and the integration running inside Home Assistant: the config and options flows, entries saved by older versions, adding, keeping, and removing the registry connection, and the UniFi case where one device has a MAC and no area and another integration has the same name.

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
