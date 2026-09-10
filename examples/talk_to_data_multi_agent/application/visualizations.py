from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from opentelemetry import trace

from examples.talk_to_data_multi_agent.application.analytics_workflow import QueryTask
from examples.talk_to_data_multi_agent.application.query_executor import QueryResult
from traccia_runtime.conversations import ChartBlock
from traccia_runtime.semantics import AnalysisKind, AnalysisOutcome, AnalysisStatus

_MAX_VISUALIZED_ROWS = 50
_DATE_TERMS = ("date", "day", "week", "month", "period", "year")
_RANK_TERMS = ("lowest", "highest", "top", "bottom", "rank")
_PALETTE = ["#176b4d", "#2f8f70", "#69b89a", "#d79b35", "#c65b4b", "#6557a4"]


class AirlineVisualizationPlanner:
    """Build trusted airline visualizations from typed plans and governed result rows."""

    def __init__(self) -> None:
        self._tracer = trace.get_tracer("talk_to_data.visualizations")

    def plan(
        self,
        task: QueryTask,
        result: QueryResult,
        analysis: AnalysisOutcome | None = None,
    ) -> ChartBlock | None:
        with self._tracer.start_as_current_span(
            "analytics.visualization.plan",
            attributes={
                "span.type": "tool",
                "tool.name": "airline.visualization_planner",
                "tool.side_effect": "read_only",
                "analytics.query.task_id": task.id,
                "visualization.input_rows": len(result.rows),
            },
        ) as span:
            chart = self._plan(task, result, analysis)
            span.set_attribute("visualization.created", chart is not None)
            if chart is not None:
                span.set_attribute("visualization.grammar", chart.grammar)
                span.set_attribute("visualization.title", chart.title or "")
            return chart

    def _plan(
        self,
        task: QueryTask,
        result: QueryResult,
        analysis: AnalysisOutcome | None,
    ) -> ChartBlock | None:
        if (
            analysis is not None
            and analysis.kind == AnalysisKind.STATIC_SCENARIO
            and analysis.status == AnalysisStatus.COMPLETED
        ):
            return _scenario_chart(analysis)
        if not result.rows:
            return None
        if "current_inventory_remaining" in task.metrics:
            return _inventory_chart(result)
        if {"forward_load_factor", "forward_yield"}.issubset(task.metrics):
            return _load_factor_yield_chart(result)

        category = _first_column(result.columns, _is_category, result.rows)
        measure = _first_column(result.columns, _is_numeric, result.rows)
        if category is None or measure is None or category == measure:
            return None
        if any(term in category.lower() for term in _DATE_TERMS):
            return _time_series_chart(task, result, category, measure)
        return _bar_chart(task, result, category, measure)


def _scenario_chart(analysis: AnalysisOutcome) -> ChartBlock:
    baseline = float(analysis.values["baseline"])
    scenario = float(analysis.values["scenario"])
    delta = float(analysis.values["delta"])
    values = [
        {"case": "Baseline", "revenue": baseline},
        {"case": "Scenario", "revenue": scenario},
    ]
    return ChartBlock(
        title="Revenue impact",
        description=f"Baseline and static scenario revenue; change {delta:+,.2f}.",
        specification=_base_spec(
            values,
            {
                "x": {
                    "field": "case",
                    "type": "nominal",
                    "axis": {"title": None, "labelAngle": 0},
                    "sort": ["Baseline", "Scenario"],
                },
                "y": {
                    "field": "revenue",
                    "type": "quantitative",
                    "axis": {"title": "Revenue", "format": ",.2f"},
                },
                "color": {
                    "field": "case",
                    "type": "nominal",
                    "scale": {
                        "domain": ["Baseline", "Scenario"],
                        "range": [_PALETTE[3], _PALETTE[0]],
                    },
                    "legend": None,
                },
                "tooltip": [
                    {"field": "case", "type": "nominal", "title": "Case"},
                    {
                        "field": "revenue",
                        "type": "quantitative",
                        "title": "Revenue",
                        "format": ",.2f",
                    },
                ],
            },
            mark={"type": "bar", "cornerRadiusTopLeft": 7, "cornerRadiusTopRight": 7},
            height=280,
        ),
    )


def _inventory_chart(result: QueryResult) -> ChartBlock | None:
    measure = next((name for name in result.columns if "remaining" in name.lower()), None)
    if measure is None:
        return None
    values: list[dict[str, Any]] = []
    for row in result.rows[:_MAX_VISUALIZED_ROWS]:
        if not isinstance(row.get(measure), (int, float)):
            continue
        label_parts = [row.get("flight"), row.get("route"), row.get("flight_date")]
        values.append({**row, "flight_label": " · ".join(str(v) for v in label_parts if v)})
    if not values:
        return None
    return ChartBlock(
        title="Flights with the lowest remaining inventory",
        description="Flight instances ranked from the lowest remaining authorized inventory.",
        specification=_base_spec(
            values,
            {
                "y": {
                    "field": "flight_label",
                    "type": "nominal",
                    "sort": {"field": measure, "order": "ascending"},
                    "axis": {"title": None, "labelLimit": 260},
                },
                "x": {
                    "field": measure,
                    "type": "quantitative",
                    "axis": {"title": "Remaining inventory", "format": ",.0f"},
                },
                "color": {
                    "field": measure,
                    "type": "quantitative",
                    "scale": {"scheme": "redyellowgreen"},
                    "legend": None,
                },
                "tooltip": _tooltips(result.columns, result.rows),
            },
            mark={"type": "bar", "cornerRadiusEnd": 6},
            height=max(220, min(520, len(values) * 34)),
        ),
    )


def _load_factor_yield_chart(result: QueryResult) -> ChartBlock | None:
    load_factor = next((name for name in result.columns if "load_factor" in name), None)
    yield_column = next((name for name in result.columns if "yield" in name), None)
    if load_factor is None or yield_column is None:
        return None
    color = next((name for name in result.columns if "route" in name), None)
    encoding: dict[str, Any] = {
        "x": {
            "field": load_factor,
            "type": "quantitative",
            "axis": {"title": "Load factor", "format": ".0%"},
        },
        "y": {
            "field": yield_column,
            "type": "quantitative",
            "axis": {"title": "Yield", "format": ",.2f"},
        },
        "tooltip": _tooltips(result.columns, result.rows),
    }
    if color:
        encoding["color"] = {
            "field": color,
            "type": "nominal",
            "scale": {"range": _PALETTE},
            "legend": {"title": "Route", "orient": "bottom"},
        }
    return ChartBlock(
        title="Load factor and yield opportunity map",
        description="Routes toward the upper-right combine stronger load factor and yield.",
        specification=_base_spec(
            list(result.rows[:_MAX_VISUALIZED_ROWS]),
            encoding,
            mark={
                "type": "circle",
                "size": 130,
                "opacity": 0.8,
                "stroke": "white",
                "strokeWidth": 1,
            },
            height=360,
        ),
    )


def _time_series_chart(
    task: QueryTask, result: QueryResult, category: str, measure: str
) -> ChartBlock:
    return ChartBlock(
        title=task.purpose,
        description=f"{measure.replace('_', ' ').title()} over {category.replace('_', ' ')}.",
        specification=_base_spec(
            list(result.rows[:_MAX_VISUALIZED_ROWS]),
            {
                "x": {"field": category, "type": "temporal", "axis": {"title": None}},
                "y": {
                    "field": measure,
                    "type": "quantitative",
                    "axis": {"title": measure.replace("_", " ").title(), "format": ",.2f"},
                },
                "tooltip": _tooltips(result.columns, result.rows),
            },
            mark={
                "type": "line",
                "point": {"filled": True, "size": 55},
                "strokeWidth": 3,
                "color": _PALETTE[0],
            },
            height=330,
        ),
    )


def _bar_chart(task: QueryTask, result: QueryResult, category: str, measure: str) -> ChartBlock:
    ranking = any(term in task.purpose.lower() for term in _RANK_TERMS)
    return ChartBlock(
        title=task.purpose,
        description=f"Comparison of {measure.replace('_', ' ')} by {category.replace('_', ' ')}.",
        specification=_base_spec(
            list(result.rows[:_MAX_VISUALIZED_ROWS]),
            {
                "y": {
                    "field": category,
                    "type": "nominal",
                    "sort": "-x" if ranking else None,
                    "axis": {"title": None, "labelLimit": 220},
                },
                "x": {
                    "field": measure,
                    "type": "quantitative",
                    "axis": {"title": measure.replace("_", " ").title(), "format": ",.2f"},
                },
                "color": {"value": _PALETTE[0]},
                "tooltip": _tooltips(result.columns, result.rows),
            },
            mark={"type": "bar", "cornerRadiusEnd": 6},
            height=max(220, min(520, len(result.rows) * 31)),
        ),
    )


def _base_spec(
    values: list[dict[str, Any]],
    encoding: dict[str, Any],
    *,
    mark: dict[str, Any],
    height: int,
) -> dict[str, Any]:
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "background": "transparent",
        "width": "container",
        "height": height,
        "autosize": {"type": "fit", "contains": "padding", "resize": True},
        "data": {"values": values},
        "mark": mark,
        "encoding": encoding,
        "config": {
            "font": "Inter, ui-sans-serif, system-ui, sans-serif",
            "view": {"stroke": None},
            "axis": {
                "domain": False,
                "gridColor": "#e8edea",
                "labelColor": "#526159",
                "titleColor": "#34443b",
                "tickColor": "#d8dfdb",
            },
            "legend": {"labelColor": "#526159", "titleColor": "#34443b"},
        },
    }


def _first_column(
    columns: Sequence[str], predicate: Any, rows: Sequence[dict[str, Any]]
) -> str | None:
    return next((column for column in columns if predicate(column, rows)), None)


def _is_category(column: str, rows: Sequence[dict[str, Any]]) -> bool:
    return any(
        row.get(column) is not None and not isinstance(row.get(column), (int, float))
        for row in rows
    )


def _is_numeric(column: str, rows: Sequence[dict[str, Any]]) -> bool:
    values = [row.get(column) for row in rows if row.get(column) is not None]
    return bool(values) and all(isinstance(value, (int, float)) for value in values)


def _tooltips(columns: Sequence[str], rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    tooltips: list[dict[str, Any]] = []
    for column in columns[:8]:
        numeric = _is_numeric(column, rows)
        tooltip: dict[str, Any] = {
            "field": column,
            "type": "quantitative" if numeric else "nominal",
            "title": column.replace("_", " ").title(),
        }
        if numeric:
            tooltip["format"] = ".1%" if "load_factor" in column else ",.2f"
        tooltips.append(tooltip)
    return tooltips
