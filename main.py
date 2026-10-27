#!/usr/bin/env python3
"""Mug lister: turns PNG designs + a spreadsheet into Printify mug products.

    python main.py check        check your spreadsheet and images (offline)
    python main.py              DRY RUN: show what would happen (offline)
    python main.py --create     upload + create products in Printify, NOT published
    python main.py --publish    create + publish to Etsy (asks you to type "yes")
    python main.py setup        one-time setup (reads your shops and the catalog)

Plain-English guide: README.md
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import textwrap
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import listing_rules as rules
    from printify_api import PrintifyClient, PrintifyError, StopRun, load_token
    from setup_wizard import SetupError, run_setup
except ImportError as exc:
    sys.exit(f"A required library is missing ({exc.name}). Install the libraries with:\n"
             "    pip install -r requirements.txt")

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"
LISTINGS_FILE = HERE / "listings.csv"
TEMPLATE_FILE = HERE / "description_template.txt"
BLOCKED_TERMS_FILE = HERE / "blocked_terms.txt"
INCOMING_DIR = HERE / "incoming"
DONE_DIR = HERE / "done"
LOG_FILE = HERE / "log.csv"

DEFAULT_LIMIT = 10
STOP_AFTER_FAILURES_IN_A_ROW = 3
LOG_COLUMNS = ["filename", "product_id", "title", "title_length", "description_length",
               "description_lines", "status", "timestamp", "error"]
DONE_STATUSES = {"created", "published"}   # images with these are skipped, so no duplicates
WIDTH = 78
NO_TOKEN = ("No Printify token found. Copy .env.example to a new file named .env and paste your "
            "token into it (see 'How to get your Printify token' in README.md).")


class FatalError(Exception):
    """Something that stops the program, with a message saying what to do."""


# --- Command line ----------------------------------------------------------------------

def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # odd characters never crash the output
    args = parse_args(argv)
    INCOMING_DIR.mkdir(exist_ok=True)
    DONE_DIR.mkdir(exist_ok=True)
    try:
        if args.command == "setup":
            return cmd_setup()
        if args.command == "check":
            return cmd_check()
        mode = "publish" if args.publish else "create" if args.create else "dry-run"
        return cmd_run(mode, args.limit)
    except KeyboardInterrupt:
        print("\nStopped by you (Ctrl+C).")
        return 130
    except (FatalError, SetupError, PrintifyError) as exc:
        print()
        print(wrap(str(exc), "ERROR: "))
        return 1


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="List AI-generated mug designs on Printify (and on Etsy, through Printify).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              python main.py check               check spreadsheet + images (offline)
              python main.py                     dry run: show what would happen (offline)
              python main.py --create --limit 1  create just the first ready product
              python main.py --create            upload + create products, don't publish
              python main.py --publish           create + publish to Etsy (asks for "yes")
              python main.py setup               one-time setup
            """))
    parser.add_argument("command", nargs="?", default="run", choices=["run", "check", "setup"],
                        help="'check' or 'setup'; leave it out to process your listings")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create", action="store_true",
                      help="upload images and create products in Printify, but do NOT publish")
    mode.add_argument("--publish", action="store_true",
                      help="create products and publish them to Etsy (asks you to type yes first)")
    parser.add_argument("--limit", type=positive_int,
                        help=f"the most listings to handle in one run (default {DEFAULT_LIMIT})")
    args = parser.parse_args(argv)
    if args.command != "run" and (args.create or args.publish or args.limit is not None):
        parser.error(f"--create, --publish and --limit can't be used with '{args.command}'")
    if args.limit is None:
        args.limit = DEFAULT_LIMIT
    return args


def positive_int(text):
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("must be a whole number of 1 or more, like 5")
    return int(text)


# --- The three commands --------------------------------------------------------------------

def cmd_setup() -> int:
    config = load_config()
    token = load_token(HERE)
    if not token:
        raise FatalError(NO_TOKEN)
    new_config = run_setup(PrintifyClient(token), config)
    save_config(new_config)
    print("Saved your choices to config.json.")
    print("Next: put PNGs in incoming/, fill in listings.csv, then run:  python main.py check")
    return 0


def cmd_check() -> int:
    config = load_config()
    print("CHECK: checking listings.csv and your images. Nothing is uploaded or sent anywhere.\n")
    print_setup_status(config)
    listings, notes = load_listings(config)
    print_report(listings, notes)
    return 1 if any(l.problems and not l.done for l in listings) else 0


def cmd_run(mode: str, limit: int) -> int:
    config = load_config()
    print({
        "dry-run": "DRY RUN: nothing will be uploaded, created, or published. This only shows\n"
                   "what WOULD happen. (Use --create or --publish to do it for real.)",
        "create": "CREATE MODE: images will be uploaded and products created in Printify.\n"
                  "Nothing will be published to Etsy.",
        "publish": "PUBLISH MODE: products will be created in Printify and published to your\n"
                   "Etsy shop. You'll see a summary and be asked to type \"yes\" first.",
    }[mode])
    print(f"Limit: at most {limit} listing(s) this run.\n")
    print_setup_status(config)
    listings, notes = load_listings(config)
    print_report(listings, notes)

    ready = [l for l in listings if l.passed and not l.done]
    skipped = [l for l in listings if l.problems and not l.done]
    batch, waiting = ready[:limit], ready[limit:]
    if not batch:
        print("\nNothing to do: no rows are ready." +
              (" Fix the FAIL rows above and run again." if skipped else ""))
        return 1 if skipped else 0

    if mode == "dry-run":
        print_plan(config, batch, waiting, limit)
        return 0

    token = preflight(config)
    if mode == "publish" and not confirm_publish(config, batch, skipped, waiting, limit):
        print("\nCancelled. Nothing was uploaded, created, or published.")
        return 0

    for listing in skipped:
        write_log(listing, "skipped", error=" / ".join(listing.problems))
    counts = process_batch(PrintifyClient(token), config, batch, publish=(mode == "publish"))
    print_summary(mode, counts, skipped, waiting, limit)
    return 1 if counts["failed"] or counts["publish_failed"] or counts["stopped"] else 0


# --- Loading files ------------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FatalError("config.json is missing. It comes with the tool; download it again.")
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise FatalError(f"config.json has a typo on line {exc.lineno} ({exc.msg}). If you edited it, "
                         "check for a missing comma or quote mark.") from None
    if not isinstance(config, dict):
        raise FatalError("config.json should contain settings inside { }.")
    return config


def save_config(config: dict):
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def setup_problems(config: dict) -> list:
    missing = [key for key in ("shop_id", "blueprint_id", "print_provider_id") if not config.get(key)]
    if not rules.print_areas_from_config(config):
        missing.append("variants")
    return missing


def description_format(config: dict) -> str:
    value = str(config.get("description_format", "html")).strip().lower()
    if value not in ("html", "plain"):
        raise FatalError(f'description_format in config.json must be "html" or "plain", not "{value}".')
    return value


def load_listings(config: dict):
    """Read and check every spreadsheet row. Returns (listings, notes). No internet."""
    for path in (LISTINGS_FILE, TEMPLATE_FILE):
        if not path.exists():
            raise FatalError(f"{path.name} is missing. It should be in the same folder as main.py.")
    template = rules.normalize_text(TEMPLATE_FILE.read_text(encoding="utf-8-sig"))
    blocked_terms = rules.load_blocked_terms(BLOCKED_TERMS_FILE)
    try:
        rows, notes = rules.read_spreadsheet(LISTINGS_FILE)
    except ValueError as exc:
        raise FatalError(str(exc)) from None
    except OSError as exc:
        raise FatalError(f"Can't open listings.csv ({exc}). If it's open in Excel, close it.") from None
    if not BLOCKED_TERMS_FILE.exists():
        notes.append("blocked_terms.txt is missing, so the trademark check is OFF.")

    done = read_log()
    print_areas = rules.print_areas_from_config(config)
    listings, first_row = [], {}
    for row_number, values in rows:
        name = (values.get("filename") or "").strip()
        if name.lower() in done:
            listing = rules.Listing(row=row_number, filename=name, done=done[name.lower()])
        else:
            listing = rules.build_listing(
                row_number, values, template=template, default_price=config.get("default_price"),
                blocked_terms=blocked_terms, incoming_dir=INCOMING_DIR, print_areas=print_areas)
        if name and name.lower() in first_row:
            listing.problems.append(f"This filename is already used in row {first_row[name.lower()]}. "
                                    "Each design needs its own file and its own row.")
        elif name:
            first_row[name.lower()] = row_number
        listings.append(listing)

    listed = {l.filename.lower() for l in listings}
    unlisted = sorted(p.name for p in INCOMING_DIR.iterdir()
                      if p.is_file() and not p.name.startswith(".") and p.name.lower() not in listed)
    if unlisted:
        notes.append(f"These files in incoming/ have no row in listings.csv, so they're ignored: "
                     f"{', '.join(unlisted)}")
    return listings, notes


# --- log.csv ---------------------------------------------------------------------------------------

def read_log() -> dict:
    """Return {filename (lowercase): latest log row} for images already made into products."""
    done = {}
    if not LOG_FILE.exists():
        return done
    try:
        with LOG_FILE.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f):
                if (row.get("status") or "").strip() in DONE_STATUSES and (row.get("filename") or "").strip():
                    done[row["filename"].strip().lower()] = row
    except OSError as exc:
        raise FatalError(f"Can't read log.csv ({exc}). If it's open in Excel, close it.") from None
    return done


def write_log(listing, status: str, product_id="", error=""):
    row = {
        "filename": listing.filename,
        "product_id": product_id,
        "title": listing.title,
        "title_length": listing.title_length,
        "description_length": listing.description_length,
        "description_lines": listing.description_lines,
        "status": status,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": " ".join(str(error).split()),
    }
    is_new = not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0
    try:
        # A new log starts with a marker that helps Excel show accented letters correctly.
        with LOG_FILE.open("w" if is_new else "a", newline="",
                           encoding="utf-8-sig" if is_new else "utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            if is_new:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
        extra = (f" Product {product_id} for {listing.filename} WAS created in Printify, so add it to "
                 "log.csv by hand (or move the image out of incoming/) before running again."
                 if product_id else "")
        raise FatalError(f"Couldn't write to log.csv ({exc}). If it's open in Excel, close it.{extra}") from None


# --- Doing the work ----------------------------------------------------------------------------------

def preflight(config: dict) -> str:
    """Everything that must be true before we touch Printify. Returns the token."""
    missing = setup_problems(config)
    if missing:
        raise FatalError(f"Setup isn't finished (missing: {', '.join(missing)}). "
                         "Run:  python main.py setup")
    token = load_token(HERE)
    if not token:
        raise FatalError(NO_TOKEN)
    description_format(config)
    try:
        with LOG_FILE.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        raise FatalError(f"Can't write to log.csv ({exc}). If it's open in Excel, close it.") from None
    return token


def confirm_publish(config, batch, skipped, waiting, limit) -> bool:
    print("\n" + "=" * WIDTH)
    print(f"READY TO PUBLISH {len(batch)} LISTING(S) TO ETSY")
    print("=" * WIDTH)
    print(f"Shop: {config.get('shop_title')} (Printify shop id {config['shop_id']})")
    print(f"Mug:  {config.get('blueprint_title')} from {config.get('print_provider_title')}\n")
    for i, listing in enumerate(batch, 1):
        print(wrap(listing.title, f"{i:>3}. {rules.format_money(listing.price_cents):>8}  "))
    print("\nEach one will be created in Printify and then published to your Etsy shop.")
    print("Etsy normally charges its listing fee for each new listing.")
    if skipped:
        print(f"Not included: {len(skipped)} row(s) with problems (listed above).")
    if waiting:
        print(f"Not included: {len(waiting)} ready row(s) over this run's limit of {limit}.")
    try:
        answer = input(f"\nType yes to publish these {len(batch)} listing(s), or anything else to cancel: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


def process_batch(client, config, batch, publish: bool) -> Counter:
    counts = Counter()
    failures_in_a_row = 0
    for i, listing in enumerate(batch, 1):
        print(f"\n[{i}/{len(batch)}] {listing.filename}  (row {listing.row})")
        try:
            outcome = process_one(client, config, listing, publish)
        except KeyboardInterrupt:
            counts["stopped"] = 1
            counts["not_attempted"] += len(batch) - i + 1   # includes the interrupted one
            print("\nStopped by you (Ctrl+C).")
            print(wrap(f'If you stopped while "Creating the mug product..." was showing, open Printify > '
                       f'My Products and look for "{short(listing.title, 60)}". If it\'s there, delete it '
                       "before running again so you don't get a duplicate."))
            break
        except PrintifyError as exc:
            already_logged = getattr(exc, "logged", False)
            if not already_logged:
                write_log(listing, "failed", error=str(exc))
                print(wrap(str(exc), "  FAILED: "))
                print("  The image stays in incoming/, so it will be tried again next run.")
            outcome = "publish_failed" if already_logged else "failed"
            if isinstance(exc, StopRun):
                counts[outcome] += 1
                counts["stopped"] = 1
                counts["not_attempted"] += len(batch) - i
                print("\nSTOPPING THE RUN: this problem would affect every listing.")
                break

        counts[outcome] += 1
        failures_in_a_row = failures_in_a_row + 1 if outcome in ("failed", "publish_failed") else 0
        if failures_in_a_row >= STOP_AFTER_FAILURES_IN_A_ROW and i < len(batch):
            counts["stopped"] = 1
            counts["not_attempted"] += len(batch) - i
            print(f"\nSTOPPING THE RUN: {failures_in_a_row} listings in a row failed, so something is "
                  "probably wrong. Read the messages above.")
            break
    return counts


def process_one(client, config, listing, publish: bool) -> str:
    shop_id = config["shop_id"]
    print("  Uploading image to Printify...")
    image = client.upload_image(INCOMING_DIR / listing.filename)
    if not image.get("id"):
        raise PrintifyError("Printify accepted the upload but didn't return an image id.")
    print(f"  Uploaded (Printify image id {image['id']}).")

    print("  Creating the mug product...")
    product = client.create_product(shop_id, build_product(config, listing, image["id"]))
    product_id = product.get("id")
    if not product_id:
        raise StopRun("Printify answered without a product id, so it's unclear whether the product was "
                      "created. Check Printify > My Products before running again.")
    write_log(listing, "created", product_id)   # recorded straight away, so a rerun can't duplicate it
    print(f"  Created product {product_id} (not published).")
    move_to_done(listing)
    price_ok = report_cost(product, config, listing)
    if not publish:
        return "created"

    if not price_ok:
        reason = "Not published: the price doesn't cover Printify's production cost."
        write_log(listing, "created", product_id, reason)
        print(wrap(f"{reason} The product is saved in Printify; raise its price there and publish it "
                   "from Printify.", "  "))
        return "publish_failed"

    print("  Publishing to Etsy through Printify...")
    try:
        client.publish_product(shop_id, product_id)
    except PrintifyError as exc:
        write_log(listing, "created", product_id, f"Created but NOT published: {exc}")
        print(wrap(str(exc), "  NOT PUBLISHED: "))
        print(wrap("The product is saved in Printify (unpublished). Fix the problem, then publish it "
                   "from the Printify dashboard. Running this tool again will NOT create a duplicate.", "  "))
        if isinstance(exc, StopRun):
            exc.logged = True
            raise
        return "publish_failed"
    write_log(listing, "published", product_id)
    print("  Publish request accepted. Printify is now sending it to Etsy.")
    return "published"


def build_product(config: dict, listing, image_id: str) -> dict:
    """The product exactly as it is sent to Printify."""
    image_w, image_h = listing.image_size
    areas = rules.print_areas_from_config(config)
    if description_format(config) == "html":
        description = rules.description_to_html(listing.description)
    else:
        description = listing.description
    return {
        "title": listing.title,
        "description": description,
        "tags": listing.tags,
        "blueprint_id": config["blueprint_id"],
        "print_provider_id": config["print_provider_id"],
        "variants": [{"id": variant_id, "price": listing.price_cents, "is_enabled": True}
                     for area in areas for variant_id in area["variant_ids"]],
        "print_areas": [{
            "variant_ids": area["variant_ids"],
            "placeholders": [{
                "position": area["position"],
                "images": [{
                    "id": image_id,
                    "x": 0.5,   # centered left-right
                    "y": 0.5,   # centered top-bottom
                    "scale": rules.fill_scale(image_w, image_h, area["width"], area["height"]),
                    "angle": 0,
                }],
            }],
        } for area in areas],
    }


def report_cost(product: dict, config: dict, listing) -> bool:
    """Show Printify's production cost. Returns False if the price doesn't cover it."""
    our_ids = {v["id"] for v in config.get("variants") or []}
    costs = [v["cost"] for v in product.get("variants") or []
             if v.get("id") in our_ids and isinstance(v.get("cost"), (int, float))]
    if not costs:
        return True
    cost, price = max(costs), listing.price_cents
    print(f"  Printify's production cost: {rules.format_money(cost)} per mug (shipping not included). "
          f"Your price: {rules.format_money(price)}.")
    if price <= cost:
        print("  WARNING: your price doesn't cover the production cost!")
        return False
    return True


def move_to_done(listing):
    source = INCOMING_DIR / listing.filename
    target = DONE_DIR / listing.filename
    if target.exists():
        target = DONE_DIR / f"{source.stem}-{datetime.now():%Y%m%d-%H%M%S}{source.suffix}"
    try:
        shutil.move(str(source), str(target))
        print(f"  Moved the image to done/{target.name}")
    except OSError as exc:
        print(wrap(f"Note: couldn't move the image to done/ ({exc}). It's recorded in log.csv, so it "
                   "won't be uploaded again; move it yourself.", "  "))


# --- Printing reports ----------------------------------------------------------------------------------

def wrap(text: str, prefix: str = "") -> str:
    return textwrap.fill(text, width=WIDTH, initial_indent=prefix, subsequent_indent=" " * len(prefix),
                         break_long_words=False, break_on_hyphens=False)


def file_size(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def short(text: str, length: int) -> str:
    return text if len(text) <= length else text[:length - 3] + "..."


def print_setup_status(config: dict):
    if setup_problems(config):
        print("Setup:  NOT DONE YET. Run 'python main.py setup' before --create or --publish.")
    else:
        areas = ", ".join(f"'{a['position']}' {a['width']}x{a['height']} px"
                          for a in rules.print_areas_from_config(config))
        print(wrap(f"shop '{config.get('shop_title')}', mug '{config.get('blueprint_title')}' from "
                   f"'{config.get('print_provider_title')}', {len(config['variants'])} option(s), "
                   f"print area {areas}", "Setup:  "))
    print("Token:  " + ("found in .env (never shown)" if load_token(HERE) else
                        "not set yet (needed for setup, --create and --publish; see README)"))
    print()


def print_report(listings: list, notes: list):
    print(f"{'Row':<4} {'File':<22} {'Title':>5} {'Desc':>5} {'Lines':>5} {'Tags':>4} {'Price':>8}  "
          f"{'Image':<11} Result")
    print(f"{'---':<4} {'-' * 22} {'-----':>5} {'-----':>5} {'-----':>5} {'----':>4} {'-' * 8:>8}  "
          f"{'-' * 11} ------")
    for l in listings:
        if l.done:
            cells = ["-"] * 6
            result = "DONE"
        else:
            image = f"{l.image_size[0]}x{l.image_size[1]}" if l.image_size else "problem"
            cells = [l.title_length, l.description_length, l.description_lines, len(l.tags),
                     rules.format_money(l.price_cents), image if l.filename else "-"]
            result = "FAIL" if l.problems else "PASS*" if l.warnings else "PASS"
        print(f"{l.row:<4} {short(l.filename or '(none)', 22):<22} {cells[0]:>5} {cells[1]:>5} "
              f"{cells[2]:>5} {cells[3]:>4} {cells[4]:>8}  {cells[5]:<11} {result}")

    for l in listings:
        if l.done or not (l.problems or l.warnings):
            continue
        status = "FAIL, this row will be skipped" if l.problems else "PASS, but please read the warnings"
        print(f"\nRow {l.row} ({l.filename or 'no filename'}): {status}")
        for problem in l.problems:
            print(wrap(problem, "  [FAIL] "))
        for warning in l.warnings:
            print(wrap(warning, "  [warn] "))

    done = [l for l in listings if l.done]
    if done:
        print()
        for l in done:
            print(wrap(f"Row {l.row} ({l.filename}): already {l.done.get('status')} on "
                       f"{l.done.get('timestamp')} as product {l.done.get('product_id')}. Skipped so it "
                       "isn't duplicated.", ""))
    for note in notes:
        print()
        print(wrap(note, "Note: "))

    counts = Counter("DONE" if l.done else "FAIL" if l.problems else "PASS" for l in listings)
    print(f"\nRules: title {rules.TITLE_MIN}-{rules.TITLE_MAX} characters | at most {rules.MAX_TAGS} tags "
          f"of up to {rules.MAX_TAG_LENGTH} characters")
    print(f"       description {rules.DESCRIPTION_MIN_CHARS}+ characters (line breaks not counted) and "
          f"{rules.DESCRIPTION_MIN_LINES}+ non-empty lines")
    print("       PASS* = passes, but read the warnings above")
    print(f"Result: {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['DONE']} already done "
          f"(out of {len(listings)} row(s) in listings.csv)")


def print_plan(config: dict, batch: list, waiting: list, limit: int):
    title = "WHAT --create WOULD DO (dry run: none of this happens now)"
    print(f"\n{title}\n{'-' * len(title)}")
    shop = config.get("shop_title") or "(not chosen yet)"
    mug = (f"{config.get('blueprint_title')} from {config.get('print_provider_title')}"
           if config.get("blueprint_id") else "Mug (not chosen yet)")
    areas = rules.print_areas_from_config(config)
    variant_count = sum(len(a["variant_ids"]) for a in areas)
    try:
        fmt = description_format(config)
    except FatalError as exc:
        fmt = f"?? ({exc})"
    for i, l in enumerate(batch, 1):
        width, height = l.image_size
        print(f"{i}. {l.filename}  (row {l.row})")
        print(f"   - Upload the image to Printify ({width}x{height} px, {file_size(l.image_bytes)})")
        print(f"   - Create an UNPUBLISHED product in shop '{shop}':")
        print(wrap(f'"{l.title}"', "       "))
        print(f"     {mug}; price {rules.format_money(l.price_cents)}"
              f"{f' on {variant_count} option(s)' if variant_count else ''}; {len(l.tags)} tags")
        for a in areas:
            scale = rules.fill_scale(width, height, a["width"], a["height"])
            print(f"     Design centered and scaled to fill the '{a['position']}' print area "
                  f"({a['width']}x{a['height']}), scale {scale:g}")
        if not areas:
            print("     Design centered and scaled to fill the print area (its size is known after setup)")
        print(f"     Description: {l.description_length} characters, {l.description_lines} lines, sent as "
              f"{'HTML paragraphs (one per line)' if fmt == 'html' else fmt}")
        print("   - Move the image to done/ and add a line to log.csv")

    print("\nWith --publish, each product would also be published to your Etsy shop through")
    print('Printify, after you see a summary and type "yes".')
    if waiting:
        print(f"{len(waiting)} more ready row(s) are over the limit of {limit} and would wait for "
              "the next run (use --limit to change).")
    todo = []
    if not load_token(HERE):
        todo.append("add your Printify token to .env (see README)")
    if setup_problems(config):
        todo.append("run  python main.py setup")
    if todo:
        print()
        print(wrap(f"Before --create or --publish will work: {'; then '.join(todo)}."))
    print("\nDry run finished. Nothing was uploaded, created, published, moved, or logged.")


def print_summary(mode: str, counts: Counter, skipped: list, waiting: list, limit: int):
    made = counts["created"] + counts["published"] + counts["publish_failed"]
    print("\n" + "=" * WIDTH)
    print("RUN STOPPED EARLY" if counts["stopped"] else "RUN FINISHED")
    print("=" * WIDTH)
    lines = [("Created in Printify", made)]
    if mode == "publish":
        lines += [("  ...and published to Etsy", counts["published"]),
                  ("  ...created but NOT published (see messages above)", counts["publish_failed"])]
    lines += [("Failed (image left in incoming/ to retry)", counts["failed"]),
              ("Skipped because of spreadsheet/image problems", len(skipped)),
              (f"Waiting for the next run (over the limit of {limit})", len(waiting)),
              ("Not finished because the run stopped", counts["not_attempted"])]
    for label, number in lines:
        if number or label == "Created in Printify":
            print(f"{label:<55} {number:>4}")
    print("\nEvery result is recorded in log.csv.")
    if made and mode == "create":
        print(wrap("Next: review the new products in Printify (My Products): check the mockups and that "
                   "the description's line breaks look right. Publish them from Printify when you're "
                   "happy, or use --publish for your next batch."))
    elif counts["published"]:
        print(wrap("New listings can take a few minutes to appear on Etsy. If one doesn't, open it in "
                   "Printify to see its publishing status."))


if __name__ == "__main__":
    sys.exit(main())
