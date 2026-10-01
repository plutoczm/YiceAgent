"""Dispatch P4 contract export in the Harness environment."""

from dev import ROOT, service_process

if __name__ == "__main__":
    raise SystemExit(
        service_process(
            "harness",
            [
                "scripts/export_p4_api.py",
                str(ROOT / "contracts/service_api/v1/p4.json"),
            ],
        ).returncode
    )
