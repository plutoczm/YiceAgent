"""Human-only local account setup. Never pass API keys in shell args or chat."""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from workflowops.config import CONFIG, Settings  # noqa: E402
from workflowops.models.node_env import node_environment  # noqa: E402


def update(changes):
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    data.update(changes)
    Settings.model_validate(data)
    temporary = CONFIG.with_suffix(".account.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, indent=2) + "\n")
    os.replace(temporary, CONFIG)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["login-chatgpt", "configure-deepseek", "select-chatgpt"]
    )
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--paid-approved", action="store_true")
    parser.add_argument("--model-id")
    args = parser.parse_args()
    if not args.approved:
        print("explicit_approval_required")
        return 1
    if args.command == "login-chatgpt":
        return subprocess.call(
            [shutil.which("node"), str(ROOT / "bridge/bridge.mjs"), "--login", "--approved"],
            env=node_environment(),
        )
    if args.command == "configure-deepseek":
        key = getpass.getpass("DeepSeek API key (hidden, project-local only): ").strip()
        if len(key) < 16 or len(key) > 500:
            print("invalid_key")
            return 1
        update(
            {
                "deepseek_key": key,
                "input_price_cny_per_million": "2",
                "output_price_cny_per_million": "8",
                "paid_calls_enabled": args.paid_approved,
                "billing_cap_cny": "15.00",
                "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                "pricing_checked_on": __import__("datetime")
                .datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat(),
            }
        )
        print(
            "Project key saved. Conservative peak rates 2/8 CNY per million; cap15 CNY. Paid enabled:",
            args.paid_approved,
        )
        return 0
    if not args.model_id:
        print("model_id_required")
        return 1
    # Registry/actual access validation occurs in provider status/smoke; no invented default model.
    update({"chatgpt_model": args.model_id})
    print("Model selected; real provider validation is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
