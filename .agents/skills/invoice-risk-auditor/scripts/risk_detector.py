"""基于企业规则和供应商历史数据进行发票风险检测。

输入：
- loader.py 读取的结构化发票；
- matcher.py 生成的历史匹配结果。

输出：
- 是否异常；
- 是否需要人工复核；
- 触发的规则编号；
- 风险类型和可核查的规则原因。

本模块不调用大模型。它负责产生确定、可复现的检测结论，后续大模型
只需要根据这些结论生成更自然的解释。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from loader import DataValidationError, load_all_data
from matcher import match_all_invoices


# “超过”统一采用严格大于，不包含刚好等于阈值的情况。
LARGE_AMOUNT_THRESHOLD = 500_000.0
OFFICE_REVIEW_THRESHOLD = 100_000.0
HISTORY_AVERAGE_MULTIPLIER = 5.0
NEW_SUPPLIER_AMOUNT_THRESHOLD = 200_000.0
OFFICE_REASONABILITY_THRESHOLD = 500_000.0
RECENT_30D_INVOICE_COUNT_THRESHOLD = 10

NEW_OR_LIMITED_PROFILES = {"新合作供应商", "历史交易较少"}


@dataclass(frozen=True)
class RuleDefinition:
    """规则的固定属性。"""

    rule_id: str
    risk_type: str
    is_abnormal: bool
    requires_review: bool


@dataclass(frozen=True)
class RuleHit:
    """某张发票触发一条规则后的结果。"""

    rule_id: str
    risk_type: str
    is_abnormal: bool
    requires_review: bool
    reason: str


@dataclass(frozen=True)
class RiskResult:
    """一张发票的最终规则检测结果。"""

    invoice_id: str
    risk_label: str
    is_abnormal: bool
    requires_review: bool
    triggered_rules: tuple[str, ...]
    risk_types: tuple[str, ...]
    risk_reasons: tuple[str, ...]
    match_level: str
    history_insufficient: bool


RULE_CATALOG = {
    "R001": RuleDefinition(
        rule_id="R001",
        risk_type="大额复核",
        is_abnormal=False,
        requires_review=True,
    ),
    "R002": RuleDefinition(
        rule_id="R002",
        risk_type="规则违规",
        is_abnormal=True,
        requires_review=True,
    ),
    "R003": RuleDefinition(
        rule_id="R003",
        risk_type="金额异常",
        is_abnormal=True,
        requires_review=True,
    ),
    "R004": RuleDefinition(
        rule_id="R004",
        risk_type="供应商风险",
        is_abnormal=True,
        requires_review=True,
    ),
    "R005": RuleDefinition(
        rule_id="R005",
        risk_type="业务合理性异常",
        is_abnormal=True,
        requires_review=True,
    ),
    "R006": RuleDefinition(
        rule_id="R006",
        risk_type="金额异常",
        is_abnormal=True,
        requires_review=True,
    ),
    "R007": RuleDefinition(
        rule_id="R007",
        risk_type="频率异常",
        is_abnormal=True,
        requires_review=True,
    ),
}


def _format_amount(value: float) -> str:
    return f"{value:,.2f}元"


def _make_hit(rule_id: str, reason: str) -> RuleHit:
    definition = RULE_CATALOG[rule_id]
    return RuleHit(
        rule_id=definition.rule_id,
        risk_type=definition.risk_type,
        is_abnormal=definition.is_abnormal,
        requires_review=definition.requires_review,
        reason=reason,
    )


def _unique_in_order(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def detect_invoice_risk(
    invoice: pd.Series,
    history_match: pd.Series,
) -> RiskResult:
    """对单张发票执行 R001～R007。"""

    invoice_id = str(invoice["invoice_id"])
    if invoice_id != str(history_match["invoice_id"]):
        raise DataValidationError(
            f"发票与历史匹配结果ID不一致：{invoice_id} / "
            f"{history_match['invoice_id']}"
        )

    amount = float(invoice["amount"])
    category = str(invoice["product_category"])
    recent_count_value = invoice.get("recent_30d_invoice_count", 0)
    recent_30d_invoice_count = (
        0 if pd.isna(recent_count_value) else int(recent_count_value)
    )
    if recent_30d_invoice_count < 0:
        raise DataValidationError(
            f"{invoice_id} 的 recent_30d_invoice_count 不能为负数"
        )
    match_level = str(history_match["match_level"])
    history_insufficient = match_level == "none"

    average_amount = history_match["average_amount"]
    maximum_amount = history_match["maximum_amount"]
    supplier_profiles = set(history_match["supplier_profiles"] or ())

    hits: list[RuleHit] = []

    # R001：超过50万元需要复核，但金额大本身不代表历史异常。
    if amount > LARGE_AMOUNT_THRESHOLD:
        hits.append(
            _make_hit(
                "R001",
                f"单笔金额{_format_amount(amount)}超过"
                f"{_format_amount(LARGE_AMOUNT_THRESHOLD)}，需要人工复核",
            )
        )

    # R002：办公用品超过10万元，属于企业规则中的重点审核情况。
    if category == "办公用品" and amount > OFFICE_REVIEW_THRESHOLD:
        hits.append(
            _make_hit(
                "R002",
                f"办公用品金额{_format_amount(amount)}超过审核阈值"
                f"{_format_amount(OFFICE_REVIEW_THRESHOLD)}",
            )
        )

    # R003：只有找到可靠历史数据时才比较历史平均金额。
    if (
        not history_insufficient
        and average_amount is not None
        and not pd.isna(average_amount)
        and amount > float(average_amount) * HISTORY_AVERAGE_MULTIPLIER
    ):
        hits.append(
            _make_hit(
                "R003",
                f"当前金额{_format_amount(amount)}超过历史平均金额"
                f"{_format_amount(float(average_amount))}的"
                f"{HISTORY_AVERAGE_MULTIPLIER:g}倍",
            )
        )

    # R004：不直接用 transaction_count < 10，避免误伤正常但样本较少的品类。
    is_new_or_limited = bool(
        supplier_profiles.intersection(NEW_OR_LIMITED_PROFILES)
    )
    if is_new_or_limited and amount > NEW_SUPPLIER_AMOUNT_THRESHOLD:
        profile_text = "、".join(sorted(supplier_profiles))
        hits.append(
            _make_hit(
                "R004",
                f"供应商画像为“{profile_text}”，且单笔金额"
                f"{_format_amount(amount)}超过"
                f"{_format_amount(NEW_SUPPLIER_AMOUNT_THRESHOLD)}",
            )
        )

    # R005：将规则文件中的示例落实为可执行条件。
    if category == "办公用品" and amount > OFFICE_REASONABILITY_THRESHOLD:
        hits.append(
            _make_hit(
                "R005",
                f"办公用品金额{_format_amount(amount)}超过业务合理性阈值"
                f"{_format_amount(OFFICE_REASONABILITY_THRESHOLD)}",
            )
        )

    # R006：只有存在历史最大值时才执行。
    if (
        not history_insufficient
        and maximum_amount is not None
        and not pd.isna(maximum_amount)
        and amount > float(maximum_amount)
    ):
        hits.append(
            _make_hit(
                "R006",
                f"当前金额{_format_amount(amount)}超过历史最大金额"
                f"{_format_amount(float(maximum_amount))}",
            )
        )

    # R007：近30天开票次数达到阈值，判定为短期高频开票风险。
    # 旧数据没有该字段时默认值为0，因此不会改变原有检测结果。
    if recent_30d_invoice_count >= RECENT_30D_INVOICE_COUNT_THRESHOLD:
        hits.append(
            _make_hit(
                "R007",
                f"近30天开票次数为{recent_30d_invoice_count}次，达到频率预警阈值"
                f"{RECENT_30D_INVOICE_COUNT_THRESHOLD}次",
            )
        )

    is_abnormal = any(hit.is_abnormal for hit in hits)
    requires_review = any(hit.requires_review for hit in hits)

    reasons = [hit.reason for hit in hits]
    risk_types = [hit.risk_type for hit in hits]

    # 无历史数据不自动判异常，但由于依据不足，需要人工复核。
    if history_insufficient:
        requires_review = True
        risk_types.append("历史数据不足")
        reasons.append("未找到同供应商、同商品类别的可靠历史数据")

    if not reasons:
        reasons.append("未触发企业审核规则，且金额处于可参考的历史范围内")

    return RiskResult(
        invoice_id=invoice_id,
        risk_label="异常" if is_abnormal else "正常",
        is_abnormal=is_abnormal,
        requires_review=requires_review,
        triggered_rules=tuple(hit.rule_id for hit in hits),
        risk_types=_unique_in_order(risk_types),
        risk_reasons=tuple(reasons),
        match_level=match_level,
        history_insufficient=history_insufficient,
    )


def detect_all_invoices(
    invoices: pd.DataFrame,
    history_matches: pd.DataFrame,
) -> pd.DataFrame:
    """对全部发票执行规则检测并返回一行一张发票的结果。"""

    if history_matches["invoice_id"].duplicated().any():
        raise DataValidationError("历史匹配结果中存在重复发票ID")

    matches_by_id = history_matches.set_index("invoice_id", drop=False)
    invoice_ids = set(invoices["invoice_id"])
    match_ids = set(history_matches["invoice_id"])
    if invoice_ids != match_ids:
        raise DataValidationError(
            "发票与历史匹配结果ID不一致；"
            f"缺少匹配：{sorted(invoice_ids - match_ids)}；"
            f"多余匹配：{sorted(match_ids - invoice_ids)}"
        )

    results = []
    for _, invoice in invoices.iterrows():
        history_match = matches_by_id.loc[invoice["invoice_id"]]
        results.append(asdict(detect_invoice_risk(invoice, history_match)))

    return pd.DataFrame(results)


def main() -> None:
    data = load_all_data()
    history_matches = match_all_invoices(
        data.invoices,
        data.supplier_history,
        data.product_aliases,
    )
    results = detect_all_invoices(data.invoices, history_matches)

    display = results[
        [
            "invoice_id",
            "risk_label",
            "requires_review",
            "triggered_rules",
            "match_level",
        ]
    ].copy()
    display["triggered_rules"] = display["triggered_rules"].map(
        lambda value: ",".join(value)
    )

    print(display.to_string(index=False))
    print("\n结果统计：")
    print(f"异常发票：{int(results['is_abnormal'].sum())} 条")
    print(f"需要复核：{int(results['requires_review'].sum())} 条")


if __name__ == "__main__":
    main()
