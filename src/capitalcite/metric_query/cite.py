"""Citation policy for one fact row. The SQL backends do not import this module."""

from __future__ import annotations

from enum import StrEnum
from typing import assert_never

from capitalcite.metric_query.query import MetricName


class CiteKind(StrEnum):
    FDIC = "fdic"
    SEC_ASSETS = "sec_assets"
    SEC_DEPOSITS = "sec_deposits"
    SEC_NET_INCOME = "sec_net_income"


def cite_kind(metric: MetricName) -> CiteKind:
    """Exhaustive match.

    total_deposits, total_assets, net_income, nim, efficiency_ratio, and
    deposits_qoq_growth return FDIC. The three holding-company metrics
    return the SEC kind with the same stem.
    """
    match metric:
        case (
            MetricName.TOTAL_DEPOSITS
            | MetricName.TOTAL_ASSETS
            | MetricName.NET_INCOME
            | MetricName.NIM
            | MetricName.EFFICIENCY_RATIO
            | MetricName.DEPOSITS_QOQ_GROWTH
        ):
            return CiteKind.FDIC
        case MetricName.HOLDING_COMPANY_ASSETS:
            return CiteKind.SEC_ASSETS
        case MetricName.HOLDING_COMPANY_DEPOSITS:
            return CiteKind.SEC_DEPOSITS
        case MetricName.HOLDING_COMPANY_NET_INCOME:
            return CiteKind.SEC_NET_INCOME
        case _:
            assert_never(metric)
