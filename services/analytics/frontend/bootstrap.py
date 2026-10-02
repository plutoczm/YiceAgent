from pathlib import Path
import sys


def ensure_project_root_on_path() -> None:
    project_root = Path(__file__).resolve().parents[1]
    project_root_text = str(project_root)
    if project_root_text not in sys.path:
        sys.path.insert(0, project_root_text)


ensure_project_root_on_path()
