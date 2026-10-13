"""tools/s5/wait_census.py reads a WaitForSingleObject call's timeout from the
instructions before it, and holds the census only when no call can pass 0.

Each case decodes a few hand-assembled instructions (no image byte) and asks the
census's own readers: the two pushes before the call, and the timeout's class."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from iced_x86 import Decoder, InstructionInfoFactory

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import wait_census as W  # noqa: E402

CALL_SLOT = bytes.fromhex("ff1534124000")      # call [0x00401234], a made-up slot
CALL_EBX = bytes.fromhex("ffd3")               # call ebx
MOV_EAX_ARG = bytes.fromhex("8b442404")        # mov eax,[esp+4]
PUSH_EAX, PUSH_ECX = bytes.fromhex("50"), bytes.fromhex("51")
TEST_EAX = bytes.fromhex("85c0")               # test eax,eax
JE_AWAY = bytes.fromhex("7410")                # je +0x10
CMP_EAX_0 = bytes.fromhex("83f800")            # cmp eax,0


def timeout(code: bytes) -> dict:
    ins = list(Decoder(32, code, ip=0x00401000))
    pushes, why = W.arguments(ins, len(ins) - 1)
    assert why is None, why
    return W.classify(ins, pushes[1], InstructionInfoFactory())


@pytest.mark.parametrize("code,kind,value", [
    (bytes.fromhex("6aff") + PUSH_ECX + CALL_SLOT, "immediate", 0xFFFFFFFF),     # push -1 (INFINITE)
    (bytes.fromhex("68e8030000") + PUSH_ECX + CALL_EBX, "immediate", 1000),
    (bytes.fromhex("6a00") + PUSH_EAX + CALL_SLOT, "immediate", 0),               # push 0
    (bytes.fromhex("b800000000") + PUSH_EAX + PUSH_ECX + CALL_EBX, "immediate", 0),  # mov eax,0
])
def test_an_immediate_timeout_is_read_with_its_sign(code, kind, value):
    got = timeout(code)
    assert (got["kind"], got["value"]) == (kind, value)


@pytest.mark.parametrize("test", [TEST_EAX, CMP_EAX_0])
def test_a_register_tested_for_zero_before_the_push_is_nonzero(test):
    got = timeout(MOV_EAX_ARG + test + JE_AWAY + PUSH_EAX + PUSH_ECX + CALL_EBX)
    assert got["kind"] == "nonzero" and got["guard"]["test"] == "0x00401004"


def test_an_untested_register_is_unclassified():
    assert timeout(MOV_EAX_ARG + PUSH_EAX + PUSH_ECX + CALL_EBX)["kind"] == "unclassified"


@pytest.mark.parametrize("je", [bytes.fromhex("7400"),          # je +0: lands on the push
                                bytes.fromhex("74019090")])     # je +1: on the second nop
def test_a_zero_branch_that_lands_before_the_push_guards_nothing(je):
    # The zero path still reaches the push with eax 0, so the timeout is not known
    # to be nonzero (the 2026-10-02 instrument review, N8).
    got = timeout(MOV_EAX_ARG + TEST_EAX + je + PUSH_EAX + PUSH_ECX + CALL_EBX)
    assert got["kind"] == "unclassified"


def test_a_zero_branch_back_to_its_test_still_guards():
    # je -4 loops on the test while eax is 0: the push is reached only past it.
    got = timeout(MOV_EAX_ARG + TEST_EAX + bytes.fromhex("74fc") + PUSH_EAX + PUSH_ECX + CALL_EBX)
    assert got["kind"] == "nonzero" and got["guard"]["taken_to"] == "0x00401004"


def test_a_jump_between_the_pushes_is_unclassified():
    ins = list(Decoder(32, PUSH_EAX + bytes.fromhex("eb00") + PUSH_ECX + CALL_EBX, ip=0x00401000))
    pushes, why = W.arguments(ins, len(ins) - 1)
    assert len(pushes) == 1 and "JMP" in why.upper()


def site(calls: list[dict], named: int = 1) -> dict:
    return {"sites": [{"site": "0x00401000", "kind": "call", "calls": calls}],
            "image_name_count": named}


def test_the_verdict_fails_a_zero_and_an_unclassified_call_and_a_dynamic_import(tmp_path):
    ok = {"call": "0x00401000", "timeout": {"kind": "immediate", "value": 0xFFFFFFFF}}
    assert W.verdict(site([ok]), [])["holds"] is True
    zero = {"call": "0x00401010", "timeout": {"kind": "immediate", "value": 0}}
    assert W.verdict(site([ok, zero]), [])["zero_calls"] == ["0x00401010"]
    assert W.verdict(site([ok, zero]), [])["holds"] is False
    odd = {"call": "0x00401020", "timeout": {"kind": "unclassified", "why": "memory"}}
    assert W.verdict(site([ok, odd]), [])["holds"] is False
    rec = tmp_path / "run.json"
    rec.write_text('{"getprocaddress": {"rows": [{"name": "WaitForSingleObject"}]}}')
    assert W.verdict(site([ok]), [rec])["holds"] is False
    assert W.verdict({"sites": [], "image_name_count": 1}, [])["holds"] is False


@pytest.mark.parametrize("named", [0, 2, None])
def test_the_verdict_needs_the_image_to_name_the_import_once(named):
    ok = {"call": "0x00401000", "timeout": {"kind": "immediate", "value": 0xFFFFFFFF}}
    got = W.verdict(site([ok], named=named), [])
    assert got["holds"] is False and got["image_name_count"] == named
