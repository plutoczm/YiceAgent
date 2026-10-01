"""基座 vs 微调模型对比评测。

用法（项目根目录）：
    python -m training.eval.evaluate --runner base   # transformers 离线评测基座
    python -m training.eval.evaluate --runner ft    # Ollama OpenAI 兼容评测微调模型

读取 config/eval.yaml 指定的评测样例（text2sql_cases / warehouse_cases），
分别打分并产出 report_<runner>.json 与 summary.md。数值如实记录。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from backend.app.application.text2sql.models import SQLEngine  # noqa: E402
from training.eval.metrics.score import render_summary_markdown, summarize, write_report  # noqa: E402
from training.eval.metrics.text2sql_metrics import score_text2sql_case  # noqa: E402
from training.eval.metrics.warehouse_metrics import score_warehouse_case  # noqa: E402


def load_cases(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    cases = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def make_runner(runner_name: str, eval_config: dict):
    if runner_name == "base":
        from training.eval.runners.base_runner import BaseRunner

        return BaseRunner(
            model_name=eval_config["base_model"],
            max_new_tokens=eval_config["max_new_tokens_sql"],
            temperature=eval_config.get("temperature", 0.0),
        )
    if runner_name == "lora":
        from training.eval.runners.base_runner import LoraRunner

        return LoraRunner(
            model_name=eval_config["base_model"],
            adapter_path=eval_config["lora_adapter"],
            max_new_tokens=eval_config["max_new_tokens_sql"],
            temperature=eval_config.get("temperature", 0.0),
        )
    if runner_name == "ft":
        from training.eval.runners.ollama_runner import OllamaRunner

        return OllamaRunner(
            base_url=eval_config["ollama_base_url"],
            model=eval_config["ollama_ft_model"],
            max_new_tokens=eval_config["max_new_tokens_sql"],
            temperature=eval_config.get("temperature", 0.0),
        )
    raise ValueError(f"未知 runner：{runner_name}")


def run_evaluation(
    *,
    runner_name: str,
    eval_config: dict,
    text2sql_cases: list[dict],
    warehouse_cases: list[dict],
) -> dict:
    runner = make_runner(runner_name, eval_config)
    text2sql_results = []
    for case in text2sql_cases:
        messages = [
            {"role": "system", "content": case.get("system", "")},
            {"role": "user", "content": case.get("prompt", case.get("question", ""))},
        ]
        predicted = runner.generate(
            messages, max_new_tokens=eval_config["max_new_tokens_sql"]
        )
        text2sql_results.append(
            score_text2sql_case(
                predicted_sql=predicted,
                gold_sql=case["gold_sql"],
                schema_context=case.get("schema_context"),
                engine=SQLEngine(case.get("engine", "spark_sql")),
            )
        )

    warehouse_results = []
    for case in warehouse_cases:
        messages = [
            {"role": "system", "content": case.get("system", "")},
            {"role": "user", "content": case.get("prompt", case.get("requirement", ""))},
        ]
        predicted = runner.generate(
            messages, max_new_tokens=eval_config["max_new_tokens_warehouse"]
        )
        warehouse_results.append(score_warehouse_case(predicted=predicted))

    return summarize(
        text2sql_results=text2sql_results,
        warehouse_results=warehouse_results,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="基座 vs 微调对比评测")
    parser.add_argument("--runner", choices=["base", "lora", "ft"], required=True)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "training/config/eval.yaml")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--max-cases", type=int, default=0, help="每个任务最多评测条数，0=全部")
    parser.add_argument("--task", choices=["both", "text2sql", "warehouse"], default="both")
    args = parser.parse_args()

    eval_config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    out_dir = args.out_dir or PROJECT_ROOT / eval_config["output_dir"]
    text2sql_cases = load_cases(PROJECT_ROOT / eval_config["text2sql_cases"])
    warehouse_cases = load_cases(PROJECT_ROOT / eval_config["warehouse_cases"])
    if args.max_cases > 0:
        text2sql_cases = text2sql_cases[: args.max_cases]
        warehouse_cases = warehouse_cases[: args.max_cases]
    if args.task == "text2sql":
        warehouse_cases = []
    elif args.task == "warehouse":
        text2sql_cases = []
    if not text2sql_cases and not warehouse_cases:
        raise RuntimeError(
            "没有评测样例。请先运行 python -m training.dataset_builder.build 生成评测用例。"
        )

    report = run_evaluation(
        runner_name=args.runner,
        eval_config=eval_config,
        text2sql_cases=text2sql_cases,
        warehouse_cases=warehouse_cases,
    )
    write_report(report, out_dir / f"report_{args.runner}.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.runner in ("ft", "lora") and (out_dir / "report_base.json").is_file():
        base_report = json.loads((out_dir / "report_base.json").read_text(encoding="utf-8"))
        (out_dir / "summary.md").write_text(
            render_summary_markdown(base_report, report),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
