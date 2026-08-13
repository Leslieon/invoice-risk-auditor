"""从历史开票事件中计算发票风险检测需要的时间窗口特征。"""

from __future__ import annotations

import pandas as pd

from loader import DataValidationError


DEFAULT_WINDOW_DAYS = 30
EVENT_COLUMNS = {"supplier_name", "event_date"}


def _validate_window_days(window_days: int) -> None:
    if isinstance(window_days, bool) or not isinstance(window_days, int):
        raise DataValidationError("window_days 必须是正整数")
    if window_days <= 0:
        raise DataValidationError("window_days 必须大于0")


def prepare_invoice_events(events: pd.DataFrame) -> pd.DataFrame:
    """校验并标准化开票事件日志，不修改调用方传入的数据。"""

    missing = sorted(EVENT_COLUMNS - set(events.columns))
    if missing:
        raise DataValidationError(
            f"开票事件日志缺少字段：{', '.join(missing)}"
        )

    prepared = events.copy()
    prepared["supplier_name"] = (
        prepared["supplier_name"].astype("string").str.strip()
    )
    if prepared["supplier_name"].isna().any() or (
        prepared["supplier_name"] == ""
    ).any():
        raise DataValidationError("开票事件日志中的供应商名称不能为空")

    try:
        prepared["event_date"] = pd.to_datetime(
            prepared["event_date"], errors="raise"
        ).dt.normalize()
    except (TypeError, ValueError) as exc:
        raise DataValidationError("开票事件日志包含无法识别的日期") from exc

    return prepared


def calculate_recent_invoice_count(
    supplier_name: object,
    invoice_date: object,
    events: pd.DataFrame,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> int:
    """计算指定供应商在发票日期之前N天内的开票次数。

    时间区间与Excel公式保持一致：
    ``invoice_date - window_days <= event_date < invoice_date``。
    因此，恰好位于窗口起点的事件会计入，当天和未来事件不会计入。
    """

    _validate_window_days(window_days)
    prepared_events = prepare_invoice_events(events)

    supplier = str(supplier_name).strip()
    if not supplier:
        raise DataValidationError("待检测发票的供应商名称不能为空")

    try:
        end_date = pd.Timestamp(invoice_date).normalize()
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"无法识别待检测发票日期：{invoice_date}"
        ) from exc
    if pd.isna(end_date):
        raise DataValidationError("待检测发票日期不能为空")

    start_date = end_date - pd.Timedelta(days=window_days)
    matched = prepared_events[
        (prepared_events["supplier_name"] == supplier)
        & (prepared_events["event_date"] >= start_date)
        & (prepared_events["event_date"] < end_date)
    ]
    return int(len(matched))


def add_recent_invoice_counts(
    invoices: pd.DataFrame,
    events: pd.DataFrame,
    *,
    supplier_column: str = "seller_name",
    date_column: str = "invoice_date",
    output_column: str = "recent_30d_invoice_count",
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> pd.DataFrame:
    """为整批发票增加近N天开票次数列，并返回新的DataFrame。"""

    _validate_window_days(window_days)
    missing = [
        column
        for column in (supplier_column, date_column)
        if column not in invoices.columns
    ]
    if missing:
        raise DataValidationError(
            f"待检测发票缺少字段：{', '.join(missing)}"
        )

    prepared_events = prepare_invoice_events(events)
    result = invoices.copy()

    # 这里逐张计算便于审计和调试；企业数据量较大时可改写为数据库窗口查询。
    result[output_column] = [
        calculate_recent_invoice_count(
            supplier_name=row[supplier_column],
            invoice_date=row[date_column],
            events=prepared_events,
            window_days=window_days,
        )
        for _, row in result.iterrows()
    ]
    result[output_column] = result[output_column].astype("int64")
    return result
