"""Encode the approved icon artwork into a multi-resolution Windows ICO."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT/'assets/branding'
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main():
    artwork = Image.open(ASSETS/'MaaBanG.png').convert('RGBA')
    frames = [artwork.resize((size, size), Image.Resampling.LANCZOS)
              for size in SIZES]
    frames[-1].save(ASSETS/'MaaBanG.ico', sizes=[(s,s) for s in SIZES],
                    append_images=frames[:-1])
    icon = Image.open(ASSETS/'MaaBanG.ico')
    assert icon.ico.sizes() == {(s,s) for s in SIZES}
    print('ICO sizes:', sorted(icon.ico.sizes()))


if __name__ == '__main__':
    main()
