"""Test-only helpers to inspect generated media without extra libraries (BOM).

PDF text is pulled straight from ReportLab's (Flate-compressed) content streams: if a
phrase is found here, it is real, searchable text — not pixels.
"""

import base64
import re
import zlib

# One object at a time: the header may not run past the object's own "endobj".
STREAM = re.compile(rb"\d+ 0 obj\s*<<((?:(?!endobj).)*?)>>\s*stream\r?\n(.*?)\r?\n?endstream", re.S)
TJ = re.compile(rb"\(((?:\\.|[^\\)])*)\)\s*Tj")


def _unescape(raw: bytes) -> str:
    out, i = bytearray(), 0
    while i < len(raw):
        c = raw[i]
        if c == 0x5C and i + 1 < len(raw):  # backslash escape
            nxt = raw[i + 1]
            if 0x30 <= nxt <= 0x37:  # octal
                j = i + 1
                while j < len(raw) and j < i + 4 and 0x30 <= raw[j] <= 0x37:
                    j += 1
                out.append(int(raw[i + 1 : j], 8))
                i = j
                continue
            out.append({ord("n"): 10, ord("r"): 13, ord("t"): 9}.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return out.decode("cp1252", errors="replace")


def pdf_pages_text(pdf: bytes) -> list[str]:
    """Text per content stream (one per page for ReportLab output, in page order)."""
    pages = []
    for m in STREAM.finditer(pdf):
        header, data = m.group(1), m.group(2)
        if b"/Subtype /Image" in header:
            continue
        if b"ASCII85Decode" in header:
            data = base64.a85decode(data.strip().removesuffix(b"~>"), adobe=False)
        if b"FlateDecode" in header:
            data = zlib.decompress(data)
        if b"BT" not in data:
            continue
        pages.append(" ".join(_unescape(t) for t in TJ.findall(data)))
    return pages


def pdf_text(pdf: bytes) -> str:
    return "\n".join(pdf_pages_text(pdf))
