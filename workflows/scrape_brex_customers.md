# Workflow: Scrape Brex Customers

## Objective
Compile a comprehensive list of companies that use Brex by scraping multiple data sources.

## Output
- **File:** `.tmp/brex_customers.csv`
- **Format:** Single column with company names (deduplicated, sorted alphabetically)

---

## Data Sources

### 1. G2 Reviews
- **URL:** https://www.g2.com/products/brex/reviews
- **Actor:** `focused_vanguard/g2-reviews-scraper`
- **Cost:** ~$0.005 per review
- **What we extract:** Reviewer's company name from review metadata

### 2. Capterra Reviews
- **URL:** https://www.capterra.com/p/167393/Brex/reviews/
- **Method:** RAG web browser scrape
- **Cost:** Free (uses platform credits)

### 3. Brex Website
- **URLs:**
  - https://www.brex.com/customers
  - https://www.brex.com/resources/case-studies
- **Method:** RAG web browser scrape
- **Known companies:** DoorDash, Airtable, SeatGeek, Hims, BambooHR, Gong, Lemonade

### 4. FeaturedCustomers.com
- **URL:** https://www.featuredcustomers.com/vendor/brex/case-studies
- **Method:** RAG web browser scrape
- **Data:** 29 case studies with company names

### 5. LinkedIn Jobs
- **Logic:** Companies posting jobs that mention "Brex" in requirements/benefits use Brex
- **Actor:** `curious_coder/linkedin-jobs-scraper`
- **Search terms:** "Brex corporate card", "Brex expense management"
- **Cost:** ~$0.001 per job listing

### 6. News & Press
- **Search queries:** "company switched to Brex", "Brex customer announcement"
- **Method:** RAG web browser with Google search
- **Cost:** Free

---

## Running the Tool

### Test Mode (validates all sources work)
```bash
python tools/scrape_brex_customers.py --test
```
- Limits each source to 10 results
- Quick validation run

### Full Run
```bash
python tools/scrape_brex_customers.py
```
- Scrapes all sources completely
- Estimated cost: $3.50-5.00
- Estimated time: 5-10 minutes

---

## Data Processing

1. **Normalization:** Remove suffixes like "Inc.", "LLC", "Corp.", "Ltd."
2. **Deduplication:** Use Python set to eliminate duplicates
3. **Sorting:** Alphabetical order
4. **Export:** CSV with single `company_name` column

---

## Edge Cases

### Missing Company Names
- Some G2 reviewers don't list their company
- Filter these out (skip empty/null values)

### Company Name Variations
- "DoorDash" vs "DoorDash, Inc." vs "DoorDash Inc"
- Normalization handles this

### Rate Limiting
- Apify actors handle their own rate limiting
- RAG browser has built-in delays

---

## Estimated Results
- **G2 Reviews:** 100-300 unique companies
- **Capterra:** 50-100 unique companies
- **Brex Website:** 20-50 companies
- **FeaturedCustomers:** 29 companies
- **LinkedIn Jobs:** 50-200 companies
- **News:** 10-30 companies

**Total after deduplication:** 200-500+ unique companies

---

## Troubleshooting

### G2 Scraper Returns Empty
- Check if URL format changed
- Verify Apify API token is valid
- Try reducing maxReviews

### LinkedIn Jobs Not Working
- LinkedIn may block scraping temporarily
- Wait and retry, or skip this source

### High Costs
- Reduce maxReviews in G2 scraper
- Skip LinkedIn jobs source
- Use test mode first to validate
