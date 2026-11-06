import pytest

from core.config import SquadConfig
from core.event_manager import EventManager, EventType
from core.state_machine import GameState
from recognition.squad_detector import MemberReading, SquadReading
from recognition.squad_detector import MemberStatus as M
from recognition.survival_detector import SquadCondition as C
from recognition.survival_detector import SquadMember, SurvivalTracker, build_status

ALIVE = MemberReading(M.ALIVE, 1.0, 1.0, box=(0, 0, 10, 10))
KNOCKED = MemberReading(M.KNOCKED, 0.7, box=(0, 0, 10, 10))
TIMER = MemberReading(M.ELIMINATED, reboot_seconds=100, box=(0, 0, 10, 10))
CARD = MemberReading(M.ELIMINATED, card_collected=True, box=(0, 0, 10, 10))
HUD = MemberReading(M.ALIVE, 0.8, 0.5, box=(0, 0, 10, 10))
HUD_KNOCKED = MemberReading(M.KNOCKED, 0.9, 0.0, box=(0, 0, 10, 10))


def in_match(*rows, hud=HUD):
    return SquadReading(tuple(rows), hud)


def statuses(status):
    return [member.status for member in status.teammates]


def test_full_squad_is_active():
    status = build_status(in_match(ALIVE, ALIVE, ALIVE), GameState.IN_MATCH, 0)
    assert status.condition is C.SQUAD_ACTIVE
    assert status.active_count == 4
    assert len(status.members) == 4
    assert status.self_member.health == 0.8


def test_knocked_teammate_still_counts_as_active():
    status = build_status(in_match(ALIVE, ALIVE, KNOCKED), GameState.IN_MATCH, 0)
    assert status.condition is C.SQUAD_ACTIVE
    assert status.active_count == 4
    assert status.knocked_teammates == 1


def test_all_teammates_knocked_means_last_standing():
    status = build_status(in_match(KNOCKED, KNOCKED, KNOCKED), GameState.IN_MATCH, 0)
    assert status.condition is C.LAST_SQUAD_MEMBER_STANDING
    assert status.active_count == 4


@pytest.mark.parametrize(
    "rows",
    [(TIMER, TIMER, KNOCKED), (CARD, KNOCKED, KNOCKED), (KNOCKED,)],
    ids=["um_caido_dois_eliminados", "dois_caidos_um_eliminado", "um_caido_dois_fora"],
)
def test_knocked_mixed_with_eliminated_means_last_standing(rows):
    status = build_status(in_match(*rows), GameState.IN_MATCH, 3)
    assert status.condition is C.LAST_SQUAD_MEMBER_STANDING


def test_one_teammate_standing_is_not_last_standing():
    status = build_status(in_match(ALIVE, KNOCKED, KNOCKED), GameState.IN_MATCH, 0)
    assert status.condition is C.SQUAD_ACTIVE


def test_player_also_knocked_is_not_last_standing():
    status = build_status(in_match(KNOCKED, KNOCKED, KNOCKED, hud=HUD_KNOCKED), GameState.IN_MATCH, 0)
    assert status.condition is C.SQUAD_ACTIVE


def test_eliminated_player_with_knocked_teammates_is_not_last_standing():
    status = build_status(SquadReading((TIMER, KNOCKED, KNOCKED), HUD_KNOCKED), GameState.ELIMINATED, 3)
    assert status.condition is C.SQUAD_ACTIVE


def test_last_member_alive_with_all_teammates_eliminated():
    status = build_status(in_match(TIMER, CARD, TIMER), GameState.IN_MATCH, 0)
    assert status.condition is C.LAST_SQUAD_MEMBER_ALIVE
    assert status.active_count == 1


def test_teammates_that_left_the_list_are_eliminated():
    status = build_status(in_match(TIMER), GameState.IN_MATCH, 3)
    assert status.condition is C.LAST_SQUAD_MEMBER_ALIVE
    assert len(status.members) == 4
    assert [member.left_match for member in status.teammates] == [False, True, True]


def test_no_rows_with_teammates_seen_means_last_member_alive():
    status = build_status(in_match(), GameState.IN_MATCH, 3)
    assert status.condition is C.LAST_SQUAD_MEMBER_ALIVE


def test_no_rows_never_seen_is_a_solo_match():
    status = build_status(in_match(), GameState.IN_MATCH, 0)
    assert status.condition is C.NO_SQUAD
    assert len(status.members) == 1


def test_hidden_hud_keeps_the_player_alive():
    status = build_status(SquadReading((ALIVE,), None), GameState.IN_MATCH, 0)
    assert status.self_member.status is M.ALIVE
    assert status.self_member.health is None


def test_killcam_has_all_teammates_in_the_list():
    status = build_status(SquadReading((ALIVE, ALIVE, ALIVE), None), GameState.ELIMINATED, 3)
    assert status.self_member.status is M.ELIMINATED
    assert statuses(status) == [M.ALIVE] * 3
    assert status.condition is C.SQUAD_ACTIVE


def test_spectating_moves_player_to_the_list_and_teammate_to_the_hud():
    status = build_status(SquadReading((TIMER, ALIVE, ALIVE), HUD), GameState.ELIMINATED, 3)

    assert len(status.members) == 4
    assert status.self_member.status is M.ELIMINATED
    assert status.self_member.reboot_seconds == 100
    assert statuses(status) == [M.ALIVE] * 3
    assert status.condition is C.SQUAD_ACTIVE


def test_whole_squad_out_while_eliminated():
    status = build_status(SquadReading((TIMER,), None), GameState.ELIMINATED, 3)
    assert status.condition is C.SQUAD_ELIMINATED
    assert status.active_count == 0


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (SquadMember(False, M.KNOCKED), True),
        (SquadMember(False, M.ELIMINATED, reboot_seconds=30), True),
        (SquadMember(False, M.ELIMINATED, card_collected=True), True),
        (SquadMember(False, M.ALIVE, health=0.2), True),
        (SquadMember(False, M.ALIVE, health=0.35), True),
        (SquadMember(False, M.ALIVE, health=0.36), False),
        (SquadMember(False, M.ALIVE), False),
        (SquadMember(False, M.ELIMINATED, left_match=True), False),
        (SquadMember(True, M.KNOCKED), False),
    ],
)
def test_needs_help(member, expected):
    assert member.needs_help(35) is expected


@pytest.fixture
def harness():
    events = EventManager()
    config = SquadConfig(confirm_frames=2, missing_row_confirm_frames=4)
    return SurvivalTracker(config, events), events


def feed(tracker, reading, times=2, state=GameState.IN_MATCH):
    for _ in range(times):
        tracker.observe(reading, state)


def messages(events, event_type):
    return [event.message for event in events.recent(50, {event_type})]


def test_starts_unknown_and_confirms_before_changing(harness):
    tracker, _events = harness
    assert tracker.status.condition is C.UNKNOWN

    tracker.observe(in_match(ALIVE, ALIVE, ALIVE), GameState.IN_MATCH)
    assert tracker.status.condition is C.UNKNOWN

    tracker.observe(in_match(ALIVE, ALIVE, ALIVE), GameState.IN_MATCH)
    assert tracker.status.condition is C.SQUAD_ACTIVE


def test_invisible_hud_keeps_last_status(harness):
    tracker, _events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))
    feed(tracker, SquadReading(), times=20)
    assert tracker.status.active_count == 4


def test_flicker_does_not_announce(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    for _ in range(5):
        tracker.observe(in_match(ALIVE, ALIVE, KNOCKED), GameState.IN_MATCH)
        tracker.observe(in_match(ALIVE, ALIVE, ALIVE), GameState.IN_MATCH)

    assert tracker.status.knocked_teammates == 0
    assert not messages(events, EventType.TEAMMATE_KNOCKED)


def test_health_updates_without_confirmation(harness):
    tracker, _events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    tracker.observe(in_match(MemberReading(M.ALIVE, 0.3, 0.0), ALIVE, ALIVE), GameState.IN_MATCH)

    assert tracker.status.teammates[0].health == 0.3


def test_vanished_row_needs_more_confirmation(harness):
    tracker, _events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    feed(tracker, in_match(ALIVE, ALIVE), times=3)
    assert tracker.status.visible_teammates == 3
    assert tracker.status.active_count == 4

    tracker.observe(in_match(ALIVE, ALIVE), GameState.IN_MATCH)
    assert tracker.status.visible_teammates == 2
    assert tracker.status.active_count == 3
    assert len(tracker.status.members) == 4


def test_teammate_knocked_and_recovered_events(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    feed(tracker, in_match(ALIVE, ALIVE, KNOCKED))
    assert messages(events, EventType.TEAMMATE_KNOCKED) == ["Colega caído: precisa de ajuda"]

    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))
    assert len(messages(events, EventType.TEAMMATE_RECOVERED)) == 1


def test_knocked_then_eliminated_event_mentions_the_card(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, KNOCKED))

    feed(tracker, in_match(ALIVE, ALIVE, TIMER))

    assert messages(events, EventType.TEAMMATE_ELIMINATED) == ["Colega eliminado: cartão no chão por 100 s"]
    assert not messages(events, EventType.TEAMMATE_RECOVERED)


def test_card_collected_and_reboot_events(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, TIMER))

    feed(tracker, in_match(ALIVE, ALIVE, CARD))
    assert len(messages(events, EventType.REBOOT_CARD_COLLECTED)) == 1

    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))
    assert messages(events, EventType.TEAMMATE_RETURNED) == ["Colega de volta à partida"]


def test_self_knocked_event(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    feed(tracker, in_match(ALIVE, ALIVE, ALIVE, hud=HUD_KNOCKED))

    assert messages(events, EventType.SELF_KNOCKED) == ["Você caiu: precisa de ajuda"]
    assert tracker.status.active_count == 4


def test_last_standing_is_announced_and_ends_when_someone_gets_up(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    feed(tracker, in_match(KNOCKED, KNOCKED, KNOCKED))
    assert tracker.status.condition is C.LAST_SQUAD_MEMBER_STANDING
    assert "Você é o último de pé: os demais colegas estão fora de combate" in messages(
        events, EventType.SQUAD_CONDITION_CHANGED
    )

    feed(tracker, in_match(ALIVE, KNOCKED, KNOCKED))
    assert tracker.status.condition is C.SQUAD_ACTIVE


def test_last_standing_then_last_alive_as_the_knocked_are_eliminated(harness):
    tracker, _events = harness
    feed(tracker, in_match(KNOCKED, KNOCKED, KNOCKED))
    assert tracker.status.condition is C.LAST_SQUAD_MEMBER_STANDING

    feed(tracker, in_match(TIMER, TIMER, TIMER))
    assert tracker.status.condition is C.LAST_SQUAD_MEMBER_ALIVE


def test_several_teammates_knocked_at_once_are_counted(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))
    feed(tracker, in_match(ALIVE, KNOCKED, KNOCKED))
    assert messages(events, EventType.TEAMMATE_KNOCKED) == ["2 colegas caídos: precisam de ajuda"]


def test_entering_spectator_mode_does_not_fake_a_teammate_leaving(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))
    feed(tracker, SquadReading((ALIVE, ALIVE, ALIVE), None), state=GameState.ELIMINATED)

    # O usuário já entrou na lista, mas o HUD do colega observado ainda não apareceu.
    feed(tracker, SquadReading((TIMER, ALIVE, ALIVE), None), times=20, state=GameState.ELIMINATED)
    feed(tracker, SquadReading((TIMER, ALIVE, ALIVE), HUD), state=GameState.ELIMINATED)

    assert tracker.status.active_teammates == 3
    assert not messages(events, EventType.TEAMMATE_ELIMINATED)
    assert not messages(events, EventType.TEAMMATE_RETURNED)


def test_last_member_alive_is_announced_once(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    feed(tracker, in_match(TIMER, TIMER, TIMER))
    feed(tracker, in_match(TIMER, TIMER, TIMER))

    assert tracker.status.condition is C.LAST_SQUAD_MEMBER_ALIVE
    announced = messages(events, EventType.SQUAD_CONDITION_CHANGED)
    assert announced.count("Você é o último integrante vivo do esquadrão") == 1


def test_first_reading_does_not_announce_teammate_changes(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, KNOCKED))
    assert not messages(events, EventType.TEAMMATE_KNOCKED)
    assert messages(events, EventType.SQUAD_CONDITION_CHANGED) == ["Esquadrão ativo: 4 de 4"]


def test_solo_match_is_announced_as_no_squad(harness):
    tracker, events = harness
    feed(tracker, in_match())
    assert tracker.status.condition is C.NO_SQUAD
    assert messages(events, EventType.SQUAD_CONDITION_CHANGED) == ["Partida sem esquadrão"]


def test_defeat_eliminates_a_squad(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    tracker.mark_defeat()

    assert tracker.status.condition is C.SQUAD_ELIMINATED
    assert tracker.status.active_count == 0
    assert len(tracker.status.members) == 4
    assert "Esquadrão eliminado" in messages(events, EventType.SQUAD_CONDITION_CHANGED)


def test_defeat_in_solo_is_not_a_squad_elimination(harness):
    tracker, _events = harness
    feed(tracker, in_match())

    tracker.mark_defeat()

    assert tracker.status.condition is C.NO_SQUAD
    assert tracker.status.self_member.status is M.ELIMINATED


def test_reset_forgets_the_squad_for_the_next_match(harness):
    tracker, _events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    tracker.reset()

    assert tracker.status.condition is C.UNKNOWN
    feed(tracker, in_match())
    assert tracker.status.condition is C.NO_SQUAD


def test_spectating_after_elimination_keeps_counts_stable(harness):
    tracker, events = harness
    feed(tracker, in_match(ALIVE, ALIVE, ALIVE))

    feed(tracker, SquadReading((ALIVE, ALIVE, ALIVE), None), state=GameState.ELIMINATED)
    assert tracker.status.active_count == 3
    feed(tracker, SquadReading((TIMER, ALIVE, ALIVE), HUD), state=GameState.ELIMINATED)

    assert tracker.status.active_count == 3
    assert len(tracker.status.members) == 4
    assert not messages(events, EventType.TEAMMATE_ELIMINATED)
