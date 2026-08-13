"""商品名称标准化与供应商历史数据匹配。

匹配优先级：
1. 供应商 + 商品类别 + 标准商品名称；
2. 供应商 + 商品类别（类别回退）；
3. 无可靠历史数据。

本模块只负责寻找和汇总历史依据，不负责判断发票是否异常。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal
import unicodedata

import pandas as pd

from loader import DataValidationError, load_all_data


MatchLevel = Literal["exact", "alias_exact", "category", "none"]


@dataclass(frozen=True)
class HistoryMatch:
    """一张发票匹配到的历史统计依据。"""

    invoice_id: str
    original_product_name: str
    standard_product_name: str
    match_level: MatchLevel
    average_amount: float | None
    minimum_amount: float | None
    maximum_amount: float | None
    transaction_count: int
    supplier_profiles: tuple[str, ...]
    matched_product_names: tuple[str, ...]
    matched_row_count: int


def _clean_name(value: object) -> str:
    """统一全角/半角字符并去除首尾空格。"""

    if pd.isna(value):
        raise DataValidationError("商品名称不能为空")
    return unicodedata.normalize("NFKC", str(value)).strip()


def build_alias_map(product_aliases: pd.DataFrame) -> dict[str, str]:
    """把别名表转换为 {原始名称: 标准名称}。"""

    alias_map: dict[str, str] = {}
    for row in product_aliases.itertuples(index=False):
        raw_name = _clean_name(row.raw_name)
        standard_name = _clean_name(row.standard_name)

        existing = alias_map.get(raw_name)
        if existing is not None and existing != standard_name:
            raise DataValidationError(
                f"商品别名冲突：{raw_name} 同时映射到 {existing} 和 {standard_name}"
            )
        alias_map[raw_name] = standard_name

    return alias_map


def standardize_product_name(name: object, alias_map: dict[str, str]) -> str:
    """返回商品标准名称；别名表没有配置时保留清洗后的原名称。"""

    cleaned_name = _clean_name(name)
    return alias_map.get(cleaned_name, cleaned_name)


def prepare_matching_data(
    invoices: pd.DataFrame,
    supplier_history: pd.DataFrame,
    product_aliases: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """复制数据并为发票、历史记录增加标准商品名称。"""

    alias_map = build_alias_map(product_aliases)
    prepared_invoices = invoices.copy()
    prepared_history = supplier_history.copy()

    prepared_invoices["standard_product_name"] = prepared_invoices[
        "product_name"
    ].map(lambda value: standardize_product_name(value, alias_map))

    prepared_history["standard_product_name"] = prepared_history[
        "product_name"
    ].map(lambda value: standardize_product_name(value, alias_map))

    return prepared_invoices, prepared_history, alias_map


def _unique_text(values: pd.Series) -> tuple[str, ...]:
    """按原出现顺序返回非空、去重后的文本。"""

    result: list[str] = []
    for value in values:
        if pd.isna(value):
            continue
        cleaned = str(value).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return tuple(result)


def _aggregate_history(
    invoice: pd.Series,
    matched_rows: pd.DataFrame,
    match_level: MatchLevel,
) -> HistoryMatch:
    """按交易次数加权汇总一组历史记录。"""

    transaction_count = int(matched_rows["transaction_count"].sum())
    if transaction_count <= 0:
        raise DataValidationError(
            f"{invoice['invoice_id']} 匹配到的历史交易次数必须大于0"
        )

    weighted_average = float(
        (
            matched_rows["average_amount"]
            * matched_rows["transaction_count"]
        ).sum()
        / transaction_count
    )

    return HistoryMatch(
        invoice_id=str(invoice["invoice_id"]),
        original_product_name=str(invoice["product_name"]),
        standard_product_name=str(invoice["standard_product_name"]),
        match_level=match_level,
        average_amount=weighted_average,
        minimum_amount=float(matched_rows["minimum_amount"].min()),
        maximum_amount=float(matched_rows["maximum_amount"].max()),
        transaction_count=transaction_count,
        supplier_profiles=_unique_text(matched_rows["supplier_profile"]),
        matched_product_names=_unique_text(matched_rows["product_name"]),
        matched_row_count=len(matched_rows),
    )


def match_invoice_history(
    invoice: pd.Series,
    prepared_history: pd.DataFrame,
) -> HistoryMatch:
    """为单张已标准化发票匹配最可靠的历史数据。"""

    same_supplier_and_category = prepared_history[
        (prepared_history["supplier_name"] == invoice["seller_name"])
        & (
            prepared_history["product_category"]
            == invoice["product_category"]
        )
    ]

    exact_matches = same_supplier_and_category[
        same_supplier_and_category["standard_product_name"]
        == invoice["standard_product_name"]
    ]

    if not exact_matches.empty:
        original_name = _clean_name(invoice["product_name"])
        history_original_names = {
            _clean_name(value) for value in exact_matches["product_name"]
        }
        match_level: MatchLevel = (
            "exact" if original_name in history_original_names else "alias_exact"
        )
        return _aggregate_history(invoice, exact_matches, match_level)

    if not same_supplier_and_category.empty:
        return _aggregate_history(
            invoice,
            same_supplier_and_category,
            "category",
        )

    return HistoryMatch(
        invoice_id=str(invoice["invoice_id"]),
        original_product_name=str(invoice["product_name"]),
        standard_product_name=str(invoice["standard_product_name"]),
        match_level="none",
        average_amount=None,
        minimum_amount=None,
        maximum_amount=None,
        transaction_count=0,
        supplier_profiles=(),
        matched_product_names=(),
        matched_row_count=0,
    )


def match_all_invoices(
    invoices: pd.DataFrame,
    supplier_history: pd.DataFrame,
    product_aliases: pd.DataFrame,
) -> pd.DataFrame:
    """匹配全部发票，返回每张发票对应的一行历史依据。"""

    prepared_invoices, prepared_history, _ = prepare_matching_data(
        invoices,
        supplier_history,
        product_aliases,
    )

    results = [
        asdict(match_invoice_history(invoice, prepared_history))
        for _, invoice in prepared_invoices.iterrows()
    ]
    return pd.DataFrame(results)


def main() -> None:
    data = load_all_data()
    matches = match_all_invoices(
        data.invoices,
        data.supplier_history,
        data.product_aliases,
    )

    display_columns = [
        "invoice_id",
        "original_product_name",
        "standard_product_name",
        "match_level",
        "average_amount",
        "maximum_amount",
        "transaction_count",
    ]
    print(matches[display_columns].to_string(index=False))
    print("\n匹配等级统计：")
    print(matches["match_level"].value_counts().to_string())


if __name__ == "__main__":
    main()
