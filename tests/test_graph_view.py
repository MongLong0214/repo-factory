"""reconcile 그래프 관측 — blocked / progress / critical_path.

reconcile 이 ready·startable·held 만 내던 동안, 의존성 때문에 대기 중인 티켓은
어느 목록에도 나타나지 않았다. 셋 다 비어 있으면 "왜 아무것도 안 도는가"에
답할 수 없었다. blocked 는 그 구멍을 메우고, critical_path 는 병렬 폭이 아니라
남은 최소 라운드 수를 알려준다.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import helpers as h

autopilot = h.load_module("autopilot", h.KIT / "scripts" / "autopilot.py")


def ctx(edges: dict[str, list[str]]):
    """{티켓: [의존]} 를 autopilot 이 읽는 context 모양으로."""
    return {"tickets": {
        tid: {"meta": {"dependencies": deps}, "path": Path(f"{tid}.md")}
        for tid, deps in edges.items()
    }}


class BlockedTest(unittest.TestCase):
    def test_dependency_wait_is_reported(self):
        c = ctx({"A": [], "B": ["A"], "C": ["B"]})
        blocked = autopilot.compute_blocked(c, set())
        self.assertEqual(
            blocked,
            [{"ticket": "B", "waiting_on": ["A"]}, {"ticket": "C", "waiting_on": ["B"]}],
        )

    def test_verified_dependency_stops_being_a_wait(self):
        c = ctx({"A": [], "B": ["A"], "C": ["B"]})
        blocked = autopilot.compute_blocked(c, {"A"})
        self.assertEqual(blocked, [{"ticket": "C", "waiting_on": ["B"]}])

    def test_ready_and_blocked_do_not_overlap(self):
        c = ctx({"A": [], "B": ["A"]})
        ready = set(autopilot.compute_ready(c, set()))
        blocked = {b["ticket"] for b in autopilot.compute_blocked(c, set())}
        self.assertEqual(ready & blocked, set())
        # 살아 있는 티켓은 둘 중 하나에는 반드시 잡힌다 — 이것이 원래의 구멍이었다
        self.assertEqual(ready | blocked, {"A", "B"})

    def test_superseded_ticket_is_not_blocked(self):
        c = ctx({"A": [], "B": ["A"]})
        c["tickets"]["A"]["meta"]["supersedes"] = ["B"]
        self.assertEqual(autopilot.compute_blocked(c, set()), [])


class CriticalPathTest(unittest.TestCase):
    def test_longest_chain_not_ticket_count(self):
        # A→B→C 사슬과 독립 티켓 3개. 병렬 폭은 4지만 남은 라운드는 3이다.
        c = ctx({"A": [], "B": ["A"], "C": ["B"], "X": [], "Y": [], "Z": []})
        cp = autopilot.critical_path(c, set())
        self.assertEqual(cp["depth"], 3)
        self.assertEqual(cp["chain"], ["A", "B", "C"])

    def test_picks_the_longer_branch_not_the_first(self):
        # D 의 의존성이 둘이고 길이가 다르다. 의존성이 하나뿐인 사슬만 검사하면
        # "가장 긴 것 선택"과 "먼저 만난 것 선택"이 구분되지 않는다.
        # short: S → D (깊이 2) / long: A → B → C → D (깊이 4)
        c = ctx({"A": [], "B": ["A"], "C": ["B"], "S": [], "D": ["S", "C"]})
        cp = autopilot.critical_path(c, set())
        self.assertEqual(cp["depth"], 4)
        self.assertEqual(cp["chain"], ["A", "B", "C", "D"])

    def test_verified_prefix_shortens_the_path(self):
        c = ctx({"A": [], "B": ["A"], "C": ["B"]})
        cp = autopilot.critical_path(c, {"A"})
        self.assertEqual(cp["depth"], 2)
        self.assertEqual(cp["chain"], ["B", "C"])

    def test_empty_when_everything_verified(self):
        c = ctx({"A": [], "B": ["A"]})
        self.assertEqual(autopilot.critical_path(c, {"A", "B"}), {"depth": 0, "chain": []})

    def test_cycle_does_not_hang(self):
        # governance 의 TICKET_DAG_CYCLE 이 먼저 잡지만, 그 검사를 건너뛴 입력에서도
        # 여기서 무한 재귀로 죽으면 안 된다.
        c = ctx({"A": ["B"], "B": ["A"]})
        cp = autopilot.critical_path(c, set())
        self.assertGreaterEqual(cp["depth"], 1)
        self.assertLessEqual(cp["depth"], 2)

    def test_missing_dependency_is_ignored_not_crashed(self):
        # TICKET_DEP_MISSING 은 governance 가 보고한다. 여기서는 죽지 않기만 하면 된다.
        c = ctx({"A": ["없는티켓"]})
        self.assertEqual(autopilot.critical_path(c, set())["depth"], 1)


if __name__ == "__main__":
    unittest.main()
