# Data contract

## Invoice input

Accept one `.png`, `.jpg`, `.jpeg`, `.bmp`, `.tif`, `.tiff`, or `.pdf` file.
The current deterministic extractor targets Chinese electronic ordinary invoice
layouts and expects an invoice number, invoice date, buyer and seller names,
one line-item block, and amount/tax/total fields.

## Supplier history

Pass an Excel workbook whose first sheet contains:

| Column | Meaning |
|---|---|
| `supplier_name` | Seller name used for exact supplier matching |
| `product_category` | Product category |
| `product_name` | Historical product name |
| `transaction_count` | Positive historical transaction count |
| `average_amount` | Historical average amount excluding tax |
| `minimum_amount` | Historical minimum amount excluding tax |
| `maximum_amount` | Historical maximum amount excluding tax |
| `supplier_profile` | Supplier profile such as long-term, new, or limited-history |

Require `minimum_amount <= average_amount <= maximum_amount` and one row per
supplier/category/product key.

## Product aliases

Pass a UTF-8, UTF-8-BOM, or GBK CSV file containing:

- `raw_name`
- `standard_name`

Do not repeat `raw_name` values.

## Invoice-event log

Pass an Excel workbook and specify the sheet containing:

- `supplier_name`
- `event_date`

Count events in the interval
`invoice_date - 30 days <= event_date < invoice_date`. Use `--no-events` only
when frequency rule R007 is intentionally out of scope.

## Output

The command writes three UTF-8 JSON files:

- `<stem>_ocr.json`: OCR text, confidence, and coordinates.
- `<stem>_extracted.json`: normalized invoice fields.
- `<stem>_analysis.json`: history evidence, deterministic risk result, and
  artifact paths.
