"""The DXVK player's Python instruments, held without a GPU, a window, VLC or game data.

tools/s5/play_report.py is the second layer over the player's verdict, and
tools/s5/frames_to_video.py binds frames to the run that wrote them, and runs
that second layer on the run's report, before it encodes them; both are
measuring instruments (AGENTS.md rule 5). Their end-to-end controls run in
host/player/test_synth.sh, which needs a GPU and a window; these hold the logic
that needs neither, against reports, DXVK logs, frames and MP4 boxes built
here, a stored SPS that x264 wrote for a synthetic fade, and the frame
fingerprints the player wrote for the synthetic stream
(host/player/synth_stream.cpp). Each comparison the two tools make has a case
here that fails when the comparison is taken out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import play_report as PR  # noqa: E402
import frames_to_video as F  # noqa: E402

END_SCENE = "scene order: EndScene with no scene open"
BEGIN_SCENE = "scene order: BeginScene inside a scene"


def allowed_call(seq: int, method: str, rule: str, hr: str = "0x8876086c") -> str:
    """An allowed call as player.cpp count() lists it."""
    return f"seq {seq} Device::{method}: host {hr}, guest 0x00000000 ({rule})"


def report(**over) -> dict:
    """A complete stage-3 report of a passing replay, with the keys the check
    reads, as host/player/player.cpp write_report lays them out."""
    rep = {
        "tool": "host/player sa2_player", "complete": True, "phase": "done", "pass": True, "exit_status": 0,
        "failures": [], "stream": "s.sa2d3d9", "stream_version": 1, "stream_schema": "sa2-d3d9-stream/v1",
        "stream_sha256": "0" * 64, "stage": 3,
        "options": {"pace": False, "speed": 1, "vsync": False, "hidden": True, "max_frames": 0,
                    "allow_truncated": False, "allow_notes": False, "stats_only": False, "report_every": 100},
        "records": {"call": 12, "d3dx": 1, "frame": 2, "note": 0, "end": 1, "unknown": 0, "seq_gaps": 0,
                    "after_end": 0},
        "stream_problems": {"count": 0, "first": []},
        "presents": 2, "presents_without_frame_record": 0, "frame_records": 2, "draws": 4, "stop": "end of stream",
        "errors": 0, "first_errors": [],
        "calls": {"handled": 11, "ignored_by_design": 2, "skipped_by_stage": 0, "not_handled": 0},
        "not_handled_values": {"count": 0, "first": []},
        "host_failures": {"count": 0, "first": [], "failed_like_guest": 0},
        "allowed_host_failures": {"rules": {END_SCENE: 1}, "calls": [allowed_call(5, "EndScene", END_SCENE)]},
        "readback_failures": 0, "present_failures": 0,
        "notes": {"count": 0, "allowed": False, "first": []},
        "direct3d_create9": {"calls": 1, "direct3d9_calls_held": True, "objects": ["0x03200000"]},
        "methods": [
            {"type": "Device", "method": "EndScene", "slot": 42, "handled": 2, "failed": 1,
             "first_fail_hr": "0x8876086c", "first_fail_seq": 5},
            {"type": "Device", "method": "Present", "slot": 17, "handled": 2},
            {"type": "d3d9", "method": "Direct3DCreate9", "entry": 12, "ignored_by_design": 1},
        ],
        "expectations": {"file": None, "selected": 0, "pass": 0, "fail": 0},
        "frames_out": None,
        "device": {"created": True, "failure": None},
        "files": {},
        "host": {"live_desktop_session": True, "live_desktop_session_basis": "inferred, not measured"},
    }
    for key, value in over.items():
        rep[key] = value
    return rep


def check(tmp_path: Path, rep: dict | None, status: int, **kw) -> dict:
    path = tmp_path / "report.json"
    if rep is not None:
        path.write_text(json.dumps(rep))
    return PR.check(argparse.Namespace(report=str(path), exit=status, frames_out=kw.get("frames_out"),
                                       dxvk_log=kw.get("dxvk_log")))


def failing(rep: dict, exit_status: int) -> dict:
    rep["pass"], rep["exit_status"] = False, exit_status
    return rep


# ------------------------------------------------------------ the second layer

def test_a_passing_report_passes(tmp_path):
    v = check(tmp_path, report(), 0)
    assert v["pass_"] is True and v["failures"] == []


def test_counts_that_contradict_the_verdict_fail_it(tmp_path):
    v = check(tmp_path, report(errors=1, first_errors=["seq 9 Device::Clear: x"]), 0)
    assert "the report says pass=True; its counts say exit 4" in v["failures"]
    assert "the process exited 0; the report's counts say 4" in v["failures"]


def test_the_reports_exit_status_is_held_to_the_process(tmp_path):
    assert check(tmp_path, report(exit_status=4), 0)["failures"] == ["the report says exit 4; the process exited 0"]


@pytest.mark.parametrize("change, want", [
    ({"failures": ["i/o: refused: x"], "stream_problems": {"count": 1, "first": ["p"]}, "errors": 1}, 2),
    ({"stream_problems": {"count": 1, "first": ["p"]}, "errors": 1}, 3),
    ({"records": dict(report()["records"], note=1), "notes": {"count": 1, "allowed": False, "first": ["n"]}}, 3),
    ({"records": dict(report()["records"], end=0)}, 3),
    ({"presents_without_frame_record": 1}, 3),
    ({"errors": 1, "first_errors": ["e"]}, 4),
    ({"calls": dict(report()["calls"], not_handled=1)}, 4),
    ({"device": {"created": False, "failure": "SDL_CreateWindow: no"}}, 4),
    ({"device": {"created": False, "failure": None}}, 4),
    ({"stop": "the window was closed"}, 4),
    ({"expectations": {"file": "x.tsv", "selected": 3, "pass": 2, "fail": 1}}, 1),
    ({"expectations": {"file": "x.tsv", "selected": 0, "pass": 0, "fail": 0}}, 1),
])
def test_each_class_is_recomputed_from_counts_in_the_players_order(tmp_path, change, want):
    rep = failing(report(**change), want)
    v = check(tmp_path, rep, want)
    assert v["pass_"] is False
    assert not [f for f in v["failures"] if "its counts say" in f or "the report's counts say" in f]
    for other in {0, 1, 2, 3, 4} - {want}:
        rep["exit_status"] = other
        assert f"the process exited {other}; the report's counts say {want}" in check(tmp_path, rep, other)["failures"]


@pytest.mark.parametrize("key", ["seq_gaps", "after_end", "unknown"])
def test_each_record_count_a_stream_must_not_have_fails_alone(tmp_path, key):
    rep = failing(report(records=dict(report()["records"], **{key: 1})), 3)
    assert check(tmp_path, rep, 3)["failures"] == [f"stream: records.{key} is 1"]


def test_notes_pass_only_when_allowed(tmp_path):
    rep = report(records=dict(report()["records"], note=2), notes={"count": 2, "allowed": True, "first": ["a", "b"]})
    rep["options"] = dict(rep["options"], allow_notes=True)
    v = check(tmp_path, rep, 0)
    assert v["pass_"] is True and v["notes"]["count"] == 2


def test_a_missing_count_fails(tmp_path):
    rep = report()
    del rep["errors"]
    assert "the report says pass=True; its counts say exit 4" in check(tmp_path, rep, 0)["failures"]


def test_a_run_stopped_by_max_frames_must_have_made_that_many(tmp_path):
    rep = failing(report(stop="--max-frames", options=dict(report()["options"], max_frames=3)), 4)
    assert check(tmp_path, rep, 4)["failures"] == ["replay: --max-frames 3 but 2 presents"]
    rep = report(stop="--max-frames", options=dict(report()["options"], max_frames=2))
    assert check(tmp_path, rep, 0)["pass_"] is True


def test_frames_written_are_held_to_the_presents(tmp_path):
    fo = {"path": "f.y4m", "times": "f.y4m.times.tsv", "frames": 1, "times_rows": 1, "y4m_bytes": 1, "bgrx_digest": "0x0"}
    rep = failing(report(frames_out=fo), 4)
    assert check(tmp_path, rep, 4)["failures"] == ["replay: 1 frame(s) written for 2 Present(s)"]


def test_a_device_failure_fails_whether_or_not_the_device_was_made(tmp_path):
    rep = failing(report(device={"created": True, "failure": "GetBackBuffer failed"}, stop="device creation failed"), 4)
    assert check(tmp_path, rep, 4)["failures"] == ["replay: no device: GetBackBuffer failed",
                                                   "replay: the run stopped: device creation failed"]


def stats_read(**over) -> dict:
    """A report of a passing stats read (--stats-only)."""
    return report(**dict({"options": dict(report()["options"], stats_only=True),
                          "device": {"created": False, "failure": None}}, **over))


def test_a_stats_read_needs_no_device_and_fails_on_the_plan(tmp_path):
    assert check(tmp_path, stats_read(), 0)["pass_"] is True
    methods = report()["methods"] + [{"type": "d3dx9_42", "method": "D3DXCreateCubeTexture", "entry": 8,
                                      "not_handled": 1}]
    rep = failing(stats_read(methods=methods, calls=dict(report()["calls"], not_handled=1)), 4)
    v = check(tmp_path, rep, 4)
    named = [f for f in v["failures"] if f.startswith("replay: 1 call(s) not handled")]
    assert len(named) == 1 and "d3dx9_42::D3DXCreateCubeTexture 1" in named[0]


def test_a_stats_read_that_stopped_short_fails(tmp_path):
    rep = failing(stats_read(stop="malformed stream"), 4)
    assert check(tmp_path, rep, 4)["failures"] == ["replay: the read stopped: malformed stream"]


def test_a_stats_read_fails_a_createdevice_it_cannot_honour(tmp_path):
    why = "back buffer format 99 is not one the player can read back"
    rep = failing(stats_read(device={"created": False, "failure": why}, stop="device creation failed"), 4)
    assert check(tmp_path, rep, 4)["failures"] == [f"replay: no device: {why}",
                                                   "replay: the read stopped: device creation failed"]


# ------------------------------------------------------------ a player that died

@pytest.mark.parametrize("over, said", [
    ({"phase": "replay", "presents": 7}, "after 7 to 107 presents (the report is rewritten every 100 Presents)"),
    ({"phase": "replay", "presents": 7, "report_every": 1},
     "after 7 to 8 presents (the report is rewritten after every Present)"),
    ({"phase": "replay", "presents": 0, "report_every": 0},
     "after at least 0 presents (--report-every 0: the report is not rewritten during the replay)"),
    ({"phase": "replay", "presents": 3, "report_every": None},
     "after at least 3 presents (the report does not say how often it was rewritten)"),
    ({"phase": "start", "presents": 0}, "after 0 presents"),
])
def test_a_signal_names_the_phase_and_bounds_the_presents(tmp_path, over, said):
    rep = report(complete=False, phase=over["phase"], presents=over["presents"])
    rep["pass"], rep["exit_status"] = False, None
    if "report_every" in over:
        rep["options"] = dict(rep["options"], report_every=over["report_every"])
        if over["report_every"] is None:
            del rep["options"]["report_every"]
    v = check(tmp_path, rep, 137)
    assert v["failures"] == [f"the player died: signal 9 in phase {over['phase']} {said}",
                             f"the player did not finish its report: phase {over['phase']}, last written at "
                             f"{over['presents']} presents"]


def test_a_signal_in_device_creation_or_after_the_last_report_or_with_no_report(tmp_path):
    rep = report(complete=False, phase="CreateDevice", presents=0)
    assert check(tmp_path, rep, 134)["failures"][0] == "device creation aborted: signal 6 in phase CreateDevice"
    assert check(tmp_path, report(), 139)["failures"] == ["the player died: signal 11 in phase done after 2 presents"]
    (tmp_path / "report.json").unlink()
    assert check(tmp_path, None, 139)["failures"] == [
        "no report: the player wrote none (exit 139)",
        "the player died: signal 11 in phase unknown after an unknown number of presents"]


# ------------------------------------------------------------ the allow-list

def test_the_allow_list_holds_the_rule_names_and_the_sum(tmp_path):
    assert PR.allow_list_problems(report()) == []
    wide = report()
    wide["allowed_host_failures"] = {"rules": {END_SCENE: 1, "scene order: anything goes": 1},
                                     "calls": [allowed_call(5, "EndScene", END_SCENE),
                                               allowed_call(9, "Clear", "scene order: anything goes")]}
    wide["methods"].append({"type": "Device", "method": "Clear", "slot": 43, "handled": 1, "failed": 1})
    problems = PR.allow_list_problems(wide)
    assert any("'scene order: anything goes'" in p and "not the player's rules" in p for p in problems)
    short = report()
    short["methods"][0]["failed"] = 2
    assert PR.allow_list_problems(short) == [
        "allow-list: the methods' failed answers sum to 2; {'host_failures.count': 0, "
        "'host_failures.failed_like_guest': 0, 'allowed': 1, 'present_failures': 0} sum to 1"]
    assert any("sum to 2" in f for f in check(tmp_path, short, 0)["failures"])
    both = report(host_failures={"count": 1, "first": ["h"], "failed_like_guest": 1}, present_failures=1)
    both["methods"][0]["failed"] = 4
    assert PR.allow_list_problems(both) == []


def test_the_allow_list_is_required(tmp_path):
    rep = report()
    del rep["allowed_host_failures"]
    assert check(tmp_path, rep, 0)["failures"] == ["allow-list: the report has no allowed_host_failures.rules"]


def test_a_rule_excuses_only_its_own_methods_failures(tmp_path):
    # The review's forged report: EndScene's failure moved to Clear, and the
    # allowed call relabelled as a Clear under the EndScene rule.
    forged = report()
    forged["methods"][0].pop("failed")
    forged["methods"].append({"type": "Device", "method": "Clear", "slot": 43, "handled": 1, "failed": 1})
    forged["allowed_host_failures"]["calls"] = [allowed_call(400, "Clear", END_SCENE)]
    assert PR.allow_list_problems(forged) == [
        f"allow-list: {END_SCENE!r} allows 1 failure(s); Device::EndScene failed 0 time(s)",
        f"allow-list: {allowed_call(400, 'Clear', END_SCENE)!r} is not a 0x8876086c answer to the one method its "
        "rule covers"]
    assert check(tmp_path, forged, 0)["pass_"] is False
    # Both calls listed on EndScene, one of the two failures BeginScene's.
    moved = report()
    moved["methods"].append({"type": "Device", "method": "BeginScene", "slot": 41, "handled": 1, "failed": 1})
    moved["allowed_host_failures"] = {"rules": {END_SCENE: 2}, "calls": [allowed_call(5, "EndScene", END_SCENE),
                                                                         allowed_call(7, "EndScene", END_SCENE)]}
    assert PR.allow_list_problems(moved) == [
        f"allow-list: {END_SCENE!r} allows 2 failure(s); Device::EndScene failed 1 time(s)"]
    # BeginScene's rule on BeginScene is fine; EndScene's rule on BeginScene is not.
    begin = report()
    begin["methods"][0] = {"type": "Device", "method": "BeginScene", "slot": 41, "handled": 2, "failed": 1}
    begin["allowed_host_failures"] = {"rules": {BEGIN_SCENE: 1}, "calls": [allowed_call(5, "BeginScene", BEGIN_SCENE)]}
    assert PR.allow_list_problems(begin) == []
    begin["allowed_host_failures"] = {"rules": {END_SCENE: 1}, "calls": [allowed_call(5, "BeginScene", END_SCENE)]}
    assert len(PR.allow_list_problems(begin)) == 2


def test_an_allowed_call_is_the_rules_one_host_answer_and_every_one_is_listed():
    other_hr = report()
    other_hr["allowed_host_failures"]["calls"] = [allowed_call(5, "EndScene", END_SCENE, hr="0x80004005")]
    assert PR.allow_list_problems(other_hr) == [
        f"allow-list: {allowed_call(5, 'EndScene', END_SCENE, hr='0x80004005')!r} is not a 0x8876086c answer to the "
        "one method its rule covers"]
    unlisted = report()
    unlisted["allowed_host_failures"]["calls"] = []
    assert PR.allow_list_problems(unlisted) == ["allow-list: 0 allowed call(s) listed for 1 allowed"]
    many = report()                               # the player lists the first 20
    many["methods"][0]["failed"] = 25
    many["allowed_host_failures"] = {"rules": {END_SCENE: 25},
                                     "calls": [allowed_call(5 + i, "EndScene", END_SCENE) for i in range(20)]}
    assert PR.allow_list_problems(many) == []
    many["allowed_host_failures"]["calls"].append(allowed_call(99, "EndScene", END_SCENE))
    assert PR.allow_list_problems(many) == ["allow-list: 21 allowed call(s) listed for 25 allowed"]


# ------------------------------------------------------------ DXVK's options and log

def dxvk_pin(log: Path, config_file: str = "") -> dict:
    """The report's "dxvk" block for a log the player read whole."""
    data = log.read_bytes()
    return {"log": str(log), "log_bytes_read": len(data), "log_sha256": hashlib.sha256(data).hexdigest(),
            "version": "v3.1.1", "driver": "NVIDIA 1", "config_file": config_file}


def dxvk_run(tmp_path: Path, file_options: list[str], applied: list[str], found: Path | None = None,
             rep: dict | None = None) -> tuple[dict, Path]:
    """A report pinning a dxvk.conf with file_options, and a DXVK log that read
    `found` (default that file) and applied `applied` (DXVK_CONFIG merges over
    the file), pinned in the report as the player pins the log it read."""
    conf = tmp_path / "dxvk.conf"
    conf.write_text("# a comment\n" + "".join(f"{o}\n" for o in file_options))
    logs = tmp_path / "dxvk"
    logs.mkdir(exist_ok=True)
    log = logs / "sa2_player_d3d9.log"
    log.write_text(
        "info:  Game: sa2_player\ninfo:  DXVK: v3.1.1\n"
        f"info:  Found config file: {found or conf}\ninfo:  Effective configuration:\n"
        + "".join(f"info:    {o}\n" for o in applied) + "info:  Vulkan: found\ninfo:    Driver   : NVIDIA 1\n")
    rep = rep if rep is not None else report()
    rep["files"] = {"dxvk_conf": {"path": str(conf), "real_path": str(conf.resolve()),
                                  "sha256": hashlib.sha256(conf.read_bytes()).hexdigest()}}
    rep["dxvk"] = dxvk_pin(log, str(found or conf))
    return rep, logs


def test_the_dxvk_check_holds_the_literal_option_set(tmp_path):
    pinned = PR.PINNED_DXVK_OPTIONS
    changed = [o.replace("Strict", "True") for o in pinned]
    conf = (tmp_path / "dxvk.conf").resolve()
    rep, logs = dxvk_run(tmp_path, pinned, pinned)
    assert check(tmp_path, rep, 0, dxvk_log=str(logs))["pass_"] is True
    # The run's own file says True, and DXVK applied it: both halves fail.
    rep, logs = dxvk_run(tmp_path, changed, changed)
    failures = check(tmp_path, rep, 0, dxvk_log=str(logs))["failures"]
    assert len(failures) == 2
    assert failures[0].startswith("dxvk: DXVK applied [") and failures[1].startswith(f"dxvk: {conf} holds [")
    assert all("'d3d9.floatEmulation = True'" in f.split("; the pinned")[0] for f in failures)
    # The file is the pinned one and DXVK applied another (DXVK_CONFIG).
    rep, logs = dxvk_run(tmp_path, pinned, changed)
    failures = check(tmp_path, rep, 0, dxvk_log=str(logs))["failures"]
    assert len(failures) == 1 and failures[0].startswith("dxvk: DXVK applied [")
    # DXVK applied the pinned set from a file that holds another.
    rep, logs = dxvk_run(tmp_path, changed, pinned)
    failures = check(tmp_path, rep, 0, dxvk_log=str(logs))["failures"]
    assert len(failures) == 1 and failures[0].startswith(f"dxvk: {conf} holds [")


def test_the_dxvk_check_holds_the_config_file_to_the_one_the_run_pinned(tmp_path):
    pinned = PR.PINNED_DXVK_OPTIONS
    other = tmp_path / "elsewhere" / "dxvk.conf"
    other.parent.mkdir()
    other.write_text("".join(f"{o}\n" for o in pinned))
    rep, logs = dxvk_run(tmp_path, pinned, pinned, found=other)
    conf = (tmp_path / "dxvk.conf").resolve()
    assert check(tmp_path, rep, 0, dxvk_log=str(logs))["failures"] == [f"dxvk: DXVK read {other}; the report pins {conf}"]
    rep, logs = dxvk_run(tmp_path, pinned, pinned)
    conf.write_text(conf.read_text() + "# edited after the run\n")
    assert check(tmp_path, rep, 0, dxvk_log=str(logs))["failures"] == [
        "dxvk: the config file changed after the run pinned it"]


def test_the_dxvk_check_holds_the_log_to_the_run_that_read_it(tmp_path):
    pinned = PR.PINNED_DXVK_OPTIONS
    rep, logs = dxvk_run(tmp_path, pinned, pinned)
    log = logs / "sa2_player_d3d9.log"
    # DXVK appends after the player read it: still the run's log.
    with log.open("a") as f:
        f.write("info:  Presenter: teardown\n")
    assert check(tmp_path, rep, 0, dxvk_log=str(logs))["pass_"] is True
    # The report names its log: another *_d3d9.log that sorts first is not read.
    (logs / "a_player_d3d9.log").write_text("info:  DXVK: v3.1.1\ninfo:  Effective configuration:\n"
                                            "info:    d3d9.floatEmulation = True\n")
    assert check(tmp_path, rep, 0, dxvk_log=str(logs))["pass_"] is True
    # Another run's log in its place, the same options, but not the bytes read.
    text = log.read_text().replace("NVIDIA 1", "NVIDIA 2")
    log.write_text(text)
    failures = check(tmp_path, rep, 0, dxvk_log=str(logs))["failures"]
    assert len(failures) == 1 and failures[0].startswith(f"dxvk: {log} is not the log this run read: its first ")
    # A report that pins no log cannot have one held to it.
    (logs / "a_player_d3d9.log").unlink()
    del rep["dxvk"]
    failures = check(tmp_path, rep, 0, dxvk_log=str(logs))["failures"]
    assert failures == ["dxvk: the report pins no DXVK log (DXVK_LOG_PATH unset, or no log when the run ended), so none "
                        "can be held to it"]
    empty = tmp_path / "nolog"
    empty.mkdir()
    assert check(tmp_path, rep, 0, dxvk_log=str(empty))["failures"] == [f"dxvk: no DXVK log in {empty}"]


def test_the_tracked_dxvk_conf_is_the_pinned_set():
    assert PR.conf_options(ROOT / "host" / "player" / "dxvk.conf") == PR.PINNED_DXVK_OPTIONS
    assert "d3d9.floatEmulation = Strict" in PR.PINNED_DXVK_OPTIONS


# ------------------------------------------------------------ output paths

def working_tree(tmp_path: Path) -> Path:
    """A git working tree as the rule sees one: a directory holding .git (a
    worktree's .git is a file), with host/ and private/ in it."""
    tree = tmp_path / "tree"
    (tree / "host" / "player").mkdir(parents=True)
    (tree / "private" / "generated").mkdir(parents=True)
    (tree / ".git").write_text("gitdir: /elsewhere\n")
    return tree


def test_outputs_inside_the_tree_are_refused_unless_under_private(tmp_path):
    tree = working_tree(tmp_path)
    why = PR.refuse_output(tree / "host" / "player" / "boot.mp4")
    assert why is not None and "outside its private/" in why and str(tree) in why
    assert PR.refuse_output(tree / "boot.mp4") is not None
    assert PR.refuse_output(tree / "private" / "generated" / "s5" / "video" / "boot.mp4") is None
    assert PR.refuse_output(tmp_path / "boot.mp4") is None                 # outside every working tree
    (tmp_path / "into-tree").symlink_to(tree / "host")
    assert PR.refuse_output(tmp_path / "into-tree" / "boot.mp4") is not None
    (tree / "host" / "to-private").symlink_to(tree / "private")
    assert PR.refuse_output(tree / "host" / "to-private" / "boot.mp4") is None


def encode_args(tmp_path: Path, out: Path, **over) -> argparse.Namespace:
    args = dict(frames=str(tmp_path / "frames.y4m"), times=None, report=str(tmp_path / "stage3.json"),
                dxvk_log=None, out=str(out), fps="60", hash=False, keep_staged=False, no_verify=False, timeout=10,
                audio_wav=None)
    args.update(over)
    return argparse.Namespace(**args)


def test_encode_refuses_an_output_in_the_tree_before_it_reads_anything(tmp_path, capsys):
    out = working_tree(tmp_path) / "host" / "player" / "must-not-exist.mp4"
    assert F.cmd_encode(encode_args(tmp_path, out)) == 2       # the frames file does not even exist
    err = capsys.readouterr().err
    # the MP4, receipt, staged frames, decoded frames, and the sound's three staged files
    assert err.count("refused: ") == 7 == len(F.encode_outputs(out)) and err.count("outside its private/") == 7
    tiny_run(tmp_path)                                          # good frames, bound to a passing run
    assert F.cmd_encode(encode_args(tmp_path, out)) == 2
    assert capsys.readouterr().err.count("outside its private/") == 7
    assert not [p for p in F.encode_outputs(out) if p.exists()]


def test_encode_needs_a_report():
    with pytest.raises(SystemExit) as stop:
        F.main(["encode", "--frames", "f.y4m", "--out", "o.mp4"])
    assert stop.value.code == 2


# ------------------------------------------------------------ frames and times

# The player's fingerprints of the synthetic stream's nine frames at stage 3
# (host/player/synth_stream.cpp, its times file's bgrx_hash column), and the
# frames digest the player's report gave for them.
SYNTH_HASHES = [0xe247a7e1bb404325, 0xc1d06d20b31a5a25, 0x4f55cc0e4343ad25, 0xc3b7c7a1e2efbc25, 0x1adfe10254e62525,
                0xd3e7391ea9a85a25, 0x60afd1cbb56a1325, 0x166fa8ba2f64e70d, 0x055f4f567363fe91]
SYNTH_DIGEST = 0x70012f1e18e62c38
SYNTH_TIMES = [0, 16666, 33332, 49998, 99996, 116662, 133328, 149994, 166660]
TIMES_HEADER = "frame\tseq\tvirtual_us\tguest_frames\tpolls\twall_us\tbgrx_hash"


def write_times(path: Path, hashes: list[int], times: list[int]) -> None:
    rows = [TIMES_HEADER]
    rows += [f"{i + 1}\t{10 * i}\t{t}\t{i + 1}\t{3 * i}\t{1000 * i}\t0x{h:016x}" for i, (h, t) in enumerate(zip(hashes, times))]
    path.write_text("\n".join(rows) + "\n")


def test_both_digests_equal_the_players(tmp_path):
    times = tmp_path / "synth.y4m.times.tsv"
    write_times(times, SYNTH_HASHES, SYNTH_TIMES)
    assert PR.bgrx_digest(times) == (9, SYNTH_DIGEST)
    assert F.bgrx_digest(F.read_times(times)) == SYNTH_DIGEST


def test_resample_holds_the_frame_that_waits_three_periods():
    assert F.resample(SYNTH_TIMES, (60, 1)) == [0, 1, 2, 3, 3, 3, 4, 5, 6, 7, 8]


def test_resample_drifts_as_its_docstring_says():
    mapping = F.resample([16666 * i for i in range(40000)], (60, 1))
    assert sorted(set(range(40000)) - set(mapping)) == [12499, 37499]


def tiny_run(tmp_path: Path, frames: int = 3) -> tuple[Path, Path, dict]:
    """A 4x4 y4m the player could have written, its times, and the report of
    the passing run that wrote them, with the dxvk.conf and DXVK log it pinned."""
    y4m = tmp_path / "frames.y4m"
    header = F.y4m_header(4, 4, (60, 1))
    y4m.write_bytes(header + b"".join(F.FRAME_TAG + bytes([16 + k]) * 16 + b"\x80" * 8 for k in range(frames)))
    times = Path(str(y4m) + ".times.tsv")
    write_times(times, SYNTH_HASHES[:frames], SYNTH_TIMES[:frames])
    rows = F.read_times(times)
    rep = report(presents=frames, frames_out={
        "path": str(y4m), "times": str(times), "frames": frames, "y4m_bytes": y4m.stat().st_size,
        "times_rows": frames, "bgrx_digest": f"0x{F.bgrx_digest(rows):016x}"})
    rep, _ = dxvk_run(tmp_path, PR.PINNED_DXVK_OPTIONS, PR.PINNED_DXVK_OPTIONS, rep=rep)
    (tmp_path / "stage3.json").write_text(json.dumps(rep))
    return y4m, times, rep


@pytest.mark.parametrize("change, said", [
    ("bytes", "frames: {size} bytes; the report says {size_plus_1}"),
    ("digest", "frames: digest 0x"),
    ("rows", "frames: 3 times rows; the report says 4 rows and 3 frames"),
    ("path", "frames: the report wrote "),
    ("missing", " or its times file is missing"),
    ("none", "frames: the report has no frames_out record"),
    ("badrow", "frames: "),
])
def test_the_check_holds_the_frames_to_the_report(tmp_path, change, said):
    y4m, times, rep = tiny_run(tmp_path)
    assert check(tmp_path, rep, 0, frames_out=str(y4m))["frames"]["rows"] == 3
    if change == "bytes":
        rep["frames_out"]["y4m_bytes"] += 1
    elif change == "digest":
        rep["frames_out"]["bgrx_digest"] = "0x0000000000000000"
    elif change == "rows":
        rep["frames_out"]["times_rows"] = 4
    elif change == "path":
        rep["frames_out"]["path"] = str(tmp_path / "other.y4m")
    elif change == "missing":
        times.unlink()
    elif change == "none":
        rep["frames_out"] = None
    elif change == "badrow":
        times.write_text(times.read_text().replace("\t0x", "\tzz", 1))
    v = check(tmp_path, rep, 0, frames_out=str(y4m))
    frames_failures = [f for f in v["failures"] if f.startswith("frames:") or "times file is missing" in f]
    said = said.format(size=y4m.stat().st_size, size_plus_1=y4m.stat().st_size + 1)
    assert v["pass_"] is False and len(frames_failures) == 1 and said in frames_failures[0]
    if change == "badrow":
        assert "line 2 has no 64-bit bgrx_hash" in frames_failures[0]


@pytest.mark.parametrize("change, refused", [
    ({}, None),
    ({"complete": False, "phase": "replay"}, "the run did not finish"),
    ({"pass": False}, "the run did not pass"),
    ({"frames_out": None}, "the run wrote no frames file"),
    ({"presents": 4}, "presents 4"),
    ("bytes", "bytes; its run wrote"),
    ("path", "{report} wrote {other}, not {y4m}"),
    ("digest", "frame digest"),
    # The report says pass; its counts, which the check recomputes, do not.
    ({"errors": 1, "first_errors": ["seq 9 Device::Clear: x"]},
     "the check: the report says pass=True; its counts say exit 4"),
    ("float", "the check: dxvk: DXVK applied ['d3d9.floatEmulation = True'"),
    ("nolog", "the report names no DXVK log to hold the run's options to"),
])
def test_encode_binds_frames_to_a_run_that_passes_the_check(tmp_path, change, refused):
    y4m, times, rep = tiny_run(tmp_path)
    if change == "bytes":
        rep["frames_out"]["y4m_bytes"] += 1
    elif change == "path":
        rep["frames_out"]["path"] = str(tmp_path / "other.y4m")
    elif change == "digest":
        rep["frames_out"]["bgrx_digest"] = "0x0000000000000000"
    elif change == "float":                          # DXVK_CONFIG overrode an option; the run's verdict did not see it
        changed = [o.replace("Strict", "True") for o in PR.PINNED_DXVK_OPTIONS]
        rep, _ = dxvk_run(tmp_path, PR.PINNED_DXVK_OPTIONS, changed, rep=rep)
    elif change == "nolog":
        del rep["dxvk"]
    else:
        rep.update(change)
    (tmp_path / "stage3.json").write_text(json.dumps(rep))
    binding = F.report_binding(tmp_path / "stage3.json", y4m, times, F.Y4M(y4m), F.read_times(times))
    if refused is None:
        assert binding["refusals"] == [] and binding["check"]["pass"] is True
        assert binding["check"]["dxvk_log"] == str(tmp_path / "dxvk")
    else:
        refused = refused.format(report=tmp_path / "stage3.json", other=tmp_path / "other.y4m", y4m=y4m)
        assert any(refused in r for r in binding["refusals"]), binding["refusals"]


def test_encode_refuses_unbound_or_broken_frames_before_vlc(tmp_path, capsys):
    y4m, times, rep = tiny_run(tmp_path)
    out = tmp_path / "v.mp4"
    with y4m.open("ab") as f:
        f.write(b"\x00" * 7)                                      # a trailing partial frame
    assert F.cmd_encode(encode_args(tmp_path, out)) == 2
    assert "not a whole frame" in capsys.readouterr().err
    y4m, times, rep = tiny_run(tmp_path)
    write_times(times, SYNTH_HASHES[:2], SYNTH_TIMES[:2])         # three frames, two times
    assert F.cmd_encode(encode_args(tmp_path, out)) == 2
    assert f"refused: {y4m} holds 3 frames and {times} times 2" in capsys.readouterr().err
    y4m.write_bytes(b"")                                          # a run refused at open empties its frames
    times.write_text(TIMES_HEADER + "\n")
    assert F.cmd_encode(encode_args(tmp_path, out)) == 2
    assert "refused:" in capsys.readouterr().err
    assert not [p for p in F.encode_outputs(out) if p.exists()]


@pytest.mark.parametrize("edit, said", [
    (lambda text: text.replace("\t16666\t", "\tzzz\t"), "line 3 is not a row of the player's"),
    (lambda text: text.replace("\tbgrx_hash", "\tother"), "no bgrx_hash column"),
    (lambda text: text.replace("\t0xc1d06d20b31a5a25", ""), "line 3 is not a row of the player's: 6 fields for 7"),
])
def test_encode_refuses_a_times_file_that_is_not_the_players(tmp_path, capsys, edit, said):
    y4m, times, rep = tiny_run(tmp_path)
    times.write_text(edit(times.read_text()))
    out = tmp_path / "v.mp4"
    assert F.cmd_encode(encode_args(tmp_path, out)) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and said in err and "Traceback" not in err
    assert not [p for p in F.encode_outputs(out) if p.exists()]
    with pytest.raises(SystemExit) as stop:                       # compare reads the same way
        F.cmd_compare(argparse.Namespace(a=str(times), b=str(times)))
    assert said in str(stop.value)


def test_encode_refuses_a_frames_file_whose_header_does_not_parse(tmp_path, capsys):
    y4m, times, rep = tiny_run(tmp_path)
    y4m.write_bytes(y4m.read_bytes().replace(b"W4 ", b"Wfour ", 1))
    assert F.cmd_encode(encode_args(tmp_path, tmp_path / "v.mp4")) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"refused: {y4m}: the YUV4MPEG2 header does not parse") and "Traceback" not in err


# ------------------------------------------------------------ the decode check

def test_the_decode_check_catches_a_slip_the_threshold_cannot():
    """A fade of 0.3 luma levels a frame, decoded exactly: the true mapping
    passes; slipped by one frame, every frame is still within the threshold,
    and only the neighbour test sees it (as decode_check_control holds with VLC)."""
    def sig(i: int) -> list[float]:
        return [100.0 + 0.3 * i] * 4
    frames = SimpleNamespace(count=30)
    dec = [sig(k) for k in range(30)]
    good = F.compare_decoded(dec, sig, list(range(30)), frames, F.DECODE_THRESHOLD)
    assert (good["worst"], good["over"], good["nearer"], good["noise"]) == (0.0, [], [], 0.0)
    slipped = F.compare_decoded(dec, sig, [min(k + 1, 29) for k in range(30)], frames, F.DECODE_THRESHOLD)
    assert slipped["over"] == [] and slipped["nearer"] == list(range(29))
    assert slipped["worst"] == pytest.approx(0.3)
    off = F.compare_decoded([[v + 3.0 for v in sig(k)] for k in range(30)], sig, list(range(30)), frames,
                            F.DECODE_THRESHOLD)
    assert off["over"] == list(range(30))


# ------------------------------------------------------------ the MP4

def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def mp4_first_sample(tmp_path: Path, nal_type: int) -> tuple[Path, list[bytes]]:
    """An MP4 as VLC's muxer lays one out: the moov before the mdat, one video
    track of three samples in one chunk, stss listing sample 2 only, and the
    first sample one NAL unit of `nal_type`."""
    samples = [struct.pack(">I", 5) + bytes([0x60 | t]) + b"\x88\x84\x00\x10" for t in (nal_type, 1, 1)]
    ftyp = box(b"ftyp", b"isom")

    def moov(chunk_offset: int) -> bytes:
        stsz = box(b"stsz", bytes(4) + struct.pack(">II", 0, 3) + b"".join(struct.pack(">I", len(s)) for s in samples))
        stco = box(b"stco", bytes(4) + struct.pack(">II", 1, chunk_offset))
        stss = box(b"stss", bytes(4) + struct.pack(">II", 1, 2))
        stbl = box(b"stbl", box(b"stsd", bytes(8)) + stsz + stco + stss)
        mdia = box(b"mdia", box(b"hdlr", bytes(8) + b"vide" + bytes(12)) + box(b"minf", stbl))
        return box(b"moov", box(b"trak", mdia))
    offset = len(ftyp) + len(moov(0)) + 8
    mp4 = tmp_path / f"first-{nal_type}.mp4"
    mp4.write_bytes(ftyp + moov(offset) + box(b"mdat", b"".join(samples)))
    return mp4, samples


def test_the_first_sample_is_marked_sync_only_when_it_is_an_idr(tmp_path):
    mp4, samples = mp4_first_sample(tmp_path, 5)
    fix = F.mark_first_sync(mp4)
    assert fix["changed"] is True and fix["first_sample_nal_types"] == [5] and fix["bytes_added"] == 4
    data = mp4.read_bytes()
    stss, stco = data.index(b"stss"), data.index(b"stco")
    assert struct.unpack(">III", data[stss + 8:stss + 20]) == (2, 1, 2)
    offset = struct.unpack(">I", data[stco + 12:stco + 16])[0]
    assert data[offset:offset + 27] == b"".join(samples)          # the chunk offset moved with the mdat
    mp4, _ = mp4_first_sample(tmp_path, 1)
    before = mp4.read_bytes()
    fix = F.mark_first_sync(mp4)
    assert fix == {"changed": False, "why": "sample 1 holds NAL types [1], no IDR (5)"}
    assert mp4.read_bytes() == before


# ------------------------------------------------------------ the SPS

# Two SPS NAL units x264 wrote through VLC 3.0.23 for a 128x64 synthetic fade
# (X264_LOW_LATENCY without, and with, chromaloc=1).
SPS_WITHOUT_CHROMALOC = bytes.fromhex("6764000bacb20413602d40404050000003001000000788f142a480")
SPS_CHROMALOC_1 = bytes.fromhex("6764000bacb20413602d4040406940000003004000001e23c50a92")


def test_the_sps_parse_reads_x264s_colour_and_chroma_siting():
    common = {"profile_idc": 100, "level_idc": 11, "coded_size": [128, 64], "crop": [0, 0, 0, 0], "vui": True,
              "video_signal_type_present": True, "video_format": 5, "video_full_range_flag": 0,
              "colour_primaries": 1, "transfer_characteristics": 1, "matrix_coefficients": 1}
    assert F.avc_sps_info(SPS_WITHOUT_CHROMALOC) == dict(common, chroma_loc_info_present=False)
    assert F.avc_sps_info(SPS_CHROMALOC_1) == dict(common, chroma_loc_info_present=True,
                                                   chroma_sample_loc_type_top_field=1,
                                                   chroma_sample_loc_type_bottom_field=1)
    assert F.bt709_limited(F.avc_sps_info(SPS_WITHOUT_CHROMALOC)) is True
    assert F.colour_ok(F.avc_sps_info(SPS_WITHOUT_CHROMALOC)) is False
    assert F.colour_ok(F.avc_sps_info(SPS_CHROMALOC_1)) is True


def test_mp4_info_finds_the_sps_in_an_avcc(tmp_path):
    """The SPS reaches avc_sps_info through a minimal moov as VLC's muxer lays it
    out: trak > mdia > (hdlr vide, minf > stbl > stsd > avc1 > avcC)."""
    avcc = box(b"avcC", bytes([1, 100, 0, 11, 0xFF, 0xE1]) + struct.pack(">H", len(SPS_CHROMALOC_1)) + SPS_CHROMALOC_1
               + bytes([0]))
    avc1 = box(b"avc1", bytes(24) + struct.pack(">HH", 128, 64) + bytes(50) + avcc)
    stsd = box(b"stsd", bytes(8) + avc1)
    stbl = box(b"stbl", stsd + box(b"stsz", bytes(4) + struct.pack(">II", 0, 3)))
    mdia = box(b"mdia", box(b"hdlr", bytes(8) + b"vide" + bytes(12)) + box(b"minf", stbl))
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(box(b"ftyp", b"isom") + box(b"moov", box(b"trak", mdia)))
    video = F.mp4_info(mp4)["video"]
    assert (video["codec"], video["width"], video["height"], video["samples"]) == ("avc1", 128, 64, 3)
    assert F.colour_ok(video["sps"]) is True


# ------------------------------------------------------------ sound

def sound_report(**over) -> dict:
    """report() of a run asked for sound with --audio-wav (and --audio when device=True)."""
    device = over.pop("device_sound", False)
    rep = report(**dict({"stream_version": 2, "stream_schema": "sa2-d3d9-stream/v2"}, **over))
    rep["options"] = dict(rep["options"], audio=device, audio_wav="s.wav")
    rep["records"] = dict(rep["records"], audio=7)
    rep["audio"] = {"asked": {"device": device, "wav": "s.wav"}, "stream_records": 7,
                    "device": {"open": True, "failure": None} if device else None,
                    "problems": {"count": 0, "first": []}}
    return rep


def test_a_version_2_stream_and_its_sound_are_held_to_their_counts(tmp_path):
    assert check(tmp_path, sound_report(), 0)["failures"] == []
    rep = sound_report(stream_schema="sa2-d3d9-stream/v1")
    assert check(tmp_path, failing(rep, 3), 3)["failures"] == [
        "stream: schema 'sa2-d3d9-stream/v1', not 'sa2-d3d9-stream/v2'"]
    rep = sound_report()
    rep["audio"]["problems"] = {"count": 1, "first": ["the stream is version 1: it holds no audio"]}
    assert check(tmp_path, failing(rep, 4), 4)["failures"] == [
        "replay: audio: 1 problem(s): ['the stream is version 1: it holds no audio']"]
    assert "the report says pass=True; its counts say exit 4" in check(tmp_path, rep | {"pass": True,
                                                                                        "exit_status": 0}, 0)["failures"]
    rep = sound_report(device_sound=True)
    assert check(tmp_path, rep, 0)["failures"] == []
    rep["audio"]["device"] = {"open": False, "failure": "SDL_OpenAudioDeviceStream: no device"}
    assert check(tmp_path, failing(rep, 4), 4)["failures"] == [
        "replay: audio: no playback device: SDL_OpenAudioDeviceStream: no device"]
    rep = sound_report()
    del rep["audio"]
    assert check(tmp_path, failing(rep, 4), 4)["failures"] == [
        "replay: the run asked for sound and the report has no audio account"]


def wav_bytes(frames: list[tuple[float, float]]) -> bytes:
    data = b"".join(struct.pack("<ff", l, r) for l, r in frames)
    return (b"RIFF" + struct.pack("<I", 50 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHHH", 18, 3, 2, 48000,
                                                                                     384000, 8, 32, 0)
            + b"fact" + struct.pack("<II", 4, len(frames)) + b"data" + struct.pack("<I", len(data)) + data)


def test_the_sound_is_aligned_to_the_first_frame_and_cut_to_the_video(tmp_path):
    # 125 us is 6 samples at 48 kHz exactly; 10 us is 0.48 of a sample
    assert F.audio_offset(5_000_000_125, 5_000_000_000) == 6
    assert F.audio_offset(5_000_000_000, 5_000_000_125) == -6
    assert (F.audio_offset(1_010, 1_000), F.audio_offset(1_000, 1_010), F.audio_offset(0, 21)) == (0, 0, -1)
    src = tmp_path / "s.wav"
    src.write_bytes(wav_bytes([(k, -k) for k in range(1, 11)]))
    out = tmp_path / "a.wav"
    # the sound began 6 samples before the first frame: it is cut there, and padded to 8
    got = F.align_wav(src, {"origin_us": 0}, 125, 2, (48000, 4), out)
    assert got == {"offset_samples": 6, "samples": 8, "from_wav": 4, "silence_before": 0, "silence_after": 4,
                   "wav_frames": 10}
    assert F.read_wav(out)["frames"] == 8
    assert struct.unpack("<16f", out.read_bytes()[58:]) == (7, -7, 8, -8, 9, -9, 10, -10) + (0,) * 8
    # it began 6 samples after: silence first
    got = F.align_wav(src, {"origin_us": 125}, 0, 2, (48000, 4), out)
    assert (got["silence_before"], got["from_wav"]) == (6, 2)
    assert struct.unpack("<16f", out.read_bytes()[58:]) == (0,) * 12 + (1, -1, 2, -2)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(wav_bytes([(0, 0)]).replace(struct.pack("<HH", 3, 2), struct.pack("<HH", 1, 2), 1))
    with pytest.raises(SystemExit, match="format tag 1"):
        F.read_wav(bad)


@pytest.mark.parametrize("change,said", [
    ({"stream_sha256": "f" * 64}, "the WAV is the sound of stream " + "f" * 64),
    ({"audio_problems": 2}, "says the mixer had 2 problem(s) making it"),
    ({"ok": False}, "says the WAV was not written whole"),
    ({"frames": 9}, "names 9 samples; "),
    ({"origin_us": None}, "names no origin_us"),
])
def test_the_sound_is_bound_to_the_frames_run(tmp_path, change, said):
    wav = tmp_path / "s.wav"
    wav.write_bytes(wav_bytes([(0.0, 0.0)] * 10))
    rep = tmp_path / "r.json"
    rep.write_text(json.dumps({"stream_sha256": "a" * 64}))
    side = {"ok": True, "frames": 10, "origin_us": 0, "audio_problems": 0, "stream_sha256": "a" * 64}
    Path(str(wav) + ".json").write_text(json.dumps(side))
    assert F.audio_binding(wav, rep)["refusals"] == []
    Path(str(wav) + ".json").write_text(json.dumps(dict(side, **change)))
    got = F.audio_binding(wav, rep)["refusals"]
    assert len(got) == 1 and said in got[0], got


def mp4_of(handler: bytes, samples: list[bytes], per_chunk: int, movie_ts: int, media_ts: int, duration: int,
           *, entry: bytes) -> bytes:
    """A one-track MP4 as VLC's muxer lays one out (ftyp, moov, wide, mdat): mvhd, then
    trak > tkhd, edts > elst, mdia > mdhd, hdlr, minf > stbl > stsd, stts, stsc, stsz,
    stco, its samples `per_chunk` to a chunk."""
    def moov(base: int) -> bytes:
        offsets, at = [], base
        for i in range(0, len(samples), per_chunk):
            offsets.append(at)
            at += sum(len(s) for s in samples[i:i + per_chunk])
        mvhd = box(b"mvhd", bytes(12) + struct.pack(">II", movie_ts, duration) + bytes(76) + struct.pack(">I", 2))
        tkhd = box(b"tkhd", bytes(12) + struct.pack(">II", 1, 0) + struct.pack(">I", duration) + bytes(60))
        elst = box(b"elst", bytes(4) + struct.pack(">IIII", 1, duration, 0, 0x10000))
        mdhd = box(b"mdhd", bytes(12) + struct.pack(">II", media_ts, duration * media_ts // movie_ts) + bytes(4))
        stbl = box(b"stbl", box(b"stsd", bytes(4) + struct.pack(">I", 1) + entry)
                   + box(b"stts", bytes(4) + struct.pack(">III", 1, len(samples), 1))
                   + box(b"stsc", bytes(4) + struct.pack(">IIII", 1, 1, per_chunk, 1))
                   + box(b"stsz", bytes(4) + struct.pack(">II", 0, len(samples))
                         + b"".join(struct.pack(">I", len(s)) for s in samples))
                   + box(b"stco", bytes(4) + struct.pack(">I", len(offsets)) + b"".join(struct.pack(">I", o) for o in offsets)))
        mdia = box(b"mdia", mdhd + box(b"hdlr", bytes(8) + handler + bytes(12)) + box(b"minf", stbl))
        return box(b"moov", mvhd + box(b"trak", tkhd + box(b"edts", elst) + mdia))
    ftyp = box(b"ftyp", b"isom")
    base = len(ftyp) + len(moov(0)) + 8 + 8
    return ftyp + moov(base) + struct.pack(">I4s", 8, b"wide") + box(b"mdat", b"".join(samples))


AVC1 = box(b"avc1", bytes(24) + struct.pack(">HH", 128, 64) + bytes(50))
MP4A = box(b"mp4a", bytes(16) + struct.pack(">HHHHI", 2, 16, 0, 0, 48000 << 16))


def test_the_sound_track_is_merged_with_both_tracks_samples_intact(tmp_path):
    vs = [bytes([v]) * (5 + v) for v in range(1, 4)]
    au = [bytes([0xA0 + k]) * (3 + k) for k in range(5)]
    video, sound, out = tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "m.mp4"
    video.write_bytes(mp4_of(b"vide", vs, 3, 90000, 90000, 4500, entry=AVC1))
    sound.write_bytes(mp4_of(b"soun", au, 2, 48000, 48000, 9600, entry=MP4A))
    assert F.track_samples(video.read_bytes(), "vide") == vs and F.track_samples(sound.read_bytes(), "soun") == au
    got = F.merge_audio(video, sound, out)
    data = out.read_bytes()
    assert F.track_samples(data, "vide") == vs and F.track_samples(data, "soun") == au
    assert got["track_id"] == 2 and got["sound_duration"] == [9600, 48000] and got["movie_timescale"] == 90000
    info = F.mp4_info(out)
    assert info["video"]["samples"] == 3 and info["audio"] == {
        "timescale": 48000, "duration": 9600, "handler": "soun", "codec": "mp4a", "channels": 2,
        "sample_rate": 48000, "sample_deltas": {1: 5}, "samples": 5, "duration_s": 0.2}
    (atrak, moov) = F._trak_of(data, "soun")
    tkhd = F._child(data, atrak[1], atrak[2], "tkhd")
    assert struct.unpack(">I", data[tkhd[1] + 12:tkhd[1] + 16])[0] == 2                 # the next track id
    assert struct.unpack(">I", data[tkhd[1] + 20:tkhd[1] + 24])[0] == 18000             # 0.2 s at 90 kHz
    mvhd = F._child(data, moov[1], moov[2], "mvhd")
    assert struct.unpack(">II", data[mvhd[1] + 12:mvhd[1] + 20]) == (90000, 18000)     # the longer track's
    assert data[mvhd[2] - 4:mvhd[2]] == struct.pack(">I", 3)
    # a file that is not VLC's layout is refused, not merged
    late = tmp_path / "late.mp4"
    v = video.read_bytes()
    late.write_bytes(v[:16] + v[v.index(b"wide") - 4:] + v[16:v.index(b"wide") - 4])
    with pytest.raises(ValueError, match="not a moov before the mdat"):
        F.merge_audio(late, sound, tmp_path / "x.mp4")


def test_the_sound_is_encoded_alone_and_the_video_without_it():
    cmd = F.vlc_audio_cmd(Path("a.wav"), Path("a.m4a"))
    assert cmd[:4] == [F.CVLC, "-I", "dummy", "--no-video"] and "a.wav" in cmd
    assert cmd[cmd.index("--sout") + 1] == ("#transcode{acodec=mp4a,ab=192,channels=2,samplerate=48000}"
                                           ":std{access=file,mux=mp4,dst=a.m4a}")
    video = F.vlc_encode_cmd("v.y4m", Path("v.mp4"), [])
    assert "--no-audio" in video and "acodec=none" in video[video.index("--sout") + 1]
    assert all(o.name.startswith("v.mp4") for o in F.encode_outputs(Path("v.mp4")))
    assert {o.name for o in F.encode_outputs(Path("v.mp4"))} >= {"v.mp4.audio.wav", "v.mp4.audio.m4a",
                                                                 "v.mp4.merged.mp4"}
