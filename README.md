# autobook

Turns a folder tree of ~1000 photos into a print-ready photo book of ~200 well-chosen photos.
autobook picks the photos, builds chapters and titles, and hands the layout to
[Fotobuch](https://github.com/EddyXorb/fotobuch), which lays out the pages (no cropping) and exports a
300 DPI PDF with bleed. You review in the browser and fine-tune in Fotobuch's GUI.

## Install on Windows

1. Download `autobook-win-Setup.exe` from the newest release on the project's GitHub page
   (*Releases*, right-hand side) and open it.
2. Windows may say "Windows protected your PC", because the installer is not signed: click *More info*,
   then *Run anyway*. This only happens at the first installation.
3. autobook installs itself for your Windows user (no administrator needed) with an icon on the desktop
   and in the Start menu, opens in your browser and shows a small icon next to the clock: *Open
   autobook*, *Check for updates*, *Quit*. Starting it again just opens the browser again.
4. For the AI (best photos, titles; a book usually costs a few cents): *⋯* → *OpenAI key*, paste your
   key from [platform.openai.com/api-keys](https://platform.openai.com/api-keys). It is kept in Windows'
   password store (Credential Manager). Without a key autobook chooses the photos without AI.
5. **Updates**: when a new version is out, autobook says so at the top of the page; *Update now* loads
   it and restarts autobook in a few seconds (never while it is working on a book). Your books stay in
   `C:\Users\<you>\autobook`, also when you uninstall autobook (Windows settings → Apps).

Fotobuch comes with the Windows app. A log for troubleshooting is in
`%LocalAppData%\autobook\Logs\autobook.log`.

## Install on Linux (or Windows from source)

1. Python 3.12+ and [uv](https://docs.astral.sh/uv/).
2. Fotobuch v0.3.x from its [releases](https://github.com/EddyXorb/fotobuch/releases); put `fotobuch` on your
   `PATH` or set `FOTOBUCH_BIN`.
3. `uv tool install .` (or `uv sync` and use `uv run autobook …` inside the repo).
4. Optional, for AI selection and titles: `autobook set-key` (stored in the OS keyring) or `OPENAI_API_KEY`.
5. `autobook doctor` checks everything.

## Use

`autobook ui` opens the browser app (`autobook install-shortcut` puts it into the desktop's app menu). It is
made for the whole family: plain words, large photos and buttons, and five steps from folder to print file.
It speaks German and English (switch at the top right; a new language is one more file in
`src/autobook/web/static/i18n/`).

- **My books**: every book in your library (`~/autobook`, or `--library DIR` / `AUTOBOOK_LIBRARY`) as a cover,
  with its state in words ("Ready to print", "Step 3 of 5").
1. **Photos**: pick the folder from tiles with preview photos, dates and a favourites marker; autobook looks
   at the photos (dates, places, sharpness, near-identical shots, your favourite marks).
2. **Wishes**: what was found, then the questions as large cards (favourites, book shape, how thick, chapters,
   focus, title tone, language). The book's language is suggested from your folder names. More settings
   (photos per page, subtitles, AI on/off and thoroughness) are folded away. *Make my book* shows the AI's
   time and cost first; the run can be left and picked up again.
3. **Selection**: the chosen photos per chapter in their own shape. Click one to see it large (← →, Esc) and
   make it bigger, use it as chapter photo or cover, move it, take it out, or swap it for another shot of
   the same moment. Titles change by clicking them; the cover is chosen from candidates. Everything is saved
   by itself, and every change can be undone.
   - **Fotos im Buch**: one slider for the whole book. While you drag, you see which photos would leave
     (dimmed) and which would come in ("neu"), with the page count; letting go applies it. Ticks mark counts
     that fill whole pages the printer takes, and letting go close to one snaps to it. − / + next to a
     chapter title does the same for one chapter. Nothing needs a new AI run: autobook takes the next-best
     photos of those days (by the scores it already has) or takes out the weakest, and never touches your
     must-have favourites or photos you put in or took out yourself.
   - **Fotos hinzufügen** under a chapter shows every photo of its days (or of the whole trip), marked when
     it is in the book, a favourite, blurry or like one in the book; tick several and add them at once.
4. **Book**: the laid-out pages as spreads, laid out again by themselves after changes. Click a photo on a page
   to swap, enlarge or remove it, or to choose its cut: drag the photo inside its space (or use the arrow
   keys) to decide which part is cut away – also for the cover and full-page chapter photos. Move a photo to
   the previous or next page, or drag it onto the other page of the spread; the chapter then keeps your page
   split ("Seiten automatisch verteilen" gives it back to autobook). "Ganze Seite" gives a photo a page to
   itself, "Über beide Seiten" lets a wide photo run across a spread as a panorama. Under the spread: the
   same photo-count slider, the frame around the photos (none / narrow / wide, the same on every page) and
   the book's colour; the palette button on a page gives that page its own colour.
5. **Print**: the 300 DPI print file is made and checked by itself; the result is a short checklist, how to
   order, and the technical report for experts.

The *More* menu has the preview PDF, the book's folder, laying all pages out anew, and the favourites test.

The same steps on the command line:

```sh
autobook scan ~/Pictures/Portugal2026 --work portugal   # EXIF/GPS, thumbnails, blur, bursts, events, favourites
autobook brief --work portugal --ai                     # the questions in the terminal → brief.json
autobook select --work portugal                         # photos, chapters, titles → book_plan.json
autobook export --work portugal                         # Fotobuch project + preview PDF
autobook release --work portugal                        # 300 DPI PDF + preflight against the printer profile
```

`autobook review --work portugal` opens a book outside the library in the browser app. Everything at once
with defaults: `autobook all ~/Pictures/Portugal2026 --work portugal --offline`. Try it without photos:
`autobook demo-photos demo && autobook all demo --work demo-book --offline`.

The Fotobuch project lives in `<work>/fotobuch/book`; open it with `fotobuch-gui` to swap photos between
pages or change weights. Laying out again after review changes updates the project in place and keeps which
photo is on which page, but autobook arranges every photo page anew so it fills the frame (slot edits made
in Fotobuch are replaced); a new cover photo replaces the old one in place. If nobody edited the
layout in Fotobuch and many photos changed (more than 20 and 15 %), the pages are laid out from scratch
instead, which is faster and better balanced (`--fresh` forces that). After `fotobuch rebuild --all` run
`autobook fix-dividers`. The preview PDF (`book.pdf`) is clean by default; `autobook export --guides` adds
Fotobuch's PREVIEW watermark and the red bleed / blue margin lines. The print file never has them.

## Favourites

autobook looks for the marks you already use: folders whose name contains a word like *Favoriten*,
*favourites*, *beste*, *best of*, *Auswahl*, *highlights* or *Fotobuch* (also "Favoriten 7", "beste Bilder"),
and file names that start with `+` (or another mark, or end in `_fav` / `-best`) on at least 3 photos.
Camera prefixes like `_DSC`, `IMG_`, `PXL_` don't count. Marks add up: a favourites folder is level 1 and
each `+` one more (at most 3), so `Favoriten/+P123.jpg` ranks above `Favoriten/P124.jpg`. A text-only AI
check of the folder names and patterns (no photos, about $0.001) confirms which marks are favourites
and which mean "the best"; without AI the heuristic decides. You confirm the marks in the first question
and choose how to use them:

- **All favourites, and the AI fills the gaps** (the default): every favourite is in the book; the AI sees
  them as already chosen and adds other moments and places up to the target. Favourites marked as the
  best get more room on the page (at most 20 % of a chapter).
- **Only the favourites**: exactly your selection; the AI only writes titles and picks cover and openers.
- **The best of the favourites** (suggested when there are more than 1.5× a usual book): the AI picks the
  target number among your favourites only, higher marks first, one shot per moment.
- **Ignore them**.

Cover and chapter openers prefer the highest-marked favourites that fit the page. `autobook eval
--favourites` measures how many of your favourites the AI would find on its own.

## What it does

- **Discovery**: all subfolders; JPEG, PNG, HEIC/HEIF, WebP, TIFF. Skips hidden/system folders and cloud-only
  placeholder files (OneDrive etc.), handles long Windows paths.
- **Ordering**: EXIF capture time (file time as fallback), events split by time gaps and GPS jumps,
  place names from offline GeoNames data. Folder names you gave the photos ("2026-05-03 Vogtsbauernhof")
  are used as titles offline and passed to the AI as the strongest hint about the place or occasion;
  camera names like `DCIM` or `100CANON` are ignored.
- **Filtering**: blur (sharpest-region Laplacian, relative to the event), exposure, screenshots,
  bursts and near-duplicates (perceptual hash; with AI also shots the model sees as "nearly the same
  picture") → one best shot per group.
- **Selection**: quota per chapter (main events weigh more), best photos with time spread, and a mix of
  people and places: "family first" (the default) aims for about 80 % photos with people and 20 % of the
  places (landscapes, sights, details); also balanced (50/50), places first (30 % people) or no target.
  With AI: OpenAI's `gpt-6-luna` at reasoning effort `high` scores every candidate (20–40 photos of one event
  per request), picks the final set per chapter (large chapters in time segments) and writes the chapter
  and book titles in one call each. When the AI has looked at a photo, its judgement replaces the local
  blur detector. Results are cached in the work directory per model and effort, so rebuilds cost nothing.
- **Even pages**: Fotobuch decides which photos share a page; autobook then arranges each page itself as
  rows and columns that fill the frame exactly, with 4 mm gaps, so every page has the same edge. Photos are
  trimmed by at most 15 % to fit (centred) and may swap places on their page; sizes stay balanced, and
  "Bigger in the book" photos stay bigger. A page that would need more trimming keeps its photos whole and
  gets even space on two sides (in the test books: about 1 mm on 5–12 of ~60 pages). Frame: none (photos
  run off the page), narrow (8 mm) or wide (16 mm); colours: 8 swatches for the book and single pages,
  with light text on dark pages.
- **Pages**: Fotobuch makes a first split of each chapter into pages; autobook then moves photos to the
  neighbouring page of their chapter where both pages fit better (the order of the photos stays), aiming at
  the book's photos per page. Page 1 is a title page; every chapter page and panorama starts on a left page,
  facing the photos that follow (one page is split or merged before it to make that work). The printer's
  page step is reached the same way, and a thin book spreads its photos over more pages; only when every
  page holds one photo are blank pages added – and with "Fotoanzahl an die Seiten anpassen" (on by default)
  autobook fills them with more photos of the trip instead.
- **Smart cut**: with AI, a small extra pass (about 2 cents for Jura's 285 photos) notes where the faces or
  the main subject are in each photo; trimmed photos, chapter pages and panoramas are cut around them.
  Your own cut always wins.
- **Book**: cover with title and dates (the cover photo is not repeated inside), a full-page opener per
  chapter with its title and dates (a sharp photo that fits the page shape, otherwise a plain page),
  page numbers. Subtitles under the titles are optional (settings, or
  `"subtitles": true` in `brief.json`). Default size: up to 250 photos at about 4.5 per page
  (`autobook brief --photos N --per-page X` to change). The book's language is suggested from your folder
  names ("Urlaub", "mit", "bis" → German).

## AI, privacy and cost

AI is opt-in per run. Only EXIF-stripped thumbnails (768 px for scoring, 512 px for ranking/titles) and place
names are sent to the OpenAI API (Responses API); the CLI asks before uploading (`--yes` skips the question,
`--offline` never uploads). Requests use `store: false`, and files uploaded for a batch are deleted once the
batch is read.

Every AI task uses `gpt-6-luna` ($0.10 input / $0.50 output per million tokens) at reasoning effort `high`;
change it with `--model` and `--effort` (`none` … `max`) on `select`, `brief`, `review`, `eval` and `all`.
Measured on two small folders (2026-10-30): `high` took 1.5 minutes and $0.01 for 56 photos, `max` took
5 minutes and $0.02 for 32 photos (about 10× more reasoning). That puts a 1000-photo book at roughly
$0.15–0.25 with `high` and $0.40 with `max`, mostly reasoning tokens. The pre-run estimate uses those
measurements per task (scoring per photo, ranking per chapter, titles per call); `--max-cost` (default $5)
stops before a run's estimate would exceed it. `autobook cost` shows the real spend per task with reasoning
tokens and seconds per call, and `select` lists any failed AI call and what autobook used instead (the review
page shows the same). `--batch` uses OpenAI's Batch API at half price but can take hours.
`autobook ai-calibrate` measures real image token counts with OpenAI's token-counting endpoint.

Speed: 8 AI calls run at the same time (`--parallel`; lower it if OpenAI reports rate limits). Measured on
2026-10-30: 1406 photos with 210 favourites filled to 250 in 2 minutes; 2941 photos, best 250 of 610
favourites in 4.5 minutes for $0.12 in total; laying out 270 photos from scratch takes 1.5 minutes, the
300 DPI print file about 5.

## Printing

Built-in profiles (`autobook profiles`) are generic: copy one (`src/autobook/resources/profiles/*.toml`) and
set your printer's exact trim size, bleed, cover width, spine growth and page-count rules, then pass
`--profile path/to/mine.toml`. `autobook release` checks page count and step, page and trim boxes,
embedded fonts, image resolution, transparency and file size, and splits the cover into its own PDF when the
profile says so. Output is sRGB (no CMYK/PDF-X); pick a printer that accepts that. Order a test print first.

**epubli** has its own formats (`epubli-a4-quer`, `epubli-a4-hoch`, `epubli-bildband`, `epubli-quadrat`;
in the app: Wishes → "epubli", or Print → "Andere Druckerei oder anderes Format"). For them autobook makes
the files epubli asks for in the book's `print` folder: the inner pages as one PDF (one page size), the
cover as JPG and PDF at the size you type in from [epubli's cover calculator](https://www.epubli.de/preise)
in the Print step (total width and height, spine, and the margin cut off or folded around the board:
3 mm softcover, about 11 mm hardcover). The PDF has no transparency, and a file over epubli's 250 MB upload
limit gets its photos encoded smaller (JPEG quality 92 down to 75, only as far as needed). Upload the
inner pages as the content file and the cover with "Eigenes Gesamtcover hochladen".

## Evaluate the selection

`autobook eval --work portugal --favourites` uses your favourite marks as the gold set; or mark a gold set in
the browser app (*Favourites test*) and run `autobook eval --work portugal`. It compares random, local and AI
selection (recall of favourites, exact and same moment, duplicate leaks, event coverage, bad picks, cost).

## Development

`uv sync`, then `uv run pytest` (the end-to-end test needs `fotobuch`; AI tests use a fake client, no
network). `docs/spike-p0.md` has the Fotobuch findings, `docs/decisions.md` the decision log and the
deferred fork plan, `docs/schemas/` the JSON Schemas of `book_plan.json` and `brief.json`.
`src/autobook/resources/autobook.typ` is generated: edit `tools/make_template.py`, then run it.

autobook is free software under the GNU AGPL v3 (`LICENSE`); `NOTICE` lists what comes from others,
including Fotobuch (AGPL-3.0), whose page template autobook adapts and whose Windows build the app ships.
The browser app bundles the fonts Newsreader and Inter (SIL Open Font License, texts in
`src/autobook/web/static/fonts/`). The app is vanilla JavaScript modules without a build step
(`src/autobook/web/static/js/`, one module per step under `screens/`).

### Releasing a new version (Windows app and updates)

1. Raise `version` in `pyproject.toml` (e.g. `0.2.0` → `0.2.1`) and run `uv lock`.
2. Commit, then tag and push: `git tag v0.2.1 && git push origin main v0.2.1`.
3. `.github/workflows/release.yml` runs on GitHub's Windows machine: tests, builds the app with
   PyInstaller (`packaging/windows/autobook.spec`, with Fotobuch from `get_fotobuch.ps1`), tries the
   built app on demo photos, then makes the installer and the update files with
   [Velopack](https://docs.velopack.io) and publishes them as the GitHub release. Installed apps find it
   at their next start.

The installer is not signed; signing (e.g. Azure Trusted Signing) can be added to the `vpk pack` step
later without changing anything for installed apps.
