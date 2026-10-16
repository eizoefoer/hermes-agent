#!/usr/bin/env python3
"""Run canonical scoped acceptance and retain source-bound, fail-closed receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path

SUMMARY = re.compile(
    r"^=== Summary: (\d+) files, (\d+) tests passed, (\d+) failed"
    r"(?:, (\d+) skipped)? \(100% complete\) in .+ ===$", re.MULTILINE
)


def parse_summary(text):
    matches = SUMMARY.findall(text)
    if len(matches) != 1:
        raise ValueError("exactly one complete canonical runner summary required")
    files, passed, failed, skipped = matches[0]
    if not int(files) or not int(passed) or int(failed):
        raise ValueError("runner requires nonzero files and passes, and zero failures")
    return {"files": int(files), "passed": int(passed), "failed": int(failed),
            "skipped": int(skipped or 0)}


def verify_source_binding(receipt, source_sha, tree_sha, run_id, manifest):
    """Compare lane claims with an independent exact-source aggregate checkout."""
    expected = {"source_sha": source_sha, "tree_sha": tree_sha, "run_id": run_id,
                "manifest_sha256": hashlib.sha256(manifest).hexdigest()}
    if any(receipt[key] != value for key, value in expected.items()):
        raise ValueError("receipt source/tree/run/manifest binding mismatch")


def git(*args):
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8", timeout=30).strip()


def prepare(manifest, expected):
    """Bind the entire checkout to the approved source stack, never a PR-only diff."""
    source = git("rev-parse", "HEAD")
    if source != expected:
        raise ValueError("expected SHA differs from checkout")
    for sha in [manifest["compatibility_baseline"], manifest["source_feature_head"],
                *[item["head"] for item in manifest["source_stack"]]]:
        subprocess.run(["git", "merge-base", "--is-ancestor", sha, source], check=True, timeout=30)
    # A modified tracked file would mean the tests did not exercise the receipt's tree.
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--"], check=True, timeout=30)
    for path in manifest["required_paths"]:
        if not Path(path).is_file():
            raise ValueError(f"required acceptance path missing: {path}")
    return {"source_sha": source, "tree_sha": git("rev-parse", "HEAD^{tree}"),
            "baseline": manifest["compatibility_baseline"],
            "feature_head": manifest["source_feature_head"],
            "changed_paths": git("diff", "--name-only", manifest["compatibility_baseline"], source).splitlines(),
            "default_main_snapshot": manifest["default_main_snapshot"]}


def selection(manifest, lane, output):
    if lane == "shared":
        paths = manifest["required_paths"]
        output.write_text("\n".join(paths) + "\n", encoding="utf-8")
        return ["--files-from", str(output)]
    if lane == "macos-native":
        paths = subprocess.check_output(
            ["python3", "scripts/ci/list_os_marked_tests.py", "macos"], text=True, encoding="utf-8", timeout=120
        ).splitlines()
        if not paths:
            raise ValueError("empty macOS native selection")
        output.write_text("\n".join(paths) + "\n", encoding="utf-8")
        return ["--files-from", str(output), "--", "-m", "platforms and not integration"]
    output.write_text("canonical default discovery; slice " + lane.removeprefix("linux-") + "/2\n", encoding="utf-8")
    return ["--slice", lane.removeprefix("linux-") + "/2"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", choices=["linux-1", "linux-2", "shared", "macos-native"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = Path("release/scoped-acceptance.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    receipt = {"status": "failed", "lane": args.lane, "expected_sha": args.expected_sha,
               "platform": platform.platform(), "run_id": os.environ.get("GITHUB_RUN_ID"),
               "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    result = 1
    try:
        receipt.update(prepare(manifest, args.expected_sha))
        if args.lane.startswith("linux") and platform.system() != "Linux":
            raise ValueError("Linux lane requires native Linux")
        if args.lane == "macos-native" and platform.system() != "Darwin":
            raise ValueError("macOS native lane requires native macOS")
        command = ["bash", "scripts/run_tests.sh", *selection(manifest, args.lane, args.output / "selection.txt")]
        receipt["command"] = command
        log = args.output / "tests.log"
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False, timeout=6600)
        text = log.read_text(encoding="utf-8-sig", errors="replace")
        print(text, flush=True)
        receipt["returncode"] = process.returncode
        receipt["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
        if process.returncode:
            raise ValueError(f"canonical runner exited {process.returncode}")
        receipt["summary"] = parse_summary(text)
        subprocess.run(["git", "diff", "--exit-code", "HEAD", "--"], check=True, timeout=30)
        receipt["status"] = "passed"
        result = 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        receipt["error"] = str(exc)
        print(f"Acceptance refused: {exc}", flush=True)
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
