from __future__ import annotations

from clash_arranger.health import (
    live_nodes,
    probe_candidates,
    responsive_nodes,
    zero_of_all_rounds,
)


def test_first_timeout_second_recover_is_jitter_not_death():
    calls = {"SG-A1": [None, 120]}

    def probe(name: str):
        return calls[name].pop(0)

    result = probe_candidates(["SG-A1"], probe, rounds=2)
    assert responsive_nodes(["SG-A1"], result) == ["SG-A1"]
    assert live_nodes(["SG-A1"], result) == []
    assert zero_of_all_rounds(["SG-A1"], result) == []


def test_two_round_504_is_dead():
    def probe(_name: str):
        return None

    result = probe_candidates(["SG-A1"], probe, rounds=2)
    assert result.results["SG-A1"] == [None, None]
    assert zero_of_all_rounds(["SG-A1"], result) == ["SG-A1"]
    assert responsive_nodes(["SG-A1"], result) == []
