from scripts.evaluate_business_analytics_live import main


def test_live_llm_evaluation_is_disabled_without_explicit_environment_opt_in(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("DATACOPILOT_EVAL_LIVE_LLM", raising=False)

    assert main() == 0
    assert "is disabled" in capsys.readouterr().out
