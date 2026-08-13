"""项目数据读取与基础校验。

本模块只负责：
1. 读取发票、供应商历史、商品别名、预期结果和规则文件；
2. 统一基础数据类型；
3. 在进入匹配和风险检测前发现明显的数据问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SOURCE_DATA_DIR = DATA_DIR / "source"
TEST_DATA_DIR = DATA_DIR / "test"


class DataValidationError(ValueError):
    """输入文件存在缺列、重复或不合法数据时抛出。"""


@dataclass(frozen=True)
class DataBundle:
    """项目运行所需的全部原始数据。"""

    invoices: pd.DataFrame
    supplier_history: pd.DataFrame
    product_aliases: pd.DataFrame
    expected_results: pd.DataFrame
    rules_text: str


INVOICE_COLUMNS = {
    "invoice_id",
    "invoice_date",
    "seller_name",
    "buyer_name",
    "product_category",
    "product_name",
    "quantity",
    "amount",
    "tax_amount",
    "total_amount",
}

HISTORY_COLUMNS = {
    "supplier_name",
    "product_category",
    "product_name",
    "transaction_count",
    "average_amount",
    "minimum_amount",
    "maximum_amount",
    "supplier_profile",
}

ALIAS_COLUMNS = {"raw_name", "standard_name"}

EXPECTED_COLUMNS = {
    "invoice_id",
    "risk_label",
    "risk_type",
    "risk_reason",
    "requires_review",
    "triggered_rules",
}


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"找不到数据文件：{path}")


def _require_columns(
    dataframe: pd.DataFrame,
    required_columns: set[str],
    dataset_name: str,
) -> None:
    missing = sorted(required_columns - set(dataframe.columns))
    if missing:
        raise DataValidationError(
            f"{dataset_name} 缺少字段：{', '.join(missing)}"
        )


def _strip_text_columns(dataframe: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        dataframe[column] = dataframe[column].astype("string").str.strip()


def _to_numeric(
    dataframe: pd.DataFrame,
    columns: list[str],
    dataset_name: str,
) -> None:
    for column in columns:
        try:
            dataframe[column] = pd.to_numeric(dataframe[column], errors="raise")
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                f"{dataset_name} 的 {column} 列包含非数字内容"
            ) from exc


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        raise DataValidationError("requires_review 不能为空")

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "是"}:
        return True
    if normalized in {"false", "0", "no", "否"}:
        return False
    raise DataValidationError(f"无法识别 requires_review 的值：{value}")


def _parse_triggered_rules(value: object) -> list[str]:
    """把 Excel 中的 R001,R002 转成去重后的规则编号列表。"""

    if pd.isna(value) or not str(value).strip():
        return []

    result: list[str] = []
    for rule_id in str(value).split(","):
        normalized = rule_id.strip().upper()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def load_invoices(path: Path = SOURCE_DATA_DIR / "invoices.xlsx") -> pd.DataFrame:
    _require_file(path)
    dataframe = pd.read_excel(path, sheet_name="invoice_data")
    _require_columns(dataframe, INVOICE_COLUMNS, "发票数据")

    _strip_text_columns(
        dataframe,
        [
            "invoice_id",
            "seller_name",
            "buyer_name",
            "product_category",
            "product_name",
        ],
    )
    _to_numeric(
        dataframe,
        ["quantity", "amount", "tax_amount", "total_amount"],
        "发票数据",
    )

    dataframe["invoice_date"] = pd.to_datetime(
        dataframe["invoice_date"], errors="raise"
    )

    if dataframe["invoice_id"].isna().any():
        raise DataValidationError("发票ID不能为空")
    if dataframe["invoice_id"].duplicated().any():
        duplicated = dataframe.loc[
            dataframe["invoice_id"].duplicated(keep=False), "invoice_id"
        ].tolist()
        raise DataValidationError(f"发票ID重复：{duplicated}")
    if (dataframe["quantity"] <= 0).any():
        raise DataValidationError("发票数量必须大于0")
    if (dataframe[["amount", "tax_amount", "total_amount"]] < 0).any().any():
        raise DataValidationError("发票金额不能为负数")

    amount_difference = (
        dataframe["amount"] + dataframe["tax_amount"] - dataframe["total_amount"]
    ).abs()
    if (amount_difference > 0.01).any():
        invalid_ids = dataframe.loc[
            amount_difference > 0.01, "invoice_id"
        ].tolist()
        raise DataValidationError(
            f"以下发票不满足 amount + tax_amount = total_amount：{invalid_ids}"
        )

    return dataframe


def load_supplier_history(
    path: Path = SOURCE_DATA_DIR / "supplier_history.xlsx",
) -> pd.DataFrame:
    _require_file(path)
    dataframe = pd.read_excel(path, sheet_name=0)
    _require_columns(dataframe, HISTORY_COLUMNS, "供应商历史数据")

    _strip_text_columns(
        dataframe,
        [
            "supplier_name",
            "product_category",
            "product_name",
            "supplier_profile",
        ],
    )
    _to_numeric(
        dataframe,
        [
            "transaction_count",
            "average_amount",
            "minimum_amount",
            "maximum_amount",
        ],
        "供应商历史数据",
    )

    if (dataframe["transaction_count"] <= 0).any():
        raise DataValidationError("历史交易次数必须大于0")

    invalid_range = ~(
        (dataframe["minimum_amount"] <= dataframe["average_amount"])
        & (dataframe["average_amount"] <= dataframe["maximum_amount"])
    )
    if invalid_range.any():
        invalid_rows = (dataframe.index[invalid_range] + 2).tolist()
        raise DataValidationError(
            f"历史金额应满足 minimum <= average <= maximum，异常Excel行：{invalid_rows}"
        )

    duplicate_key = ["supplier_name", "product_category", "product_name"]
    if dataframe.duplicated(subset=duplicate_key).any():
        duplicated = dataframe.loc[
            dataframe.duplicated(subset=duplicate_key, keep=False), duplicate_key
        ].to_dict("records")
        raise DataValidationError(f"供应商历史记录重复：{duplicated}")

    return dataframe


def load_product_aliases(
    path: Path = SOURCE_DATA_DIR / "product_alias.csv",
) -> pd.DataFrame:
    _require_file(path)

    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            dataframe = pd.read_csv(path, encoding=encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    else:
        raise DataValidationError(f"无法识别商品别名文件编码：{path}") from last_error

    _require_columns(dataframe, ALIAS_COLUMNS, "商品别名数据")
    _strip_text_columns(dataframe, ["raw_name", "standard_name"])

    if dataframe[["raw_name", "standard_name"]].isna().any().any():
        raise DataValidationError("商品别名不能为空")
    if dataframe["raw_name"].duplicated().any():
        duplicated = dataframe.loc[
            dataframe["raw_name"].duplicated(keep=False), "raw_name"
        ].tolist()
        raise DataValidationError(f"商品原始名称重复：{duplicated}")

    return dataframe


def load_expected_results(
    path: Path = SOURCE_DATA_DIR / "expected_results.xlsx",
) -> pd.DataFrame:
    _require_file(path)
    dataframe = pd.read_excel(path, sheet_name="invoice_data")
    _require_columns(dataframe, EXPECTED_COLUMNS, "预期结果")

    _strip_text_columns(
        dataframe,
        ["invoice_id", "risk_label", "risk_type", "risk_reason"],
    )
    dataframe["requires_review"] = dataframe["requires_review"].map(_parse_bool)
    dataframe["triggered_rules"] = dataframe["triggered_rules"].map(
        _parse_triggered_rules
    )

    if dataframe["invoice_id"].duplicated().any():
        duplicated = dataframe.loc[
            dataframe["invoice_id"].duplicated(keep=False), "invoice_id"
        ].tolist()
        raise DataValidationError(f"预期结果中的发票ID重复：{duplicated}")

    allowed_labels = {"正常", "异常"}
    invalid_labels = sorted(
        set(dataframe["risk_label"].dropna()) - allowed_labels
    )
    if invalid_labels:
        raise DataValidationError(f"未知风险标签：{invalid_labels}")

    return dataframe


def load_rules_text(path: Path = SOURCE_DATA_DIR / "rules.txt") -> str:
    _require_file(path)

    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            content = path.read_text(encoding=encoding).strip()
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DataValidationError(f"无法识别规则文件编码：{path}")

    if not content:
        raise DataValidationError("规则文件不能为空")
    return content


def load_all_data(project_root: Path | str = PROJECT_ROOT) -> DataBundle:
    """读取全部数据，并检查发票ID是否与预期结果一一对应。"""

    root = Path(project_root)
    source_dir = root / "data" / "source"
    invoices = load_invoices(source_dir / "invoices.xlsx")
    supplier_history = load_supplier_history(source_dir / "supplier_history.xlsx")
    product_aliases = load_product_aliases(source_dir / "product_alias.csv")
    expected_results = load_expected_results(source_dir / "expected_results.xlsx")
    rules_text = load_rules_text(source_dir / "rules.txt")

    invoice_ids = set(invoices["invoice_id"])
    expected_ids = set(expected_results["invoice_id"])
    if invoice_ids != expected_ids:
        missing_expected = sorted(invoice_ids - expected_ids)
        extra_expected = sorted(expected_ids - invoice_ids)
        raise DataValidationError(
            "发票数据与预期结果ID不一致；"
            f"缺少预期结果：{missing_expected}；"
            f"多余预期结果：{extra_expected}"
        )

    return DataBundle(
        invoices=invoices,
        supplier_history=supplier_history,
        product_aliases=product_aliases,
        expected_results=expected_results,
        rules_text=rules_text,
    )


def main() -> None:
    data = load_all_data()
    print("数据读取与校验通过")
    print(f"发票数据：{len(data.invoices)} 条")
    print(f"供应商历史：{len(data.supplier_history)} 条")
    print(f"商品别名：{len(data.product_aliases)} 条")
    print(f"预期结果：{len(data.expected_results)} 条")
    print(f"规则文本：{len(data.rules_text)} 个字符")


if __name__ == "__main__":
    main()
