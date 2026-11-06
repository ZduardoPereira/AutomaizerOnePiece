from core.state_machine import GameState, StateMachine


def make_machine(confirm_frames=2, grace=8.0):
    return StateMachine(confirm_frames, grace)


def test_starts_unknown_and_requires_consecutive_frames():
    machine = make_machine(confirm_frames=3)
    assert machine.state is GameState.UNKNOWN

    assert machine.observe(GameState.LOBBY, 0.9, 0.0) is None
    assert machine.observe(GameState.LOBBY, 0.9, 0.5) is None
    transition = machine.observe(GameState.LOBBY, 0.92, 1.0)

    assert transition.previous is GameState.UNKNOWN
    assert transition.current is GameState.LOBBY
    assert transition.confidence == 0.92
    assert machine.state is GameState.LOBBY


def test_flicker_does_not_change_state():
    machine = make_machine(confirm_frames=2)
    machine.observe(GameState.IN_MATCH, 0.9, 0.0)
    machine.observe(GameState.IN_MATCH, 0.9, 0.5)

    assert machine.observe(GameState.VICTORY, 0.8, 1.0) is None
    assert machine.observe(GameState.IN_MATCH, 0.9, 1.5) is None
    assert machine.observe(GameState.VICTORY, 0.8, 2.0) is None
    assert machine.state is GameState.IN_MATCH


def test_different_candidates_do_not_add_up():
    machine = make_machine(confirm_frames=2)
    assert machine.observe(GameState.LOBBY, 0.9, 0.0) is None
    assert machine.observe(GameState.LOADING, 0.9, 0.5) is None
    assert machine.state is GameState.UNKNOWN


def test_unknown_frames_are_tolerated_during_grace_period():
    machine = make_machine(confirm_frames=1, grace=8.0)
    machine.observe(GameState.IN_MATCH, 0.9, 100.0)

    assert machine.observe(GameState.UNKNOWN, 0.3, 104.0) is None
    assert machine.state is GameState.IN_MATCH

    transition = machine.observe(GameState.UNKNOWN, 0.3, 108.0)
    assert transition.current is GameState.UNKNOWN
    assert machine.state is GameState.UNKNOWN


def test_seeing_the_state_again_restarts_grace_period():
    machine = make_machine(confirm_frames=1, grace=8.0)
    machine.observe(GameState.IN_MATCH, 0.9, 0.0)
    machine.observe(GameState.UNKNOWN, 0.3, 6.0)
    machine.observe(GameState.IN_MATCH, 0.9, 7.0)

    assert machine.observe(GameState.UNKNOWN, 0.3, 14.0) is None
    assert machine.state is GameState.IN_MATCH


def test_unknown_frame_interrupts_pending_confirmation():
    machine = make_machine(confirm_frames=2)
    machine.observe(GameState.LOBBY, 0.9, 0.0)
    machine.observe(GameState.UNKNOWN, 0.2, 0.5)
    assert machine.observe(GameState.LOBBY, 0.9, 1.0) is None
    assert machine.state is GameState.UNKNOWN


def test_reset_returns_to_unknown_once():
    machine = make_machine(confirm_frames=1)
    machine.observe(GameState.LOBBY, 0.9, 0.0)

    transition = machine.reset()
    assert transition.previous is GameState.LOBBY
    assert transition.current is GameState.UNKNOWN
    assert machine.reset() is None
