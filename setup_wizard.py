"""The one-time "python main.py setup" questions.

Setup only READS from Printify (your shops and the product catalog). It never
creates or changes anything, and nothing is saved until you've answered every
question.
"""
from __future__ import annotations

import html
import re

from listing_rules import format_money, parse_price
from printify_api import PrintifyError

# Words that suggest a mug that is NOT a plain 11oz white ceramic mug.
NOT_PLAIN_WHITE = ("black", "color", "colour", "accent", "two-tone", "enamel", "travel", "stainless",
                   "steel", "glass", "latte", "magic", "heat", "glitter", "metallic", "camping",
                   "frosted", "tumbler", "inner", "rim", "15oz", "15 oz", "20oz", "20 oz", "matte")


class SetupError(Exception):
    pass


def run_setup(client, config: dict) -> dict:
    print("SETUP: this only reads information from Printify. Nothing in your account is changed,")
    print("and nothing is saved until the end. Press Ctrl+C at any time to cancel.\n")

    shop = choose_shop(client)
    blueprint = choose_blueprint(client)
    provider = choose_provider(client, blueprint)
    variants = choose_variants(client, blueprint, provider)
    show_care_details(client, blueprint)
    default_price = ask_default_price(config.get("default_price"))

    new_config = dict(config)
    new_config.update({
        "default_price": default_price,
        "shop_id": shop["id"],
        "shop_title": shop.get("title"),
        "blueprint_id": blueprint["id"],
        "blueprint_title": blueprint.get("title"),
        "print_provider_id": provider["id"],
        "print_provider_title": provider.get("title"),
        "variants": variants,
    })
    return new_config


# --- Step 1: shop ------------------------------------------------------------------

def choose_shop(client) -> dict:
    print("STEP 1 of 5: Your Printify shops")
    shops = client.list_shops()
    if not shops:
        raise SetupError("Your Printify account has no shops. Connect your Etsy shop in Printify first.")
    etsy_default = next((i for i, s in enumerate(shops, 1) if _is_etsy(s)), 1)
    for i, shop in enumerate(shops, 1):
        print(f"  {i}) {shop.get('title')}   [{shop.get('sales_channel')}]   (shop id {shop.get('id')})")
    shop = shops[ask_number("Which one is your Etsy shop?", len(shops), etsy_default) - 1]
    if not _is_etsy(shop) and not ask_yes_no(
            f"'{shop.get('title')}' doesn't look like an Etsy shop. Use it anyway?"):
        raise SetupError("Setup cancelled. Run it again and pick your Etsy shop.")
    print()
    return shop


def _is_etsy(shop) -> bool:
    return "etsy" in str(shop.get("sales_channel", "")).lower()


# --- Step 2: which mug ---------------------------------------------------------------

def choose_blueprint(client) -> dict:
    print("STEP 2 of 5: Finding the 11oz white ceramic mug in Printify's catalog...")
    candidates = rank_mug_blueprints(client.list_blueprints())
    if not candidates:
        raise SetupError("Couldn't find an 11oz mug in Printify's catalog. Printify may have renamed it; "
                         "please tell whoever maintains this tool.")
    candidates = candidates[:8]
    for i, bp in enumerate(candidates, 1):
        brand = f" by {bp['brand']}" if bp.get("brand") else ""
        print(f"  {i}) {bp.get('title')}{brand}   (blueprint id {bp.get('id')})")
    print("  The first one is the best match for '11oz white ceramic mug'.")
    blueprint = candidates[ask_number("Which mug?", len(candidates), 1) - 1]
    print()
    return blueprint


def rank_mug_blueprints(blueprints) -> list:
    scored = []
    for bp in blueprints:
        title = str(bp.get("title", "")).lower()
        if "mug" not in title or not re.search(r"\b11\s?oz\b", title):
            continue
        score = 3 * ("white" in title) + 2 * ("ceramic" in title)
        score -= 3 * sum(word in title for word in NOT_PLAIN_WHITE)
        scored.append((-score, len(title), bp))
    scored.sort(key=lambda item: item[:2])
    return [bp for _, _, bp in scored]


# --- Step 3: print provider -----------------------------------------------------------

def choose_provider(client, blueprint) -> dict:
    print(f"STEP 3 of 5: Print providers for '{blueprint.get('title')}'")
    providers = client.list_print_providers(blueprint["id"])
    if not providers:
        raise SetupError("No print providers offer this mug right now. Run setup again and pick another mug.")

    rows = []
    for i, provider in enumerate(providers, 1):
        print(f"  looking up provider {i} of {len(providers)}...", end="\r")
        rows.append((provider, *provider_facts(client, blueprint["id"], provider["id"])))
    print(" " * 50, end="\r")

    print(f"  {'#':>2}  {'Provider':<30} {'Ships from':<12} {'Production time':<16} US shipping, 1 mug")
    for i, (provider, country, handling, shipping) in enumerate(rows, 1):
        print(f"  {i:>2}) {str(provider.get('title'))[:30]:<30} {country:<12} {handling:<16} {shipping}")

    print("""
  How to choose, in plain terms:
  - PRICE: Printify charges you a base price for each mug, plus shipping. A cheaper
    provider leaves you more profit per sale. This list doesn't show base prices, so
    compare them on this mug's page in the Printify catalog (it lists every provider).
  - SPEED: "Production time" is how long the provider takes to print before shipping.
    A provider in the same country as most of your buyers (usually the US for Etsy)
    also delivers faster and usually charges less for shipping.
  - Etsy shows buyers an estimated delivery date, so a faster provider makes your
    listings more attractive and means fewer "where is my order?" messages.
  - A good default: a provider in your buyers' country with a short production time,
    even if its base price is slightly higher. Printify's dashboard also shows each
    provider's quality score, which is worth a look.
""")
    provider = rows[ask_number("Which provider?", len(rows), 1) - 1][0]
    print()
    return provider


def provider_facts(client, blueprint_id, provider_id):
    """Return (ships_from, production_time, us_shipping) as short text."""
    country = handling = shipping = "unknown"
    try:
        location = client.get_print_provider(provider_id).get("location") or {}
        country = location.get("country") or "unknown"
    except PrintifyError:
        pass
    try:
        info = client.get_shipping(blueprint_id, provider_id)
        time_info = info.get("handling_time") or {}
        if time_info.get("value") is not None:
            unit = str(time_info.get("unit", "day"))
            handling = f"{time_info['value']} {unit}{'' if time_info['value'] == 1 else 's'}"
        shipping = us_shipping_cost(info.get("profiles") or [])
    except PrintifyError:
        pass
    return country, handling, shipping


def us_shipping_cost(profiles) -> str:
    for wanted in ("US", "REST_OF_THE_WORLD"):
        for profile in profiles:
            if wanted in (profile.get("countries") or []):
                first = profile.get("first_item") or {}
                if isinstance(first.get("cost"), (int, float)):
                    label = format_money(first["cost"])
                    if first.get("currency") not in (None, "USD"):
                        label = f"{first['cost'] / 100:.2f} {first['currency']}"
                    return label if wanted == "US" else f"{label} (worldwide rate)"
    return "unknown"


# --- Step 4: variants and print area ----------------------------------------------------

def choose_variants(client, blueprint, provider) -> list:
    print(f"STEP 4 of 5: Mug options from {provider.get('title')}")
    variants = (client.list_variants(blueprint["id"], provider["id"]) or {}).get("variants") or []
    usable = [v for v in variants if pick_placeholder(v)]
    if not usable:
        raise SetupError("This provider has no in-stock options with a print area right now. "
                         "Run setup again and choose another provider.")

    for i, variant in enumerate(usable, 1):
        area = pick_placeholder(variant)
        cost = variant.get("cost", variant.get("price"))
        cost_text = f"   base cost {format_money(cost)}" if isinstance(cost, (int, float)) else ""
        print(f"  {i}) {variant.get('title')}   (variant id {variant['id']})   "
              f"print area '{area['position']}' {area['width']}x{area['height']} px{cost_text}")

    suggested = [i for i, v in enumerate(usable, 1) if _looks_like_11oz_white(v)] or [1]
    picks = ask_number_list("Which option(s)? Press Enter for the suggestion, or type numbers like 1,2",
                            len(usable), suggested)

    chosen = []
    for i in picks:
        variant = usable[i - 1]
        area = pick_placeholder(variant)
        positions = sorted({p.get("position") for p in variant.get("placeholders") or []})
        if len(positions) > 1:
            print(f"  Note: '{variant.get('title')}' has print positions {', '.join(positions)}. "
                  f"Your design goes on '{area['position']}'.")
        chosen.append({"id": variant["id"], "title": variant.get("title"), "placeholder": area})
    print()
    return chosen


def pick_placeholder(variant):
    """Use the 'front' print area if there is one, otherwise the biggest."""
    areas = [p for p in variant.get("placeholders") or []
             if p.get("position") and isinstance(p.get("width"), int) and isinstance(p.get("height"), int)]
    if not areas:
        return None
    front = [p for p in areas if p["position"] == "front"]
    best = front[0] if front else max(areas, key=lambda p: p["width"] * p["height"])
    return {"position": best["position"], "width": best["width"], "height": best["height"]}


def _looks_like_11oz_white(variant) -> bool:
    text = f"{variant.get('title', '')} {variant.get('options', '')}".lower()
    return bool(re.search(r"\b11\s?oz\b", text)) and ("white" in text or "black" not in text)


# --- Step 5: care details and price ------------------------------------------------------

def show_care_details(client, blueprint):
    print("STEP 5 of 5: Care instructions and price")
    try:
        text = client.get_blueprint(blueprint["id"]).get("description") or ""
    except PrintifyError:
        text = ""
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    pieces = [p.strip(" .:-•\t") for p in re.split(r"[\n.]", text)]
    care = [p for p in pieces if re.search(r"dishwasher|microwave|hand ?wash|care|oven", p, re.I)]
    if care:
        print("  Printify's catalog says this about the mug:")
        for line in dict.fromkeys(care):
            print(f"    - {line}")
    else:
        print("  Printify's catalog description doesn't mention care instructions for this mug.")
    print("  -> Open description_template.txt and make sure its 'Care:' line matches what the")
    print("     provider says (it's also shown on this mug's page in the Printify catalog).\n")


def ask_default_price(current) -> float:
    current_text = str(current) if current not in (None, "") else "16.99"
    while True:
        answer = _ask(f"Default price in US dollars for each mug (used when the price cell is empty) "
                      f"[{current_text}]: ") or current_text
        cents, problems, warnings = parse_price(answer, None)
        if problems:
            print(f"  {problems[0]}")
            continue
        for warning in warnings:
            print(f"  Note: {warning}")
        return cents / 100


# --- Asking questions -----------------------------------------------------------------------

def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        raise SetupError("Setup needs you to answer questions; run it in a terminal window.") from None


def ask_number(question: str, count: int, default: int) -> int:
    while True:
        answer = _ask(f"{question} [{default}]: ") or str(default)
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer)
        print(f"  Please type a number from 1 to {count}.")


def ask_number_list(question: str, count: int, default: list) -> list:
    default_text = ",".join(str(n) for n in default)
    while True:
        answer = _ask(f"{question} [{default_text}]: ") or default_text
        parts = [p.strip() for p in answer.replace(" ", ",").split(",") if p.strip()]
        if parts and all(p.isdigit() and 1 <= int(p) <= count for p in parts):
            return list(dict.fromkeys(int(p) for p in parts))
        print(f"  Please type numbers from 1 to {count}, separated by commas.")


def ask_yes_no(question: str) -> bool:
    return _ask(f"{question} Type yes or no: ").lower() in ("y", "yes")
