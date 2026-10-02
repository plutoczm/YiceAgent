from evals import cases, e2e_cases, retrieval_cases

_ALLOWED_TOOLS = {
    "knowledge.search",
    "order.get",
    "refund.quote",
    "refund.execute",
    "return.execute",
    "ticket.create",
}
_MUTATION_TOOLS = {"refund.execute", "return.execute"}


def test_e2e_holdout_has_unique_minimum_size_and_text() -> None:
    scenarios = e2e_cases.build_e2e_holdout_scenarios()
    assert len(scenarios) >= 60
    assert len({scenario.id for scenario in scenarios}) == len(scenarios)
    assert len({_normalize(scenario.message) for scenario in scenarios}) == len(scenarios)


def test_e2e_holdout_has_no_exact_normalized_component_overlap() -> None:
    component_text = {
        _normalize(str(case["text"])) for case in cases.build_cases() if case.get("text")
    }
    component_text.update(
        _normalize(str(case["query"]))
        for case in retrieval_cases.build_retrieval_cases()
        if case.get("query")
    )
    assert all(
        _normalize(scenario.message) not in component_text
        for scenario in e2e_cases.build_e2e_holdout_scenarios()
    )


def test_e2e_holdout_tool_contract_is_allowlisted() -> None:
    for scenario in e2e_cases.build_e2e_holdout_scenarios():
        assert set(scenario.expected_tools) <= _ALLOWED_TOOLS
        assert {operation for operation, _ in scenario.expected_tool_resources} <= _ALLOWED_TOOLS
        if scenario.mutation_authorized:
            assert scenario.resolution == "confirm"
            assert set(scenario.expected_tools) & _MUTATION_TOOLS


def test_e2e_resolution_cases_declare_pending_and_terminal_state() -> None:
    for scenario in e2e_cases.build_e2e_holdout_scenarios():
        if scenario.resolution is None:
            continue
        assert scenario.expect_pending_action is True
        assert scenario.expected_terminal_status is not None


def _normalize(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())
