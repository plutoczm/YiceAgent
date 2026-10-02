"""Verify producer/root/consumer bytes and consumer acceptance without rewriting."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    paths = [
        root / "contracts/business_data/v1/contract.json",
        root / "services/support/contracts/business_data/v1/contract.json",
        root / "services/analytics/contracts/external/business_data/v1/contract.json",
    ]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    acceptance = json.loads(
        (
            root
            / "services/analytics/contracts/external/business_data/v1/acceptance.json"
        ).read_text(encoding="utf-8")
    )
    if len(set(hashes)) != 1 or acceptance["contract_sha256"] != hashes[0]:
        raise ValueError("contract bytes/acceptance mismatch")
    print("PASS: root/producer/consumer/acceptance", hashes[0])


if __name__ == "__main__":
    check()
