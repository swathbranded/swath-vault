# The SWATH Vault

The Vault is the static site at vault.swathbranded.com: everything behind The Collection,
generated every night from PCNA's public product feeds. No database, no server, no monthly fee. The listing page has
search and filters, every item has its own page, and every page ends in a
"Request a quote" button that points at the Framer quote form.

## What is in this folder

| File | What it does |
|---|---|
| `build.py` | Downloads the four PCNA feeds, groups them by style, applies the rules in `config.json`, writes the site to `dist/` |
| `config.json` | Everything you would want to change without touching code: links, pricing display, filters, featured items, the "How this works" copy |
| `templates/` | The page layouts (Jinja2 HTML). `base.html` is the header and footer, `index.html` the listing, `product.html` the item page |
| `static/` | The stylesheet and the two small scripts (`vault.js` for the listing filters, `product.js` for the item gallery) |
| `assets/` | SWATH logo files and favicon |
| `.github/workflows/build.yml` | The nightly rebuild and deploy to GitHub Pages |

`cache/` (the downloaded feeds) and `dist/` (the built site) are generated and are not
meant to be committed.

## Run it on your own computer

You need Python 3.10 or newer.

```
pip install -r requirements.txt
python build.py --local
```

The first run downloads about 11 MB of feeds into `cache/` and keeps them for 20 hours.
`--local` writes links that work when you double-click `dist/index.html`, so you can
browse the whole Vault offline. Leave `--local` off for the real deployment so the URLs
stay clean (`/p/1603-21/` instead of `/p/1603-21/index.html`).

Other options: `--refresh` forces a fresh download, `--limit 200` builds only the first
200 styles for a quick test.

## Deploy it (GitHub Pages, free)

1. Create a GitHub account if you do not have one, then create a new repository called
   `swath-vault`. Public or private both work with Pages on a free account, but a
   public repo is simplest.
2. Upload everything in this folder to the repository (the GitHub website lets you drag
   a folder onto the "uploading an existing file" page, or use GitHub Desktop).
3. In the repository, open Settings, then Pages. Under "Build and deployment" set Source
   to **GitHub Actions**.
4. Open the Actions tab and run "Build and publish the Vault" once (or just push any
   change). The first build takes about two minutes.
5. Custom domain: in Settings, then Pages, enter `vault.swathbranded.com`. At your DNS
   provider add a CNAME record: host `vault`, value `YOUR-GITHUB-USERNAME.github.io`.
   Tick "Enforce HTTPS" once the certificate shows up (usually within an hour).

After that the workflow rebuilds the site every morning at 4:15 AM Central, a couple of
hours after PCNA regenerates its feeds, so the Vault never goes stale. You never need to
touch it again unless you want to change something below.

If you would rather use Cloudflare Pages or Netlify, both can run the same command
(`pip install -r requirements.txt && python build.py --refresh`, output folder `dist`).

## Live stock (PromoStandards)

Every nightly build also asks PCNA's PromoStandards Inventory service how many of each
item are on hand. Item pages get a stock line (In stock, Limited stock, Restocking with the
next arrival date, or Made to order), each color shows its own count, listing cards show an
"In stock" tag, and the filters get an "In stock only" checkbox. "Limited stock" means fewer
than `pricing.program_quantity` pieces (100) across all colors. The check takes about six
minutes for the full Vault.

The login is never stored in this folder. It comes from two environment variables:

| Name | Value |
|---|---|
| `PCNA_PS_ID` | PromoStandards production ID from Kerry Schutte's email |
| `PCNA_PS_PASSWORD` | PromoStandards production password from the same email |

On GitHub: repository Settings, then Secrets and variables, then Actions, then "New
repository secret", once for each name. The workflow already passes them to the build.

If the secrets are missing, the password is wrong, or PCNA's service is down, the build
still publishes. It reuses the last good stock check if it is under 48 hours old
(`inventory.max_cache_hours`), and otherwise publishes without stock lines. Turn the
feature off entirely with `"inventory": {"enabled": false}` in `config.json`.

The PromoStandards specification documents are licensed for internal use only, so keep
them out of this repository. They live in `Suppliers/PCNA/PromoStandards/`.

## Things you will actually want to change

All of these live in `config.json`.

**Which pieces show up.** `filters.min_from_price` drops anything whose display price is
below the floor (currently $3). `exclude_name_keywords` removes items whose name contains
a word (stress balls, lanyards and drawstring bags are already out). `exclude_categories`,
`exclude_brands` and `exclude_styles` do what they say. Style numbers are the ones shown on
each item page.

**Featured items.** `featured_styles` is the row under the hero titled "From The
Collection". Put the style numbers of the Collection items there, in the order you want.

**Pricing.** `pricing.mode` is `list` (show PCNA list price, the safe public number) or
`markup` (take PCNA net and divide by one minus `markup_margin`, currently 40%). Cards and
item pages lead with the decorated price at `program_quantity` (100). `show_price_table`
turns the full price-by-quantity table on or off, and `show_blank_prices` adds a blank
column to it. `spotlight_price` sets which price point sorts to the top of the default
listing. `note` is the sentence printed under every price.

**Links.** `quote_url` and `quote_param`: the quote button opens the Framer quote page
with `?piece=Item name (style)` on the end. If you add a small code override in Framer that
reads the `piece` query parameter and fills the "Piece of interest" field, the form arrives
pre-filled. Until then the button simply lands on the form.

**Copy.** `how_it_works` is the three-step strip near the bottom of the listing page.
Hero copy lives in `templates/index.html`. The word "catalog" is kept out of everything the
visitor sees on purpose (the CSS class names are the only place it survives).

## What the site deliberately does not do

- It never shows PCNA net pricing. The build reads it only when `pricing.mode` is
  `markup`, and even then it publishes your marked-up number, never the net.
- It never names the supplier. Brand names that are really PCNA business units (Leed's,
  Bullet, Trimark) are blanked, and any sentence in a description that mentions the
  supplier is dropped. Product images are hotlinked from PCNA's image server, which is
  what the media feed is for, so the image URLs do carry their domain.
- It has no live inventory. That needs PCNA's PromoStandards web services, which are
  requested through the "Get Web Services" link on pcna.com/en-us/tools-services/electronic-integration
  or by emailing Digital_Sales@Leedsworld.com. Once approved, an inventory step can be
  added to `build.py`.
- It never uses an em dash, including in supplier copy, which the build rewrites.
