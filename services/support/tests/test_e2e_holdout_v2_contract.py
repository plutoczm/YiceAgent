from evals.e2e_holdout_v2 import build_e2e_holdout_v2_scenarios
from evals.e2e_sources import component_benchmark_texts, regression_v1_texts

_ALLOWED_TOOLS = {
    "knowledge.search",
    "order.get",
    "refund.quote",
    "refund.execute",
    "return.execute",
    "ticket.create",
}
_MUTATION_TOOLS = {"refund.execute", "return.execute"}


def test_holdout_v2_has_unique_sixty_scenarios() -> None:
    scenarios = build_e2e_holdout_v2_scenarios()
    assert len(scenarios) == 60
    assert len({scenario.id for scenario in scenarios}) == 60
    assert len({_normalize(scenario.message) for scenario in scenarios}) == 60


def test_holdout_v2_has_zero_component_or_regression_text_overlap() -> None:
    prohibited = {
        _normalize(text)
        for text in [*component_benchmark_texts(), *regression_v1_texts()]
    }
    assert all(
        _normalize(scenario.message) not in prohibited
        for scenario in build_e2e_holdout_v2_scenarios()
    )


def test_holdout_v2_tool_and_resolution_contracts_are_safe() -> None:
    for scenario in build_e2e_holdout_v2_scenarios():
        assert set(scenario.expected_tools) <= _ALLOWED_TOOLS
        assert {operation for operation, _ in scenario.expected_tool_resources} <= _ALLOWED_TOOLS
        if scenario.mutation_authorized:
            assert scenario.resolution == "confirm"
            assert set(scenario.expected_tools) & _MUTATION_TOOLS
        if scenario.resolution is not None:
            assert scenario.expect_pending_action is True
            assert scenario.expected_terminal_status is not None


def _normalize(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())
