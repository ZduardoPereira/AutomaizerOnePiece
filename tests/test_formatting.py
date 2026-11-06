from pathlib import Path

import pytest

from assistant.guided_state_machine import NavigationButton
from assistant.navigation_controller import AssistantStatus
from assistant.user_confirmation import ConfirmationState
from core.capture import CaptureSnapshot, CaptureState
from database.models import MatchRecord
from recognition.reward_detector import RewardState, RewardStatus
from recognition.squad_detector import MemberStatus as M
from recognition.survival_detector import SquadCondition as C
from recognition.survival_detector import SquadMember, SquadStatus
from ui.formatting import (
    CAPTURE_STATE_LABELS,
    MEMBER_STATUS_LABELS,
    SQUAD_CONDITION_LABELS,
    alive_count_text,
    match_numbers_text,
    match_result_text,
    assistance_state_text,
    assistant_action_text,
    assistant_button_text,
    assistant_confirmation_text,
    capture_hint,
    fit_size,
    format_duration,
    last_survivor_text,
    member_details_text,
    member_status_text,
    reward_closing_text,
    reward_found_text,
    squad_help_text,
)


@pytest.mark.parametrize(("seconds", "text"), [(0, "00:00:00"), (59.9, "00:00:59"), (3661, "01:01:01"), (-5, "00:00:00")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    ("source", "area", "expected"),
    [
        ((1920, 1080), (960, 960), (960, 540)),
        ((1920, 1080), (1000, 270), (480, 270)),
        ((640, 480), (1920, 1080), (640, 480)),
        ((1000, 1), (10, 10), (10, 1)),
    ],
)
def test_fit_size(source, area, expected):
    assert fit_size(*source, *area) == expected


def test_every_capture_state_has_a_label():
    assert set(CAPTURE_STATE_LABELS) == set(CaptureState)


def test_capture_hint():
    assert capture_hint(CaptureSnapshot(state=CaptureState.CAPTURING)) == ""
    assert "preto" in capture_hint(CaptureSnapshot(state=CaptureState.CAPTURING, is_black=True))
    assert "primeiro plano" in capture_hint(CaptureSnapshot(state=CaptureState.BACKGROUND))
    assert "disco" in capture_hint(CaptureSnapshot(state=CaptureState.ERROR, error="disco"))


YOU = SquadMember(True, M.ALIVE, health=1.0, shield=0.5)


def test_every_squad_label_exists():
    assert set(SQUAD_CONDITION_LABELS) == set(C)
    assert set(MEMBER_STATUS_LABELS) == set(M)


def test_alive_count():
    assert alive_count_text(SquadStatus()) == ("—", "")
    assert alive_count_text(SquadStatus(C.NO_SQUAD, (YOU,))) == ("—", "Partida sem esquadrão")
    squad = SquadStatus(C.SQUAD_ACTIVE, (YOU, SquadMember(False, M.KNOCKED), SquadMember(False, M.ELIMINATED, reboot_seconds=5)))
    assert alive_count_text(squad) == ("2 de 3", "1 caído")


def test_last_survivor_refers_to_the_squad_only():
    assert last_survivor_text(SquadStatus()) == ("—", "")
    assert last_survivor_text(SquadStatus(C.NO_SQUAD, (YOU,)))[0] == "—"
    assert last_survivor_text(SquadStatus(C.SQUAD_ACTIVE, (YOU,)))[0] == "Não"
    value, hint = last_survivor_text(SquadStatus(C.LAST_SQUAD_MEMBER_ALIVE, (YOU,)))
    assert value == "Sim"
    assert "não da partida" in hint


def test_help_text():
    calm = SquadStatus(C.SQUAD_ACTIVE, (YOU, SquadMember(False, M.ALIVE, health=1.0)))
    assert squad_help_text(calm, 35) == ""
    one = SquadStatus(C.SQUAD_ACTIVE, (YOU, SquadMember(False, M.KNOCKED)))
    assert squad_help_text(one, 35) == "1 colega precisa de ajuda"
    two = SquadStatus(C.SQUAD_ACTIVE, (YOU, SquadMember(False, M.KNOCKED), SquadMember(False, M.ALIVE, health=0.1)))
    assert squad_help_text(two, 35) == "2 colegas precisam de ajuda"


@pytest.mark.parametrize(
    ("member", "status", "details"),
    [
        (SquadMember(False, M.ALIVE, health=0.66, shield=0.2), "Vivo", "Vida 66% • Escudo 20%"),
        (SquadMember(False, M.ALIVE, health=0.2, shield=0.0), "Vivo", "Vida baixa 20% • Escudo 0%"),
        (SquadMember(True, M.ALIVE, health=0.2, shield=0.0), "Vivo", "Vida 20% • Escudo 0%"),
        (SquadMember(True, M.ALIVE), "Vivo", ""),
        (SquadMember(False, M.KNOCKED, health=0.5), "Caído", "Precisa de ajuda"),
        (SquadMember(False, M.ELIMINATED, reboot_seconds=113), "Eliminado", "Cartão no chão: 113 s"),
        (SquadMember(False, M.ELIMINATED, card_collected=True), "Eliminado", "Cartão coletado: leve à van"),
        (SquadMember(False, M.ELIMINATED, left_match=True), "Fora da partida", ""),
    ],
)
def test_member_texts(member, status, details):
    assert member_status_text(member) == status
    assert member_details_text(member, 35) == details


def test_last_standing_counts_as_last_survivor():
    value, hint = last_survivor_text(SquadStatus(C.LAST_SQUAD_MEMBER_STANDING, (YOU,)))
    assert value == "Sim"
    assert "caídos" in hint


@pytest.mark.parametrize(
    ("status", "found", "hint", "closing"),
    [
        (RewardStatus(), "Não encontrado", "", "—"),
        (RewardStatus(RewardState.CANDIDATE), "Confirmando...", "Possível token na tela", "—"),
        (RewardStatus(candidate_path=Path("c.png")), "A confirmar", "Confira em Recompensas", "—"),
        (RewardStatus(RewardState.WAITING_FOR_MATCH_END, confidence=0.91), "Obtido", "Confiança 91%", "Aguardando o fim da partida"),
        (RewardStatus(RewardState.STOPPED, confirmed_by_user=True), "Obtido", "Confirmado por você", "Partida encerrada"),
    ],
)
def test_reward_texts(status, found, hint, closing):
    assert reward_found_text(status) == (found, hint)
    assert reward_closing_text(status) == closing
    assert reward_closing_text(status, finished=True) == "Monitoramento finalizado"


def test_assistant_texts():
    off = AssistantStatus()
    assert assistant_action_text(off) == "Assistência desligada"
    assert assistant_button_text(off) == ("—", "")
    assert assistant_confirmation_text(off) == "—"

    assert assistant_action_text(AssistantStatus(active=True)) == "Aguardando o momento de agir"

    waiting = AssistantStatus(
        active=True,
        next_action="Clique em Jogar novamente",
        button=NavigationButton.PLAY_AGAIN,
        button_score=0.97,
        confirmation=ConfirmationState.WAITING,
    )
    assert assistant_action_text(waiting) == "Clique em Jogar novamente"
    assert assistant_button_text(waiting) == ("Jogar novamente", "Confiança 97%")
    assert assistant_confirmation_text(waiting) == "Aguardando seu clique"
    assert assistant_confirmation_text(AssistantStatus(confirmation=ConfirmationState.CONFIRMED)) == "Clique confirmado"


def match_record(**changes):
    values = {
        "id": 1,
        "started_at": "2026-11-05T10:00:00",
        "ended_at": "2026-11-05T10:20:00",
        "duration_seconds": 1200,
        "result": "VICTORY",
        "player_status": "ALIVE",
        "squad_status": "SQUAD_ACTIVE",
        "token_obtained": False,
        "kills": 3,
        "assists": 1,
        "chests_opened": None,
        "players_left": None,
    }
    return MatchRecord(**{**values, **changes})


@pytest.mark.parametrize(
    ("result", "text"),
    [("VICTORY", "Vitória"), ("DEFEAT", "Derrota"), ("UNDETERMINED", "Sem resultado"), (None, "Em andamento")],
)
def test_match_result_text(result, text):
    assert match_result_text(result) == text


def test_match_numbers_hide_what_was_not_seen():
    assert match_numbers_text(match_record()) == "3 abates  •  1 assist."


def test_match_numbers_show_chests_and_players_when_known():
    text = match_numbers_text(match_record(chests_opened=5, players_left=12))
    assert text == "3 abates  •  1 assist.  •  5 baús  •  12 restantes"


def test_assistance_state_says_when_it_cannot_act():
    on = AssistantStatus(active=True)
    off = AssistantStatus()

    assert assistance_state_text(on, True, False) == "Ligada: mouse e notificação"
    assert "parado" in assistance_state_text(on, False, False)
    assert assistance_state_text(off, True, False) == "Desligada: o app só observa"
    assert assistance_state_text(off, False, True) == "Token obtido: monitoramento finalizado"
