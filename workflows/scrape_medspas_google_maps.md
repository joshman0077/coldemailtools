# Workflow: Scrape All US Medspas from Google Maps

## Objective
Build a comprehensive list of medspa businesses across the entire United States from Google Maps, including contact details (email, social media) scraped from each business website.

## Output
- **File:** `.tmp/medspas_us.csv`
- **Fields:** placeId, title, address, city, state, postalCode, countryCode, phone, website, email, facebook, instagram, twitter, linkedin, rating, reviewsCount, url, categoryName
- **Deduplication:** by Google `placeId`

---

## Coverage Strategy

Google Maps caps results at ~120 places per search+location. To cover the full US TAM:

**Phase 1 — 50 States:** Run one job per state with 4 search terms. Catches rural and small-market medspas.

**Phase 2 — ~300 Cities (50k+ population):** Run one job per major US city. Catches dense metro medspas that overflow the state-level cap.

**Search terms used:** `medspa`, `med spa`, `medical spa`, `medi spa`

**Total jobs:** ~350 | **Estimated unique results:** 10,000–20,000

---

## Actor
- **ID:** `compass/crawler-google-places`
- **Pricing:** $0.004/place + $0.002/place contact enrichment (FREE tier)
- **Stats:** 308k users, 95.5% success rate, 4.71/5 rating

---

## Running the Tool

### Test Mode (~2 min, low cost)
```bash
python tools/scrape_medspas_google_maps.py --test
```
Runs TX, CA, NY with 20 results each. Validates the pipeline before a full run.

### Single State
```bash
python tools/scrape_medspas_google_maps.py --state Florida
```
Runs all jobs for one state (state job + city jobs for that state).

### Full Run (~2–4 hours, ~$90)
```bash
python tools/scrape_medspas_google_maps.py
```
Runs all 50 states + ~300 cities. Progress is checkpointed after every job.

### Resume After Failure
Just re-run the same command — the script reads `.tmp/medspas_progress.json` and skips completed jobs automatically.

### Incremental Run (fill gaps, skip already-scraped places)
Use `--exclude-file` to pre-load an existing CSV's `placeId` column. Any place already in that file is skipped in the output — no duplicates, no wasted credits on places you already have.

```bash
# Fill Texas gap (Houston/Dallas/San Antonio all missing from original scrape)
python tools/scrape_medspas_google_maps.py \
  --state Texas \
  --exclude-file medspas_us_64k.csv \
  --output .tmp/medspas_tx_new.csv

# Fill New York gap (NYC metro nearly empty)
python tools/scrape_medspas_google_maps.py \
  --state "New York" \
  --exclude-file medspas_us_64k.csv \
  --output .tmp/medspas_ny_new.csv
```

Note: Apify still charges per place *scraped* before dedup. Running `--state Texas` will scrape the full state — the exclude-file dedup only affects your output CSV, not Apify costs. Since TX and NY were essentially unscraped, overlap with the existing file will be near zero, so cost waste is minimal.

### Without Contact Enrichment (~$60)
```bash
python tools/scrape_medspas_google_maps.py --no-enrich-contacts
```

---

## Cost Breakdown

| Tier | Per place (base) | Per place (+ contacts) |
|------|-----------------|----------------------|
| FREE | $0.004 | $0.006 |
| SILVER | $0.003 | $0.0045 |
| GOLD | $0.0021 | $0.00315 |

At 15,000 places on FREE tier: **~$90 total**

---

## Edge Cases

### Run Hits ~120 Results Per Search
Google Maps caps at ~120 per location. Phase 2 city-level runs handle this — dense metros like LA/NYC/Houston are covered by their city jobs, not just the state job.

### Actor Run Fails Mid-Job
The script exits with an error and saves progress. Re-run the same command to resume. No duplicate charges — completed jobs are skipped.

### Contact Enrichment Returns Empty
Normal for businesses without a website. The `email`, `facebook`, etc. columns will be blank for those rows.

### Duplicate Places Between Phases
Handled automatically — deduplication by `placeId` ensures each medspa appears once regardless of how many jobs found it.

### City Not in List
The hardcoded city list covers all US cities with 50k+ population. Medspas in smaller cities are caught by the Phase 1 state-level run.

---

## Estimated Results by State
High-density medspa states (expected top results):
- California: 1,500–3,000
- Texas: 1,000–2,000
- Florida: 800–1,500
- New York: 600–1,200
- Illinois: 400–800

Low-density states: 20–100 medspas each
