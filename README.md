# Mug Lister

A small tool that turns your finished mug designs (PNG images) plus a spreadsheet into
11oz white ceramic mug products on **Printify**. When you explicitly ask, it also
publishes them to your **Etsy** shop through Printify.

You drop images into the `incoming` folder, fill in `listings.csv`, and run one command.

> **Safe by default.** Running the tool with no options is a *dry run*: it checks
> everything and shows what it *would* do, but changes nothing. Real changes only
> happen with `--create` or `--publish`, and publishing asks you to type `yes` first.

---

## What's in this folder

| File / folder | What it is | Do you edit it? |
|---|---|---|
| `incoming/` | Put new PNG designs here | Yes, every week |
| `done/` | Images move here after their product is created | No |
| `listings.csv` | Your spreadsheet: one row per design | Yes, every week |
| `description_template.txt` | Standard text added to the end of every description | Once, then rarely |
| `blocked_terms.txt` | Brand/trademark words that make a row get skipped | Whenever you like |
| `config.json` | Your shop, mug and provider choices (filled in by `setup`) | Only `default_price` and `description_format` |
| `.env` | Your secret Printify token (you create this file) | Once |
| `.env.example` | A sample showing what `.env` should look like | No |
| `log.csv` | A record of every listing the tool created, published, skipped or failed | No (but read it!) |
| `main.py`, `listing_rules.py`, `printify_api.py`, `setup_wizard.py` | The program itself | No |

---

## One-time setup

You only do this once. It takes about 15 minutes.

### 1. Install Python

Download Python 3 (version 3.9 or newer) from <https://www.python.org/downloads/> and install it.
**On Windows**, tick the box **"Add Python to PATH"** on the first screen of the installer.

### 2. Open a terminal in this folder

- **Mac:** open the *Terminal* app, type `cd ` (with a space after it), drag this folder
  onto the Terminal window, and press Return.
- **Windows:** open this folder in File Explorer, click the address bar at the top,
  type `cmd`, and press Enter.

> In this guide, commands start with `python`. **On a Mac, type `python3` instead.**
> On Windows, if `python` doesn't work, try `py`.

### 3. Install the two libraries the tool uses

```
python -m pip install -r requirements.txt
```

### 4. Add your Printify token

Follow [How to get your Printify token](#how-to-get-your-printify-token) below, then:

1. Make a copy of `.env.example` and name the copy exactly **`.env`** (nothing before the dot).
2. Open `.env` in a plain text editor (Notepad, TextEdit) and replace `paste-your-token-here`
   with your token. It should look like `PRINTIFY_API_TOKEN=eyJ0eXAiOiJKV1Qi...` with no
   spaces or quotes.

> **Windows tip:** Windows sometimes secretly saves the file as `.env.txt`. In File
> Explorer, turn on *View → Show → File name extensions* to check, and rename it if needed.

### 5. Run setup

```
python main.py setup
```

Setup only *reads* from Printify; it never changes anything in your account. It asks you
five questions. Pressing Enter accepts the suggested answer in `[brackets]`.

1. **Which shop** is your Etsy shop?
2. **Which mug.** It searches Printify's catalog for 11oz mugs and suggests the best match
   for "11oz white ceramic mug".
3. **Which print provider.** It shows where each provider ships from, how long it takes to
   print, and the US shipping cost, and explains the trade-off between price and speed.
4. **Which mug option(s)**, for example "11oz / White". It also saves the size of the print
   area, which the tool uses to check your images.
5. **Your default price** (used when a row's price cell is empty).

Your answers are saved in `config.json`. You can run setup again any time to change them.

### 6. Check the standard description text

Open `description_template.txt` and read it. In particular:

- **The `Care:` line must match what your print provider says.** Setup shows what
  Printify's catalog says about care (for example "dishwasher and microwave safe"). It's also
  on the mug's page in the Printify catalog. Edit the line if it doesn't match.
- Adjust anything else you like, but keep it at least 8 non-empty lines and roughly
  900-1100 characters, so your per-design text only needs to provide the rest.

### 7. Do one test product first (important)

1. Put **one** PNG in `incoming/` and give it **one** row in `listings.csv` (delete or replace the
   two sample rows).
2. Run `python main.py check` and fix anything marked FAIL.
3. Run `python main.py` (dry run) and read what it says it would do.
4. Run `python main.py --create --limit 1`.
5. In the **Printify dashboard → My Products**, open the new product and check:
   the mockup (is the design centered and filling the mug?), the title, the tags, the price,
   and that **the description's line breaks look right**.
6. Publish that one product from Printify (or later with `--publish`), then open the listing
   **on Etsy** and check the **line breaks again**. Etsy shows plain text, so this is where
   formatting problems would show up.
7. If the line breaks look wrong on Printify or Etsy, open `config.json`, change
   `"description_format": "html"` to `"description_format": "plain"`, delete that test product,
   and try again (see [Redo a product](#i-want-to-redo-a-product)).

---

## How to get your Printify token

A token is like a password that lets this tool use your Printify account. Keep it secret.

1. Log in to the **Printify dashboard** at <https://printify.com>.
2. Click your **account** (top-right corner) and open your account settings.
3. Go to **Connections**.
4. Find **Personal access tokens** (Printify may label it "API tokens") and click **Generate**.
5. Give it a name you'll recognize, like `mug lister`.
6. When asked about access scopes, choose **all scopes / full access**. The tool needs
   to read your shops and the catalog, upload images, and create and publish products.
7. **Copy the token right away.** Printify only shows it once. Paste it into your `.env`
   file (step 4 above).

The tool never prints your token, never writes it to `log.csv`, and `.gitignore` stops `.env`
from being uploaded if you ever use git. If you think the token leaked, delete it in the same
Connections page and generate a new one. (Printify may rename these menus over time.)

---

## Weekly workflow

1. **Export your designs as PNGs** into `incoming/`. Give every design its own file name
   (for example `sunrise-fox.png`). For a sharp print, make them at least the print-area size
   that `python main.py check` mentions, in the same shape.
2. **Fill in `listings.csv`**, one row per image (see [The spreadsheet](#the-spreadsheet-listingscsv)).
3. **Check:** `python main.py check`. Fix every row marked **FAIL**, and read the warnings
   on rows marked **PASS\***.
4. **Dry run:** `python main.py`. Read the plan. Nothing happens yet.
5. **Do it for real**, choosing one of:
   - `python main.py --create` to create the products in Printify **without** publishing,
     so you can review them first. Publish from the Printify dashboard when you're happy.
   - `python main.py --publish` to create the products **and** publish them to Etsy. You'll
     see a summary (how many listings, every title and price) and must type `yes`.
6. **Look at `log.csv`** to see what happened to each image.

You can leave old rows in `listings.csv`: anything already created is skipped
automatically and shown as `DONE`. Or delete old rows to keep the sheet tidy.

---

## What each mode does

| Command | Internet? | What happens |
|---|---|---|
| `python main.py check` | No | Checks every row's text and image and prints a table: title length, description length, line count, tag count, PASS/FAIL. Changes nothing. |
| `python main.py` | No | **Dry run.** Same checks, then shows exactly what `--create` would do for each ready row. Changes nothing: no uploads, no moved files, no log entries. |
| `python main.py --create` | Yes | Uploads each ready image, creates an **unpublished** mug product in Printify, moves the image to `done/`, and writes to `log.csv`. Nothing goes to Etsy. |
| `python main.py --publish` | Yes | Shows a summary and asks you to type `yes`. Then does everything `--create` does **and** asks Printify to publish each product to your Etsy shop. |
| `python main.py setup` | Yes (read-only) | Picks your shop, mug, print provider and mug options, and saves them to `config.json`. |
| `--limit N` | | Handle at most N listings this run. **The default is 10.** For example `--publish --limit 25` allows up to 25, and `--create --limit 1` makes just one. Extra ready rows wait for the next run. |

Other safety features:

- **No duplicates.** Before doing anything, the tool reads `log.csv` and skips every image
  that was already created or published. If Printify answers with an error while creating a
  product, the tool checks whether the product was made anyway before trying again.
- **Price check.** After creating a product, the tool shows Printify's production cost. In
  `--publish` mode it will **not** publish a product whose price doesn't cover that cost.
- **Retries.** If Printify says "too many requests" (rate limit) or has a server problem, the
  tool waits (2s, 4s, 8s, 16s) and tries again, up to 5 tries in total. If it still fails, or if
  3 listings in a row fail, it stops cleanly and tells you what happened.
- **Everything goes through Printify.** The tool never talks to Etsy directly.
- Publishing a listing on Etsy normally costs Etsy's listing fee, so the `--publish`
  summary reminds you before you type `yes`.

---

## The spreadsheet (`listings.csv`)

The first row must be the column names: `filename,title,tags,price,description`

| Column | What to put |
|---|---|
| `filename` | The image's exact file name in `incoming/`, e.g. `sunrise-fox.png` |
| `title` | 130-140 characters (see rules below) |
| `tags` | Up to 13 tags separated by **semicolons**: `fox mug; watercolor fox; nature lover gift` |
| `price` | In US dollars, like `17.99`. Leave it empty to use `default_price` from `config.json` |
| `description` | Your design-specific intro (the standard template text is added automatically) |

**Line breaks in the description.** Use any of these:
- a real line break inside the cell (Excel on Windows: **Alt+Enter**; Excel on Mac:
  **Control+Option+Return**; Google Sheets: **Ctrl+Enter**),
- ` | ` (space, vertical bar, space): `First line | Second line`,
- or `\n`: `First line\nSecond line`.

Save the file as **CSV**. In Excel, choose **"CSV UTF-8"** so accented letters survive.
The two sample rows show both styles: the fox row uses real line breaks and the moth row
uses ` | `.

---

## The listing text rules

These are checked in **every** mode, including `check` and the dry run. A row that breaks a
rule is **skipped** (never uploaded), and the tool tells you exactly what to fix.

**Title**
- At least **130** and at most **140** characters (140 is Etsy's maximum).
- Spaces at the very start and end don't count (they're trimmed).
- If a title is outside 130-140, you'll see its current length and how many characters to
  add or remove.

**Description**
- The final description is: *your text from the spreadsheet*, then a blank line, then the
  text in `description_template.txt`.
- That combined text must have at least **1800 characters** (spaces count; line breaks don't)
  **and** at least **12 non-empty lines** (blank lines don't count).
- If it's too short, you'll see the current character count, the current line count, and how
  much is missing. The template provides roughly 950 characters and 12 lines, so your
  per-design text needs to add about 850+ characters.
- Line breaks are kept. Printify's description field accepts HTML, so each line is sent as its
  own paragraph. (Set `"description_format": "plain"` in `config.json` to send plain text instead.)

**Tags** (Etsy's rules)
- At most **13** tags, each at most **20** characters, separated by semicolons.
- Fewer than 13 is allowed, but you'll get a warning, because every tag helps buyers find you.

**Brand / trademark check**
- If the title, tags or description contains a word or phrase from `blocked_terms.txt`
  (Disney, Pokemon, Nike, Taylor Swift, Stanley, Harry Potter, "in the style of", ...),
  the row is skipped with a message saying which word and where.
- Edit `blocked_terms.txt` to add your own words, one per line. This list is a safety net,
  not legal advice.

**Images** (warnings: the row still passes, but read them)
- The image must be a real PNG file in `incoming/` (a missing or non-PNG file fails the row).
- You'll be warned if it's **smaller than the mug's print area** (it would print blurry), or if
  its **shape** doesn't match (part of the design would be cut off, and the warning says how much).
- The design is always placed in the center and scaled to fill the whole print area.

---

## Reading `log.csv`

Every listing the tool handles in `--create` or `--publish` mode gets a line with: file name,
Printify product ID, title, title length, description length, description line count,
status, time, and the error (if any).

| Status | Meaning |
|---|---|
| `created` | The product exists in Printify (not published by this line). The image is in `done/`. |
| `published` | Printify accepted the request to publish it to Etsy. |
| `failed` | Something went wrong **before** the product was created. The image stays in `incoming/` and will be retried next run. |
| `skipped` | The row broke a rule; see the error column. |

In `--publish` mode each successful mug gets **two** lines: `created`, then `published`. The
tool writes `created` the moment the product exists, so even if the run is interrupted, it
will never create that product twice.

---

## Troubleshooting

**"python is not recognized" / "command not found: python"**
On a Mac use `python3`. On Windows try `py`, or reinstall Python with "Add Python to PATH" ticked.

**"A required library is missing"**
Run `python -m pip install -r requirements.txt` in this folder.

**"No Printify token found"**
The file must be named exactly `.env` (not `.env.txt`, not `env`) and be in the same
folder as `main.py`, with a line like `PRINTIFY_API_TOKEN=...`.

**"Printify rejected your token (HTTP 401)"**
The token is mistyped, expired, or was deleted. Generate a new one and paste it into `.env`.

**"Your token isn't allowed to do this (HTTP 403)"**
The token is missing access scopes. Generate a new one with all scopes.

**"Printify's rate limit was reached (HTTP 429)" / "Gave up after 5 tries"**
The tool already waited and retried. Printify allows about 200 publishes per 30 minutes, so
wait half an hour and run the same command again. Nothing will be duplicated.

**A row says FAIL**
Read the message under the table. It says exactly what to change (for example "Add 12 more").
Fix the spreadsheet, save it, and run `python main.py check` again.

**A brand-check false alarm** (for example your design really is about a man named Stanley)
Remove that line from `blocked_terms.txt`, or reword the listing.

**Image warnings: blurry or cut off**
Re-export the design at the print-area size shown in the warning (and the same shape).

**Line breaks don't show correctly on Printify or Etsy**
Change `"description_format"` in `config.json` from `"html"` to `"plain"` (or back) and test
with one product, as in [step 7](#7-do-one-test-product-first-important).

**"Can't write to log.csv" / "Can't open listings.csv"**
The file is probably open in Excel. Close it and run again.

**Accented letters look strange**
Save `listings.csv` as **CSV UTF-8** in Excel.

**I stopped the program in the middle (Ctrl+C)**
Finished items are already in `log.csv`. If you stopped while "Creating the mug product..."
was showing, look in Printify → My Products for that title. If it's there, delete it before
running again, so you don't end up with two.

**"Created but NOT published"**
The product is safe in Printify but didn't get published (the message says why). Fix the
problem and click **Publish** on that product in the Printify dashboard. Running the tool
again will not create a duplicate.

**I published, but the listing isn't on Etsy yet**
It can take a few minutes. If it still isn't there, open the product in Printify to see its
publishing status. Your Etsy connection in Printify may need reconnecting.

<a id="i-want-to-redo-a-product"></a>
**I want to redo a product**
1. Delete the product in Printify (and on Etsy, if it was published).
2. Delete that image's lines from `log.csv`.
3. Move the image from `done/` back to `incoming/`.
4. Run the tool again.

**I want a different mug, provider or default price**
Run `python main.py setup` again. (You can also change `default_price` directly in `config.json`.)

**Printify changed their API**
All Printify calls are in `printify_api.py`; the tool uses Printify's API v1
(<https://developers.printify.com/>).
