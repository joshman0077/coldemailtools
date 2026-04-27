# Example Workflow: Data Processing Pipeline

## Objective
Process CSV data from a source, clean it, transform it, and export results to Google Sheets.

## Required Inputs
- Source CSV file path or URL
- Target Google Sheet ID
- Column mappings (if needed)

## Tools Required
- `tools/download_file.py` - Download file from URL if needed
- `tools/clean_csv.py` - Remove duplicates, handle missing values
- `tools/transform_data.py` - Apply transformations (calculations, formatting)
- `tools/export_to_sheets.py` - Upload to Google Sheets

## Process Steps

1. **Validate inputs**
   - Confirm source file exists or URL is valid
   - Verify Google Sheet ID is accessible
   - Check required columns are present

2. **Download/Load data**
   - If URL: Run `tools/download_file.py` to save to `.tmp/`
   - If local: Verify file path exists
   - Store in `.tmp/raw_data.csv`

3. **Clean data**
   - Run `tools/clean_csv.py` with input file
   - Output: `.tmp/cleaned_data.csv`
   - Log: Number of rows removed, issues found

4. **Transform data**
   - Run `tools/transform_data.py` with cleaned file
   - Apply any calculations or formatting
   - Output: `.tmp/final_data.csv`

5. **Export to Google Sheets**
   - Run `tools/export_to_sheets.py` with final file and Sheet ID
   - Confirm successful upload
   - Return Sheet URL

## Edge Cases

### Missing columns
- **Issue**: Required columns not in source data
- **Action**: List missing columns, ask user for alternatives or abort

### API rate limits
- **Issue**: Google Sheets API rate limit hit
- **Action**: Implement exponential backoff in tool, retry up to 3 times

### Invalid data types
- **Issue**: Non-numeric data in calculation columns
- **Action**: Clean tool should coerce or flag, document in workflow

### Empty dataset
- **Issue**: After cleaning, no rows remain
- **Action**: Alert user, provide summary of what was filtered out

## Expected Outputs
- Google Sheet with processed data
- Summary report: rows processed, issues encountered
- Sheet URL for direct access

## Notes
- All temporary files in `.tmp/` can be deleted after successful export
- If export fails, keep `.tmp/final_data.csv` for retry
- Update this workflow if new edge cases are discovered
