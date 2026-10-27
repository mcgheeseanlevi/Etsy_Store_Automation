"""Rules every listing must pass before anything is sent to Printify.

These checks run in every mode (check, dry run, --create, --publish) and never
touch the internet. If a row breaks a rule it is skipped, with a message that
says exactly what to fix.
"""
from __future__ import annotations

import csv
import difflib
import html
import io
import math
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

# --- Listing text rules -------------------------------------------------------
TITLE_MIN = 130
TITLE_MAX = 140                # Etsy's maximum title length
DESCRIPTION_MIN_CHARS = 1800   # line breaks are not counted, spaces are
DESCRIPTION_MIN_LINES = 12     # blank lines are not counted
MAX_TAGS = 13                  # Etsy allows at most 13 tags...
MAX_TAG_LENGTH = 20            # ...of at most 20 characters each

# --- Gentle warnings (the row still passes) -------------------------------------
LOW_PRICE = Decimal("10")
HIGH_PRICE = Decimal("100")
UPLOAD_SIZE_WARNING = 5 * 1024 * 1024  # Printify suggests uploads under 5 MB (after encoding)
CROP_WARNING = 0.02                    # warn if more than 2% of the design would be cut off

REQUIRED_COLUMNS = ("filename", "title", "tags")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass
class Listing:
    """One row of listings.csv, plus everything we found out while checking it."""

    row: int                      # row number as shown in your spreadsheet
    filename: str
    title: str = ""
    tags: list = field(default_factory=list)
    price_cents: int | None = None
    description: str = ""         # final text: your intro + blank line + template
    image_size: tuple | None = None
    image_bytes: int = 0
    problems: list = field(default_factory=list)   # any problem means the row is skipped
    warnings: list = field(default_factory=list)   # worth reading, but the row still passes
    done: dict | None = None      # log.csv entry if this image was already created

    @property
    def passed(self) -> bool:
        return not self.problems

    @property
    def title_length(self) -> int:
        return len(self.title)

    @property
    def description_length(self) -> int:
        return count_characters(self.description)

    @property
    def description_lines(self) -> int:
        return count_lines(self.description)


# --- Reading the spreadsheet ----------------------------------------------------

def read_spreadsheet(path: Path):
    """Return (rows, notes). Each row is (spreadsheet_row_number, {column: value})."""
    notes = []
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
        notes.append(
            "listings.csv isn't saved as UTF-8, so it was read as older Windows/Excel text. "
            "If accented letters look wrong, re-save it as 'CSV UTF-8'."
        )

    records = list(csv.reader(io.StringIO(text, newline="")))
    if not records:
        raise ValueError("listings.csv is empty. It needs a header row: filename,title,tags,price,description")

    header = [h.strip().lower().replace(" ", "_") for h in records[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ValueError(
            f"listings.csv is missing the column(s): {', '.join(missing)}. "
            "The first row must be: filename,title,tags,price,description"
        )

    rows = []
    # A quoted cell can contain line breaks, but it is still one spreadsheet row,
    # so counting records (header = row 1) matches the row numbers you see in Excel.
    for row_number, cells in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in cells):
            continue  # skip completely empty rows
        values = {name: (cells[i] if i < len(cells) else "") for i, name in enumerate(header)}
        rows.append((row_number, values))
    return rows, notes


def normalize_text(text: str) -> str:
    """Turn the line-break markers "\\n" and " | " into real line breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]*\\n[ \t]*", "\n", text)
    text = re.sub(r"[ \t]+\|[ \t]+", "\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def build_description(intro: str, template: str) -> str:
    """Your design-specific text, a blank line, then description_template.txt."""
    return "\n\n".join(part for part in (intro, template) if part)


def count_characters(text: str) -> int:
    return len(text) - text.count("\n")


def count_lines(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.strip())


def description_to_html(text: str) -> str:
    """Printify's description field supports HTML, so each line becomes a paragraph.

    Blank lines become an empty paragraph, which is how Printify's own editor
    stores them.
    """
    parts = []
    for line in text.split("\n"):
        line = line.strip()
        parts.append(f"<p>{html.escape(line, quote=False)}</p>" if line else "<p><br></p>")
    return "".join(parts)


def format_money(cents: int | None) -> str:
    return "-" if cents is None else f"${cents / 100:,.2f}"


# --- Individual checks ------------------------------------------------------------
# Each returns (problems, warnings) as lists of plain-English messages.

def check_title(title: str):
    problems, warnings = [], []
    n = len(title)
    if "\n" in title:
        problems.append("Title must be on a single line (it contains a line break).")
    if n < TITLE_MIN:
        problems.append(f"Title is {n} characters; it must be {TITLE_MIN}-{TITLE_MAX}. Add {TITLE_MIN - n} more.")
    elif n > TITLE_MAX:
        problems.append(f"Title is {n} characters; it must be {TITLE_MIN}-{TITLE_MAX}. Remove {n - TITLE_MAX}.")
    for symbol in "%:&+":
        if title.count(symbol) > 1:
            warnings.append(f'Etsy allows "{symbol}" only once in a title; this one has it '
                            f"{title.count(symbol)} times, so Etsy may reject it.")
    return problems, warnings


def parse_tags(raw: str) -> list:
    return [tag.strip() for tag in raw.split(";") if tag.strip()]


def check_tags(raw: str, tags: list):
    problems, warnings = [], []
    if "," in raw and ";" not in raw:
        return ["Tags must be separated with semicolons (;), not commas."], warnings
    if len(tags) > MAX_TAGS:
        problems.append(f"There are {len(tags)} tags; Etsy allows at most {MAX_TAGS}. Remove {len(tags) - MAX_TAGS}.")
    for tag in tags:
        if len(tag) > MAX_TAG_LENGTH:
            problems.append(f'Tag "{tag}" is {len(tag)} characters; Etsy\'s limit is {MAX_TAG_LENGTH}.')
    if len(tags) < MAX_TAGS:
        warnings.append(f"Only {len(tags)} tag{'' if len(tags) == 1 else 's'}. Etsy allows {MAX_TAGS}, and "
                        "using all of them helps buyers find you.")
    lowered = [t.lower() for t in tags]
    repeats = sorted({t for t in lowered if lowered.count(t) > 1})
    if repeats:
        warnings.append(f"Repeated tag(s): {', '.join(repeats)}. Each repeat wastes one of your {MAX_TAGS} tags.")
    odd = sorted({c for t in tags for c in t if not (c.isalnum() or c in " -'™©®")})
    if odd:
        warnings.append(f"Tags contain character(s) Etsy doesn't allow in tags: {' '.join(odd)} "
                        "(only letters, numbers, spaces, - and ' are safe).")
    return problems, warnings


def check_description(text: str):
    chars, lines = count_characters(text), count_lines(text)
    if chars >= DESCRIPTION_MIN_CHARS and lines >= DESCRIPTION_MIN_LINES:
        return [], []
    parts = []
    if chars < DESCRIPTION_MIN_CHARS:
        parts.append(f"add {DESCRIPTION_MIN_CHARS - chars} more characters")
    if lines < DESCRIPTION_MIN_LINES:
        parts.append(f"add {DESCRIPTION_MIN_LINES - lines} more non-empty lines")
    return [
        f"Description is too short: {chars} characters and {lines} non-empty lines "
        f"(your text + description_template.txt). It needs at least {DESCRIPTION_MIN_CHARS} "
        f"characters and {DESCRIPTION_MIN_LINES} lines, so {' and '.join(parts)} to your description."
    ], []


def parse_price(raw: str, default):
    """Return (price_in_cents, problems, warnings). Blank means use default_price."""
    text = (raw or "").strip()
    source = "the spreadsheet"
    if not text:
        if default is None or not str(default).strip():
            return None, ["No price in the spreadsheet and no default_price in config.json."], []
        text, source = str(default).strip(), "default_price in config.json"

    cleaned = text.replace("$", "").replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")   # 16,99 -> 16.99
    else:
        cleaned = cleaned.replace(",", "")    # 1,016.99 -> 1016.99
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None, [f'Price "{text}" (from {source}) is not a number. Write it like 16.99'], []
    if not value.is_finite() or value <= 0:
        return None, [f'Price "{text}" (from {source}) must be more than zero.'], []

    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    warnings = []
    if value < LOW_PRICE:
        warnings.append(f"Price {format_money(cents)} is low for a mug. Make sure it covers Printify's "
                        "cost plus Etsy fees.")
    elif value > HIGH_PRICE:
        warnings.append(f"Price {format_money(cents)} is unusually high for a mug. Is that a typo?")
    return cents, [], warnings


# --- Trademark / brand red flags ---------------------------------------------------

def load_blocked_terms(path: Path) -> list:
    if not path.exists():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def _fold(text: str) -> str:
    """Lowercase and drop accents, so "Pokemon" also catches "Pokémon"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _term_pattern(term: str):
    # Whole words only ("Nike" won't match "Nikeisha"), and spaces/hyphens are
    # flexible, so "Harry Potter" also catches "harry-potter" and "HarryPotter".
    words = [w for w in re.split(r"[\s\-_]+", _fold(term)) if w]
    return re.compile(r"(?<!\w)" + r"[\s\-_]*".join(re.escape(w) for w in words) + r"(?!\w)")


def find_blocked_terms(text: str, terms: list) -> list:
    folded = _fold(text)
    return [term for term in terms if _term_pattern(term).search(folded)]


# --- Images --------------------------------------------------------------------------

def read_png_size(path: Path):
    """Return (width, height) from the PNG header, or raise ValueError."""
    with path.open("rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != PNG_SIGNATURE:
        raise ValueError("this file is not really a PNG image")
    if head[12:16] != b"IHDR":
        raise ValueError("this PNG file looks damaged")
    return struct.unpack(">II", head[16:24])


def fill_scale(image_w: int, image_h: int, area_w: int, area_h: int) -> float:
    """Printify's "scale" = design width / print-area width (1 = exactly as wide).

    To fill the whole area, a design that is relatively wider than the area must
    be scaled up until its height matches. Rounded up so no thin gap is left.
    """
    scale = max(1.0, (image_w / image_h) / (area_w / area_h))
    return math.ceil(scale * 10000) / 10000


def crop_amount(image_w: int, image_h: int, area_w: int, area_h: int):
    """How much of the design is cut off when it is scaled to fill the area."""
    image_ratio, area_ratio = image_w / image_h, area_w / area_h
    if image_ratio > area_ratio:
        return 1 - area_ratio / image_ratio, "left and right"
    return 1 - image_ratio / area_ratio, "top and bottom"


def print_areas_from_config(config: dict) -> list:
    """Group the chosen mug variants by print-area size (usually there is just one)."""
    groups = {}
    for variant in config.get("variants") or []:
        area = variant.get("placeholder") or {}
        key = (area.get("position"), area.get("width"), area.get("height"))
        if None in key:
            continue
        groups.setdefault(key, []).append(variant["id"])
    return [
        {"position": pos, "width": w, "height": h, "variant_ids": ids}
        for (pos, w, h), ids in groups.items()
    ]


def check_image(path: Path, print_areas: list):
    """Return (size, file_bytes, problems, warnings)."""
    problems, warnings = [], []
    if not path.name.lower().endswith(".png"):
        problems.append("The image must be a PNG file (its name should end in .png).")
    if not path.is_file():
        message = f'Image "{path.name}" was not found in the incoming folder.'
        if path.parent.is_dir():
            names = [p.name for p in path.parent.iterdir() if p.is_file()]
            close = difflib.get_close_matches(path.name, names, n=1)
            if close:
                message += f' Did you mean "{close[0]}"?'
        return None, 0, problems + [message], warnings

    try:
        width, height = read_png_size(path)
    except (OSError, ValueError, struct.error) as exc:
        return None, 0, problems + [f"Can't read the image: {exc}."], warnings
    size_bytes = path.stat().st_size

    if not print_areas:
        warnings.append("Image size not checked yet: run 'python main.py setup' first so the tool "
                        "knows the mug's print area.")
    for area in print_areas:
        area_w, area_h = area["width"], area["height"]
        if width < area_w or height < area_h:
            factor = max(area_w / width, area_h / height)
            warnings.append(
                f"Image is {width}x{height} pixels, smaller than the mug print area ({area_w}x{area_h}). "
                f"It will be enlarged {factor:.1f}x and may print blurry. Export it at "
                f"{area_w}x{area_h} pixels or larger."
            )
        cut, sides = crop_amount(width, height, area_w, area_h)
        if cut > CROP_WARNING:
            warnings.append(
                f"Image shape ({width}x{height}) doesn't match the print area shape ({area_w}x{area_h}). "
                f"To fill the mug, about {cut:.0%} of the design will be cut off at the {sides}. "
                f"For a perfect fit, make it {area_w}x{area_h} pixels (or the same proportions)."
            )

    encoded_size = 4 * math.ceil(size_bytes / 3)  # images are sent as base64 text
    if encoded_size > UPLOAD_SIZE_WARNING:
        warnings.append(
            f"Image file is {size_bytes / 1024 / 1024:.1f} MB. Printify recommends keeping uploads like this "
            "under about 3.7 MB, so it may upload slowly or fail. If it fails, shrink it with a PNG "
            "compressor (that keeps the same pixel size)."
        )
    return (width, height), size_bytes, problems, warnings


# --- Putting it together ----------------------------------------------------------------

def build_listing(row_number: int, values: dict, *, template: str, default_price,
                  blocked_terms: list, incoming_dir: Path, print_areas: list) -> Listing:
    listing = Listing(row=row_number, filename=(values.get("filename") or "").strip())
    if not listing.filename:
        listing.problems.append("The filename cell is empty.")

    def add(result):
        listing.problems.extend(result[0])
        listing.warnings.extend(result[1])

    listing.title = (values.get("title") or "").strip()
    add(check_title(listing.title))

    raw_tags = values.get("tags") or ""
    listing.tags = parse_tags(raw_tags)
    add(check_tags(raw_tags, listing.tags))

    intro = normalize_text(values.get("description") or "")
    listing.description = build_description(intro, template)
    add(check_description(listing.description))

    listing.price_cents, price_problems, price_warnings = parse_price(values.get("price"), default_price)
    add((price_problems, price_warnings))

    for where, text in (("title", listing.title), ("tags", "; ".join(listing.tags)),
                        ("description", listing.description)):
        for term in find_blocked_terms(text, blocked_terms):
            listing.problems.append(
                f'Possible trademark/brand problem: "{term}" appears in the {where}. Remove it, '
                "or take it out of blocked_terms.txt if this is a false alarm."
            )

    if listing.filename:
        listing.image_size, listing.image_bytes, image_problems, image_warnings = check_image(
            incoming_dir / listing.filename, print_areas
        )
        add((image_problems, image_warnings))
    return listing
