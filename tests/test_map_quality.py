import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
MAP = """<repository_map>
src/api.py:
│1: class API:
│2:     def run(self, value):
│8: LIMIT = 10
src/helper.py:
│4: def parse(value):
</repository_map>
"""
PATCH = """diff --git a/src/api.py b/src/api.py
--- a/src/api.py
+++ b/src/api.py
@@ -2,2 +2,2 @@ class API:
-        return value
+        return parse(value)
diff --git a/src/missing.py b/src/missing.py
--- a/src/missing.py
+++ b/src/missing.py
@@ -1 +1 @@
-OLD = 0
+OLD = 1
"""


@pytest.fixture
def metrics(monkeypatch):
    monkeypatch.syspath_prepend(str(PROJECT / "scripts"))
    import map_quality_metrics

    return map_quality_metrics


def symbol(path="src/api.py", line=2, name="run", identifier="src/api.py::API.run"):
    return {"id": identifier, "path": path, "line": line, "name": name, "kind": "method", "scope": "API"}


def test_official_paths_and_not_agent_patch_define_file_recall(metrics):
    score = metrics.evaluate(MAP, metrics.gold_files(PATCH), {}, [], True)
    assert score["target_file_recall"]["value"] == 0.5
    assert score["target_file_recall"]["missed_files"] == ["src/missing.py"]
    assert score["target_symbol_recall"]["value"] is None
    assert score["symbol_precision"]["value"] is None
    assert score["map_utilization"]["value"] is None


def test_task_related_files_are_reviewed_separately_from_official_targets(metrics):
    annotation = {
        "additional_target_files": {
            "reviewed": True,
            "items": [
                {"path": "src/helper.py", "reason": "target delegates parsing", "evidence": "task and call site"}
            ],
        }
    }
    score = metrics.evaluate(MAP, metrics.gold_files(PATCH), annotation, [], True)
    assert score["target_file_recall"]["value"] == 0.5
    assert score["augmented_target_file_recall"]["value"] == 2 / 3
    annotation["additional_target_files"]["items"][0].pop("evidence")
    with pytest.raises(ValueError, match="evidence"):
        metrics.evaluate(MAP, metrics.gold_files(PATCH), annotation, [], True)


def test_rename_new_file_and_hunk_text_are_not_confused_with_headers(metrics):
    patch = """diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
diff --git a/added.py b/added.py
new file mode 100644
--- /dev/null
+++ b/added.py
@@ -0,0 +1 @@
+VALUE = 1
diff --git a/real.py b/real.py
--- a/real.py
+++ b/real.py
@@ -1 +1 @@
--- a/fake.py
+++ b/fake.py
"""
    files = metrics.gold_files(patch)
    assert [(f["map_path"], f["change"]) for f in files] == [
        ("old.py", "renamed"),
        ("added.py", "added"),
        ("real.py", "modified"),
    ]
    score = metrics.evaluate("old.py:\nreal.py:\n", files, {}, [], True)["target_file_recall"]
    assert score["value"] == 2 / 3
    assert score["new_files_not_present_at_base"] == ["added.py"]
    assert score["existing_target_file_recall"]["value"] == 1


def test_symbol_recall_checks_path_location_and_declaration(metrics):
    labels = {
        "reviewed": True,
        "items": [
            symbol(),
            symbol(line=20, identifier="src/api.py::Other.run"),
            symbol(path="src/other.py", identifier="src/other.py::API.run"),
            symbol(line=8, name="LIMIT", identifier="src/api.py::LIMIT"),
        ],
    }
    score = metrics.reviewed_recall(metrics.parse_map(MAP), labels)
    assert score["value"] == 0.5
    assert score["hit_ids"] == ["src/api.py::API.run", "src/api.py::LIMIT"]
    wrong_overload = symbol() | {"declaration": "def run(self, other):"}
    assert not metrics.visible(metrics.parse_map(MAP), wrong_overload)


def test_dependency_file_presence_does_not_imply_symbol_coverage(metrics):
    items = [
        {"id": "helper-file", "kind": "file", "path": "src/helper.py"},
        symbol(path="src/helper.py", line=4, name="parse", identifier="helper-parse"),
        symbol(path="src/helper.py", line=12, name="missing", identifier="helper-missing"),
    ]
    score = metrics.reviewed_recall(metrics.parse_map(MAP), {"reviewed": True, "items": items})
    assert score["value"] == 2 / 3
    assert metrics.reviewed_recall(metrics.parse_map(MAP), {"reviewed": True, "items": []})["value"] is None
    external = {"id": "stdlib:parse", "eligible": False, "exclusion_reason": "outside repository"}
    score = metrics.reviewed_recall(metrics.parse_map(MAP), {"reviewed": True, "items": [external]})
    assert score["value"] is None and score["excluded_ids"] == ["stdlib:parse"]
    with pytest.raises(ValueError, match="cannot be excluded"):
        metrics.evaluate(
            MAP, metrics.gold_files(PATCH), {"target_symbols": {"reviewed": True, "items": [external]}}, [], True
        )


def test_precision_does_not_promote_partially_labeled_subset(metrics):
    items = [
        symbol() | {"relevance": "relevant"},
        symbol(line=1, name="API", identifier="API") | {"relevance": "irrelevant"},
        symbol(line=8, name="LIMIT", identifier="LIMIT") | {"relevance": "unknown"},
    ]
    labels = {"inventory_complete": True, "relevance_reviewed": True, "items": items}
    score = metrics.precision(metrics.parse_map(MAP), labels)
    assert score["value"] is None
    assert score["labeled_subset_precision"]["value"] == 0.5
    assert score["label_coverage"] == 2 / 3
    assert score["bounds"] == [1 / 3, 2 / 3]
    items[-1]["relevance"] = "relevant"
    assert metrics.precision(metrics.parse_map(MAP), labels)["value"] == 2 / 3
    labels["inventory_complete"] = False
    assert metrics.precision(metrics.parse_map(MAP), labels)["value"] is None


def test_invisible_inventory_and_empty_denominators_are_not_success(metrics):
    with pytest.raises(ValueError, match="not visible"):
        metrics.precision(metrics.parse_map(MAP), {"items": [symbol(line=30)]})
    assert metrics.fraction(0, 0)["value"] is None
    assert metrics.reviewed_recall(metrics.parse_map(MAP), {})["status"] == "pending_annotation"


def utilization_example():
    messages = [
        {"role": "user", "content": MAP},
        {
            "role": "assistant",
            "content": "The repository map lists API.run in src/api.py.",
            "extra": {"actions": [{"command": "sed -n '1,20p' src/api.py"}]},
        },
    ]
    annotation = {
        "reviewed": True,
        "status": "confirmed_reference",
        "events": [
            {
                "reference_message_index": 1,
                "quote": messages[1]["content"],
                "explicit_map_reference": True,
                "map_item": symbol(),
                "action_message_index": 1,
                "action_index": 0,
                "action_contains": "src/api.py",
                "operation": "read",
            }
        ],
    }
    return messages, annotation


def test_utilization_requires_reviewed_reference_and_following_action(metrics):
    messages, annotation = utilization_example()
    score = metrics.utilization(metrics.parse_map(MAP), annotation, messages, True)
    assert score["status"] == "confirmed_reference" and score["value"] == 1
    assert score["causal_effect_established"] is False
    annotation["events"][0]["explicit_map_reference"] = False
    with pytest.raises(ValueError, match="path alone"):
        metrics.utilization(metrics.parse_map(MAP), annotation, messages, True)
    annotation["events"][0]["explicit_map_reference"] = True
    messages[1]["content"] = annotation["events"][0]["quote"] = "I will read src/api.py."
    with pytest.raises(ValueError, match="explicitly mention"):
        metrics.utilization(metrics.parse_map(MAP), annotation, messages, True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote", "not in trajectory"),
        ("action_message_index", 0),
        ("action_index", 7),
        ("action_contains", "different.py"),
    ],
)
def test_invalid_utilization_evidence_is_rejected(metrics, field, value):
    messages, annotation = utilization_example()
    annotation["events"][0][field] = value
    with pytest.raises(ValueError):
        metrics.utilization(metrics.parse_map(MAP), annotation, messages, True)


def test_reading_a_matching_file_and_absence_of_reference_are_not_actual_use(metrics):
    score = metrics.utilization(metrics.parse_map(MAP), {"reviewed": True, "status": "consistent"}, [], True)
    assert score["value"] is None
    score = metrics.utilization(metrics.parse_map(MAP), {"reviewed": True, "status": "not_observed"}, [], True)
    assert score["value"] is None and score["confirmed_reference"] is False
    with pytest.raises(ValueError, match="incomplete"):
        metrics.utilization(metrics.parse_map(MAP), {"reviewed": True, "status": "not_observed"}, [], False)


def test_offline_cli_uses_gold_and_exports_pending_labels_without_mutating_run(tmp_path):
    run = tmp_path / "run"
    directory = run / "repomap"
    directory.mkdir(parents=True)
    iid = "fixture__repo-1"
    trajectory = directory / f"{iid}.traj.json"
    payload = {
        "info": {
            "exit_status": "Submitted",
            "submission": "Agent changed another file.",
            "config": {"agent": {"repomap": {"map_tokens": 1024, "refresh": "once"}}},
        },
        "messages": [{"role": "user", "content": MAP}, {"role": "exit", "content": ""}],
    }
    trajectory.write_text(json.dumps(payload))
    original = trajectory.read_bytes()
    item = {"instance_id": iid, "repo": "fixture/repo", "base_commit": "base"}
    (run / "plan.json").write_text(json.dumps([{"variant": "repomap", "item": item, "output": str(directory)}]))
    dataset = run / "evaluation" / "repomap"
    dataset.mkdir(parents=True)
    (dataset / "dataset.json").write_text(json.dumps([item | {"patch": PATCH, "problem_statement": "Fix API"}]))
    output = tmp_path / "quality"
    command = [sys.executable, str(PROJECT / "scripts/evaluate_map_quality.py"), str(run), "--output", str(output)]
    completed = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(completed.stdout)["model_calls"] == 0
    report = json.loads((output / "report.json").read_text())
    assert report["aggregate"]["target_file_recall"]["macro"] == 0.5
    assert report["aggregate"]["target_symbol_recall"]["scored_instances"] == 0
    assert report["instances"][0]["map_sha256"] == hashlib.sha256(MAP.encode()).hexdigest()
    assert trajectory.read_bytes() == original
    assert (output / "annotation-template.json").is_file()
    payload["info"]["config"]["agent"]["repomap"]["refresh"] = "interval"
    trajectory.write_text(json.dumps(payload))
    rejected = subprocess.run([*command[:-1], str(tmp_path / "other")], capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0 and "initial once map required" in rejected.stderr
    retained = tmp_path / "retained"
    subprocess.run(
        [*command[:-1], str(retained), "--snapshot", "retained-final"], check=True, capture_output=True, timeout=30
    )
    report = json.loads((retained / "report.json").read_text())
    assert report["instances"][0]["map_snapshot"] == "retained_final"
    assert report["instances"][0]["target_file_recall"]["value"] == 0.5
    labels = json.loads((retained / "annotation-template.json").read_text())
    labels["instances"][iid]["utilization"] = {"reviewed": True, "status": "not_observed"}
    label_path = tmp_path / "labels.json"
    label_path.write_text(json.dumps(labels))
    rejected = subprocess.run(
        [*command[:-1], str(tmp_path / "bad-labels"), "--snapshot", "retained-final", "--labels", str(label_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rejected.returncode != 0 and "per-request maps" in rejected.stderr


def test_historical_cli_selects_retry_and_budget_and_checks_reconstruction_integrity(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    iid = "fixture__repo-1"
    item = {"instance_id": iid, "repo": "fixture/repo", "base_commit": "base"}
    plan = []
    for budget in (1024, 2048):
        directory = run / str(budget)
        directory.mkdir()
        data = {
            "info": {
                "exit_status": "Submitted" if budget == 2048 else "RuntimeError",
                "config": {"agent": {"repomap": {"map_tokens": budget}}},
            },
            "messages": [{"role": "user", "content": "src/missing.py:\n"}, {"role": "exit"}],
        }
        (directory / f"{iid}.traj.json").write_text(json.dumps(data))
        plan.append({"variant": "repomap", "budget": budget, "item": item, "output": str(directory)})
        if budget == 1024:
            data["info"]["exit_status"] = "Submitted"
            data["messages"][0]["content"] = MAP
            retry = run / "retry.traj.json"
            retry.write_text(json.dumps(data))
    (run / "plan.json").write_text(json.dumps(plan))
    dataset = run / "evaluation"
    dataset.mkdir()
    (dataset / "dataset.json").write_text(json.dumps([item | {"patch": PATCH, "problem_statement": "Fix API"}]))
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({iid: str(retry)}))
    output = tmp_path / "retained"
    command = [
        sys.executable,
        str(PROJECT / "scripts/evaluate_map_quality.py"),
        str(run),
        "--budget",
        "1024",
        "--trajectory-overrides",
        str(overrides),
        "--snapshot",
        "retained-final",
    ]
    subprocess.run([*command, "--output", str(output)], capture_output=True, check=True, timeout=30)
    report = json.loads((output / "report.json").read_text())
    assert len(report["instances"]) == 1
    assert report["instances"][0]["trajectory"] == str(retry)
    assert report["aggregate"]["target_file_recall"]["macro"] == 0.5

    initial_map = tmp_path / "initial.txt"
    initial_map.write_text("src/api.py:\nsrc/missing.py:\n")
    manifest = tmp_path / "reconstruction.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "map_snapshot": "reconstructed_initial",
                "run": str(run),
                "budget": 1024,
                "instances": {
                    iid: {
                        "base_commit": "base",
                        "trajectory_sha256": hashlib.sha256(retry.read_bytes()).hexdigest(),
                        "map_path": initial_map.name,
                        "map_sha256": hashlib.sha256(initial_map.read_bytes()).hexdigest(),
                    }
                },
            }
        )
    )
    command[-1] = "reconstructed-initial"
    command += ["--reconstruction", str(manifest)]
    reconstructed = tmp_path / "initial"
    subprocess.run([*command, "--output", str(reconstructed)], capture_output=True, check=True, timeout=30)
    report = json.loads((reconstructed / "report.json").read_text())
    assert report["aggregate"]["target_file_recall"]["macro"] == 1.0
    assert report["instances"][0]["map_snapshot"] == "reconstructed_initial"
    initial_map.write_text(MAP)
    rejected = subprocess.run(
        [*command, "--output", str(tmp_path / "tampered")], capture_output=True, text=True, timeout=30
    )
    assert rejected.returncode != 0 and "map hash mismatch" in rejected.stderr
