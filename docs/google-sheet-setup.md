# Google Sheet Setup

## Purpose
This Google Sheet stores one row per processed PDF. Each processed file becomes one row with metadata, summary, and tags.

## Sheet name
pdf_reads

## Exact header row
doc_id,file_name,source_path,processed_at,title,summary,summary_length,tags,primary_topic,text_length,status,error_message

## Manual setup steps
1. Open Google Sheets and create a new spreadsheet.
2. Rename the first tab to pdf_reads.
3. Paste the exact header row into the first row.
4. Copy the spreadsheet ID from the URL.
5. Save that spreadsheet ID for later configuration.

## What we need later
- spreadsheet ID
- Google access credentials
- one test PDF file
