"""어댑터 배선 상태가 reconcile 에 드러나는지.

install-governance 는 invoke 를 {execute: null, review: null, repair: null} 로 깔고
운영자가 채우기를 기대한다. 채우기 전까지 dispatch 는 invoke_adapter 에서 exit 2 로
끝나는데, reconcile 은 startable 만 보여줘 "왜 아무 일도 안 일어나는가"에 답하지 못했다.

phase-gate 의 "도구 부재는 침묵 통과가 아니라 FAIL" 규칙을 실행 어댑터에도 적용한다.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import helpers as h

autopilot = h.load_module("autopilot", h.KIT / "scripts" / "autopilot.py")


class AdapterWiringTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir = self.root / "governance" / "adapters"
        self.dir.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, aid: str, invoke: dict):
        (self.dir / f"{aid}.json").write_text(
            json.dumps({"schema": "agent-adapter:v1", "id": aid,
                        "roles": ["worker"], "invoke": invoke}),
            encoding="utf-8")

    def test_missing_directory_is_reported(self):
        empty = Path(self._tmp.name) / "no-governance"
        empty.mkdir()
        self.assertEqual(autopilot.adapter_wiring(empty)["state"], "MISSING")

    def test_install_default_is_unwired(self):
        # install-governance.py 가 실제로 까는 모양 그대로
        self.write("default", {"execute": None, "review": None, "repair": None})
        out = autopilot.adapter_wiring(self.root)
        self.assertEqual(out["state"], "UNWIRED")
        self.assertEqual(out["unwired"][0]["operations"], ["execute", "repair", "review"])

    def test_execute_missing_is_unwired_not_partial(self):
        # execute 가 없으면 dispatch 가 통째로 무의미하다. PARTIAL 로 덮으면 안 된다.
        self.write("default", {"execute": None, "review": ["r"], "repair": ["p"]})
        self.assertEqual(autopilot.adapter_wiring(self.root)["state"], "UNWIRED")

    def test_execute_present_but_others_missing_is_partial(self):
        self.write("default", {"execute": ["e"], "review": None, "repair": None})
        out = autopilot.adapter_wiring(self.root)
        self.assertEqual(out["state"], "PARTIAL")
        self.assertEqual(out["unwired"][0]["operations"], ["repair", "review"])

    def test_fully_wired(self):
        self.write("default", {"execute": ["e"], "review": ["r"], "repair": ["p"]})
        out = autopilot.adapter_wiring(self.root)
        self.assertEqual(out["state"], "WIRED")
        self.assertEqual(out["unwired"], [])

    def test_one_unwired_adapter_among_many_is_caught(self):
        self.write("good", {"execute": ["e"], "review": ["r"], "repair": ["p"]})
        self.write("bad", {"execute": None, "review": ["r"], "repair": ["p"]})
        out = autopilot.adapter_wiring(self.root)
        self.assertEqual(out["state"], "UNWIRED")
        self.assertEqual([u["adapter"] for u in out["unwired"]], ["bad"])

    def test_corrupt_adapter_counts_as_unwired(self):
        (self.dir / "broken.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(autopilot.adapter_wiring(self.root)["state"], "UNWIRED")


if __name__ == "__main__":
    unittest.main()
