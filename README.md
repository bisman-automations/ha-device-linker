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

Device Linker adds a shared MAC address connection (or, when the devices have no MAC in common, another shared ID such as a serial number or VIN) to devices that are already in Home Assistant, so Home Assistant can show them together under **Linked devices**. It links registry entries. It does not merge devices, and it does not copy or synchronize their entities.

One device is the main device: the one that already has the MAC address, or a MAC you type in. Every other device you pick gets that same MAC address, and Home Assistant treats the shared connection as a link.

## Requirements

- Home Assistant 2026.8 or newer.
- The main device and each linked device must already be registered, and they must belong to different config entries. Home Assistant allows a connection on only one device inside a single config entry.

## Installation

Requires **Home Assistant 2026.8 or newer**.

### Marketplace (Home Assistant 2026.11 and newer)

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=bisman-automations&repository=ha-device-linker&category=integration)

1. Select the button above, or go to **Settings > Marketplace**, choose **Add from link**, enter `bisman-automations/ha-device-linker` under **GitHub link**, and select **Add**. Adding a repository from a link needs a GitHub connection; the Marketplace asks you to connect one if you haven't.
2. Install **Device Linker**. If a repair asks you to restart Home Assistant, restart it.

New versions show up under **Settings > Updates**. Already installed Device Linker with HACS? There's nothing to do: Home Assistant moves it into the Marketplace the first time it starts on 2026.11.

### HACS (Home Assistant 2026.10 and older)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-device-linker&category=integration)

1. Select the button above, or in HACS open the menu, choose **Custom repositories**, and add `https://github.com/bisman-automations/ha-device-linker` as an **Integration**.
2. Download **Device Linker** and restart Home Assistant.

### Manual

1. Download `device_linker.zip` from the [latest release](https://github.com/bisman-automations/ha-device-linker/releases/latest).
2. Unzip it into `config/custom_components/device_linker/` and restart Home Assistant.

The zip is the integration folder itself (`manifest.json` is at the top of the archive), which is the same layout the Marketplace and HACS use.

## Setup

[![Open your Home Assistant instance and start setting up Device Linker.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=device_linker)

Select the button above, or go to **Settings > Devices & services > Add integration** and search for **Device Linker**. It is added once. Device Linker then starts looking for devices to link.

Each link is listed under the Device Linker entry, the way a group of devices is listed under one integration. Links Device Linker finds on its own appear there by themselves. To add one yourself, open **Device Linker** under **Settings > Devices & services** and choose **Add link**:

1. Choose the main device, the one that already has the MAC address. Each device is listed with its integration, and any MAC addresses already registered on it. Or choose **Manual MAC entry** when the address is not registered on a device yet. Devices already linked to another device aren't listed here or in the next step, whether Device Linker linked them or Home Assistant already shows them as linked. A note above the list names them, with the link each one is in (up to five, then how many more). To change a link, edit it.
2. Select one or more devices to link. You can pick several. Each must come from a different config entry than the main device and than each other. A device Home Assistant already shows as linked to the main device (because they share an identifier or connection, like the Ford integration and Ford Assistant sharing a VIN) is refused with its name, since there's nothing to add.
3. Confirm the MAC address. When the main device has registered addresses, they are listed and a single address is preselected. You can still type a different address. Colons and hyphens both work (`AA:BB:CC:DD:EE:FF` or `AA-BB-CC-DD-EE-FF`).

   No MAC address in common? Type another ID the devices share instead, such as a serial number or a VIN. Every device in the link gets it, the main device included, and Home Assistant shows them as linked. Something that looks like part of a MAC address (`AA:BB:CC`) is refused, so a typo doesn't become an ID by mistake.

The confirmation screen lists the selected device names. If the address is already in use, the error names the device that has it. Choose a different MAC or a different device.

To add devices to a link that's already there, choose **Add devices to an existing link** as the main device, pick the link, then the devices to add. They get the link's MAC address or ID. Devices already linked to another device, and devices from an integration already in that link, aren't listed.

**Upgrading from 1.x.** Each link used to be its own Device Linker entry. The first start after upgrading moves every one of them under a single Device Linker entry, keeping the devices, MAC addresses and titles. Nothing is unlinked along the way, and the old entries disappear.

## Editing and unlinking

Open **Device Linker** under **Settings > Devices & services**. Each link has its own menu.

- **Edit link** (the gear) lists every device in the link, including the main device the MAC address came from. The MAC address list shows each address next to the device it came from. Pick a different address and the device that has it becomes the main device; the others get that address instead. Removing a device from the list unlinks it. Removing the main device too, or typing an address none of them has, turns the link into a manual one.
- **Delete** unlinks every device on that link.

Device Linker removes a MAC connection only when it was the integration that added it, so an address another integration registered is left alone. Deleting the whole Device Linker entry unlinks every link.

A device you remove, and every device on a link you delete, is remembered. Device Linker will not link that pair again, including after a restart. The **Reset dismissed and unlinked** action forgets those pairs and any suggestion you ignored, then looks again.

## How auto-linking works

Once Device Linker is set up, it watches the device registry. A strong match is linked for you. You do not have to confirm it. The result is a normal link under the Device Linker entry, with the same kind of title as one you would add by hand: **Living Room TV (3 Devices Linked)**. Every link is named after its most trusted device (see **Preferred MAC address sources**), without a tag like "(BLE)", and counts every device in it. The title updates whenever devices are added or removed.

The anchor is the device that already has the MAC address, for example the UniFi client. Every other device in the match gets that address. Devices that share one anchor and one MAC are grouped onto one link rather than one link per pair. At most 10 devices are added automatically; any further matches stay as suggestions.

Matches are checked in this order:

1. **MAC address.** The strongest match, and it is linked automatically. Device Linker compares MAC addresses on every device, from every integration, whether the address is stored as a connection or as an identifier. A UniFi client and the Shelly, ESPHome, or TV device for the same hardware are linked even when they are in different areas. Bluetooth addresses are included, and a Zigbee address that was made by extending a MAC (with `fffe` in the middle) matches that MAC.
2. **Other hardware identifiers.** A shared Zigbee address or serial number is next, also across areas. It is linked automatically when Device Linker can turn it into one MAC address and one of the devices already has that address as a connection. Otherwise it stays a suggestion and asks you for the address.
3. **Names.** This is the match that matters when only one of the devices has a MAC. Names are compared without regard to case or punctuation. The area name, words such as light, switch, sensor, and plug, and common brand names (including UniFi, Apple, and Sonos) are set aside as whole words for the fuzzy score. An area called Room does not change a device named bedroom. Those words are not set aside for an exact name, so two devices that are both called the same thing still match when those words were the whole name.

   An exact name of more than one word is linked automatically. **Living Room TV** qualifies. A device named only **TV** does not, when either device has no area: that pair stays a suggestion, so an unassigned UniFi client called TV is not linked to every TV in the house. A fuzzy name is linked automatically only when both devices are in the same area, they share at least two distinctive words, and the score reaches **Automatic name similarity** (default **0.95**). **North window** and **North porch** share one word, so they stay a suggestion even when the score is high. Between the suggestion minimum (default **0.8**) and that automatic threshold, a name stays a suggestion.

   **Link similar names automatically** (on by default) goes one step further for names that are the same device with an extra word. When every word of the shorter name is in the longer one, and the words left over are only device words (camera, cam, light, sensor and the like), numbers or model codes such as G4, the pair is linked even if one side has no area. **Front Driveway** (UniFi) and **Front Driveway Camera** (UniFi Protect) are linked, and so are **Deck** and **G4 Deck Camera**, or **Mamaroo** and **4moms Mamaroo**. A leftover word that could mean a different device keeps the pair a suggestion: **Garage Door** and **Garage Camera**, or **Living Room TV** and **Living Room Apple TV**, still ask first. The score must still reach **Automatic name similarity**, and two devices that are each in a different area are never linked on name alone.

A missing area does not block a name match. UniFi often leaves a client unassigned while the Apple TV, Sonos, or Shelly integration puts the same device in a room. Those two still match when the full name matches. So do two devices that both have no area. Two devices that are each assigned to a different area are not matched on name alone.

Devices in the same area are still suggested when neither has a MAC address. Other name pairs are compared when at least one of them has a MAC. That keeps a large list of devices without an address from being compared with itself, and it keeps a ceiling light and a ceiling in the same room visible as a suggestion.

A name is linked automatically only when exactly one of the devices has exactly one network MAC that Device Linker did not copy onto it. That device is the anchor. After **Living Room TV** is linked, the Apple TV has the UniFi address because Device Linker added it. A later device with the same name is added to the UniFi link. The Apple TV does not start a second link. If both devices already have different addresses, or neither has one, Device Linker asks you instead of guessing.

**UniFi example.** UniFi Network registers **Living Room TV** with a MAC connection and no area. The Apple TV integration registers **Living Room TV** in the Living Room and does not have a MAC. HomeKit or Shelly might register the same name again. Device Linker adds one link, **Living Room TV (N Devices Linked)**, anchored on the UniFi device, and copies the UniFi MAC onto the others. Home Assistant then shows them under **Linked devices**. The **Suggestions** sensor can still read 0, because there is nothing left to confirm. The new link and the repair are the result.

A device is not linked or suggested when it is disabled, when it belongs to the same config entry (Home Assistant cannot put one MAC on two of that entry's devices), when the two devices already share a MAC connection, when it is a service rather than hardware (what an integration adds next to your devices, such as a Daily Routine routine, or any device of a service integration, such as Music Assistant's players; turn on **Include services** under **Matching settings** to link them too), or when you have already ignored or unlinked that pair. An existing link does not hide a different device: a third **Living Room TV** is added to the same link.

Weaker matches still appear under **Settings > Devices & services** as a discovered integration: **Link X with Y?** Confirming it adds the link under the Device Linker entry. **Ignore** dismisses that pair and does not offer it again, including after a restart. To stop suggesting a device altogether, tick it under **Don't link: never suggest these devices again** on the suggestion. It goes on **Devices never suggested** in **Matching settings**, where you can take it off again.

A device that isn't linked yet but matches a device already in a link is offered to join that link instead: **Add X to Y (N Devices Linked)?** Confirming it adds the device to that link, with the link's MAC address or ID. It's offered once per device and link, whichever device in the link it matched, and not when the link already has a device from the same integration. A device that's already linked to another device is never offered for a new link. Every new, changed or deleted link, and every change to the settings, updates the suggestions straight away.

Turn **Link strong matches automatically** off under **Configure** to go back to confirm-each-one. The area rule above still applies.

The **Suggestions** sensor on the Device Linker device shows how many suggestions are still waiting. Its attributes list those suggestions (devices, reason, score, and whether the name was exact), the automatic links, and the time of the last scan. **Download diagnostics** on the integration shows the same, every link, and whether each was automatic or added by hand. MAC addresses are left out of both. The count stays at 0 when everything strong was linked, and Home Assistant does not move "last changed" while the count stays 0. The **last scan** attribute is the time to look at.

**Preferred MAC address sources** (in **Matching settings**) lists the integrations you trust to report a device's real MAC address, most trusted first. Out of the box that's network integrations such as UniFi. When a link has a choice of addresses, the one from the first integration on the list is picked: in suggestions, in **Add link**, and when two devices in an automatic match each have an address. That device becomes the main device and names the link. The order is the order you add them; to move one, remove it and add it again.

**Configure** on the Device Linker entry has two choices. **Matching settings** sets both similarity minimums (from 0.5 to 1) and can turn hardware matching, name matching, automatic linking, or similar-name linking off. **Rescan suggestions** looks again immediately. Registry updates are batched for a couple of seconds so a large UniFi refresh is scanned once. Device Linker also looks again after Home Assistant has finished starting, because other integrations may still be registering devices when Device Linker itself starts.

## Cleaning up leftover addresses

Deleting a link takes its MAC address or shared ID off the devices Device Linker added it to. Links made by versions before 1.3.0 didn't record which devices those were, so deleting one of them could leave the address behind, and Home Assistant keeps showing those devices as linked.

Choose **Configure** on the Device Linker entry, then **Clean up leftover addresses**. It lists every address that's still on a device but that no current link uses there, each with the device's integration and the other devices that share it. Tick the ones to remove and submit. If you remove a MAC address the device's own integration registered (UniFi registers its clients' MACs, for example), that integration adds it back on its next refresh, so a mistake does no harm.

## Troubleshooting

- **Devices still show as linked after deleting their link.** Use **Clean up leftover addresses** (see above).

- **A device is not listed.** It has to be registered by another integration, and it has to belong to a different config entry than the main device and the other devices you selected. A device with no config entry is not offered.
- **Two devices that are already linked were suggested.** Home Assistant links devices that share any identifier or connection. Device Linker no longer suggests those pairs; run **Rescan suggestions** and ignore any leftover discovery.
- **The devices are still separate.** Device Linker only adds a registry connection (or a shared ID). Home Assistant shows **Linked devices** for devices that share one. It does not combine their entities, states, or controls.
- **The MAC is already in use.** The confirm screen names the device that has it. Two devices from the same config entry cannot share a MAC address. Pick another address, or link a device from a different entry.
- **The Suggestions sensor stays at 0.** That is expected after automatic linking: the matches became links, and a repair lists them. Open **Device Linker** under **Settings > Devices & services** and look for links named **… (N Devices Linked)**, and the **last scan** attribute on the sensor. A UniFi client with no area is included. Two devices assigned to different areas are not matched on name alone.
- **A device was linked and you do not want it.** Open **Device Linker**, choose **Edit link** on that link and remove the device, or delete the link. It will not be linked again. Run **Reset dismissed and unlinked** only when you want those pairs to be eligible again.
- **Two different devices were linked because their names were alike.** Delete that link, then turn **Link similar names automatically** off under **Configure** if it keeps happening. Please open an issue with the two names so the rule can be tightened.
- **A suggestion came back, or never appeared.** Ignored pairs and unlinked pairs stay that way until you reset them. Raise or lower the two similarity sliders under **Configure**, or turn the hardware, name, and automatic switches on. Then run **Rescan suggestions**. Devices that already share a MAC, or that sit on the same integration entry, are not suggested.
- **Diagnostics.** On the integration's page, open the menu and choose **Download diagnostics**. The file lists suggestions, every link, and how many connections each link added. The MAC address is removed. Review device names before you share the file.
- **Debug logs.** On the integration's page choose **Enable debug logging**, reproduce the problem, then **Disable debug logging** to download the log. Or add this to `configuration.yaml` and restart:

  ```yaml
  logger:
    logs:
      custom_components.device_linker: debug
  ```

- Report problems at [GitHub Issues](https://github.com/bisman-automations/ha-device-linker/issues). Check a log before you attach it.

## Development

Tests cover MAC address checking on its own, and the integration running inside Home Assistant: the setup, link and options flows, moving 1.x entries under one entry, adding, keeping, and removing the registry connection, and the UniFi case where one device has a MAC and no area and another integration has the same name.

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
