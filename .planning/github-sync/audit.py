"""Inspect only publishable source/history and report paths, never matched secrets."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".runtime/v2/github-sync"
OUT.mkdir(exist_ok=True)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


TAGS = ["v1.0.0", "v2.0.0-p2", "v2.0.0-p3", "v2.0.0-p4", "v2.0.0-p5-react"]
RULES = {
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "provider_key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{28,}\b"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s*[A-Za-z0-9+/=\r\n]{80,}"),
    "oauth_callback_code": re.compile(rb"[?&]code=ac_[A-Za-z0-9_-]{24,}"),
}


def check(name, data, *, origin):
    findings = []
    if len(data) > 50 * 1024 * 1024:
        findings.append({"path": name, "rule": "large_blob", "origin": origin})
    lower = name.lower().replace("\\", "/")
    if any(p in lower.split("/") for p in (".runtime", "node_modules", ".venv")) or (
        Path(lower).name.startswith(".env") and not lower.endswith(".example")
    ):
        findings.append({"path": name, "rule": "private_runtime_path", "origin": origin})
    for rule, pattern in RULES.items():
        for match in pattern.finditer(data):
            findings.append({"path": name, "rule": rule, "origin": origin, "line": data[:match.start()].count(b"\n") + 1, "value_sha256": hashlib.sha256(match.group()).hexdigest()})
    return findings


paths = sorted(set(x.decode("utf-8") for x in git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0") if x))
current = []
findings = []
for name in paths:
    p = ROOT / name
    if p.is_file():
        value = p.read_bytes()
        findings.extend(check(name, value, origin="current"))
        current.append({"path": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()})

objects = {}
for line in git("rev-list", "--objects", "HEAD", *TAGS).decode("utf-8").splitlines():
    oid, _, name = line.partition(" ")
    objects[oid] = name
frames = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, input="".join(x + "\n" for x in objects).encode(), capture_output=True, check=True).stdout
offset, blobs, largest = 0, 0, 0
for oid, name in objects.items():
    end = frames.index(b"\n", offset)
    info = frames[offset:end].split()
    length = int(info[2])
    payload = frames[end + 1 : end + 1 + length]
    offset = end + 1 + length + 1
    if info[1] == b"blob":
        blobs += 1
        largest = max(largest, length)
        findings.extend(check(name, payload, origin="historical_blob:" + oid))
assert offset == len(frames)
reviewed = []
unresolved = []
for item in findings:
    if (
        item["path"] == "tests/unit/test_model_protocols.py"
        and item["rule"] == "provider_key"
        and item["value_sha256"] == "0b8031074c00b175e247eb7472fddcae8c07c4d4fa9c265b77e6a720a634936b"
    ):
        reviewed.append({**item, "classification": "fixed_sequential_alphabet_and_digits_negative_test_payload"})
    else:
        unresolved.append(item)
report = {"status": "PASS" if not unresolved else "BLOCKED", "current_files": len(current), "reachable_source_blobs": blobs, "largest_blob_bytes": largest, "tags": TAGS, "unresolved": unresolved, "reviewed_synthetic_fixtures": reviewed, "files": current}
(OUT / "publication-audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k != "files"}))
if unresolved:
    raise SystemExit(1)
