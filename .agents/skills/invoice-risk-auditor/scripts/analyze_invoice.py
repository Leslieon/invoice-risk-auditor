"""Analyze one invoice from the command line.

Pipeline:
    image/PDF -> OCR -> field extraction -> history matching
    -> deterministic rules -> JSON files

This is the reusable entry point intended for a future Codex skill. It does
not import the Streamlit application and it does not compare with test labels.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

from history_features import add_recent_invoice_counts
from invoice_extractor import extract_invoice, save_extraction
from loader import load_product_aliases, load_supplier_history
from matcher import match_all_invoices
from ocr_service import PaddleOCRService, save_document
from risk_detector import detect_all_invoices


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
SAMPLE_DATA_DIR = SKILL_ROOT / "assets" / "sample-data"
DEFAULT_HISTORY = SAMPLE_DATA_DIR / "supplier_history.xlsx"
DEFAULT_ALIASES = SAMPLE_DATA_DIR / "product_alias.csv"
DEFAULT_EVENTS = SAMPLE_DATA_DIR / "invoice_events.xlsx"
DEFAULT_EVENTS_SHEET = "开票事件日志"
DEFAULT_OUTPUT_DIR = Path.cwd() / "invoice-risk-output"


class InvoiceAnalysisError(RuntimeError):
    """Raised when the end-to-end analysis cannot be completed."""


def _json_safe(value: Any) -> Any:
    """Convert pandas, numpy and dataclass values into JSON-safe values."""

    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if not isinstance(value, (str, bytes)):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
    return value


def _series_to_dict(series: pd.Series) -> dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in series.items()}


def _invoice_dataframe(extracted: dict[str, Any]) -> pd.DataFrame:
    invoices = pd.DataFrame([extracted])
    invoices["invoice_date"] = pd.to_datetime(
        invoices["invoice_date"], errors="raise"
    )
    return invoices


def _load_events(path: Path, sheet_name: str) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"找不到开票事件数据：{path}")
    try:
        return pd.read_excel(path, sheet_name=sheet_name)
    except ValueError as exc:
        raise InvoiceAnalysisError(
            f"开票事件工作簿中找不到工作表“{sheet_name}”：{path}"
        ) from exc


def analyze_invoice(
    invoice_file: str | Path,
    *,
    supplier_history_file: str | Path = DEFAULT_HISTORY,
    product_aliases_file: str | Path = DEFAULT_ALIASES,
    events_file: str | Path | None = DEFAULT_EVENTS,
    events_sheet: str = DEFAULT_EVENTS_SHEET,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    min_confidence: float = 0.5,
) -> dict[str, Any]:
    """Run one invoice through the complete reusable analysis pipeline."""

    input_path = Path(invoice_file).expanduser().resolve()
    history_path = Path(supplier_history_file).expanduser().resolve()
    aliases_path = Path(product_aliases_file).expanduser().resolve()
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    if not input_path.is_file():
        raise FileNotFoundError(f"找不到待分析发票：{input_path}")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence 必须位于 0 到 1 之间")

    stem = input_path.stem
    ocr_path = output_path / f"{stem}_ocr.json"
    extraction_path = output_path / f"{stem}_extracted.json"
    result_path = output_path / f"{stem}_analysis.json"

    ocr_document = PaddleOCRService(
        min_confidence=min_confidence
    ).recognize(input_path)
    save_document(ocr_document, ocr_path)

    extracted_invoice = extract_invoice(ocr_path)
    save_extraction(extracted_invoice, extraction_path)
    invoices = _invoice_dataframe(asdict(extracted_invoice))

    if events_file is None:
        invoices["recent_30d_invoice_count"] = 0
        events_source: str | None = None
    else:
        events_path = Path(events_file).expanduser().resolve()
        events = _load_events(events_path, events_sheet)
        invoices = add_recent_invoice_counts(invoices, events)
        events_source = str(events_path)

    supplier_history = load_supplier_history(history_path)
    product_aliases = load_product_aliases(aliases_path)
    history_matches = match_all_invoices(
        invoices, supplier_history, product_aliases
    )
    risk_results = detect_all_invoices(invoices, history_matches)

    invoice = invoices.iloc[0]
    history_match = history_matches.iloc[0]
    risk_result = risk_results.iloc[0]

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(input_path),
        "reference_data": {
            "supplier_history": str(history_path),
            "product_aliases": str(aliases_path),
            "events": events_source,
            "events_sheet": events_sheet if events_source else None,
        },
        "ocr": {
            "page_count": ocr_document.page_count,
            "line_count": ocr_document.line_count,
            "mean_confidence": round(
                sum(page.mean_confidence for page in ocr_document.pages)
                / len(ocr_document.pages),
                6,
            ),
            "full_text": ocr_document.full_text,
        },
        "invoice": _series_to_dict(invoice),
        "history_match": _series_to_dict(history_match),
        "risk_result": _series_to_dict(risk_result),
        "artifacts": {
            "ocr_json": str(ocr_path),
            "extracted_json": str(extraction_path),
            "analysis_json": str(result_path),
        },
    }
    result_path.write_text(
        json.dumps(_json_safe(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "分析一张发票：OCR、字段抽取、历史匹配和确定性规则检测。"
        )
    )
    parser.add_argument("--invoice", required=True, help="发票图片或PDF路径")
    parser.add_argument(
        "--supplier-history",
        default=str(DEFAULT_HISTORY),
        help=f"供应商历史数据，默认：{DEFAULT_HISTORY}",
    )
    parser.add_argument(
        "--product-aliases",
        default=str(DEFAULT_ALIASES),
        help=f"商品别名CSV，默认：{DEFAULT_ALIASES}",
    )
    parser.add_argument(
        "--events",
        default=str(DEFAULT_EVENTS),
        help=f"开票事件Excel，默认：{DEFAULT_EVENTS}",
    )
    parser.add_argument(
        "--no-events",
        action="store_true",
        help="跳过开票事件数据并将近30天开票次数设为0",
    )
    parser.add_argument(
        "--events-sheet",
        default=DEFAULT_EVENTS_SHEET,
        help=f"开票事件工作表名称，默认：{DEFAULT_EVENTS_SHEET}",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"结果目录，默认：{DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="OCR文字行最低置信度，默认：0.5",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        result = analyze_invoice(
            args.invoice,
            supplier_history_file=args.supplier_history,
            product_aliases_file=args.product_aliases,
            events_file=None if args.no_events else args.events,
            events_sheet=args.events_sheet,
            output_dir=args.output_dir,
            min_confidence=args.min_confidence,
        )
    except Exception as exc:
        print(f"处理失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    risk = result["risk_result"]
    print("发票分析完成")
    print(f"  发票编号：{result['invoice']['invoice_id']}")
    print(f"  风险结论：{risk['risk_label']}")
    print(f"  是否复核：{'是' if risk['requires_review'] else '否'}")
    print(
        "  触发规则：" + (", ".join(risk["triggered_rules"]) or "无")
    )
    print(f"  结果文件：{result['artifacts']['analysis_json']}")


if __name__ == "__main__":
    main()
