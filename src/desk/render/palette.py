"""Media palette from Design doc v1.0 ("chosen design, not mandatory industry colouring").

No buy/sell colour semantics: positive and negative bars share one colour; the sign and
a word ("up"/"down") carry the meaning, so nothing depends on colour alone (D05).
"""

WHITE = "#FFFFFF"
INK = "#172B3A"  # near-black: primary text
GREY = "#52616B"  # secondary text
NAVY = "#244A63"  # data marks
TEAL = "#0B625C"
WARNING = "#9C2727"  # reserved for warnings, never for "down"
RULES = "#E9EEF2"


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
