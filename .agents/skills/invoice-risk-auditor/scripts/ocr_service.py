"""Local OCR service for invoice images and PDFs.

Responsibility boundary:
    image/PDF -> recognized text, confidence scores and coordinates

This module deliberately does not extract invoice fields and does not judge risk.
Those steps belong to invoice_extractor.py and risk_detector.py.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "generated" / "ocr_results"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".pdf"}


class OCRServiceError(RuntimeError):
    """Raised when OCR cannot be initialized or an input cannot be processed."""


@dataclass(frozen=True)
class OCRLine:
    """One recognized text region."""

    text: str
    confidence: float
    box: list[list[int]]


@dataclass(frozen=True)
class OCRPage:
    """OCR result for one image or one page of a PDF."""

    page_index: int
    lines: list[OCRLine]
    full_text: str
    mean_confidence: float


@dataclass(frozen=True)
class OCRDocument:
    """Normalized OCR result consumed by downstream modules."""

    source_file: str
    page_count: int
    line_count: int
    full_text: str
    pages: list[OCRPage]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_supported_file(file_path: str | Path) -> Path:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"OCR输入文件不存在：{path}")
    if not path.is_file():
        raise OCRServiceError(f"OCR输入必须是文件：{path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        supported = "、".join(sorted(SUPPORTED_SUFFIXES))
        raise OCRServiceError(f"不支持的文件格式：{path.suffix}；支持：{supported}")
    return path


def _normalize_box(raw_box: Any) -> list[list[int]]:
    """Normalize either [x1,y1,x2,y2] or a four-point polygon."""
    if hasattr(raw_box, "tolist"):
        raw_box = raw_box.tolist()

    if not isinstance(raw_box, list):
        return []

    if len(raw_box) == 4 and all(isinstance(value, (int, float)) for value in raw_box):
        x1, y1, x2, y2 = (int(round(value)) for value in raw_box)
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    points: list[list[int]] = []
    for point in raw_box:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            points.append([int(round(point[0])), int(round(point[1]))])
    return points


def _extract_result_dict(result: Any) -> dict[str, Any]:
    """Read PaddleOCR 3.x Result.json without exposing Paddle-specific objects."""
    raw = getattr(result, "json", None)
    if callable(raw):
        raw = raw()
    if not isinstance(raw, dict):
        raise OCRServiceError("PaddleOCR返回了无法解析的结果对象。")

    payload = raw.get("res", raw)
    if not isinstance(payload, dict):
        raise OCRServiceError("PaddleOCR结果中缺少res对象。")
    return payload


def _make_page(
    payload: dict[str, Any],
    *,
    fallback_page_index: int,
    min_confidence: float,
) -> OCRPage:
    texts = list(payload.get("rec_texts") or [])
    scores = list(payload.get("rec_scores") or [])
    boxes = payload.get("rec_boxes")
    if boxes is None:
        boxes = payload.get("rec_polys")
    if hasattr(boxes, "tolist"):
        boxes = boxes.tolist()
    boxes = list(boxes or [])

    lines: list[OCRLine] = []
    for index, text in enumerate(texts):
        cleaned_text = str(text).strip()
        if not cleaned_text:
            continue

        score = float(scores[index]) if index < len(scores) else 0.0
        if score < min_confidence:
            continue

        raw_box = boxes[index] if index < len(boxes) else []
        lines.append(
            OCRLine(
                text=cleaned_text,
                confidence=round(score, 6),
                box=_normalize_box(raw_box),
            )
        )

    page_value = payload.get("page_index")
    page_index = fallback_page_index if page_value is None else int(page_value)
    full_text = "\n".join(line.text for line in lines)
    mean_confidence = (
        round(sum(line.confidence for line in lines) / len(lines), 6) if lines else 0.0
    )

    return OCRPage(
        page_index=page_index,
        lines=lines,
        full_text=full_text,
        mean_confidence=mean_confidence,
    )


class PaddleOCRService:
    """Thin wrapper around PaddleOCR local inference."""

    def __init__(self, *, min_confidence: float = 0.5) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence必须位于0到1之间。")

        self.min_confidence = min_confidence
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine

        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRServiceError(
                "尚未安装PaddleOCR。请在项目虚拟环境中安装paddlepaddle和paddleocr。"
            ) from exc

        # The generated invoices are upright, clean Chinese documents. Disabling
        # orientation and unwarping keeps the first Demo smaller and faster.
        self._engine = PaddleOCR(
            lang="ch",
            ocr_version="PP-OCRv5",
            device="cpu",
            # On some Windows CPU builds, batching text crops with different
            # widths can fail inside oneDNN concat.  A batch size of one avoids
            # that incompatible concat while retaining the stable CPU backend.
            text_recognition_batch_size=1,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        return self._engine

    def recognize(self, file_path: str | Path) -> OCRDocument:
        path = _require_supported_file(file_path)
        engine = self._get_engine()

        try:
            raw_results = list(engine.predict(str(path)))
        except Exception as exc:
            raise OCRServiceError(f"OCR识别失败：{path.name}：{exc}") from exc

        pages = [
            _make_page(
                _extract_result_dict(result),
                fallback_page_index=index,
                min_confidence=self.min_confidence,
            )
            for index, result in enumerate(raw_results)
        ]
        if not pages:
            raise OCRServiceError(f"OCR没有返回任何页面：{path.name}")

        full_text = "\n\n".join(page.full_text for page in pages if page.full_text)
        return OCRDocument(
            source_file=str(path),
            page_count=len(pages),
            line_count=sum(len(page.lines) for page in pages),
            full_text=full_text,
            pages=pages,
        )


def save_document(document: OCRDocument, output_file: str | Path) -> Path:
    path = Path(output_file).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def iter_input_files(input_path: str | Path) -> Iterable[Path]:
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"输入路径不存在：{path}")
    if path.is_file():
        yield _require_supported_file(path)
        return

    files = sorted(
        file
        for file in path.iterdir()
        if file.is_file() and file.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        raise OCRServiceError(f"目录中没有支持的图片或PDF：{path}")
    yield from files


def run_cli() -> None:
    parser = argparse.ArgumentParser(description="批量识别发票图片/PDF并保存JSON。")
    parser.add_argument("input", help="单个图片/PDF文件，或包含这些文件的目录。")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"JSON输出目录，默认：{DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="保留文字行的最低识别置信度，默认0.5。",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    service = PaddleOCRService(min_confidence=args.min_confidence)
    input_files = list(iter_input_files(args.input))

    print(f"准备识别 {len(input_files)} 个文件。首次运行可能需要下载OCR模型。")
    failures = 0
    for index, input_file in enumerate(input_files, start=1):
        print(f"[{index:02d}/{len(input_files):02d}] {input_file.name}")
        try:
            document = service.recognize(input_file)
            output_file = output_dir / f"{input_file.stem}_ocr.json"
            saved_path = save_document(document, output_file)
            print(
                f"  完成：{document.page_count}页，"
                f"{document.line_count}行，保存至 {saved_path.name}"
            )
        except Exception as exc:
            failures += 1
            print(f"  ERROR：{exc}")

    if failures:
        raise SystemExit(f"完成，但有 {failures} 个文件识别失败。")


if __name__ == "__main__":
    run_cli()
