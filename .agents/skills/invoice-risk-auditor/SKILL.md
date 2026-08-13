---
name: invoice-risk-auditor
description: Analyze Chinese invoice images or PDFs with local OCR, structured field extraction, supplier-history matching, and deterministic enterprise rules. Use when Codex needs to review an invoice, identify abnormal invoice risks, explain triggered rules, or produce a traceable invoice-risk JSON report from enterprise history data.
---

# Invoice Risk Auditor

Analyze one invoice through a deterministic, auditable pipeline. Treat the
rule engine as the source of truth and use the language model only to explain
the facts and rules already produced by the pipeline.

## Required inputs

Obtain these inputs before running the analysis:

- An invoice image or PDF.
- A supplier-history Excel workbook.
- A product-alias CSV file.
- An invoice-event Excel workbook when evaluating recent-frequency rule R007.

If enterprise reference data is missing, state which input is missing. Do not
invent enterprise history or rules.

## Workflow

1. Validate the invoice path and supported format.
2. Ensure the active Python environment satisfies
   `scripts/requirements.txt`. PaddleOCR downloads its model cache on first
   use; inform the user when network access or model download is required.
3. Run `scripts/analyze_invoice.py` with explicit input and output paths:

   ```powershell
   python <skill-dir>\scripts\analyze_invoice.py `
     --invoice <invoice-path> `
     --supplier-history <supplier-history.xlsx> `
     --product-aliases <product-alias.csv> `
     --events <invoice-events.xlsx> `
     --events-sheet <event-sheet-name> `
     --output-dir <output-directory>
   ```

   Use `--no-events` only when frequency rule R007 is intentionally excluded.
4. Inspect the generated OCR, extraction, and final analysis JSON files.
5. Report the structured invoice fields, history match level, risk label,
   manual-review requirement, triggered rule IDs, reasons, and output path.
6. Explain the deterministic result in concise Chinese without adding,
   removing, or modifying triggered rules.

## Decision boundaries

- Never let an LLM add, remove, or modify a triggered rule.
- Distinguish `is_abnormal` from `requires_review`; a normal invoice may still
  require review.
- Describe the result as risk screening or auxiliary review, not official tax
  verification or a legal conclusion.
- Do not call an external language-model API. Let Codex explain only the
  deterministic JSON artifacts produced locally.
- Do not expose raw invoice or enterprise data beyond the files explicitly
  supplied by the user and the selected local output directory.
- Do not run evaluation against labeled answers during a real invoice review.

## Output expectations

Return a concise summary and retain the complete JSON artifacts. At minimum,
include:

- `invoice_id`
- `risk_label` and `is_abnormal`
- `requires_review`
- `triggered_rules`
- `risk_types` and `risk_reasons`
- `match_level` and `history_insufficient`
- local artifact paths used as the evidence source

Read `references/data-contract.md` when validating custom enterprise data or
integrating the output with another system. Read
`references/rule-catalog.md` when interpreting or auditing triggered rules.

Use the synthetic files in `assets/sample-data/` only for a smoke test. Never
present sample-data conclusions as conclusions about a real enterprise.
