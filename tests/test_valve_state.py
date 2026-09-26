"""Which watering program runs, from section and main valve outputs."""

from custom_components.nexo.valve_state import valve_states

LAWN = {"id": "lawn", "sections": ["S1", "S2", "S3"], "main_valve": "ZG"}
DRIP = {"id": "drip", "sections": ["S7", "S8"], "main_valve": "ZG"}
VALVES = [LAWN, DRIP]
ALL_OFF = {"S1": 0, "S2": 0, "S3": 0, "S7": 0, "S8": 0, "ZG": 0}


def run(steps):
    """Feed successive output readings, return the states after each."""
    last: dict[str, str] = {}
    return [valve_states(VALVES, {**ALL_OFF, **step}, last) for step in steps]


def test_idle() -> None:
    assert run([{}]) == [{"lawn": False, "drip": False}]


def test_lawn_program_stays_open_through_pauses() -> None:
    states = run([
        {"ZG": 1},              # main valve first
        {"ZG": 1, "S1": 1},
        {"ZG": 1},              # 5 s pause between sections
        {"ZG": 1, "S2": 1},
        {"ZG": 1},
        {},                     # main valve closed: done
    ])
    assert [s["lawn"] for s in states] == [False, True, True, True, True, False]
    assert all(s["drip"] is False for s in states)


def test_drip_on_its_own() -> None:
    states = run([{"ZG": 1, "S7": 1}, {"ZG": 1}, {"ZG": 1, "S8": 1}, {}])
    assert [s["drip"] for s in states] == [True, True, True, False]
    assert all(s["lawn"] is False for s in states)


def test_programs_back_to_back_hand_over() -> None:
    states = run([{"ZG": 1, "S3": 1}, {"ZG": 1}, {"ZG": 1, "S7": 1}, {"ZG": 1}])
    assert [(s["lawn"], s["drip"]) for s in states] == [
        (True, False), (True, False), (False, True), (False, True)
    ]


def test_closing_the_main_valve_forgets_the_program() -> None:
    last: dict[str, str] = {}
    valve_states(VALVES, {**ALL_OFF, "ZG": 1, "S1": 1}, last)
    valve_states(VALVES, ALL_OFF, last)
    # Main valve on again with no section yet: nothing is running
    assert valve_states(VALVES, {**ALL_OFF, "ZG": 1}, last) == {"lawn": False, "drip": False}


def test_without_main_valve_a_pause_reads_closed() -> None:
    valve = {"id": "v", "sections": ["S1", "S2"]}
    last: dict[str, str] = {}
    assert valve_states([valve], {"S1": 1, "S2": 0}, last) == {"v": True}
    assert valve_states([valve], {"S1": 0, "S2": 0}, last) == {"v": False}


def test_unknown_without_sections_or_before_the_first_read() -> None:
    last: dict[str, str] = {}
    assert valve_states([{"id": "v"}], {}, last) == {"v": None}
    assert valve_states([LAWN], {}, last) == {"lawn": None}
