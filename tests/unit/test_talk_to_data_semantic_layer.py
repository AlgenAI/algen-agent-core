from __future__ import annotations

from pathlib import Path

import pytest

from traccia_runtime.exceptions.errors import ConfigurationError
from traccia_runtime.semantics import (
    FilterOperator,
    MetricFilter,
    MetricQuery,
    SemanticLayer,
    SemanticQueryCompiler,
)

SEMANTIC_LAYER = (
    Path(__file__).parents[2]
    / "examples"
    / "talk_to_data_multi_agent"
    / "config"
    / "semantic_layer.yaml"
)


def test_talk_to_data_semantic_layer_matches_verified_inventory_schema() -> None:
    layer = SemanticLayer.from_yaml(SEMANTIC_LAYER)

    assert layer.definition.version == "1.2.0"
    assert not layer.definition.joins
    inventory = next(
        model for model in layer.definition.models if model.name == "forward_inventory"
    )
    assert inventory.table == "public.pp_fare_class_history"
    assert inventory.required_filter_dimensions == ("capture_date",)
    assert layer.metric("authorized_capacity")[1].expression == "authorized_units"
    assert layer.dimension("inventory_route")[1].expression == "sector"


def test_forward_load_factor_and_yield_support_verified_weekday_screening() -> None:
    layer = SemanticLayer.from_yaml(SEMANTIC_LAYER)

    metrics, dimensions = layer.validate_selection(
        ("forward_load_factor", "forward_yield"),
        ("forward_route", "forward_weekday"),
        minimum_certification="verified",
        maximum_sensitivity="confidential",
    )

    assert {metric.name for _, metric in metrics} == {
        "forward_load_factor",
        "forward_yield",
    }
    assert {dimension.name for _, dimension in dimensions} == {
        "forward_route",
        "forward_weekday",
    }
    assert layer.metric("forward_yield")[1].expression == (
        "SUM(current_revenue) / NULLIF(SUM(current_sold), 0)"
    )


def test_current_competitor_fares_always_apply_current_version_filter() -> None:
    layer = SemanticLayer.from_yaml(SEMANTIC_LAYER)
    compiled = SemanticQueryCompiler().compile(
        layer,
        MetricQuery(
            metrics=("current_minimum_fare",),
            dimensions=("competitor_market",),
        ),
    )

    assert 'FROM "public"."pp_competitor_fares"' in compiled.sql
    assert "is_current = $1" in compiled.sql
    assert compiled.parameters == (True,)

    with pytest.raises(ConfigurationError, match="must enforce"):
        layer.validate_generated_sql(
            ("current_minimum_fare",),
            ("competitor_market",),
            "SELECT query_sector, MIN(price) FROM public.pp_competitor_fares "
            "GROUP BY query_sector",
        )
    layer.validate_generated_sql(
        ("current_minimum_fare",),
        ("competitor_market",),
        "SELECT query_sector, MIN(price) FROM public.pp_competitor_fares "
        "WHERE is_current = TRUE GROUP BY query_sector",
    )


def test_historical_inventory_requires_an_explicit_capture_filter() -> None:
    layer = SemanticLayer.from_yaml(SEMANTIC_LAYER)
    with pytest.raises(ConfigurationError, match="capture_date"):
        SemanticQueryCompiler().compile(
            layer,
            MetricQuery(
                metrics=("sold_seats",),
                dimensions=("inventory_route",),
            ),
        )

    compiled = SemanticQueryCompiler().compile(
        layer,
        MetricQuery(
            metrics=("sold_seats",),
            dimensions=("inventory_route",),
            filters=(
                MetricFilter(
                    field="capture_date",
                    operator=FilterOperator.EQ,
                    value="2026-09-05",
                ),
            ),
        ),
    )
    assert "capture_date = $1" in compiled.sql

    with pytest.raises(ConfigurationError, match="predicate on 'capture_date'"):
        layer.validate_generated_sql(
            ("sold_seats",),
            ("inventory_route",),
            "SELECT sector, SUM(sold_seats) FROM public.pp_fare_class_history "
            "GROUP BY sector",
        )
    layer.validate_generated_sql(
        ("sold_seats",),
        ("inventory_route",),
        "SELECT sector, SUM(sold_seats) FROM public.pp_fare_class_history "
        "WHERE capture_date = DATE '2026-09-05' GROUP BY sector",
    )


def test_unsafe_rbd_revenue_is_rejected_at_verified_certification() -> None:
    layer = SemanticLayer.from_yaml(SEMANTIC_LAYER)
    with pytest.raises(ConfigurationError, match="below required certification"):
        SemanticQueryCompiler(minimum_certification="verified").compile(
            layer, MetricQuery(metrics=("rbd_revenue",))
        )
