"""Draw aiGo's icon (a white map pin on a blue tile) and save it as a multi-size .ico."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # draw large, then downsample for smooth edges
BLUE = (47, 111, 236, 255)
WHITE = (255, 255, 255, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=BLUE)
    cx, cy, r = S // 2, 430, 230  # pin head
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    d.polygon([(cx - 200, cy + 120), (cx + 200, cy + 120), (cx, 870)], fill=WHITE)  # pin tail
    hole = 95
    d.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=BLUE)
    return img


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "packaging/aigo.ico")
    big = draw()
    big.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
    print("wrote", out)
