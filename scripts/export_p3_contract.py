"""Export the P3 wire DTO snapshot without service application imports."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ast.parse(
        (ROOT / "services/support/app/domain.py").read_text(encoding="utf-8")
    )
    classes = {
        node.name: node for node in source.body if isinstance(node, ast.ClassDef)
    }
    needed = {
        "OrderView",
        "OperationView",
        "ConfirmationResponse",
        "SupportResponse",
        "TicketView",
        "TicketMessageView",
    }
    while True:
        dependencies = {
            node.id
            for name in needed
            for node in ast.walk(classes[name])
            if isinstance(node, ast.Name) and node.id in classes
        }
        if dependencies <= needed:
            break
        needed |= dependencies
    for name in needed:
        node = classes[name]
        if ast.get_docstring(node) is None:
            node.body.insert(
                0, ast.Expr(ast.Constant(f"SupportOps v1 {name} wire contract."))
            )
    nodes = [
        node
        for node in source.body
        if isinstance(node, ast.ClassDef) and node.name in needed
    ]
    used = {
        node.id
        for model in nodes
        for node in ast.walk(model)
        if isinstance(node, ast.Name)
    }
    imports = []
    for node in source.body:
        if isinstance(node, ast.ImportFrom):
            node.names = [
                alias
                for alias in node.names
                if node.module == "__future__" or (alias.asname or alias.name) in used
            ]
            if node.names:
                imports.append(node)
    module = ast.Module(body=[*imports, *nodes], type_ignores=[])
    output = ROOT / "services/harness/backend/src/app/adapters/supportops_dto.py"
    output.write_text(
        '"""P3 HTTP wire snapshot generated from domain DTOs; no cross-service imports."""\n'
        + ast.unparse(module)
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "I", "--fix", str(output)],
        check=True,
    )
    subprocess.run([sys.executable, "-m", "ruff", "format", str(output)], check=True)


if __name__ == "__main__":
    main()
