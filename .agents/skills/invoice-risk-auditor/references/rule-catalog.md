# Deterministic rule catalog

All amount thresholds use the invoice amount excluding tax. Strict `>` rules
do not trigger when the amount equals the threshold.

| Rule | Trigger | Abnormal | Manual review |
|---|---|---:|---:|
| R001 | Amount > 500,000 | No | Yes |
| R002 | Office-supplies amount > 100,000 | Yes | Yes |
| R003 | Amount > 5 × matched historical average | Yes | Yes |
| R004 | New/limited-history supplier and amount > 200,000 | Yes | Yes |
| R005 | Office-supplies amount > 500,000 | Yes | Yes |
| R006 | Amount > matched historical maximum | Yes | Yes |
| R007 | At least 10 prior invoice events in 30 days | Yes | Yes |

If no reliable supplier/category history is found, do not automatically mark
the invoice abnormal. Set `history_insufficient=true` and require manual review.

The rule engine is authoritative. Codex may explain these results but must not
change `is_abnormal`, `requires_review`, or `triggered_rules`.
