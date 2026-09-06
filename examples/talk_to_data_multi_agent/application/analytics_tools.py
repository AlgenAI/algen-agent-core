from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from traccia_runtime.methods import (
    AnalyticalMethodManifest,
    AnalyticalMethodRegistry,
    MethodLifecycle,
    adapt_sync_method,
)
from traccia_runtime.semantics import (
    AnalysisKind,
    AnalysisOutcome,
    AnalysisStatus,
    Assumption,
    DataRequirement,
)

Rows = Sequence[dict[str, Any]]
AnalysisTool = Callable[[Rows, dict[str, Any]], AnalysisOutcome]


class AnalyticsToolParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_task_id: str | None = None


class NoParameters(AnalyticsToolParameters):
    pass


class StaticFareParameters(AnalyticsToolParameters):
    value_column: str = Field(min_length=1)
    percent_change: float


class AnomalyParameters(AnalyticsToolParameters):
    value_column: str = "value"


class ForecastParameters(AnalyticsToolParameters):
    value_column: str = "value"
    horizon: int = Field(default=7, ge=1, le=365)


class CapacityParameters(AnalyticsToolParameters):
    load_factor_column: str = "load_factor"
    yield_column: str = "yield"
    group_column: str = "route"
    load_factor_threshold: float = Field(default=0.85, ge=0, le=1)


class DiagnosticParameters(AnalyticsToolParameters):
    target_column: str = "revenue"


class CausalScenarioParameters(AnalyticsToolParameters):
    scenario: str = "requested operational change"


class OpportunityParameters(AnalyticsToolParameters):
    entity_column: str = "route"
    score_column: str = "opportunity_score"
    top_n: int = Field(default=5, ge=1, le=25)


class AirlineAnalyticsToolRegistry:
    """Domain analytics registered by the application, never by runtime core."""

    def __init__(self) -> None:
        self._tools: dict[str, AnalysisTool] = {}
        self._parameter_models: dict[str, type[AnalyticsToolParameters]] = {}
        self._analysis_kinds: dict[str, tuple[AnalysisKind, ...]] = {}

    def register(
        self,
        name: str,
        tool: AnalysisTool,
        parameter_model: type[AnalyticsToolParameters] = NoParameters,
        analysis_kinds: tuple[AnalysisKind, ...] = (AnalysisKind.DESCRIPTIVE,),
    ) -> None:
        if name in self._tools:
            raise ValueError(f"analytics tool {name!r} is already registered")
        self._tools[name] = tool
        self._parameter_models[name] = parameter_model
        self._analysis_kinds[name] = analysis_kinds

    def install(self, runtime_registry: AnalyticalMethodRegistry) -> None:
        """Publish airline-owned methods through the Runtime's generic method contract."""
        for name in self.names():
            runtime_registry.register(
                AnalyticalMethodManifest(
                    name=name,
                    version="1.0.0",
                    description=f"Airline application analytical method: {name}",
                    analysis_kinds=self._analysis_kinds[name],
                    input_schema={"type": "array", "items": {"type": "array"}},
                    parameter_schema=self._parameter_models[name].model_json_schema(),
                    lifecycle=MethodLifecycle.VALIDATED,
                    validation_reference="tests/unit/test_talk_to_data_analytics_tools.py",
                    metadata={"owner": "talk-to-data application", "runtime_core": False},
                ),
                adapt_sync_method(self._tools[name]),
            )

    def execute(
        self, name: str, rows: Rows, parameters: dict[str, Any]
    ) -> AnalysisOutcome:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ValueError(f"analytics tool {name!r} is not registered") from exc
        validated = self._parameter_models[name].model_validate(parameters)
        return tool(rows, validated.model_dump(exclude_none=True))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def parameter_error(self, name: str, parameters: dict[str, Any]) -> str | None:
        try:
            self._parameter_models[name].model_validate(parameters)
        except (KeyError, ValidationError) as exc:
            return str(exc)
        return None

    def normalized_parameters(
        self, name: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        return self._parameter_models[name].model_validate(parameters).model_dump(
            exclude_none=True
        )

    def specifications(self) -> dict[str, dict[str, Any]]:
        return {
            name: self._parameter_models[name].model_json_schema()
            for name in sorted(self._tools)
        }


def default_airline_analytics_tools() -> AirlineAnalyticsToolRegistry:
    registry = AirlineAnalyticsToolRegistry()
    registry.register("none", descriptive_summary, analysis_kinds=(AnalysisKind.DESCRIPTIVE,))
    registry.register(
        "static_fare_scenario", static_fare_scenario, StaticFareParameters,
        (AnalysisKind.STATIC_SCENARIO,),
    )
    registry.register(
        "anomaly_detection", anomaly_detection, AnomalyParameters,
        (AnalysisKind.ANOMALY,),
    )
    registry.register(
        "linear_forecast", linear_forecast, ForecastParameters,
        (AnalysisKind.FORECAST,),
    )
    registry.register(
        "capacity_screen", capacity_screen, CapacityParameters,
        (AnalysisKind.OPTIMIZATION,),
    )
    registry.register(
        "revenue_diagnostics", revenue_diagnostics, DiagnosticParameters,
        (AnalysisKind.DIAGNOSTIC,),
    )
    registry.register(
        "opportunity_screen", opportunity_screen, OpportunityParameters,
        (AnalysisKind.DISCOVERY,),
    )
    registry.register(
        "causal_scenario_gate", causal_scenario_gate, CausalScenarioParameters,
        (AnalysisKind.CAUSAL_SCENARIO,),
    )
    return registry


def _numbers(rows: Rows, column: str) -> list[float]:
    return [float(row[column]) for row in rows if isinstance(row.get(column), (int, float))]


def descriptive_summary(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    del parameters
    return AnalysisOutcome(
        kind=AnalysisKind.DESCRIPTIVE,
        status=AnalysisStatus.COMPLETED,
        summary=f"Returned {len(rows)} governed result rows.",
        values={"row_count": len(rows)},
        method="governed SQL aggregation",
        confidence=1.0,
    )


def static_fare_scenario(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    column = str(parameters["value_column"])
    percent = float(parameters["percent_change"])
    values = _numbers(rows, column)
    if not values:
        return _missing(AnalysisKind.STATIC_SCENARIO, column)
    baseline = sum(values)
    projected = baseline * (1 + percent / 100)
    return AnalysisOutcome(
        kind=AnalysisKind.STATIC_SCENARIO,
        status=AnalysisStatus.COMPLETED,
        summary=(
            f"A {percent:+.2f}% arithmetic fare change moves revenue from "
            f"{baseline:,.2f} to {projected:,.2f} if demand does not change."
        ),
        values={"baseline": baseline, "scenario": projected, "delta": projected - baseline},
        assumptions=(
            Assumption(
                statement="Passenger volume, mix, cancellations, and competitor response remain unchanged."
            ),
        ),
        method="constant-volume arithmetic sensitivity",
        model_version="static-fare-sensitivity-v1",
        confidence=1.0,
        warnings=("This is not a demand forecast or elasticity estimate.",),
    )


def anomaly_detection(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    column = str(parameters.get("value_column", "value"))
    values = _numbers(rows, column)
    if len(values) < 5:
        return AnalysisOutcome(
            kind=AnalysisKind.ANOMALY,
            status=AnalysisStatus.INSUFFICIENT_DATA,
            summary="At least five comparable observations are required for anomaly screening.",
            requirements=(DataRequirement(name=column, reason="numeric series with >=5 rows"),),
        )
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values)
    flagged = [index for index, value in enumerate(values) if deviation and abs(value - mean) / deviation >= 2]
    return AnalysisOutcome(
        kind=AnalysisKind.ANOMALY,
        status=AnalysisStatus.COMPLETED,
        summary=f"Found {len(flagged)} observations at least two standard deviations from the mean.",
        values={"mean": mean, "standard_deviation": deviation, "flagged_indexes": flagged},
        method="two-sided z-score screen",
        model_version="zscore-v1",
        confidence=0.7,
        warnings=("Operational and seasonal context must be reviewed before acting.",),
    )


def linear_forecast(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    column = str(parameters.get("value_column", "value"))
    horizon = int(parameters.get("horizon", 7))
    values = _numbers(rows, column)
    if len(values) < 8:
        return AnalysisOutcome(
            kind=AnalysisKind.FORECAST,
            status=AnalysisStatus.INSUFFICIENT_DATA,
            summary="A forecast requires at least eight ordered historical observations.",
            requirements=(DataRequirement(name=column, reason="ordered historical numeric series"),),
        )
    x_mean = (len(values) - 1) / 2
    y_mean = statistics.fmean(values)
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    slope = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values)) / denominator
    intercept = y_mean - slope * x_mean
    fitted = [intercept + slope * index for index in range(len(values))]
    residual = math.sqrt(sum((actual - fit) ** 2 for actual, fit in zip(values, fitted, strict=True)) / max(1, len(values) - 2))
    forecast = [intercept + slope * (len(values) + step) for step in range(horizon)]
    return AnalysisOutcome(
        kind=AnalysisKind.FORECAST,
        status=AnalysisStatus.COMPLETED,
        summary=f"Produced a {horizon}-period linear baseline forecast.",
        values={"forecast": forecast, "slope": slope, "residual_error": residual},
        confidence_interval=(forecast[-1] - 1.96 * residual, forecast[-1] + 1.96 * residual),
        method="ordinary least-squares linear trend",
        model_version="linear-trend-v1",
        confidence=0.5,
        warnings=("This baseline does not model seasonality, events, or unconstrained demand.",),
    )


def capacity_screen(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    load_column = str(parameters.get("load_factor_column", "load_factor"))
    yield_column = str(parameters.get("yield_column", "yield"))
    route_column = str(parameters.get("group_column", "route"))
    threshold = float(parameters.get("load_factor_threshold", 0.85))
    yields = _numbers(rows, yield_column)
    if not yields:
        return _missing(AnalysisKind.OPTIMIZATION, yield_column)
    median_yield = statistics.median(yields)
    candidates = [
        {
            "route": row.get(route_column),
            "load_factor": row.get(load_column),
            "yield": row.get(yield_column),
        }
        for row in rows
        if isinstance(row.get(load_column), (int, float))
        and float(row[load_column]) >= threshold
        and isinstance(row.get(yield_column), (int, float))
        and float(row[yield_column]) >= median_yield
    ]
    return AnalysisOutcome(
        kind=AnalysisKind.OPTIMIZATION,
        status=AnalysisStatus.COMPLETED,
        summary=f"Screened {len(candidates)} high-load, above-median-yield candidates.",
        values={"candidates": candidates, "load_factor_threshold": threshold, "median_yield": median_yield},
        assumptions=(Assumption(statement="Load factor values are expressed on a 0-1 scale."),),
        method="capacity candidate screen",
        model_version="capacity-screen-v1",
        confidence=0.55,
        warnings=("Candidates are not recommendations until cost, aircraft, slot, and demand constraints are evaluated.",),
    )


def revenue_diagnostics(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    target = str(parameters.get("target_column", "revenue"))
    targets = _numbers(rows, target)
    if len(targets) < 5:
        return _missing(AnalysisKind.DIAGNOSTIC, target)
    correlations: dict[str, float] = {}
    for column in rows[0] if rows else ():
        pairs = [
            (float(row[target]), float(row[column]))
            for row in rows
            if isinstance(row.get(target), (int, float))
            and isinstance(row.get(column), (int, float))
        ]
        if column == target or len(pairs) < 5:
            continue
        left, right = zip(*pairs, strict=True)
        try:
            correlations[column] = statistics.correlation(left, right)
        except statistics.StatisticsError:
            continue
    return AnalysisOutcome(
        kind=AnalysisKind.DIAGNOSTIC,
        status=AnalysisStatus.COMPLETED,
        summary="Computed descriptive correlations with revenue.",
        values={"correlations": dict(sorted(correlations.items(), key=lambda item: abs(item[1]), reverse=True))},
        method="Pearson correlation screen",
        model_version="revenue-diagnostics-v1",
        confidence=0.45,
        warnings=("Correlation does not establish causal impact.",),
    )


def causal_scenario_gate(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    del rows
    scenario = str(parameters.get("scenario", "requested operational change"))
    return AnalysisOutcome(
        kind=AnalysisKind.CAUSAL_SCENARIO,
        status=AnalysisStatus.REQUIRES_MODEL,
        summary=f"A defensible result for {scenario} requires a calibrated causal or simulation model.",
        requirements=(
            DataRequirement(name="intervention_history", reason="historical actions and observed outcomes"),
            DataRequirement(name="demand_model", reason="estimate demand substitution and stimulation"),
            DataRequirement(name="operational_constraints", reason="capacity, schedule, fleet, slot, and cost constraints"),
        ),
        warnings=("Traccia Runtime will not fabricate a scenario result from an LLM response.",),
    )


def opportunity_screen(rows: Rows, parameters: dict[str, Any]) -> AnalysisOutcome:
    entity_column = str(parameters.get("entity_column", "route"))
    score_column = str(parameters.get("score_column", "opportunity_score"))
    top_n = min(max(int(parameters.get("top_n", 5)), 1), 25)
    scored: list[dict[str, Any]] = [
        {
            "entity": row.get(entity_column),
            "opportunity_score": float(row[score_column]),
        }
        for row in rows
        if row.get(entity_column) is not None
        and isinstance(row.get(score_column), (int, float))
    ]
    if not scored:
        return _missing(AnalysisKind.DISCOVERY, score_column)
    candidates = sorted(
        scored, key=lambda item: item["opportunity_score"], reverse=True
    )[:top_n]
    return AnalysisOutcome(
        kind=AnalysisKind.DISCOVERY,
        status=AnalysisStatus.COMPLETED,
        summary=f"Ranked {len(candidates)} candidates using the supplied governed opportunity score.",
        values={"candidates": candidates},
        method="deterministic governed-score ranking",
        model_version="opportunity-screen-v1",
        confidence=0.5,
        warnings=(
            "Ranking quality depends entirely on the governed score definition and source freshness.",
            "This screen does not establish causal uplift or operational feasibility.",
        ),
    )


def _missing(kind: AnalysisKind, column: str) -> AnalysisOutcome:
    return AnalysisOutcome(
        kind=kind,
        status=AnalysisStatus.INSUFFICIENT_DATA,
        summary=f"The query result does not contain usable numeric values for {column!r}.",
        requirements=(DataRequirement(name=column, reason="numeric analytical input"),),
    )
