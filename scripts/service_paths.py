"""Physical Monorepo directories; preserve the established CLI/service identities."""

SERVICE_DIRECTORIES = {
    "harness": "harness",
    "supportops": "support",
    "datacopilot": "analytics",
}


def relocated_path(relative):
    """Resolve historical source-manifest paths without rewriting its evidence."""
    for identity, directory in SERVICE_DIRECTORIES.items():
        prefix = f"services/{identity}/"
        if relative.startswith(prefix):
            return f"services/{directory}/" + relative[len(prefix) :]
    return relative
