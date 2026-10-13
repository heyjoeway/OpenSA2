"""The re-take tool and the recorder after the word log: the fold review of U2's tests (2026-09-25)."""
import test_retake_replays as TR
from test_retake_replays import world  # noqa: F401  (fixture)

R, T, REC = TR.R, TR.T, TR.REC


def test_v13_a_pre_word_log_recording_whose_probe_moved_is_sent_to_the_re_take_tool(
        world, monkeypatch, capsys):
    """The real case (mh-record-20260924-202305): no word log AND a moved tick probe."""
    TR.capture(REC, mode="record", builds=TR.built(["INPUT_MODE=1"],
                                                   {"tick_probe": {"source_sha256": "0" * 64}}))
    TR.fake_run(monkeypatch, {})
    assert R.main(["--replay-only", REC]) == 1
    out = capsys.readouterr().out
    assert f"STOPPED: {REC} was recorded before the tick probe kept its word log" in out
    assert "Put them back as they were" not in out


def test_v16_every_re_take_waits_not_only_the_first(world, monkeypatch):
    TR.capture(REC, mode="record")
    calls = TR.fake_run(monkeypatch, {})
    monkeypatch.setattr(T, "POLL", 0.0)
    seen, idle = [], iter([10.0, 95.0, 10.0, 91.0])

    def idle_before_run():
        v = next(idle)
        seen.append((len(calls), v))
        return {"seconds": v}

    monkeypatch.setattr(R.RP, "idle_before_run", idle_before_run)
    assert T.main([REC, "--min-idle", "90", "--max-wait", "60"]) == 0
    assert seen == [(0, 10.0), (0, 95.0), (1, 10.0), (1, 91.0)]


def test_v14_under_a_stage_mask_a_re_take_without_a_log_is_not_identical(world, monkeypatch,
                                                                         tmp_path):
    TR.capture(REC, mode="record")
    TR.fake_run(monkeypatch, [{}, {}])
    mask = tmp_path / "metalharbor.h"
    mask.write_text("/* a stage mask */\n")
    monkeypatch.setattr(R.RP, "capture_from_log", lambda d, m=None: None)
    assert T.main([REC, "--mask", str(mask)]) == 1
    assert "tick_captures_identical" in TR.failed_checks()
