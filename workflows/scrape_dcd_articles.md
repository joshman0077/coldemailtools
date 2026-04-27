# Scrape Data Center Dynamics Construction Articles

## Objective
Extract all article URLs from the Construction & Site Selection news section of datacenterdynamics.com.

## Target URL
```
https://www.datacenterdynamics.com/en/news/?term=construction-site-selection
```

## Website Structure
- **Listing pages**: Paginated at `?page=N&term=construction-site-selection`
- **Total pages**: ~257 (as of Jan 2026)
- **Articles per page**: ~25-30
- **Article link pattern**: `/en/news/[article-slug]/`

## Required Inputs
| Input | Source | Notes |
|-------|--------|-------|
| APIFY_API_TOKEN | `.env` | Apify API key |

## Tool
`tools/scrape_dcd_articles.py`

## Apify Actor
**apify/cheerio-scraper** (FREE)
- Fast HTTP-based scraping
- No browser needed
- Perfect for extracting links from HTML

## Configuration

### Start URL
```
https://www.datacenterdynamics.com/en/news/?term=construction-site-selection
```

### Link Selector
```css
a[href]
```

### Glob Patterns (pagination only)
```
https://www.datacenterdynamics.com/en/news/?page=*&term=construction-site-selection
```

### Page Function
```javascript
async function pageFunction(context) {
    const { $, request, log } = context;

    // Extract all article links from the listing page
    const articleUrls = [];

    $('h1 a[href*="/en/news/"]').each((i, el) => {
        const href = $(el).attr('href');
        if (href && !href.includes('?') && href !== '/en/news/') {
            const fullUrl = href.startsWith('http')
                ? href
                : 'https://www.datacenterdynamics.com' + href;
            articleUrls.push(fullUrl);
        }
    });

    log.info(`Found ${articleUrls.length} articles on ${request.url}`);

    // Return each URL as a separate record
    return articleUrls.map(url => ({ url }));
}
```

## Expected Output
Single-column CSV:
```csv
url
https://www.datacenterdynamics.com/en/news/250mw-data-center-planned-for-bordeaux-france/
https://www.datacenterdynamics.com/en/news/39bn-data-center-approved-spalding-county-georgia/
...
```

## Edge Cases

| Scenario | Mitigation |
|----------|------------|
| Rate limiting | Use Apify Proxy (automatic) |
| Pagination ends | Glob pattern won't match non-existent pages |
| Duplicate URLs | Deduplicate in Python after download |
| Network errors | Cheerio Scraper auto-retries (3x default) |

## Estimated Results
- ~6,000-7,000 unique article URLs
- Cost: FREE (Cheerio Scraper has no per-result fees)
- Runtime: ~5-10 minutes

## Verification
1. Test with `maxPagesPerCrawl: 5` first
2. Check CSV has valid URLs
3. Verify no duplicates
4. Run full scrape

## Output Location
`.tmp/dcd_article_urls.csv`
