#!/usr/bin/env python3
"""Behaviour tests for Documentation/scripts/check_s7_exit.py, G7's predicates.

Most of S7's evidence does not exist yet, so each predicate is exercised on crafted
evidence in a scratch tree, every crafted receipt to its contract's schema: the receipt
its contract describes reads met, and each part of the contract taken away reads unmet
with its reason. The three installer holes the confirm review of S7's first wave
reproduced (a typed bytes_match over a measured one, any decision file as the ruling, a
measurement receipt of any schema) are tests that must read unmet, and so is the hole the
review of the second wave reproduced (a typed or stale target receipt): targets_input
recomputes each receipt, here with a stand-in for the receipt tool whose answers the tests
set (test_s7_targets.py runs the real one end to end). Then every falsifier
gate-criteria.json declares for a G7 predicate is applied, as check_criterion_clauses.py
applies it, to a tree where that predicate reads met, and must fail its check: the gate
defers them while the criterion is unmet. For targets_input that tree is an overlay of the
whole repository, since the recomputation reads it. The last group holds the live tree.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "Documentation" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_s7_exit as X  # noqa: E402
import metrics  # noqa: E402
from check_criterion_clauses import build_overlay  # noqa: E402

TOOL = "tools/s7/writer.py"
TARGETS = list(X.TARGETS)


def write(root: Path, rel: str, doc: object) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=1), encoding="utf-8")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A tree with two registered replays, one host residue record and the writer tools."""
    for tool in (TOOL, X.DRM_TOOL, X.TARGETS_TOOL, X.TARGET_TOOL):
        write(tmp_path, tool, "# writer\n")
    write(tmp_path, X.REPLAYS, {"replays": [{"name": "boot"}, {"name": "metalharbor"}]})
    write(tmp_path, f"{X.RESIDUES_DIR}/host-timing-v1.json", {"id": "host-timing-v1",
                                                               "class": "host-timing"})
    return tmp_path


def receipt(schema: str, tool: str = TOOL, **body) -> dict:
    return {"schema": schema, "generated_by": tool, **body}


# ------------------------------------------------------------------ M8


M8_LINE = "| M8 targets | {value} | CI | {status} |\n"


@pytest.mark.parametrize(("value", "status", "met"), [
    ("3/3 (100.0%): x86_64-linux, x86_64-windows, arm64-linux", "measured", True),
    ("2/3 (66.7%): x86_64-linux, arm64-linux", "measured", False),
    ("0/3 (0.0%): none", "measured (stage not started)", False),
    ("4/4 (100.0%): x86_64-linux, x86_64-windows, arm64-linux, arm64-android", "measured", False),
    ("3/3 (100.0%): x86_64-linux, x86_64-windows, arm64-linux", "not measured", False),
])
def test_m8_needs_every_design_target_green_and_measured(tmp_path, value, status, met) -> None:
    path = write(tmp_path, "METRICS.md", "| Metric | Value | Denominator source | Status |\n"
                 + M8_LINE.format(value=value, status=status))
    result = X.m8(path)
    assert result.met is met, result.detail


def test_metrics_counts_m8_over_the_design_targets() -> None:
    assert tuple(metrics.TARGETS) == X.TARGETS


def test_m8_refuses_a_denominator_metrics_does_not_count(tmp_path, monkeypatch) -> None:
    path = write(tmp_path, "METRICS.md", M8_LINE.format(
        value="3/3 (100.0%): x86_64-linux, x86_64-windows, arm64-linux", status="measured"))
    monkeypatch.setattr(metrics, "TARGETS", X.TARGETS + ("arm64-android",))
    result = X.m8(path)
    assert not result.met and "not the design's three" in result.detail


# ------------------------------------------------------------------ arm64


def arm64_runs() -> dict:
    return {"boot": {"result": "residues only", "residues": ["host-timing-v1"],
                     "first_divergent_tick": 4, "transcendental_sites": 12,
                     "magnitude": "host-timing words only"},
            "metalharbor": {"result": "no divergence", "residues": [],
                            "first_divergent_tick": None, "transcendental_sites": 30,
                            "magnitude": "none"}}


def test_arm64_reads_met_when_every_registered_replay_is_labelled(tree) -> None:
    write(tree, X.ARM64_RECEIPT, receipt(X.ARM64_SCHEMA, replays=arm64_runs()))
    result = X.arm64(tree)
    assert result.met, result.detail
    assert result.detail == "boot residues only (host-timing); metalharbor no divergence"


@pytest.mark.parametrize(("change", "reason"), [
    (lambda r: r.pop("metalharbor"), "metalharbor: no arm64 run"),
    (lambda r: r["boot"].update(result="bug"), "is not one of"),
    (lambda r: r["boot"].update(residues=["no-such-v1"]), "no-such-v1.json is absent"),
    (lambda r: r["boot"].update(residues=[]), "with no residue named"),
    (lambda r: r["metalharbor"].update(first_divergent_tick=9), "yet a residue or a divergent"),
    (lambda r: r["boot"].pop("transcendental_sites"), "transcendental sites"),
    (lambda r: r["boot"].update(magnitude=" "), "no divergence magnitude"),
])
def test_arm64_reads_unmet_on_each_missing_label(tree, change, reason) -> None:
    runs = arm64_runs()
    change(runs)
    write(tree, X.ARM64_RECEIPT, receipt(X.ARM64_SCHEMA, replays=runs))
    result = X.arm64(tree)
    assert not result.met and reason in result.detail, result.detail


def test_arm64_needs_a_registered_replay_and_a_receipt_to_its_contract(tree) -> None:
    assert "is absent" in X.arm64(tree).detail
    write(tree, X.ARM64_RECEIPT, receipt(X.ARM64_SCHEMA, tool="tools/none.py",
                                         replays=arm64_runs()))
    assert "names no generating tool" in X.arm64(tree).detail
    write(tree, X.ARM64_RECEIPT, receipt(X.ARM64_SCHEMA, replays=arm64_runs()))
    write(tree, X.REPLAYS, {"replays": []})
    assert "fewer than 1" in X.arm64(tree).detail


# ------------------------------------------------------------------ installer


SHIPPED, UNPACKED = "a1" * 32, "b2" * 32
MEASURED = "2026-10-11T10:00:00+00:00"
PASSED = {"x86_64-linux": "2026-10-11T12:00:00Z", "x86_64-windows": "2026-10-11T15:00:00Z"}
RULING = "Documentation/decisions/2026-10-11-ruling.md"


def pins(root: Path) -> None:
    write(root, X.DRM_PINS, {"executables": {"sonic2app.exe": {"sha256": SHIPPED},
                                             "sonic2app-unpacked.exe": {"sha256": UNPACKED}}})


def measurement(root: Path, **changes) -> None:
    """The DRM-unwrap receipt as tools/s7/drm_unwrap.py writes it, with `changes` applied."""
    doc = receipt(X.DRM_SCHEMA, tool=X.DRM_TOOL,
                  inputs={"shipped": {"path": "private/exe/sonic2app.exe", "sha256": SHIPPED},
                          "unpacked": {"path": "private/exe/sonic2app-unpacked.exe",
                                       "sha256": UNPACKED}},
                  bytes_match=True, **{X.INSTALLER_WRITTEN: MEASURED})
    doc.update(changes)
    write(root, X.DRM_RECEIPT, {k: v for k, v in doc.items() if v is not None})


SIGNED = (f"# The user's ruling on the DRM unwrap\n\nDate: 2026-10-11. Stage S7. "
          f"{X.DRM_DECISION}, recorded by the agent.\n\nIt answers {X.DRM_QUESTION}, item 3, "
          f"on the measurement {X.DRM_RECEIPT}.\n")


def ruling(root: Path, rel: str = RULING, text: str | None = None) -> str:
    write(root, rel, text if text is not None else SIGNED)
    return rel


def installer(**cited) -> dict:
    routes = {route: {target: dict(X.INSTALLER_RUN, **{X.INSTALLER_FIRST: PASSED[target]})
                      for target in X.INSTALLER_TARGETS}
              for route in X.INSTALLER_ROUTES}
    drm = {"receipt": X.DRM_RECEIPT}
    drm.update(cited)
    return receipt(X.INSTALLER_SCHEMA, **{X.INSTALLER_DRM: drm, "routes": routes})


@pytest.fixture
def measured(tree: Path) -> Path:
    pins(tree)
    measurement(tree)
    return tree


def test_installer_holds_the_users_two_routes_in_the_users_order() -> None:
    """The user's G7 bar: x86-64 Linux, then x86-64 Windows, both M8 targets; not arm64
    Linux, and not Android (the closing answers of 2026-10-10, answer 1)."""
    assert X.INSTALLER_TARGETS == ("x86_64-linux", "x86_64-windows")
    assert set(X.INSTALLER_TARGETS) < set(X.TARGETS) and "arm64-linux" not in X.INSTALLER_TARGETS


def test_installer_reads_met_with_every_route_on_both_targets_after_the_measurement(
        measured) -> None:
    write(measured, X.INSTALLER_RECEIPT, installer())
    result = X.installer(measured)
    assert result.met, result.detail
    assert result.detail == ("steam-autodiscovery, folder, zip on x86_64-linux, then "
                             "x86_64-windows; DRM unwrap not needed, measured first")


def test_installer_reads_met_when_the_bytes_differ_and_the_user_ruled(measured) -> None:
    measurement(measured, bytes_match=False)
    write(measured, X.INSTALLER_RECEIPT, installer(ruling=ruling(measured)))
    result = X.installer(measured)
    assert result.met, result.detail
    assert result.detail.endswith(f"DRM unwrap ruled in {RULING}, measured first")


def test_installer_needs_no_arm64_linux_run(measured) -> None:
    """A receipt with no arm64 Linux route reads met: the installer is not required there."""
    doc = installer()
    assert all("arm64-linux" not in per for per in doc["routes"].values())
    write(measured, X.INSTALLER_RECEIPT, doc)
    assert X.installer(measured).met


def test_hole_a_typed_bytes_match_cannot_override_the_measurement(measured) -> None:
    """Confirm review, hole 1: the measurement read the bytes differ, and the installer
    receipt's copy says they match. The measurement's own receipt decides."""
    measurement(measured, bytes_match=False)
    write(measured, X.INSTALLER_RECEIPT, installer(bytes_match=True))
    result = X.installer(measured)
    assert not result.met
    assert result.detail == (f"DRM-unwrap measurement: {X.INSTALLER_RECEIPT}'s bytes_match "
                             f"reads True, and {X.DRM_RECEIPT} measured False")
    write(measured, X.INSTALLER_RECEIPT, installer())
    result = X.installer(measured)
    assert not result.met and "the bytes differ and no ruling of the user's" in result.detail


def test_hole_any_decision_file_is_not_a_ruling(measured) -> None:
    """Confirm review, hole 2: when the bytes differ, a file under Documentation/decisions/
    that does not name this question is not the user's ruling on it."""
    measurement(measured, bytes_match=False)
    other = ruling(measured, "Documentation/decisions/anything.md", "x\n")
    write(measured, X.INSTALLER_RECEIPT, installer(ruling=other))
    result = X.installer(measured)
    assert not result.met
    assert result.detail == (f"DRM-unwrap measurement: {other} is not the user's ruling on "
                             f"this question: its Date paragraph does not say "
                             f"{X.DRM_DECISION!r}")
    signed_only = ruling(measured, "Documentation/decisions/signed.md",
                         "Date: 2026-10-11. A decision of the user's.\n")
    write(measured, X.INSTALLER_RECEIPT, installer(ruling=signed_only))
    detail = X.installer(measured).detail
    assert f"does not say {X.DRM_RECEIPT!r}, {X.DRM_QUESTION!r}" in detail


@pytest.mark.parametrize("text", [
    f"Date: 2026-10-11. The agent's notes on {X.DRM_RECEIPT} and {X.DRM_QUESTION}.\n",
    f"Date: 2026-10-11. This is not {X.DRM_DECISION}.\n\n{X.DRM_RECEIPT}, {X.DRM_QUESTION}\n",
    f"Date: 2026-10-11. Recorded by the agent.\n\n{X.DRM_DECISION}. {X.DRM_RECEIPT}, "
    f"{X.DRM_QUESTION}\n",
    f"{X.DRM_DECISION}. {X.DRM_RECEIPT}, {X.DRM_QUESTION}\n",
    f"Date: 2026-10-11. {X.DRM_DECISION}s agent, {X.DRM_RECEIPT}, {X.DRM_QUESTION}\n",
])
def test_a_ruling_is_signed_by_the_user_in_its_date_paragraph(measured, text: str) -> None:
    """The Date paragraph says the record is a decision of the user's, as a sentence of its
    own: an agent's notes, a negation, the words in the body, no Date paragraph, or the words
    run into another all read unmet, however much of the question the file names."""
    measurement(measured, bytes_match=False)
    write(measured, X.INSTALLER_RECEIPT, installer(ruling=ruling(measured, RULING, text)))
    detail = X.installer(measured).detail
    assert f"its Date paragraph does not say {X.DRM_DECISION!r}" in detail, detail


@pytest.mark.parametrize(("rel", "at"), [
    ("agents-journals/s7/ruling.md", "agents-journals/s7/ruling.md"),
    ("Documentation/decisions/../ruling.md", "Documentation/ruling.md"),
    ("Documentation/decisions/ruling.txt", "Documentation/decisions/ruling.txt"),
    ("private/ruling.md", None),
    (None, None),
])
def test_a_ruling_is_a_decision_record_under_documentation_decisions(measured, rel, at) -> None:
    """A file elsewhere in the tree that says every phrase (a journal), a path that climbs
    out of the decisions directory, one that is not a Markdown record, and one that does not
    exist are not the user's ruling."""
    measurement(measured, bytes_match=False)
    ruling(measured)
    if at is not None:
        write(measured, at, SIGNED)
        assert (measured / at).resolve() == (measured / rel).resolve()
    write(measured, X.INSTALLER_RECEIPT, installer(ruling=rel))
    assert (f"the bytes differ and no ruling of the user's under {X.DECISIONS} is cited "
            f"({rel!r})") in X.installer(measured).detail


def test_the_ruling_reads_the_date_paragraph_as_the_regression_gate_does() -> None:
    import check_replay_regression as R  # noqa: PLC0415
    assert (X.DECISION_DATE.pattern, X.DECISION_DATE.flags) == (R.DATE_PARAGRAPH.pattern,
                                                                R.DATE_PARAGRAPH.flags)


def test_hole_a_measurement_receipt_of_any_schema_is_refused(measured) -> None:
    """Confirm review, hole 3: the measurement's receipt is DRM_SCHEMA, written by DRM_TOOL,
    and measured the executables tools/pins.json pins."""
    write(measured, X.INSTALLER_RECEIPT, installer())
    write(measured, X.DRM_RECEIPT, {X.INSTALLER_WRITTEN: MEASURED, "bytes_match": True})
    assert X.installer(measured).detail == (f"DRM-unwrap measurement: {X.DRM_RECEIPT} has "
                                            f"schema None, not {X.DRM_SCHEMA!r}")
    measurement(measured, generated_by=TOOL)
    assert f"{X.DRM_RECEIPT} is written by {TOOL}, not {X.DRM_TOOL}" in X.installer(measured).detail
    measurement(measured, inputs={"shipped": {"sha256": UNPACKED}, "unpacked": {"sha256": UNPACKED}})
    assert (f"{X.DRM_RECEIPT} measured shipped executable {UNPACKED!r}, not the sonic2app.exe"
            in X.installer(measured).detail)
    measurement(measured, inputs={"shipped": {"sha256": SHIPPED}, "unpacked": {"sha256": SHIPPED}})
    assert (f"{X.DRM_RECEIPT} measured unpacked executable {SHIPPED!r}, not the "
            f"sonic2app-unpacked.exe" in X.installer(measured).detail)
    measurement(measured, inputs=None)
    assert "measured shipped executable None" in X.installer(measured).detail
    measurement(measured, bytes_match="yes")
    assert "states no boolean bytes_match" in X.installer(measured).detail
    measurement(measured)
    write(measured, X.DRM_PINS, {"executables": {}})
    assert "pins no sha256 for sonic2app.exe" in X.installer(measured).detail


def test_installer_reads_unmet_without_the_measurement_or_a_route(measured) -> None:
    write(measured, X.INSTALLER_RECEIPT, receipt(X.INSTALLER_SCHEMA, routes={}))
    assert "no DRM-unwrap measurement cited" in X.installer(measured).detail
    write(measured, X.INSTALLER_RECEIPT, installer(receipt="Documentation/receipts/other.json"))
    assert ("cites 'Documentation/receipts/other.json', not the measurement's receipt"
            in X.installer(measured).detail)
    (measured / X.DRM_RECEIPT).unlink()
    write(measured, X.INSTALLER_RECEIPT, installer())
    assert f"{X.DRM_RECEIPT} is absent" in X.installer(measured).detail
    measurement(measured)
    doc = installer()
    doc["routes"]["zip"]["x86_64-linux"]["manual_steps"] = 1
    write(measured, X.INSTALLER_RECEIPT, doc)
    assert "zip on x86_64-linux: manual_steps not as required" in X.installer(measured).detail
    doc = installer()
    doc["routes"]["folder"]["x86_64-windows"]["hash_checked"] = 1
    write(measured, X.INSTALLER_RECEIPT, doc)
    assert "folder on x86_64-windows: hash_checked not as required" in X.installer(measured).detail
    doc = installer()
    del doc["routes"]["steam-autodiscovery"]["x86_64-windows"]
    write(measured, X.INSTALLER_RECEIPT, doc)
    assert "steam-autodiscovery on x86_64-windows: hash_checked, installed, manual_steps, " \
           "launched not as required" in X.installer(measured).detail


def test_installer_reads_unmet_when_the_measurement_is_not_first(measured) -> None:
    """The DRM-unwrap measurement first: a route that passed before the measurement's receipt
    was written, or a measurement with no instant, reads unmet."""
    measurement(measured, **{X.INSTALLER_WRITTEN: "2026-10-11T13:00:00+00:00"})
    write(measured, X.INSTALLER_RECEIPT, installer())
    detail = X.installer(measured).detail
    assert "zip on x86_64-linux passed before the DRM-unwrap measurement" in detail
    assert "x86_64-windows passed before" not in detail
    measurement(measured, **{X.INSTALLER_WRITTEN: None})
    assert X.installer(measured).detail == (f"DRM-unwrap measurement: {X.DRM_RECEIPT}'s "
                                            "written is None, not an instant")


def test_installer_reads_unmet_when_windows_passed_before_linux(measured) -> None:
    """x86-64 Linux, then x86-64 Windows: a route that first passed on Windows reads unmet, and
    a run with no first-passed instant cannot be ordered."""
    doc = installer()
    doc["routes"]["folder"]["x86_64-windows"][X.INSTALLER_FIRST] = "2026-10-11T11:00:00Z"
    write(measured, X.INSTALLER_RECEIPT, doc)
    assert X.installer(measured).detail == "folder passed on x86_64-windows before x86_64-linux"
    doc = installer()
    doc["routes"]["zip"]["x86_64-linux"].pop(X.INSTALLER_FIRST)
    write(measured, X.INSTALLER_RECEIPT, doc)
    assert "zip on x86_64-linux's first_passed is None, not an instant" in X.installer(measured).detail
    doc = installer()
    doc["routes"]["zip"]["x86_64-linux"][X.INSTALLER_FIRST] = "2026-10-11T12:00:00"
    write(measured, X.INSTALLER_RECEIPT, doc)
    assert "with no UTC offset" in X.installer(measured).detail


def test_the_recorded_ruling_and_measurement_hold_on_the_live_tree() -> None:
    """The user's ruling of 2026-10-11 names this question, and the measurement's receipt is
    to its contract against the tree's pins: the installer receipt, when it lands, has a
    measurement and a ruling it can cite."""
    assert X.ruled(ROOT, "Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md")
    bytes_match, written = X.drm_measurement(ROOT)
    assert bytes_match is False and written.tzinfo is not None


# ------------------------------------------------------------------ M8's input


RECORD = """# What green means

| Target | Clause | Evidence field | Green when |
|---|---|---|---|
| `x86_64-linux` | `build` | `clauses.build` | built |
| `x86_64-linux` | `replays` | `clauses.replays` | replayed |
| `x86_64-windows` | `build` | `clauses.build` | built |
| `x86_64-windows` | `native_run` | `clauses.native_run` | run by the user |
| `arm64-linux` | `replays` | `clauses.replays` | replayed under qemu |

| Target | Runner | Triple |
|---|---|---|
| `x86_64-linux` | `native` | `x86_64-linux-musl` |
"""
CLAUSES = {"x86_64-linux": ["build", "replays"], "x86_64-windows": ["build", "native_run"],
           "arm64-linux": ["replays"]}
EVIDENCE = "Documentation/receipts/evidence.json"
PRIVATE = "private/generated/s7/evidence.json"


def targets_module():
    spec = importlib.util.spec_from_file_location("s7_targets", ROOT / X.TARGETS_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def target_receipt(root: Path, target: str, verdicts: dict[str, bool], green: bool | None = None,
                   **changes) -> None:
    """A receipt as tools/s7/target_receipt.py writes it, under the tree's record."""
    evidence = [{"path": EVIDENCE, "sha256": sha(root / EVIDENCE)},
                {"path": PRIVATE, "sha256": "c3" * 32}]
    clauses = {name: {"evidence": evidence if ok else [], "facts": {}, "verdict": ok,
                      "why": "crafted"} for name, ok in verdicts.items()}
    doc = receipt(X.TARGET_SCHEMA, tool=X.TARGET_TOOL, target=target,
                  green_means=X.GREEN_MEANS, green_means_sha256=sha(root / X.GREEN_MEANS),
                  clauses=clauses, green=all(verdicts.values()) if green is None else green)
    doc.update(changes)
    write(root, X.TARGET_RECEIPT.format(target=target), doc)


def generated(root: Path) -> dict:
    module = targets_module()
    module.receipt_tool = lambda: X.target_tool(root)
    doc = module.generate(root)
    write(root, X.TARGETS_FILE, doc)
    return doc


class Reproducing:
    """A stand-in for TARGET_TOOL whose recomputation is the tracked receipt as it stands,
    with `changes` applied to the named targets' recomputations."""

    def __init__(self, **changes) -> None:
        self.changes = changes

    @staticmethod
    def moved(held: dict, doc: dict) -> list[str]:
        return sorted(k for k in set(doc) | set(held) if k not in X.RECOMPUTE_IGNORED
                      and doc.get(k) != held.get(k))

    def build_receipt(self, root: Path, target: str) -> dict:
        doc = json.loads((root / X.TARGET_RECEIPT.format(target=target)).read_text())
        change = self.changes.get(target.replace("-", "_"))
        if change is not None:
            change(doc)
        return doc


@pytest.fixture
def recorded(tree: Path, monkeypatch) -> Path:
    """The record, a tracked evidence file and the three receipts: x86_64-linux green. The
    receipt tool's recomputation is a stand-in that reproduces each receipt; the tests of
    tools/s7/target_receipt.py hold the real one, and the end-to-end test there runs it."""
    monkeypatch.setattr(X, "target_tool", lambda _root: Reproducing())
    write(tree, X.GREEN_MEANS, RECORD)
    write(tree, EVIDENCE, {"measured": True})
    target_receipt(tree, "x86_64-linux", {"build": True, "replays": True})
    target_receipt(tree, "x86_64-windows", {"build": True, "native_run": False})
    target_receipt(tree, "arm64-linux", {"replays": False})
    generated(tree)
    return tree


def test_targets_input_reads_met_when_generated_from_the_receipts(recorded) -> None:
    result = X.targets_input(recorded)
    assert result.met, result.detail
    assert result.detail == ("1 of 3 green by their receipts' clause verdicts, written by "
                             f"{X.TARGETS_TOOL}")
    assert json.loads((recorded / X.TARGETS_FILE).read_text())["green"] == ["x86_64-linux"]


def test_targets_input_reads_the_record_s_table(tmp_path: Path) -> None:
    """The clause table alone: the runner table's rows are not clauses."""
    write(tmp_path, X.GREEN_MEANS, RECORD)
    assert X.green_clauses(tmp_path) == CLAUSES


def test_targets_input_recomputes_green_and_does_not_trust_the_flag(recorded) -> None:
    """A receipt whose green flag says true over a false clause verdict reads unmet; so does
    one that says false over true verdicts. The green list follows the verdicts."""
    target_receipt(recorded, "x86_64-windows", {"build": True, "native_run": False}, green=True)
    rel = X.TARGET_RECEIPT.format(target="x86_64-windows")
    result = X.targets_input(recorded)
    assert not result.met
    assert f"{rel} reads green True, and its clause verdicts False" in result.detail
    target_receipt(recorded, "x86_64-linux", {"build": True, "replays": True}, green=False)
    assert "reads green False, and its clause verdicts True" in X.targets_input(recorded).detail


def test_targets_input_holds_every_clause_the_record_lists(recorded) -> None:
    """A receipt that leaves out a clause the record lists for its target, or holds one with
    no boolean verdict, or a true one on no evidence, reads unmet."""
    rel = X.TARGET_RECEIPT.format(target="x86_64-linux")
    target_receipt(recorded, "x86_64-linux", {"build": True})
    assert f"{rel} holds no verdict for its replays clause" in X.targets_input(recorded).detail
    doc = json.loads((recorded / rel).read_text())
    doc["clauses"] = {"build": {"evidence": [], "verdict": True},
                      "replays": {"evidence": [], "verdict": True}}
    write(recorded, rel, doc)
    assert f"{rel}: its build clause reads True on no evidence" in X.targets_input(recorded).detail
    doc["clauses"]["build"] = {"evidence": [{"path": EVIDENCE}], "verdict": True}
    write(recorded, rel, doc)
    assert "cites evidence with no path and sha256" in X.targets_input(recorded).detail


def test_targets_input_refuses_a_receipt_under_another_record_or_stale_evidence(recorded) -> None:
    """The record changed after the receipts were written: every receipt reads stale. A
    tracked evidence file changed: the receipt that read it reads stale. Evidence under
    private/ is the writer's to hash, and is not read again."""
    rel = X.TARGET_RECEIPT.format(target="x86_64-linux")
    write(recorded, EVIDENCE, {"measured": False})
    result = X.targets_input(recorded)
    assert not result.met and f"{rel}: {EVIDENCE} is not the file its build clause read" \
        in result.detail
    write(recorded, EVIDENCE, {"measured": True})
    write(recorded, PRIVATE, "anything\n")
    assert X.targets_input(recorded).met
    write(recorded, X.GREEN_MEANS, RECORD + "\nA later line.\n")
    detail = X.targets_input(recorded).detail
    assert f"{X.TARGETS_FILE} was generated under another version of {X.GREEN_MEANS}" in detail
    generated_doc = json.loads((recorded / X.TARGETS_FILE).read_text())
    generated_doc["green_means_sha256"] = sha(recorded / X.GREEN_MEANS)
    write(recorded, X.TARGETS_FILE, generated_doc)
    detail = X.targets_input(recorded).detail
    for target in TARGETS:
        assert (f"{X.TARGET_RECEIPT.format(target=target)} was written under another version "
                f"of {X.GREEN_MEANS}") in detail


def test_targets_input_needs_a_receipt_for_every_target(recorded) -> None:
    """A structural zero: no receipt at all reads unmet, naming each; so does one target
    without its receipt."""
    for target in TARGETS:
        (recorded / X.TARGET_RECEIPT.format(target=target)).unlink()
    generated(recorded)
    result = X.targets_input(recorded)
    assert not result.met
    for target in TARGETS:
        assert f"{X.TARGET_RECEIPT.format(target=target)} is absent" in result.detail
    target_receipt(recorded, "x86_64-linux", {"build": False, "replays": False})
    target_receipt(recorded, "arm64-linux", {"replays": False})
    generated(recorded)
    result = X.targets_input(recorded)
    assert result.detail == (f"{X.TARGET_RECEIPT.format(target='x86_64-windows')} is absent "
                             "(target x86_64-windows's receipt)")


def test_targets_input_refuses_a_typed_list_or_one_the_receipts_do_not_hold(recorded) -> None:
    doc = json.loads((recorded / X.TARGETS_FILE).read_text())
    write(recorded, X.TARGETS_FILE, dict(doc, generated_by=TOOL))
    assert f"is not written by {X.TARGETS_TOOL}" in X.targets_input(recorded).detail
    write(recorded, X.TARGETS_FILE, dict(doc, **{X.TARGET_MEANS: "private/x.md"}))
    assert "not the tracked record" in X.targets_input(recorded).detail
    write(recorded, X.TARGETS_FILE, dict(doc, green=["x86_64-linux", "arm64-linux"]))
    assert (f"{X.TARGETS_FILE} lists ['arm64-linux', 'x86_64-linux'] green; the receipts read "
            "['x86_64-linux']") in X.targets_input(recorded).detail
    write(recorded, X.TARGETS_FILE, doc)
    target_receipt(recorded, "arm64-linux", {"replays": False}, written="later")
    assert (f"{X.TARGETS_FILE} was not generated from "
            f"{X.TARGET_RECEIPT.format(target='arm64-linux')} as it stands"
            in X.targets_input(recorded).detail)
    target_receipt(recorded, "arm64-linux", {"replays": False}, generated_by=TOOL)
    assert f"is written by {TOOL}, not {X.TARGET_TOOL}" in X.targets_input(recorded).detail
    target_receipt(recorded, "arm64-linux", {"replays": False}, green_means="other.md")
    assert f"does not cite {X.GREEN_MEANS}" in X.targets_input(recorded).detail


def test_targets_input_recomputes_each_receipt_and_reads_a_typed_one_unmet(recorded,
                                                                         monkeypatch) -> None:
    """The review of S7's second wave: a receipt whose verdicts were typed, or went stale,
    passes every structural check. G7 recomputes it with TARGET_TOOL and reads unmet when the
    recomputation differs in anything but when and at which commit it was written."""
    rel = X.TARGET_RECEIPT.format(target="x86_64-linux")

    def flipped(doc: dict) -> None:
        doc["clauses"]["replays"].update(verdict=False, why="its runs are not the tree's")
        doc["green"] = False

    monkeypatch.setattr(X, "target_tool", lambda _root: Reproducing(x86_64_linux=flipped))
    result = X.targets_input(recorded)
    assert not result.met
    assert (f"{rel} is not what {X.TARGET_TOOL} derives from the evidence now: it differs in "
            "clauses, green (replays recomputed False: its runs are not the tree's)"
            in result.detail)
    monkeypatch.setattr(X, "target_tool", lambda _root: Reproducing(
        x86_64_linux=lambda d: d.update(written="2030-01-01T00:00:00+00:00", commit="0" * 40)))
    assert X.targets_input(recorded).met
    monkeypatch.setattr(X, "target_tool", lambda _root: Reproducing(
        arm64_linux=lambda d: d["clauses"]["replays"].update(facts={"moved": 1})))
    assert (f"{X.TARGET_RECEIPT.format(target='arm64-linux')} is not what {X.TARGET_TOOL} "
            "derives from the evidence now: it differs in clauses") in \
        X.targets_input(recorded).detail
    assert X.RECOMPUTE_IGNORED == ("written", "commit") and X.TARGET_RECOMPUTE == "build_receipt"


def test_targets_input_reads_unmet_when_the_receipt_tool_cannot_run(recorded,
                                                                    monkeypatch) -> None:
    def broken(_root: Path):
        raise FileNotFoundError("tools/s7/target_receipt.py")

    monkeypatch.setattr(X, "target_tool", broken)
    detail = X.targets_input(recorded).detail
    assert f"{X.TARGET_TOOL} cannot recompute" in detail
    assert "FileNotFoundError" in detail
    monkeypatch.setattr(X, "target_tool", lambda _root: object())
    assert "cannot recompute" in X.targets_input(recorded).detail
    monkeypatch.undo()
    assert "cannot recompute" in X.targets_input(recorded).detail


def test_targets_input_refuses_a_receipt_at_another_target_s_path(recorded) -> None:
    """x86_64-linux's green receipt copied to arm64-linux's path is not arm64-linux's."""
    linux = json.loads((recorded / X.TARGET_RECEIPT.format(target="x86_64-linux")).read_text())
    rel = X.TARGET_RECEIPT.format(target="arm64-linux")
    write(recorded, rel, linux)
    assert f"{rel} is not arm64-linux's green or not" in X.targets_input(recorded).detail


def test_targets_input_refuses_a_record_listing_a_target_that_is_not_m8_s(recorded) -> None:
    write(recorded, X.GREEN_MEANS, RECORD.replace(
        "| `arm64-linux` | `replays` |",
        "| `arm64-android` | `replays` | `clauses.replays` | x |\n| `arm64-linux` | `replays` |"))
    generated_doc = json.loads((recorded / X.TARGETS_FILE).read_text())
    generated_doc["green_means_sha256"] = sha(recorded / X.GREEN_MEANS)
    write(recorded, X.TARGETS_FILE, generated_doc)
    assert ("lists clauses for ['arm64-android', 'arm64-linux', 'x86_64-linux', "
            "'x86_64-windows'], not for each of M8's") in X.targets_input(recorded).detail


def test_targets_input_needs_the_record_to_list_every_target(recorded) -> None:
    write(recorded, X.GREEN_MEANS, RECORD.replace("| `arm64-linux` | `replays` |",
                                                  "| `arm64-android` | `replays` |"))
    generated_doc = json.loads((recorded / X.TARGETS_FILE).read_text())
    generated_doc["green_means_sha256"] = sha(recorded / X.GREEN_MEANS)
    write(recorded, X.TARGETS_FILE, generated_doc)
    assert ("lists clauses for ['arm64-android', 'x86_64-linux', 'x86_64-windows'], not for each "
            "of M8's") in X.targets_input(recorded).detail


# ------------------------------------------------------------------ the declared falsifiers


def g7() -> list[dict]:
    return json.loads((ROOT / "Documentation" / "gate-criteria.json").read_text())["G7"]


PREDICATE_OF = {"m8": "M8", "arm64": "S7 arm divergence", "installer": "S7 installer",
                "targets_input": "S7 targets input"}


def criterion(name: str) -> dict:
    found = [c for c in g7() if f"x.{name}(" in c["check"]]
    assert len(found) == 1, name
    return found[0]


def run_check(name: str, root: Path) -> tuple[int, str]:
    """The criterion's check, run from `root` exactly as gate_report.py runs it."""
    command = criterion(name)["check"].replace("{python}", shlex.quote(sys.executable))
    done = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
    lines = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    return done.returncode, lines[-1] if lines else ""


def copy(root: Path, rel: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / rel, root / rel)


def met_root(tmp_path: Path, name: str) -> tuple[Path, dict[str, str | None]]:
    """(A root on which `name`'s criterion reads met, the overlay mutations that make it).

    m8, arm64 and installer: a small tree of copies, built from the real files their
    falsifiers anchor on and crafted receipts for the evidence not yet written.

    targets_input recomputes every target receipt with the receipt tool, which reads the
    whole tree, so its root is an overlay of the tree (check_criterion_clauses.build_overlay,
    as the gate builds one), carrying the receipt tool's own receipts for the two targets that
    have none yet and the generator's targets.json. It reads met only while the tracked
    x86_64-linux receipt is what the tool writes now: when a change elsewhere has made its
    evidence stale, this fails as the replay regression gate does."""
    root = tmp_path / "root"
    if name == "targets_input":
        # Every target's receipt is written afresh by the receipt tool, so the met state does not
        # hang on the tracked ones being current: their staleness is
        # test_every_tracked_target_receipt_is_current_or_read_stale's (fast mode refreshes them
        # at G7's close, Documentation/decisions/2026-10-11-S7-fast-mode.md).
        others = X.TARGETS
        mutations: dict[str, str | None] = {X.TARGET_RECEIPT.format(target=t): "{}"
                                            for t in others}
        mutations[X.TARGETS_FILE] = "{}"
        build_overlay(ROOT, root, mutations)
        tool = X.target_tool(root)
        for target in others:
            rel = X.TARGET_RECEIPT.format(target=target)
            mutations[rel] = json.dumps(tool.build_receipt(root, target), indent=2) + "\n"
            (root / rel).write_text(mutations[rel])
        module = targets_module()
        module.receipt_tool = lambda: tool
        mutations[X.TARGETS_FILE] = json.dumps(module.generate(root), indent=2) + "\n"
        (root / X.TARGETS_FILE).write_text(mutations[X.TARGETS_FILE])
        status, last = run_check(name, root)
        assert status == 0, (name, last)
        return root, mutations
    scripts = root / "Documentation" / "scripts"
    scripts.mkdir(parents=True)
    for script in SCRIPTS.glob("*.py"):
        shutil.copyfile(script, scripts / script.name)
    write(root, TOOL, "# writer\n")
    if name == "m8":
        write(root, "METRICS.md", M8_LINE.format(
            value="3/3 (100.0%): x86_64-linux, x86_64-windows, arm64-linux", status="measured"))
    elif name == "arm64":
        copy(root, X.REPLAYS)
        shutil.copytree(ROOT / X.RESIDUES_DIR, root / X.RESIDUES_DIR)
        if X.arm64(ROOT).met:
            # the receipt has landed and reads met: its falsifiers anchor on it as it stands
            copy(root, X.ARM64_RECEIPT)
            copy(root, json.loads((ROOT / X.ARM64_RECEIPT).read_text())["generated_by"])
        else:
            runs = {n: {"result": "no divergence", "residues": [], "first_divergent_tick": None,
                        "transcendental_sites": 1, "magnitude": "none"}
                    for n in X.registered(ROOT)}
            write(root, X.ARM64_RECEIPT, receipt(X.ARM64_SCHEMA, replays=runs))
    else:
        for rel in (X.DRM_RECEIPT, X.DRM_PINS, X.DRM_TOOL, LIVE_RULING):
            copy(root, rel)
        _, written = X.drm_measurement(ROOT)
        doc = installer(ruling=LIVE_RULING)
        for route in doc["routes"].values():
            for hours, target in enumerate(X.INSTALLER_TARGETS, start=1):
                route[target][X.INSTALLER_FIRST] = (written + timedelta(hours=hours)).isoformat()
        write(root, X.INSTALLER_RECEIPT, doc)
    status, last = run_check(name, root)
    assert status == 0, (name, last)
    return root, {}


LIVE_RULING = "Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md"


@pytest.mark.parametrize("name", list(PREDICATE_OF))
def test_every_declared_falsifier_fails_its_check_on_met_evidence(tmp_path: Path,
                                                                  name: str) -> None:
    """check_criterion_clauses.py defers a criterion's falsifiers while its control fails,
    which is today for three of these four. Here each falsifier gate-criteria.json declares is
    applied as that gate applies it, to a root where the criterion reads met: every one must
    fail the check with its own verdict line, and the root unmutated must pass it. An overlay
    root gets a fresh overlay per falsifier, so no mutation is written through a link."""
    root, base = met_root(tmp_path, name)
    applied, skipped = 0, []
    label = PREDICATE_OF[name].split()
    for clause in criterion(name)["clauses"]:
        for falsifier in clause["falsifiers"]:
            rel = falsifier["file"]
            if base and rel in base and base[rel] is not None:
                text = base[rel]
            else:
                path = root / rel
                text = path.read_text() if path.is_file() else None
            if falsifier.get("absent") is True:
                content = None
            else:
                assert text is not None, falsifier
                live = (ROOT / rel).read_text() if (ROOT / rel).is_file() else None
                if text == live:
                    assert text.count(falsifier["replace"]) == falsifier.get("occurrences", 1), \
                        falsifier
                elif name == "targets_input" and text.count(falsifier["replace"]) == 0 \
                        and stale_live(rel):
                    # The live receipt is stale (fast mode refreshes it at G7's close), so the
                    # met root's fresh one reads not green and this falsifier has nothing to
                    # flip; check_criterion_clauses applies it on the refreshed evidence.
                    skipped.append(rel)
                    continue
                else:   # a crafted stand-in for evidence not met yet: it must hold the anchor
                    assert text.count(falsifier["replace"]) >= 1, falsifier
                content = text.replace(falsifier["replace"], falsifier["with"])
            applied += 1
            if base:
                where = tmp_path / f"mutant{applied:03d}"
                build_overlay(ROOT, where, {**base, rel: content})
            else:
                where, saved = root, (root / rel).read_bytes() if (root / rel).is_file() else None
                if content is None:
                    if saved is not None:
                        (root / rel).unlink()
                else:
                    (root / rel).write_text(content)
            status, last = run_check(name, where)
            if not base and saved is not None:
                (root / rel).write_bytes(saved)
            assert (status, last.split()[:len(label)]) == (1, label), \
                (clause["promise"], falsifier, last)
    assert applied >= len(criterion(name)["clauses"])
    assert set(skipped) <= {X.TARGET_RECEIPT.format(target=t) for t in X.TARGETS}, skipped
    assert run_check(name, root)[0] == 0


def stale_live(rel: str) -> bool:
    """Whether the tracked target receipt `rel` is not what the receipt tool writes now."""
    target = json.loads((ROOT / rel).read_text()).get("target")
    fresh = json.dumps(X.target_tool(ROOT).build_receipt(ROOT, target), indent=2) + "\n"
    return (ROOT / rel).read_text() != fresh


# ------------------------------------------------------------------ the live criteria


def test_every_g7_predicate_is_called_by_a_criterion_of_its_own() -> None:
    """Each predicate has a criterion of its own in G7, the floor criterion's count aside."""
    checks = [c["check"] for c in g7()]
    for name in ("m8", "arm64", "installer", "targets_input"):
        assert sum(f"x.{name}(" in check for check in checks) == 1, name


def test_g7_holds_the_users_bar_and_none_of_the_carried_items() -> None:
    """G7's criteria are the user's bar of 2026-10-10 and nothing beyond it: no criterion
    carried from G5 (the stock-SDL gate, the depth clamp, the Metal check, R6), which the user
    placed with the Android step, the macOS work and S6."""
    texts = [c["criterion"] for c in g7()]
    assert len(texts) == 7
    assert not [t for t in texts if t.endswith("(carried from G5)")]
    assert sum(t.endswith("(design 5, S7 exit)") for t in texts) == 2
    assert sum(t.endswith("(design 5, S7 work)") for t in texts) == 2
    assert not [n for n in ("stock_sdl", "depth_clamp", "metal", "r6") if hasattr(X, n)]


@pytest.mark.parametrize(("name", "rel"), [
    ("arm64", X.ARM64_RECEIPT), ("installer", X.INSTALLER_RECEIPT),
    ("targets-input", X.TARGET_RECEIPT.format(target="arm64-linux"))])
def test_a_predicate_whose_receipt_is_absent_reads_unmet_naming_it(name, rel) -> None:
    """On the live tree: while an item's receipt is not written, its criterion cannot read
    met, and says which file is missing. Once the receipt lands the crafted cases above hold
    the predicate, and here it reads the receipt rather than reporting it absent (no skip:
    a skip is not a pass)."""
    result = X.PREDICATES[name](ROOT)
    if (ROOT / rel).is_file():
        assert f"{rel} is absent" not in result.detail, result.detail
        return
    assert not result.met, result.detail
    if name != "targets-input" or (ROOT / X.TARGETS_FILE).is_file() and \
            json.loads((ROOT / X.TARGETS_FILE).read_text()).get("generated_by") == X.TARGETS_TOOL:
        assert rel in result.detail, result.detail


def test_the_live_record_lists_every_target() -> None:
    clauses = X.green_clauses(ROOT)
    assert sorted(clauses) == sorted(X.TARGETS)
    assert "native_run" in clauses["x86_64-windows"] and "host" not in clauses["arm64-linux"]


# The build censuses the non-reference target receipts read (Documentation/s7-green-means.md,
# `build`): evidence the targets_input recomputation reads, so each is pinned there too.
CENSUSES = tuple(f"Documentation/receipts/s7-{t}-build-v1.json" for t in X.TARGETS
                 if t != "x86_64-linux")


def landed() -> list[str]:
    """The S7 receipts the G7 predicates read that exist on the live tree."""
    return [rel for rel in (X.DRM_RECEIPT, X.ARM64_RECEIPT, X.INSTALLER_RECEIPT,
                            *(X.TARGET_RECEIPT.format(target=t) for t in X.TARGETS), *CENSUSES)
            if (ROOT / rel).is_file()]


def test_a_receipt_that_exists_is_falsified_and_pinned_by_its_criterion() -> None:
    """When one of S7's receipts lands, its criterion gains a falsifier on the receipt itself
    and a pinned shape for it (the G7 journal: "its criterion gains a falsifier on the receipt
    itself")."""
    owner = {X.DRM_RECEIPT: "installer", X.ARM64_RECEIPT: "arm64",
             X.INSTALLER_RECEIPT: "installer",
             **{X.TARGET_RECEIPT.format(target=t): "targets_input" for t in X.TARGETS},
             **{rel: "targets_input" for rel in CENSUSES}}
    owed = []
    for rel in landed():
        entry = criterion(owner[rel])
        if rel not in {s["file"] for s in entry.get("evidence_shape", [])}:
            owed.append(f"{rel}: no shape pinned")
        if not any(f["file"] == rel for c in entry["clauses"] for f in c["falsifiers"]):
            owed.append(f"{rel}: no falsifier")
    assert owed == []
