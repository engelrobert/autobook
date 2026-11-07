"""Write packaging/windows/autobook.ico (the icon of resources/autobook.svg, drawn with Pillow)."""

from pathlib import Path

from autobook.icon import write_ico

if __name__ == "__main__":
    out = Path(__file__).with_name("autobook.ico")
    write_ico(out)
    print(f"wrote {out}")
