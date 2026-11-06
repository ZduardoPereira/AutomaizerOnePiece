import sqlite3

import pytest

from database.database import Database
from database.models import EventRecord, MatchSummary, MonitorControl, RewardRecord
from database.repositories import (
    EventRepository,
    MatchRepository,
    MonitorControlRepository,
    RewardRepository,
    SquadRepository,
)

STARTED = "2026-11-05T10:00:00"
ENDED = "2026-11-05T10:20:00"


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "pasta" / "monitor.db")
    yield db
    db.close()


def summary(result="VICTORY", **changes):
    values = {
        "ended_at": ENDED,
        "duration_seconds": 1200,
        "result": result,
        "player_status": "ALIVE",
        "squad_status": "SQUAD_ACTIVE",
        "kills": 3,
        "assists": 1,
        "chests_opened": 2,
        "players_left": 1,
    }
    return MatchSummary(**{**values, **changes})


def test_creates_the_folder_and_the_specified_tables(database):
    names = {row["name"] for row in database.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"matches", "squad_members", "rewards", "events", "monitor_control"} <= names


def test_specified_columns_exist(database):
    def columns(table):
        return {row["name"] for row in database.query(f"PRAGMA table_info({table})")}

    assert columns("matches") >= {
        "id", "started_at", "ended_at", "duration_seconds", "result", "player_status", "squad_status", "token_obtained",
    }
    assert columns("squad_members") >= {"id", "match_id", "player_number", "status", "updated_at"}
    assert columns("rewards") >= {"id", "match_id", "reward_type", "status", "confidence", "detected_at", "evidence_path"}
    assert columns("events") >= {"id", "match_id", "event_type", "description", "confidence", "created_at"}
    assert columns("monitor_control") >= {"id", "token_obtained", "shutdown_pending", "monitor_stopped"}


def test_indexes_exist(database):
    names = {row["name"] for row in database.query("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_matches_started_at", "idx_events_match_id", "idx_rewards_match_id"} <= names


def test_foreign_keys_are_enforced(database):
    with pytest.raises(sqlite3.IntegrityError):
        SquadRepository(database).save(999, ["ALIVE"], ENDED)


def test_deleting_a_match_removes_its_squad_and_keeps_its_events(database):
    match_id = MatchRepository(database).create(STARTED)
    SquadRepository(database).save(match_id, ["ALIVE"], ENDED)
    EventRepository(database).add(match_id, EventRecord("MATCH_STARTED", "Partida iniciada", STARTED))

    database.execute("DELETE FROM matches WHERE id = ?", (match_id,))

    assert SquadRepository(database).for_match(match_id) == []
    row = database.query("SELECT match_id FROM events")[0]
    assert row["match_id"] is None


def test_reopening_keeps_the_data(tmp_path):
    path = tmp_path / "monitor.db"
    first = Database(path)
    match_id = MatchRepository(first).create(STARTED)
    first.close()

    second = Database(path)
    assert [match.id for match in MatchRepository(second).recent()] == [match_id]
    second.close()


@pytest.mark.parametrize("content", [b"isto nao e um banco de dados" * 100])
def test_damaged_file_is_kept_and_replaced(tmp_path, content):
    path = tmp_path / "monitor.db"
    path.write_bytes(content)

    database = Database(path)

    assert "danificado" in database.warning
    backups = list(tmp_path.glob("monitor.danificado-*.db"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == content
    assert MonitorControlRepository(database).load() == MonitorControl()
    database.close()


def test_healthy_file_has_no_warning(database):
    assert database.warning is None


def test_connection_can_be_used_from_other_threads(database):
    import threading

    errors = []

    def work():
        try:
            for _ in range(20):
                MatchRepository(database).create(STARTED)
        except sqlite3.Error as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert MatchRepository(database).totals().matches == 80


def test_match_starts_open_and_is_finished_later(database):
    matches = MatchRepository(database)
    match_id = matches.create(STARTED)

    match = matches.recent()[0]
    assert (match.id, match.started_at, match.ended_at, match.result) == (match_id, STARTED, None, None)
    assert not match.token_obtained

    matches.finish(match_id, summary())
    match = matches.recent()[0]
    assert match.ended_at == ENDED
    assert match.duration_seconds == 1200
    assert match.result == "VICTORY"
    assert (match.kills, match.assists, match.chests_opened, match.players_left) == (3, 1, 2, 1)


def test_recent_matches_come_newest_first_and_limited(database):
    matches = MatchRepository(database)
    ids = [matches.create(STARTED) for _ in range(5)]
    assert [match.id for match in matches.recent(limit=2)] == [ids[4], ids[3]]


def test_token_mark_applies_to_a_single_match(database):
    matches = MatchRepository(database)
    first = matches.create(STARTED)
    second = matches.create(STARTED)

    matches.mark_token_obtained(first)

    assert {match.id: match.token_obtained for match in matches.recent()} == {first: True, second: False}


def test_unfinished_matches_are_closed_without_a_result(database):
    matches = MatchRepository(database)
    done = matches.create(STARTED)
    matches.finish(done, summary())
    open_match = matches.create(STARTED)

    assert matches.close_unfinished(ENDED) == 1

    by_id = {match.id: match for match in matches.recent()}
    assert by_id[open_match].result == "UNDETERMINED"
    assert by_id[open_match].ended_at == ENDED
    assert by_id[done].result == "VICTORY"
    assert matches.close_unfinished(ENDED) == 0


def test_totals(database):
    matches = MatchRepository(database)
    assert matches.totals().matches == 0
    matches.finish(matches.create(STARTED), summary("VICTORY", kills=4, assists=2, chests_opened=3))
    matches.finish(matches.create(STARTED), summary("DEFEAT", kills=1, assists=0, chests_opened=None))
    matches.finish(matches.create(STARTED), summary("UNDETERMINED", kills=0, assists=1, chests_opened=2))

    totals = matches.totals()
    assert (totals.matches, totals.victories, totals.defeats) == (3, 1, 1)
    assert (totals.kills, totals.assists, totals.chests_opened) == (5, 3, 5)


def test_squad_is_saved_in_order_and_updated_in_place(database):
    match_id = MatchRepository(database).create(STARTED)
    squad = SquadRepository(database)

    squad.save(match_id, ["ALIVE", "ALIVE"], STARTED)
    squad.save(match_id, ["ALIVE", "ELIMINATED"], ENDED)

    members = squad.for_match(match_id)
    assert [(member.player_number, member.status) for member in members] == [(1, "ALIVE"), (2, "ELIMINATED")]
    assert members[1].updated_at == ENDED


def test_events_belong_to_their_match_and_can_be_outside_of_one(database):
    match_id = MatchRepository(database).create(STARTED)
    events = EventRepository(database)

    events.add(None, EventRecord("MONITORING_STARTED", "Monitoramento iniciado", STARTED))
    events.add(match_id, EventRecord("SCREEN_CHANGED", "Partida", STARTED, 0.9))

    assert events.for_match(match_id) == [EventRecord("SCREEN_CHANGED", "Partida", STARTED, 0.9)]


def test_reward_has_a_single_row_per_type(database):
    rewards = RewardRepository(database)
    assert rewards.load("event_token") is None

    rewards.save(RewardRecord("event_token", "CANDIDATE", 0.6))
    rewards.save(RewardRecord("event_token", "STOPPED", 0.9, ENDED, "token.png", True))

    record = rewards.load("event_token")
    assert (record.status, record.confidence, record.evidence_path) == ("STOPPED", 0.9, "token.png")
    assert record.confirmed_by_user
    assert len(database.query("SELECT id FROM rewards")) == 1

    rewards.delete("event_token")
    assert rewards.load("event_token") is None


def test_reward_keeps_its_link_with_the_match(database):
    match_id = MatchRepository(database).create(STARTED)
    rewards = RewardRepository(database)

    rewards.save(RewardRecord("event_token", "CONFIRMED", match_id=match_id))

    assert rewards.load("event_token").match_id == match_id


def test_monitor_control_has_one_row_and_starts_clear(database):
    control = MonitorControlRepository(database)
    assert control.load() == MonitorControl()

    control.save(MonitorControl(True, True, False))
    assert control.load() == MonitorControl(True, True, False)
    assert len(database.query("SELECT id FROM monitor_control")) == 1
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO monitor_control (id) VALUES (2)")
