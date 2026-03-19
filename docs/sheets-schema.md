# PDF Reads Sheet Schema

This file defines the columns for the `pdf_reads` Google Sheet, where n8n writes the processed output for each PDF.

## Sheet: `pdf_reads`

| Column | Type | Description | Example |
|---|---|---|---|
| `doc_id` | String | Unique ID for each document (auto-generated) | `doc_a1b2c3` |
| `file_name` | String | Name of the PDF file | `intro-to-ml.pdf` |
| `source_path` | String | Full path to the PDF in the watched folder | `/pdfs/intro-to-ml.pdf` |
| `processed_at` | DateTime | When n8n processed the file | `2026-03-19 21:00:00` |
| `title` | String | Document title extracted by AI | `Introduction to Machine Learning` |
| `summary` | String | Short AI-generated summary of the content | `A beginner's overview of supervised and unsupervised learning.` |
| `summary_length` | Integer | Word count of the summary | `42` |
| `tags` | String | Comma-separated keywords extracted by AI | `machine learning, AI, supervised learning` |
| `primary_topic` | String | Single main topic identified by AI | `Machine Learning` |
| `text_length` | Integer | Word count of the full extracted PDF text | `8300` |
| `status` | String | Processing result: `success` or `error` | `success` |
| `error_message` | String | Error details if processing failed, blank otherwise | `Could not extract text from PDF` |
