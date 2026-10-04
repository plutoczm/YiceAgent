"""Fetch the pinned public CUDA wheel in bounded ranges; require the official SHA-256."""

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".runtime/v2/wheels"
INDEX = "https://download.pytorch.org/whl/cu130/torch/"
FILENAME = "torch-2.14.1+cu130-cp312-cp312-win_amd64.whl"
CHUNK = 32 * 1024 * 1024


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    html = opener.open(INDEX, timeout=30).read().decode("utf-8")
    urls = re.findall(r'href="([^"]+)"', html)
    candidate = next(
        url for url in urls if urllib.parse.unquote(url.split("/")[-1].split("#")[0]) == FILENAME
    )
    url = urllib.parse.urljoin(INDEX, candidate.split("#")[0])
    expected = urllib.parse.parse_qs(urllib.parse.urlsplit(candidate).fragment)["sha256"][0]
    if urllib.parse.urlsplit(url).hostname not in {
        "download.pytorch.org",
        "download-r2.pytorch.org",
    }:
        raise ValueError("wheel_source_not_official")
    # This CDN forbids HEAD but supports authenticated-free HTTP byte ranges.
    with opener.open(
        urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "workflowops-v2"}),
        timeout=30,
    ) as response:
        if response.status != 206:
            raise ValueError("wheel_range_not_supported")
        total = int(response.headers["Content-Range"].split("/")[-1])
        response.read()

    DIRECTORY.mkdir(parents=True, exist_ok=True)
    target = DIRECTORY / FILENAME
    partial = target.with_suffix(".part")
    if target.exists():
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected:
            raise ValueError("existing_wheel_hash_mismatch")
        print(
            json.dumps(
                {"event": "wheel_verified", "bytes": target.stat().st_size, "sha256": digest}
            ),
            flush=True,
        )
        return 0
    size = partial.stat().st_size if partial.exists() else 0
    if size > total:
        raise ValueError("partial_wheel_size_invalid")
    while size < total:
        end = min(size + CHUNK - 1, total - 1)
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    url, headers={"Range": f"bytes={size}-{end}", "User-Agent": "workflowops-v2"}
                )
                with opener.open(request, timeout=60) as response:
                    if response.status != 206 or not response.headers.get(
                        "Content-Range", ""
                    ).startswith(f"bytes {size}-"):
                        raise ValueError("wheel_range_not_supported")
                    content = response.read(end - size + 2)
                if len(content) != end - size + 1:
                    raise ValueError("wheel_chunk_incomplete")
                with partial.open("ab") as stream:
                    stream.write(content)
                size += len(content)
                print(
                    json.dumps({"event": "wheel_progress", "bytes": size, "total": total}),
                    flush=True,
                )
                break
            except Exception as error:
                print(
                    json.dumps(
                        {
                            "event": "wheel_chunk_retry",
                            "attempt": attempt + 1,
                            "error_type": type(error).__name__,
                        }
                    ),
                    flush=True,
                )
                if attempt == 2:
                    raise
    with partial.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("wheel_hash_mismatch")
    partial.rename(target)
    (DIRECTORY / "cuda-wheel.json").write_text(
        json.dumps({"url": url, "filename": FILENAME, "sha256": actual, "bytes": total}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"event": "wheel_verified", "bytes": total, "sha256": actual}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
