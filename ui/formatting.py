"""Formatação de valores exibidos na interface, sem dependência de widgets."""

from __future__ import annotations

from assistant.guided_state_machine import BUTTON_LABELS
from assistant.navigation_controller import AssistantStatus
from assistant.user_confirmation import ConfirmationState
from core.capture import CaptureSnapshot, CaptureState
from core.state_machine import GAME_STATE_LABELS, GameState
from database.models import MatchRecord
from recognition.reward_detector import RewardState, RewardStatus
from recognition.squad_detector import MemberStatus
from recognition.survival_detector import SquadCondition, SquadMember, SquadStatus

CAPTURE_STATE_LABELS = {
    CaptureState.STOPPED: "Monitoramento parado",
    CaptureState.SEARCHING: "Fortnite não encontrado",
    CaptureState.MINIMIZED: "Fortnite minimizado",
    CaptureState.BACKGROUND: "Fortnite em segundo plano",
    CaptureState.CAPTURING: "Capturando",
    CaptureState.ERROR: "Erro na captura",
}

SQUAD_CONDITION_LABELS = {
    SquadCondition.UNKNOWN: "—",
    SquadCondition.NO_SQUAD: "Sem esquadrão",
    SquadCondition.SQUAD_ACTIVE: "Ativo",
    SquadCondition.LAST_SQUAD_MEMBER_STANDING: "Só você de pé",
    SquadCondition.LAST_SQUAD_MEMBER_ALIVE: "Só você restou",
    SquadCondition.SQUAD_ELIMINATED: "Eliminado",
}

MEMBER_STATUS_LABELS = {
    MemberStatus.ALIVE: "Vivo",
    MemberStatus.KNOCKED: "Caído",
    MemberStatus.ELIMINATED: "Eliminado",
    MemberStatus.UNKNOWN: "Desconhecido",
}


MEMBER_STATUS_BY_VALUE = {status.value: label for status, label in MEMBER_STATUS_LABELS.items()}
_MATCH_RESULT_LABELS = {"VICTORY": "Vitória", "DEFEAT": "Derrota", "UNDETERMINED": "Sem resultado"}


def fortnite_status(capture: CaptureSnapshot, state: GameState) -> str:
    """Mostra a tela reconhecida; fora da captura, o motivo de não haver reconhecimento."""
    if capture.state is CaptureState.CAPTURING:
        return GAME_STATE_LABELS[state]
    return CAPTURE_STATE_LABELS[capture.state]


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    """Maior tamanho que cabe na área mantendo a proporção; nunca amplia."""
    scale = min(max_width / width, max_height / height, 1.0)
    return max(1, int(width * scale)), max(1, int(height * scale))


def capture_hint(snapshot: CaptureSnapshot) -> str:
    """Orientação ao usuário sobre o estado atual da captura; vazia quando não há o que fazer."""
    if snapshot.state is CaptureState.ERROR:
        return f"Falha ao capturar: {snapshot.error}"
    if snapshot.state is CaptureState.BACKGROUND:
        return "A captura pausa até o jogo voltar ao primeiro plano. Isso pode ser alterado em Configurações."
    if snapshot.state is CaptureState.CAPTURING and snapshot.is_black:
        return "Quadro preto. Se persistir fora de telas de transição, use o modo Tela cheia em janela no jogo."
    return ""


def squad_help_text(squad: SquadStatus, low_health_percent: int) -> str:
    count = sum(member.needs_help(low_health_percent) for member in squad.teammates)
    if count == 0:
        return ""
    return "1 colega precisa de ajuda" if count == 1 else f"{count} colegas precisam de ajuda"


def alive_count_text(squad: SquadStatus) -> tuple[str, str]:
    """Retorna o total de integrantes vivos e, à parte, quantos deles estão caídos."""
    if not squad.members:
        return "—", ""
    if squad.condition is SquadCondition.NO_SQUAD:
        return "—", "Partida sem esquadrão"
    knocked = sum(member.status is MemberStatus.KNOCKED for member in squad.members)
    hint = "" if knocked == 0 else "1 caído" if knocked == 1 else f"{knocked} caídos"
    return f"{squad.active_count} de {len(squad.members)}", hint


def last_survivor_text(squad: SquadStatus) -> tuple[str, str]:
    if not squad.members:
        return "—", ""
    if squad.condition is SquadCondition.NO_SQUAD:
        return "—", "Partida sem esquadrão"
    if squad.condition is SquadCondition.LAST_SQUAD_MEMBER_STANDING:
        return "Sim", "De pé; colegas caídos podem voltar"
    if squad.condition is SquadCondition.LAST_SQUAD_MEMBER_ALIVE:
        return "Sim", "Do esquadrão, não da partida"
    return "Não", "Do esquadrão, não da partida"


def member_status_text(member: SquadMember) -> str:
    return "Fora da partida" if member.left_match else MEMBER_STATUS_LABELS[member.status]


def member_details_text(member: SquadMember, low_health_percent: int) -> str:
    if member.left_match:
        return ""
    if member.status is MemberStatus.KNOCKED:
        return "Precisa de ajuda"
    if member.status is MemberStatus.ELIMINATED:
        if member.reboot_seconds is not None:
            return f"Cartão no chão: {member.reboot_seconds} s"
        return "Cartão coletado: leve à van" if member.card_collected else ""
    if member.health is None:
        return ""
    health = f"{member.health * 100:.0f}%"
    shield = f"{(member.shield or 0.0) * 100:.0f}%"
    prefix = "Vida baixa" if member.needs_help(low_health_percent) else "Vida"
    return f"{prefix} {health} • Escudo {shield}"


def reward_found_text(status: RewardStatus) -> tuple[str, str]:
    """Retorna se o token foi obtido e, à parte, como isso foi decidido."""
    if status.token_obtained:
        origin = "Confirmado por você" if status.confirmed_by_user else f"Confiança {status.confidence:.0%}"
        return "Obtido", origin
    if status.state is RewardState.CANDIDATE:
        return "Confirmando...", "Possível token na tela"
    if status.candidate_path is not None:
        return "A confirmar", "Confira em Recompensas"
    return "Não encontrado", ""


def reward_closing_text(status: RewardStatus, finished: bool = False) -> str:
    if finished:
        return "Monitoramento finalizado"
    if status.state in (RewardState.CONFIRMED, RewardState.WAITING_FOR_MATCH_END):
        return "Aguardando o fim da partida"
    if status.state is RewardState.STOPPED:
        return "Partida encerrada"
    return "—"


def assistance_state_text(status: AssistantStatus, monitoring: bool, finished: bool) -> str:
    if finished:
        return "Token obtido: monitoramento finalizado"
    if not status.active:
        return "Desligada: o app só observa"
    if not monitoring:
        return "Ligada, mas o monitoramento está parado"
    return "Ligada: mouse e notificação"


def assistant_action_text(status: AssistantStatus) -> str:
    if not status.active:
        return "Assistência desligada"
    return status.next_action or "Aguardando o momento de agir"


def assistant_button_text(status: AssistantStatus) -> tuple[str, str]:
    if status.button is None:
        return "—", ""
    return BUTTON_LABELS[status.button], f"Confiança {status.button_score:.0%}"


def assistant_confirmation_text(status: AssistantStatus) -> str:
    if status.confirmation is ConfirmationState.WAITING:
        return "Aguardando seu clique"
    if status.confirmation is ConfirmationState.CONFIRMED:
        return "Clique confirmado"
    return "—"


def match_result_text(result: str | None) -> str:
    return _MATCH_RESULT_LABELS.get(result, "Em andamento" if result is None else result)


def match_numbers_text(match: MatchRecord) -> str:
    """Resume os números de uma partida; baús só aparecem se a missão estava visível."""
    parts = [f"{match.kills} abates", f"{match.assists} assist."]
    if match.chests_opened is not None:
        parts.append(f"{match.chests_opened} baús")
    if match.players_left is not None:
        parts.append(f"{match.players_left} restantes")
    return "  •  ".join(parts)
