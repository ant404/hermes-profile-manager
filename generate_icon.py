"""Generate app icon for Hermes Profile Manager.

Draws a cute "H" monogram in a rounded gradient square - no external assets needed.
Outputs: icon.ico (multi-size) and icon.png (256x256).
"""
from PIL import Image, ImageDraw, ImageFont
import os

def draw_icon(size=256):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rect background - gradient from #58a6ff to #a371f7
    margin = size // 10
    radius = size // 5
    for i in range(size):
        r = int(0x58 + (0xa3 - 0x58) * i / size)
        g = int(0xa6 + (0x71 - 0xa6) * i / size)
        b = int(0xff + (0xf7 - 0xff) * i / size)
        draw.line([(0, i), (size, i)], fill=(r, g, b, 255))

    # Mask to rounded rect
    mask = Image.new("L", (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([margin, margin, size - margin, size - margin], radius=radius, fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    draw = ImageDraw.Draw(result)

    # Draw "H" monogram - thick rounded lines
    cx, cy = size // 2, size // 2
    bar_w = size // 7
    bar_h = size // 7
    h_w = size // 3
    h_h = size // 2

    left_x = cx - h_w // 2
    right_x = cx + h_w // 2
    top_y = cy - h_h // 2
    bot_y = cy + h_h // 2

    # Left vertical
    draw.rounded_rectangle([left_x - bar_w // 2, top_y, left_x + bar_w // 2, bot_y], radius=bar_w // 2, fill=(255, 255, 255, 255))
    # Right vertical
    draw.rounded_rectangle([right_x - bar_w // 2, top_y, right_x + bar_w // 2, bot_y], radius=bar_w // 2, fill=(255, 255, 255, 255))
    # Horizontal bar
    draw.rounded_rectangle([left_x - bar_w // 2, cy - bar_h // 2, right_x + bar_w // 2, cy + bar_h // 2], radius=bar_h // 2, fill=(255, 255, 255, 255))

    # Small accent dots (like a gear/settings theme)
    dot_r = size // 30
    dot_color = (255, 255, 255, 200)
    for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
        px = cx + dx * (h_w // 2 + dot_r * 2)
        py = cy + dy * (h_h // 2 + dot_r * 2)
        # Keep within bounds
        if margin < px < size - margin and margin < py < size - margin:
            draw.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r], fill=dot_color)

    return result

if __name__ == "__main__":
    out_dir = os.path.dirname(os.path.abspath(__file__))
    # Generate 256x256 PNG
    icon_256 = draw_icon(256)
    png_path = os.path.join(out_dir, "icon.png")
    icon_256.save(png_path)
    print(f"Saved: {png_path}")

    # Generate multi-size ICO
    sizes = [16, 32, 48, 64, 128, 256]
    icons = [draw_icon(s) for s in sizes]
    ico_path = os.path.join(out_dir, "icon.ico")
    icons[0].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes], append_images=icons[1:])
    print(f"Saved: {ico_path}")

    # Also generate 256x256 for PyInstaller
    icon_256.save(os.path.join(out_dir, "icon_256.png"))
    print("Done!")
