"""部署前校验无外部依赖的 DataCopilot Web 演示版。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
REQUIRED = ("index.html", "styles.css", "app.js")


def main() -> None:
    missing = [name for name in REQUIRED if not (WEB / name).is_file()]
    if missing:
        raise SystemExit(f"Missing web assets: {', '.join(missing)}")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    if "DataCopilot" not in html or "app.js" not in html:
        raise SystemExit("web/index.html does not contain the expected application entry points")
    print(f"Validated DataCopilot web demo: {WEB}")


if __name__ == "__main__":
    main()
