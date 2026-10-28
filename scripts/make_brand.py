"""Build the dark-theme brand images from the light icons.

The light icons are the chain artwork on white. Home Assistant uses
``dark_icon.png`` (and ``dark_logo.png``) on a dark background, where that
white square would show as a tile. This knocks the paper-white background out
to transparency and keeps the chain and its cyan glow.

The light ``icon.png`` / ``logo.png`` files (and their ``@2x`` copies) are the
source artwork. This script does not redraw them.

    pip install pillow
    python scripts/make_brand.py

Pillow stays out of the integration's requirements: the published hassfest
rejects it there, and Home Assistant already ships Pillow.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components" / "device_linker" / "brand"

# Paper is nearly white and nearly gray. The chain's glow is cyan, so it keeps
# chroma (a lower red channel) as it brightens and is left alone. The outer
# fringe fades instead of ending in a hard white rim.
_SOLID = 6
_FADE = 28
_CHROMA = 16


def _alpha(red: int, green: int, blue: int) -> int:
    """Return 0 for paper-white, 255 for the artwork, and a fade on the rim."""
    distance = (255 - red) + (255 - green) + (255 - blue)
    chroma = max(red, green, blue) - min(red, green, blue)
    if distance <= _SOLID and chroma <= 10:
        return 0
    if distance <= _FADE and chroma <= _CHROMA:
        return int(255 * (distance - _SOLID) / (_FADE - _SOLID))
    return 255


def knockout(source: Path, dest: Path) -> None:
    """Write a transparent-background copy of a light brand image."""
    image = Image.open(source).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            pixels[x, y] = (red, green, blue, min(alpha, _alpha(red, green, blue)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)


def main() -> None:
    """Rebuild every dark brand image from its light source."""
    pairs = (
        ("icon.png", "dark_icon.png"),
        ("icon@2x.png", "dark_icon@2x.png"),
        ("logo.png", "dark_logo.png"),
        ("logo@2x.png", "dark_logo@2x.png"),
    )
    for source_name, dest_name in pairs:
        source = BRAND / source_name
        dest = BRAND / dest_name
        knockout(source, dest)
        print("wrote", dest)


if __name__ == "__main__":
    main()
