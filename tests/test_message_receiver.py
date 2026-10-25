import pytest

from app.message_receiver import MessageReceiver


def test_message_receiver_rejects_duplicate_event_id():
    """The same event ID must not be accepted twice."""

    receiver = MessageReceiver()

    assert receiver.accept_event("J-EVT-000184") is True

    assert receiver.accept_event("J-EVT-000184") is False


def test_message_receiver_accepts_different_event_ids():
    """Different event IDs must be accepted independently."""

    receiver = MessageReceiver()

    assert receiver.accept_event("J-EVT-000184") is True
    assert receiver.accept_event("J-EVT-000185") is True
    assert receiver.accept_event("J-EVT-000186") is True


def test_message_receiver_remembers_events_after_restart(tmp_path):
    """Previously accepted events must remain recorded after restart."""

    database_path = tmp_path / "receiver.db"

    receiver1 = MessageReceiver(database_path=database_path)

    assert receiver1.accept_event("J-EVT-000184") is True

    # Simulate restarting FundX.
    receiver2 = MessageReceiver(database_path=database_path)

    assert receiver2.accept_event("J-EVT-000184") is False


def test_duplicate_event_across_receiver_instances(tmp_path):
    """Two receivers sharing a database must reject duplicate events."""

    database_path = tmp_path / "shared_receiver.db"

    receiver1 = MessageReceiver(database_path=database_path)
    receiver2 = MessageReceiver(database_path=database_path)

    assert receiver1.accept_event("J-EVT-000184") is True

    assert receiver2.accept_event("J-EVT-000184") is False

    assert receiver2.accept_event("J-EVT-000185") is True


def test_simultaneous_duplicate_event_across_receivers(tmp_path):
    """Only one receiver may accept the same event concurrently."""

    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "concurrent_receiver.db"

    barrier = Barrier(2)

    def submit_event():
        # Each thread creates its own SQLite connection.
        receiver = MessageReceiver(database_path=database_path)

        # Both threads attempt the same event simultaneously.
        barrier.wait()

        return receiver.accept_event("J-EVT-000184")

    with ThreadPoolExecutor(max_workers=2) as executor:
        future1 = executor.submit(submit_event)
        future2 = executor.submit(submit_event)

        results = [future1.result(), future2.result()]

    assert sorted(results) == [False, True]


def test_message_receiver_rejects_empty_event_id():
    """The receiver must reject an empty event ID."""

    with pytest.raises(ValueError, match="event_id"):
        receiver = MessageReceiver()
        receiver.accept_event("")


def test_message_receiver_rejects_whitespace_event_id():
    """The receiver must reject event IDs containing only whitespace."""

    receiver = MessageReceiver()

    with pytest.raises(ValueError, match="event_id"):
        receiver.accept_event("   ")


def test_message_receiver_rejects_non_string_event_id():
    """The receiver must reject event IDs that are not strings."""

    receiver = MessageReceiver()

    with pytest.raises(ValueError, match="event_id"):
        receiver.accept_event(12345)


def test_message_receiver_accepts_first_sequence_number(tmp_path):
    """The first message in a new session must be accepted."""

    database_path = tmp_path / "sequence_receiver.db"

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence(
        session_id="FUNDX-20261025-01",
        sequence_number=1,
    ) is True


def test_message_receiver_accepts_next_sequence_number(tmp_path):
    """Accept sequence 2 after sequence 1 in the same session."""
    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence(
        session_id="FUNDX-20261025-01",
        sequence_number=1,
    ) is True

    assert receiver.accept_sequence(
        session_id="FUNDX-20261025-01",
        sequence_number=2,
    ) is True


def test_message_receiver_rejects_new_session_starting_above_one(tmp_path):
    """A new session must begin with sequence number 1."""
    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence(
        session_id="FUNDX-NEW-SESSION",
        sequence_number=5,
    ) is False

    assert receiver.accept_sequence(
        session_id="FUNDX-NEW-SESSION",
        sequence_number=1,
    ) is True


def test_message_receiver_rejects_duplicate_sequence_number(tmp_path):
    """A sequence number can be accepted only once per session."""
    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence(
        session_id="FUNDX-DUPLICATE-TEST",
        sequence_number=1,
    ) is True

    assert receiver.accept_sequence(
        session_id="FUNDX-DUPLICATE-TEST",
        sequence_number=2,
    ) is True

    assert receiver.accept_sequence(
        session_id="FUNDX-DUPLICATE-TEST",
        sequence_number=2,
    ) is False

    assert receiver.accept_sequence(
        session_id="FUNDX-DUPLICATE-TEST",
        sequence_number=3,
    ) is True


def test_message_receiver_rejects_sequence_gap(tmp_path):
    """A missing sequence number prevents advancing the counter."""
    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    session_id = "FUNDX-GAP-TEST"

    assert receiver.accept_sequence(session_id, 1) is True
    assert receiver.accept_sequence(session_id, 2) is True

    # Sequence 3 is missing.
    assert receiver.accept_sequence(session_id, 4) is False

    # The missing sequence arrives, but recovery is still required.
    assert receiver.accept_sequence(session_id, 3) is False

    # Sequence 4 must also remain blocked.
    assert receiver.accept_sequence(session_id, 4) is False

    # The gap warning remains recorded.
    assert receiver.has_sequence_gap(session_id) is True


def test_message_receiver_remembers_sequence_after_restart(tmp_path):
    """Sequence tracking must survive a receiver restart."""
    database_path = tmp_path / "sequence_receiver.db"
    session_id = "FUNDX-RESTART-TEST"

    first_receiver = MessageReceiver(database_path=database_path)

    assert first_receiver.accept_sequence(session_id, 1) is True
    assert first_receiver.accept_sequence(session_id, 2) is True
    assert first_receiver.accept_sequence(session_id, 3) is True

    # Simulate a restart using a new receiver instance.
    second_receiver = MessageReceiver(database_path=database_path)

    # Previously accepted sequence numbers cannot be accepted again.
    assert second_receiver.accept_sequence(session_id, 3) is False

    # Processing continues from the last accepted sequence.
    assert second_receiver.accept_sequence(session_id, 4) is True


def test_simultaneous_duplicate_sequence_across_receivers(tmp_path):
    """Only one receiver may advance the same sequence number."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "sequence_receiver.db"
    session_id = "FUNDX-CONCURRENT-TEST"

    first_receiver = MessageReceiver(database_path=database_path)
    assert first_receiver.accept_sequence(session_id, 1) is True

    barrier = Barrier(2)

    def submit_sequence():
        receiver = MessageReceiver(database_path=database_path)
        barrier.wait()
        return receiver.accept_sequence(session_id, 2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: submit_sequence(),
            range(2),
        ))

    assert sorted(results) == [False, True]


def test_sequence_gap_remains_recorded_after_restart(tmp_path):
    """A detected sequence gap must survive a receiver restart."""
    database_path = tmp_path / "sequence_receiver.db"
    session_id = "FUNDX-PERSISTENT-GAP"

    first_receiver = MessageReceiver(database_path=database_path)

    assert first_receiver.accept_sequence(session_id, 1) is True
    assert first_receiver.accept_sequence(session_id, 2) is True

    # Sequence 3 is missing.
    assert first_receiver.accept_sequence(session_id, 4) is False

    # Restart the receiver using the same database.
    second_receiver = MessageReceiver(database_path=database_path)

    # The missing-sequence warning must still be recorded.
    assert second_receiver.has_sequence_gap(session_id) is True


def test_sequence_gap_requires_explicit_recovery(tmp_path):
    """Receiving a missing sequence must not silently clear the gap."""
    database_path = tmp_path / "sequence_receiver.db"
    session_id = "FUNDX-RECOVERY-TEST"

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence(session_id, 1) is True
    assert receiver.accept_sequence(session_id, 2) is True

    # Sequence 3 is missing.
    assert receiver.accept_sequence(session_id, 4) is False
    assert receiver.has_sequence_gap(session_id) is True

    # The missing sequence arrives, but trading remains blocked
    # until an explicit recovery procedure is completed.
    assert receiver.accept_sequence(session_id, 3) is False

    # The warning must remain until explicit recovery.
    assert receiver.has_sequence_gap(session_id) is True

    # Restarting must not clear the warning.
    restarted_receiver = MessageReceiver(database_path=database_path)
    assert restarted_receiver.has_sequence_gap(session_id) is True


def test_unresolved_gap_blocks_new_sequences(tmp_path):
    """An unresolved gap must block further sequence advancement."""
    database_path = tmp_path / "sequence_receiver.db"
    session_id = "FUNDX-BLOCKED-TEST"

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence(session_id, 1) is True
    assert receiver.accept_sequence(session_id, 2) is True

    # Sequence 3 is missing.
    assert receiver.accept_sequence(session_id, 4) is False
    assert receiver.has_sequence_gap(session_id) is True

    # Sequence 3 arrives, but the gap is still unresolved.
    assert receiver.accept_sequence(session_id, 3) is False

    # Sequence 4 must also remain blocked.
    assert receiver.accept_sequence(session_id, 4) is False

    # Restarting cannot bypass the restriction.
    restarted_receiver = MessageReceiver(database_path=database_path)

    assert restarted_receiver.accept_sequence(session_id, 3) is False


def test_concurrent_sequence_gap_detection(tmp_path):
    """Concurrent receivers must not bypass sequence-gap protection."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "sequence_receiver.db"
    session_id = "FUNDX-CONCURRENT-GAP"

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence(session_id, 1) is True
    assert receiver.accept_sequence(session_id, 2) is True

    barrier = Barrier(2)

    def submit_sequence(sequence_number):
        separate_receiver = MessageReceiver(
            database_path=database_path
        )
        barrier.wait()
        return separate_receiver.accept_sequence(
            session_id,
            sequence_number,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            submit_sequence,
            [3, 5],
        ))

    # Sequence 5 must never be accepted.
    assert results[1] is False

    # A gap must be recorded regardless of execution order.
    assert receiver.has_sequence_gap(session_id) is True

    # Further processing remains blocked.
    assert receiver.accept_sequence(session_id, 4) is False


def test_sequence_gap_blocks_other_sessions(tmp_path):
    """An unresolved gap must block new instructions in every session."""
    database_path = tmp_path / "sequence_receiver.db"

    receiver = MessageReceiver(database_path=database_path)

    session_a = "FUNDX-SESSION-A"
    session_b = "FUNDX-SESSION-B"

    # Establish two sessions.
    assert receiver.accept_sequence(session_a, 1) is True
    assert receiver.accept_sequence(session_b, 1) is True

    # Session A detects a missing sequence number.
    assert receiver.accept_sequence(session_a, 3) is False
    assert receiver.has_sequence_gap(session_a) is True

    # Session B must also stop accepting new instructions.
    assert receiver.accept_sequence(session_b, 2) is False

    # The restriction must survive a restart.
    restarted_receiver = MessageReceiver(database_path=database_path)

    assert restarted_receiver.accept_sequence(session_b, 2) is False


def test_global_sequence_halt_survives_restart(tmp_path):
    """A sequence gap must block all sessions after a restart."""
    database_path = tmp_path / "sequence_receiver.db"

    first_receiver = MessageReceiver(database_path=database_path)

    assert first_receiver.accept_sequence("SESSION-A", 1) is True
    assert first_receiver.accept_sequence("SESSION-B", 1) is True

    # Session A detects a missing sequence.
    assert first_receiver.accept_sequence("SESSION-A", 3) is False
    assert first_receiver.has_sequence_gap("SESSION-A") is True

    # Simulate restarting FundX.
    restarted_receiver = MessageReceiver(database_path=database_path)

    # Both sessions must remain blocked.
    assert restarted_receiver.accept_sequence("SESSION-A", 2) is False
    assert restarted_receiver.accept_sequence("SESSION-B", 2) is False

    # A new session must also be blocked.
    assert restarted_receiver.accept_sequence("SESSION-C", 1) is False


def test_global_sequence_halt_blocks_new_events(tmp_path):
    """An unresolved sequence gap must block new trading events."""
    database_path = tmp_path / "sequence_receiver.db"

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence("SESSION-A", 1) is True

    # Sequence 2 is missing.
    assert receiver.accept_sequence("SESSION-A", 3) is False
    assert receiver.has_sequence_gap("SESSION-A") is True

    # A new event must not bypass the global halt.
    assert receiver.accept_event("EVENT-AFTER-GAP") is False

    # Restarting must not bypass the halt.
    restarted_receiver = MessageReceiver(database_path=database_path)

    assert restarted_receiver.accept_event("EVENT-AFTER-RESTART") is False


def test_halted_event_is_not_recorded_as_processed(tmp_path):
    """An event rejected during a halt must not be marked processed."""
    import sqlite3

    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_sequence("SESSION-A", 1) is True

    # Sequence 2 is missing, triggering the global halt.
    assert receiver.accept_sequence("SESSION-A", 3) is False
    assert receiver.has_sequence_gap("SESSION-A") is True

    # The event is rejected during the halt.
    assert receiver.accept_event("EVENT-BLOCKED") is False

    # Verify it was not recorded as an accepted event.
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM received_events
            WHERE event_id = ?
            """,
            ("EVENT-BLOCKED",),
        ).fetchone()

    assert row is None


def test_sequence_gap_recovery_requires_authorization(tmp_path):
    """An unauthorized recovery attempt must not clear a sequence gap."""
    import pytest

    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    session_id = "FUNDX-RECOVERY-AUTH"

    assert receiver.accept_sequence(session_id, 1) is True

    # Sequence 2 is missing.
    assert receiver.accept_sequence(session_id, 3) is False
    assert receiver.has_sequence_gap(session_id) is True

    # Recovery without authorization must be rejected.
    with pytest.raises(PermissionError):
        receiver.clear_sequence_gap(
            session_id,
            authorized=False,
        )

    # The halt must remain active.
    assert receiver.has_sequence_gap(session_id) is True
    assert receiver.accept_sequence(session_id, 2) is False


def test_authorization_alone_cannot_clear_sequence_gap(tmp_path):
    """Authorization without reconciliation must not resume trading."""
    import pytest

    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    session_id = "FUNDX-RECOVERY-SAFETY"

    assert receiver.accept_sequence(session_id, 1) is True

    # Sequence 2 is missing.
    assert receiver.accept_sequence(session_id, 3) is False
    assert receiver.has_sequence_gap(session_id) is True

    # Authorization alone is insufficient.
    with pytest.raises(NotImplementedError):
        receiver.clear_sequence_gap(
            session_id,
            authorized=True,
        )

    # The original gap remains recorded.
    assert receiver.has_sequence_gap(session_id) is True

    # No session may resume accepting instructions.
    assert receiver.accept_sequence(session_id, 2) is False
    assert receiver.accept_sequence("ANOTHER-SESSION", 1) is False

    # Restarting cannot bypass the halt.
    restarted_receiver = MessageReceiver(database_path=database_path)

    assert restarted_receiver.has_sequence_gap(session_id) is True
    assert restarted_receiver.accept_sequence("NEW-SESSION", 1) is False


def test_sequence_gap_recovery_rejects_invalid_session(tmp_path):
    """Recovery must reject invalid session identifiers."""
    import pytest

    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    invalid_sessions = [
        "",
        "   ",
        None,
        123,
    ]

    for session_id in invalid_sessions:
        with pytest.raises(ValueError):
            receiver.clear_sequence_gap(
                session_id,
                authorized=True,
            )


def test_missing_sequence_known_does_not_authorize_recovery(tmp_path):
    """Knowing the missing sequence is not sufficient for recovery."""
    import pytest

    database_path = tmp_path / "sequence_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    session_id = "FUNDX-MISSING-KNOWN"

    assert receiver.accept_sequence(session_id, 1) is True

    # Sequence 2 is missing.
    assert receiver.accept_sequence(session_id, 3) is False
    assert receiver.has_sequence_gap(session_id) is True

    # Even an authorized caller cannot clear the gap yet.
    with pytest.raises(NotImplementedError):
        receiver.clear_sequence_gap(
            session_id,
            authorized=True,
        )

    # Knowing which sequence is missing does not resume processing.
    assert receiver.accept_sequence(session_id, 2) is False
    assert receiver.has_sequence_gap(session_id) is True


def test_halted_event_remains_unrecorded_after_restart(tmp_path):
    """A rejected event must remain unrecorded across receiver restarts."""
    import sqlite3

    database_path = tmp_path / "sequence_receiver.db"

    first_receiver = MessageReceiver(database_path=database_path)

    assert first_receiver.accept_sequence("SESSION-A", 1) is True

    # Sequence 2 is missing.
    assert first_receiver.accept_sequence("SESSION-A", 3) is False

    # This event must not be accepted during the halt.
    assert first_receiver.accept_event("EVENT-RETRY") is False

    # Simulate restarting the receiver.
    restarted_receiver = MessageReceiver(database_path=database_path)

    assert restarted_receiver.accept_event("EVENT-RETRY") is False

    # The rejected event must not exist in the durable event table.
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM received_events
            WHERE event_id = ?
            """,
            ("EVENT-RETRY",),
        ).fetchone()

    assert row is None


def test_instruction_acceptance_records_event_and_sequence(tmp_path):
    """An instruction must record its event and sequence together."""
    import sqlite3

    database_path = tmp_path / "instruction_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    accepted = receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=(
            '{"event_id":"EVENT-001",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":1}'
        ),
    )

    assert accepted is True

    with sqlite3.connect(database_path) as connection:
        event_row = connection.execute(
            """
            SELECT event_id
            FROM received_events
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

        sequence_row = connection.execute(
            """
            SELECT last_sequence_number
            FROM message_sequences
            WHERE session_id = ?
            """,
            ("FUNDX-SESSION",),
        ).fetchone()

    assert event_row == ("EVENT-001",)
    assert sequence_row == (1,)


def test_rejected_instruction_does_not_consume_event_id(tmp_path):
    """A rejected sequence must not permanently record its event ID."""
    import sqlite3

    database_path = tmp_path / "instruction_receiver.db"
    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=(
            '{"event_id":"EVENT-001",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":1}'
        ),
    ) is True

    # Duplicate sequence number with a new event ID.
    assert receiver.accept_instruction(
        event_id="EVENT-002",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=(
            '{"event_id":"EVENT-002",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":1}'
        ),
    ) is False

    # The rejected event must not have been recorded.
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT event_id
            FROM received_events
            WHERE event_id = ?
            """,
            ("EVENT-002",),
        ).fetchone()

    assert row is None


def test_concurrent_instructions_same_sequence(tmp_path):
    """Only one competing instruction may claim a sequence number."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "instruction_receiver.db"
    session_id = "FUNDX-CONCURRENT-INSTRUCTION"

    barrier = Barrier(2)

    def submit(event_id):
        # Each worker creates its own SQLite connection.
        receiver = MessageReceiver(database_path=database_path)

        barrier.wait()

        return receiver.accept_instruction(
            event_id=event_id,
            session_id=session_id,
            sequence_number=1,
            payload_json=(
                '{"event_id":"' + event_id + '",'
                '"session_id":"' + session_id + '",'
                '"sequence_number":1}'
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(submit, "EVENT-A")
        second_future = executor.submit(submit, "EVENT-B")

        results = [
            first_future.result(),
            second_future.result(),
        ]

    assert sorted(results) == [False, True]


def test_competing_instruction_records_only_winner(tmp_path):
    """Only the winning instruction's event ID may be recorded."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "instruction_receiver.db"
    session_id = "FUNDX-COMPETING-EVENTS"
    barrier = Barrier(2)

    def submit(event_id):
        receiver = MessageReceiver(database_path=database_path)
        barrier.wait()
        return (
            event_id,
            receiver.accept_instruction(
                event_id=event_id,
                session_id=session_id,
                sequence_number=1,
                payload_json=(
                    '{"event_id":"' + event_id + '",'
                    '"session_id":"' + session_id + '",'
                    '"sequence_number":1}'
                ),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(submit, "EVENT-A")
        second = executor.submit(submit, "EVENT-B")
        results = [first.result(), second.result()]

    winners = [event_id for event_id, accepted in results if accepted]
    losers = [event_id for event_id, accepted in results if not accepted]

    assert len(winners) == 1
    assert len(losers) == 1

    with sqlite3.connect(database_path) as connection:
        recorded_events = {
            row[0]
            for row in connection.execute(
                "SELECT event_id FROM received_events"
            )
        }

    assert recorded_events == set(winners)
    assert losers[0] not in recorded_events


def test_instruction_inbox_table_exists(tmp_path):
    """The receiver must create a durable instruction inbox."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"

    MessageReceiver(database_path=database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(instruction_inbox)"
            )
        }

    assert {
        "event_id",
        "session_id",
        "sequence_number",
        "payload_json",
        "processing_status",
    }.issubset(columns)


def test_accept_instruction_stores_payload_in_inbox(tmp_path):
    """An accepted instruction must preserve its original JSON payload."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001","session_id":"FUNDX-SESSION",'
        '"sequence_number":1,"action":"NEW","quantity":2}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT payload_json, processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

    assert row == (payload, "RECEIVED")


def test_empty_instruction_payload_does_not_change_database(tmp_path):
    """An empty payload must not consume an event or sequence."""
    import sqlite3
    import pytest

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    with pytest.raises(ValueError):
        receiver.accept_instruction(
            event_id="EVENT-001",
            session_id="FUNDX-SESSION",
            sequence_number=1,
            payload_json="",
        )

    with sqlite3.connect(database_path) as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM received_events"
        ).fetchone()[0]

        sequence_count = connection.execute(
            "SELECT COUNT(*) FROM message_sequences"
        ).fetchone()[0]

        inbox_count = connection.execute(
            "SELECT COUNT(*) FROM instruction_inbox"
        ).fetchone()[0]

    assert event_count == 0
    assert sequence_count == 0
    assert inbox_count == 0


def test_instruction_payload_rejects_mismatched_identifiers(tmp_path):
    """Payload identifiers must match the instruction arguments."""
    import sqlite3
    import pytest

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-999",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"NEW","quantity":2}'
    )

    with pytest.raises(ValueError):
        receiver.accept_instruction(
            event_id="EVENT-001",
            session_id="FUNDX-SESSION",
            sequence_number=1,
            payload_json=payload,
        )

    with sqlite3.connect(database_path) as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM received_events"
        ).fetchone()[0]

        sequence_count = connection.execute(
            "SELECT COUNT(*) FROM message_sequences"
        ).fetchone()[0]

        inbox_count = connection.execute(
            "SELECT COUNT(*) FROM instruction_inbox"
        ).fetchone()[0]

    assert event_count == 0
    assert sequence_count == 0
    assert inbox_count == 0


def test_malformed_instruction_json_does_not_change_database(tmp_path):
    """Malformed JSON must not consume an event or sequence."""
    import sqlite3
    import pytest

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    malformed_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"NEW","quantity":2'
    )

    with pytest.raises(ValueError):
        receiver.accept_instruction(
            event_id="EVENT-001",
            session_id="FUNDX-SESSION",
            sequence_number=1,
            payload_json=malformed_payload,
        )

    with sqlite3.connect(database_path) as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM received_events"
        ).fetchone()[0]

        sequence_count = connection.execute(
            "SELECT COUNT(*) FROM message_sequences"
        ).fetchone()[0]

        inbox_count = connection.execute(
            "SELECT COUNT(*) FROM instruction_inbox"
        ).fetchone()[0]

    assert event_count == 0
    assert sequence_count == 0
    assert inbox_count == 0


def test_instruction_without_payload_is_rejected(tmp_path):
    """An instruction without a payload must not change database state."""
    import sqlite3
    import pytest

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    with pytest.raises(ValueError):
        receiver.accept_instruction(
            event_id="EVENT-001",
            session_id="FUNDX-SESSION",
            sequence_number=1,
        )

    with sqlite3.connect(database_path) as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) FROM received_events"
        ).fetchone()[0]

        sequence_count = connection.execute(
            "SELECT COUNT(*) FROM message_sequences"
        ).fetchone()[0]

        inbox_count = connection.execute(
            "SELECT COUNT(*) FROM instruction_inbox"
        ).fetchone()[0]

    assert event_count == 0
    assert sequence_count == 0
    assert inbox_count == 0


def test_rejected_instruction_does_not_create_inbox_record(tmp_path):
    """A rejected duplicate sequence must not create an inbox record."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    second_payload = (
        '{"event_id":"EVENT-002",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    assert receiver.accept_instruction(
        event_id="EVENT-002",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=second_payload,
    ) is False

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT event_id, payload_json, processing_status
            FROM instruction_inbox
            ORDER BY event_id
            """
        ).fetchall()

    assert rows == [
        ("EVENT-001", first_payload, "RECEIVED")
    ]


def test_instruction_inbox_survives_restart(tmp_path):
    """An accepted instruction must remain durable after restart."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"NEW","quantity":2}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    # Simulate shutdown and restart.
    receiver._connection.close()
    restarted_receiver = MessageReceiver(database_path=database_path)

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT
                event_id,
                session_id,
                sequence_number,
                payload_json,
                processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

    assert row == (
        "EVENT-001",
        "FUNDX-SESSION",
        1,
        payload,
        "RECEIVED",
    )

    # The restarted receiver must reject the same instruction.
    assert restarted_receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is False

    restarted_receiver._connection.close()


def test_sequence_gap_preserves_inbox_after_restart(tmp_path):
    """A sequence gap must halt new instructions without losing the inbox."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    third_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":3}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    # Sequence 2 is missing.
    assert receiver.accept_instruction(
        event_id="EVENT-003",
        session_id="FUNDX-SESSION",
        sequence_number=3,
        payload_json=third_payload,
    ) is False

    receiver._connection.close()

    restarted_receiver = MessageReceiver(database_path=database_path)

    assert restarted_receiver.has_sequence_gap("FUNDX-SESSION") is True

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT event_id, sequence_number, payload_json
            FROM instruction_inbox
            ORDER BY sequence_number
            """
        ).fetchall()

    assert rows == [
        ("EVENT-001", 1, first_payload)
    ]

    restarted_receiver._connection.close()


def test_sequence_gap_blocks_other_session_inbox_after_restart(tmp_path):
    """An unresolved gap must block inbox writes in other sessions."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1}'
    )

    gap_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"SESSION-A",'
        '"sequence_number":3}'
    )

    other_payload = (
        '{"event_id":"EVENT-OTHER",'
        '"session_id":"SESSION-B",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="SESSION-A",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    # Missing sequence 2 creates a durable global halt.
    assert receiver.accept_instruction(
        event_id="EVENT-003",
        session_id="SESSION-A",
        sequence_number=3,
        payload_json=gap_payload,
    ) is False

    receiver._connection.close()

    restarted_receiver = MessageReceiver(database_path=database_path)

    # Even another session cannot accept a new instruction.
    assert restarted_receiver.accept_instruction(
        event_id="EVENT-OTHER",
        session_id="SESSION-B",
        sequence_number=1,
        payload_json=other_payload,
    ) is False

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT event_id, session_id, sequence_number
            FROM instruction_inbox
            ORDER BY event_id
            """
        ).fetchall()

    assert rows == [
        ("EVENT-001", "SESSION-A", 1)
    ]

    restarted_receiver._connection.close()


def test_new_instruction_remains_received_until_processed(tmp_path):
    """Receipt alone must not imply risk approval or execution."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"NEW","quantity":2}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            """
            SELECT processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

    assert status == ("RECEIVED",)


def test_list_pending_instructions_returns_received_in_order(tmp_path):
    """Pending instructions must be retrievable in sequence order."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    for number in (1, 2, 3):
        event_id = f"EVENT-{number:03d}"

        payload = (
            '{"event_id":"' + event_id + '",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":' + str(number) + '}'
        )

        assert receiver.accept_instruction(
            event_id=event_id,
            session_id="FUNDX-SESSION",
            sequence_number=number,
            payload_json=payload,
        ) is True

    pending = receiver.list_pending_instructions()

    assert len(pending) == 3

    assert [
        item["event_id"] for item in pending
    ] == [
        "EVENT-001",
        "EVENT-002",
        "EVENT-003",
    ]

    assert all(
        item["processing_status"] == "RECEIVED"
        for item in pending
    )


def test_pending_instructions_retrievable_after_restart(tmp_path):
    """Pending instructions must remain available after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,"action":"NEW","quantity":2}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    receiver._connection.close()

    # Start a new receiver using the existing database.
    restarted_receiver = MessageReceiver(database_path=database_path)

    pending = restarted_receiver.list_pending_instructions()

    assert pending == [
        {
            "event_id": "EVENT-001",
            "session_id": "FUNDX-SESSION",
            "sequence_number": 1,
            "payload_json": payload,
            "processing_status": "RECEIVED",
        }
    ]

    # Reading pending instructions must not change their status.
    assert restarted_receiver.list_pending_instructions() == pending

    restarted_receiver._connection.close()


def test_pending_instructions_readable_during_sequence_gap(tmp_path):
    """A sequence-gap halt must not block read-only inbox inspection."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    third_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":3}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    # Missing sequence 2 triggers the safety halt.
    assert receiver.accept_instruction(
        event_id="EVENT-003",
        session_id="FUNDX-SESSION",
        sequence_number=3,
        payload_json=third_payload,
    ) is False

    assert receiver.has_sequence_gap("FUNDX-SESSION") is True

    # Previously accepted instructions remain readable.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-001"
    assert pending[0]["payload_json"] == first_payload
    assert pending[0]["processing_status"] == "RECEIVED"


def test_reading_pending_instructions_does_not_clear_sequence_gap(tmp_path):
    """Reading the inbox must never clear a safety halt."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    gap_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":3}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    # Missing sequence 2 triggers the safety halt.
    assert receiver.accept_instruction(
        event_id="EVENT-003",
        session_id="FUNDX-SESSION",
        sequence_number=3,
        payload_json=gap_payload,
    ) is False

    assert receiver.has_sequence_gap("FUNDX-SESSION") is True

    # Repeated inbox reads must not alter the safety halt.
    for _ in range(3):
        pending = receiver.list_pending_instructions()
        assert len(pending) == 1
        assert receiver.has_sequence_gap("FUNDX-SESSION") is True

    # The missing instruction must still be blocked.
    second_payload = (
        '{"event_id":"EVENT-002",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":2}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-002",
        session_id="FUNDX-SESSION",
        sequence_number=2,
        payload_json=second_payload,
    ) is False

    assert receiver.has_sequence_gap("FUNDX-SESSION") is True


def test_mark_instruction_processing_changes_status(tmp_path):
    """Processing must require an explicit durable state change."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            """
            SELECT processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

    assert status == ("PROCESSING",)


def test_sequence_gap_blocks_instruction_processing(tmp_path):
    """An unresolved sequence gap must prevent processing."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    third_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":3}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    # Missing sequence 2 triggers the safety halt.
    assert receiver.accept_instruction(
        event_id="EVENT-003",
        session_id="FUNDX-SESSION",
        sequence_number=3,
        payload_json=third_payload,
    ) is False

    assert receiver.has_sequence_gap("FUNDX-SESSION") is True

    # Even an instruction accepted before the gap cannot begin processing.
    assert receiver.mark_instruction_processing("EVENT-001") is False

    # Verify that the database status remains unchanged.
    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            """
            SELECT processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

    assert status == ("RECEIVED",)


def test_two_receivers_cannot_claim_same_instruction(tmp_path):
    """Only one receiver may claim an instruction for processing."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"

    receiver_a = MessageReceiver(database_path=database_path)
    receiver_b = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver_a.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    # Both receivers attempt to claim the same instruction.
    first_claim = receiver_a.mark_instruction_processing("EVENT-001")
    second_claim = receiver_b.mark_instruction_processing("EVENT-001")

    assert first_claim is True
    assert second_claim is False

    # Confirm exactly one durable PROCESSING record exists.
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT event_id, processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchall()

    assert rows == [("EVENT-001", "PROCESSING")]


def test_simultaneous_receivers_cannot_claim_same_instruction(tmp_path):
    """Concurrent receivers must not claim the same instruction twice."""
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    import threading

    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    receiver._connection.close()

    barrier = threading.Barrier(2)

    def attempt_claim():
        worker = MessageReceiver(database_path=database_path)

        try:
            barrier.wait(timeout=5)
            return worker.mark_instruction_processing("EVENT-001")
        finally:
            worker._connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt_claim)
            for _ in range(2)
        ]

        results = [future.result() for future in futures]

    # Exactly one worker must succeed.
    assert sorted(results) == [False, True]

    # The final database state must be durable and unambiguous.
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT event_id, processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchall()

    assert rows == [("EVENT-001", "PROCESSING")]


def test_processing_status_survives_restart(tmp_path):
    """An interrupted processing claim must remain durable."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Simulate a shutdown after processing was claimed.
    receiver._connection.close()

    # Restart using the same durable database.
    restarted = MessageReceiver(database_path=database_path)

    # The same instruction must not be claimed again.
    assert restarted.mark_instruction_processing("EVENT-001") is False

    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            """
            SELECT processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

    assert status == ("PROCESSING",)

    restarted._connection.close()


def test_processing_instruction_excluded_from_pending_inbox(tmp_path):
    """Claimed instructions must not appear as unprocessed work."""
    import sqlite3

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    for number in (1, 2):
        event_id = f"EVENT-{number:03d}"

        payload = (
            '{"event_id":"' + event_id + '",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":' + str(number) + '}'
        )

        assert receiver.accept_instruction(
            event_id=event_id,
            session_id="FUNDX-SESSION",
            sequence_number=number,
            payload_json=payload,
        ) is True

    # Claim the first instruction for processing.
    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Only the second instruction should remain pending.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-002"
    assert pending[0]["processing_status"] == "RECEIVED"

    # The claimed instruction must still exist in durable storage.
    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            """
            SELECT processing_status
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            ("EVENT-001",),
        ).fetchone()

    assert status == ("PROCESSING",)


def test_list_processing_instructions_after_restart(tmp_path):
    """Interrupted processing instructions must be visible after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Simulate shutdown.
    receiver._connection.close()

    # Restart with the same durable database.
    restarted = MessageReceiver(database_path=database_path)

    interrupted = restarted.list_processing_instructions()

    assert interrupted == [
        {
            "event_id": "EVENT-001",
            "session_id": "FUNDX-SESSION",
            "sequence_number": 1,
            "payload_json": payload,
            "processing_status": "PROCESSING",
        }
    ]

    # Inspection must not change the processing state.
    assert restarted.list_processing_instructions() == interrupted

    # The interrupted instruction must not reenter the pending queue.
    assert restarted.list_pending_instructions() == []

    restarted._connection.close()


def test_processing_instructions_readable_during_sequence_gap(tmp_path):
    """Recovery inspection must work without clearing a safety halt."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    def payload(event_id, sequence_number):
        return (
            '{"event_id":"' + event_id + '",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":' + str(sequence_number) + '}'
        )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload("EVENT-001", 1),
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Sequence 2 is missing. This triggers the safety halt.
    assert receiver.accept_instruction(
        event_id="EVENT-003",
        session_id="FUNDX-SESSION",
        sequence_number=3,
        payload_json=payload("EVENT-003", 3),
    ) is False

    assert receiver.has_sequence_gap("FUNDX-SESSION") is True

    # Recovery inspection must remain available.
    interrupted = receiver.list_processing_instructions()

    assert len(interrupted) == 1
    assert interrupted[0]["event_id"] == "EVENT-001"
    assert interrupted[0]["processing_status"] == "PROCESSING"

    # Inspection must not clear the halt.
    assert receiver.has_sequence_gap("FUNDX-SESSION") is True

    # The instruction cannot be claimed again.
    assert receiver.mark_instruction_processing("EVENT-001") is False


def test_processing_recovery_visibility_after_restart_with_gap(tmp_path):
    """Interrupted work and sequence-gap halt must survive restart."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    third_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":3}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Missing sequence 2 triggers the safety halt.
    assert receiver.accept_instruction(
        event_id="EVENT-003",
        session_id="FUNDX-SESSION",
        sequence_number=3,
        payload_json=third_payload,
    ) is False

    assert receiver.has_sequence_gap("FUNDX-SESSION") is True

    # Simulate shutdown.
    receiver._connection.close()

    # Restart from the same database.
    restarted = MessageReceiver(database_path=database_path)

    assert restarted.has_sequence_gap("FUNDX-SESSION") is True

    interrupted = restarted.list_processing_instructions()

    assert len(interrupted) == 1
    assert interrupted[0]["event_id"] == "EVENT-001"
    assert interrupted[0]["payload_json"] == first_payload
    assert interrupted[0]["processing_status"] == "PROCESSING"

    # Recovery inspection must not clear the halt.
    assert restarted.has_sequence_gap("FUNDX-SESSION") is True

    # Processing remains blocked.
    assert restarted.mark_instruction_processing("EVENT-001") is False

    restarted._connection.close()


def test_detect_interrupted_processing_after_restart(tmp_path):
    """Startup must detect instructions left in PROCESSING."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    # No interrupted processing exists initially.
    assert receiver.has_interrupted_processing() is False

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    # RECEIVED does not count as interrupted processing.
    assert receiver.has_interrupted_processing() is False

    assert receiver.mark_instruction_processing("EVENT-001") is True

    receiver._connection.close()

    # Simulate restarting FundX.
    restarted = MessageReceiver(database_path=database_path)

    # The interrupted instruction must be detected.
    assert restarted.has_interrupted_processing() is True

    # Detection must not alter its status.
    interrupted = restarted.list_processing_instructions()

    assert len(interrupted) == 1
    assert interrupted[0]["processing_status"] == "PROCESSING"

    restarted._connection.close()


def test_interrupted_processing_blocks_new_processing_after_restart(tmp_path):
    """Unresolved processing must block claims after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    for number in (1, 2):
        event_id = f"EVENT-{number:03d}"

        payload = (
            '{"event_id":"' + event_id + '",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":' + str(number) + '}'
        )

        assert receiver.accept_instruction(
            event_id=event_id,
            session_id="FUNDX-SESSION",
            sequence_number=number,
            payload_json=payload,
        ) is True

    # Instruction 1 begins processing.
    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Simulate shutdown and restart.
    receiver._connection.close()

    restarted = MessageReceiver(database_path=database_path)

    assert restarted.has_interrupted_processing() is True

    # Instruction 2 must not begin processing while 1 is unresolved.
    assert restarted.mark_instruction_processing("EVENT-002") is False

    # Instruction 2 must remain pending.
    pending = restarted.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-002"
    assert pending[0]["processing_status"] == "RECEIVED"

    restarted._connection.close()


def test_processing_block_applies_across_sessions(tmp_path):
    """Unresolved processing must block claims in other sessions."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    for event_id, session_id in (
        ("EVENT-A", "SESSION-A"),
        ("EVENT-B", "SESSION-B"),
    ):
        payload = (
            '{"event_id":"' + event_id + '",'
            '"session_id":"' + session_id + '",'
            '"sequence_number":1}'
        )

        assert receiver.accept_instruction(
            event_id=event_id,
            session_id=session_id,
            sequence_number=1,
            payload_json=payload,
        ) is True

    # Session A begins processing.
    assert receiver.mark_instruction_processing("EVENT-A") is True

    # Session B must remain blocked.
    assert receiver.mark_instruction_processing("EVENT-B") is False

    # Session B's instruction must remain pending.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-B"
    assert pending[0]["session_id"] == "SESSION-B"
    assert pending[0]["processing_status"] == "RECEIVED"

    # Session A remains recorded as PROCESSING.
    interrupted = receiver.list_processing_instructions()

    assert len(interrupted) == 1
    assert interrupted[0]["event_id"] == "EVENT-A"


def test_global_processing_block_survives_restart(tmp_path):
    """Unresolved processing must block other sessions after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    for event_id, session_id in (
        ("EVENT-A", "SESSION-A"),
        ("EVENT-B", "SESSION-B"),
    ):
        payload = (
            '{"event_id":"' + event_id + '",'
            '"session_id":"' + session_id + '",'
            '"sequence_number":1}'
        )

        assert receiver.accept_instruction(
            event_id=event_id,
            session_id=session_id,
            sequence_number=1,
            payload_json=payload,
        ) is True

    assert receiver.mark_instruction_processing("EVENT-A") is True

    # Simulate shutdown.
    receiver._connection.close()

    # Restart from the same durable database.
    restarted = MessageReceiver(database_path=database_path)

    assert restarted.has_interrupted_processing() is True

    # Session B must remain blocked after restart.
    assert restarted.mark_instruction_processing("EVENT-B") is False

    # Session B remains pending.
    pending = restarted.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-B"
    assert pending[0]["processing_status"] == "RECEIVED"

    # Session A remains unresolved.
    interrupted = restarted.list_processing_instructions()

    assert len(interrupted) == 1
    assert interrupted[0]["event_id"] == "EVENT-A"
    assert interrupted[0]["processing_status"] == "PROCESSING"

    restarted._connection.close()


def test_concurrent_claims_different_instructions_global_block(tmp_path):
    """Concurrent claims for different instructions must be globally exclusive."""
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    import threading

    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    for event_id, session_id in (
        ("EVENT-A", "SESSION-A"),
        ("EVENT-B", "SESSION-B"),
    ):
        payload = (
            '{"event_id":"' + event_id + '",'
            '"session_id":"' + session_id + '",'
            '"sequence_number":1}'
        )

        assert receiver.accept_instruction(
            event_id=event_id,
            session_id=session_id,
            sequence_number=1,
            payload_json=payload,
        ) is True

    receiver._connection.close()

    barrier = threading.Barrier(2)

    def attempt_claim(event_id):
        worker = MessageReceiver(database_path=database_path)
        try:
            barrier.wait(timeout=5)
            return worker.mark_instruction_processing(event_id)
        finally:
            worker._connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt_claim, event_id)
            for event_id in ("EVENT-A", "EVENT-B")
        ]
        results = [future.result() for future in futures]

    # Exactly one instruction may enter PROCESSING.
    assert sorted(results) == [False, True]

    with sqlite3.connect(database_path) as connection:
        statuses = connection.execute(
            """
            SELECT processing_status, COUNT(*)
            FROM instruction_inbox
            GROUP BY processing_status
            """
        ).fetchall()

    assert sorted(statuses) == [
        ("PROCESSING", 1),
        ("RECEIVED", 1),
    ]


def test_new_instruction_can_be_received_while_processing_blocked(tmp_path):
    """Receiving new work must not bypass the processing safety gate."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    second_payload = (
        '{"event_id":"EVENT-002",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":2}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=first_payload,
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # A correctly sequenced instruction can still be stored.
    assert receiver.accept_instruction(
        event_id="EVENT-002",
        session_id="FUNDX-SESSION",
        sequence_number=2,
        payload_json=second_payload,
    ) is True

    # But it cannot begin processing.
    assert receiver.mark_instruction_processing("EVENT-002") is False

    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-002"
    assert pending[0]["processing_status"] == "RECEIVED"

    processing = receiver.list_processing_instructions()

    assert len(processing) == 1
    assert processing[0]["event_id"] == "EVENT-001"
    assert processing[0]["processing_status"] == "PROCESSING"


def test_queued_instruction_remains_blocked_after_restart(tmp_path):
    """Queued work must not bypass an unresolved claim after restart."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    for number in (1, 2):
        event_id = f"EVENT-{number:03d}"
        payload = (
            '{"event_id":"' + event_id + '",'
            '"session_id":"FUNDX-SESSION",'
            '"sequence_number":' + str(number) + '}'
        )

        assert receiver.accept_instruction(
            event_id=event_id,
            session_id="FUNDX-SESSION",
            sequence_number=number,
            payload_json=payload,
        ) is True

        if number == 1:
            assert receiver.mark_instruction_processing(event_id) is True

    # Simulate shutdown.
    receiver._connection.close()

    # Restart using the same durable database.
    restarted = MessageReceiver(database_path=database_path)

    # Previously claimed instruction remains unresolved.
    processing = restarted.list_processing_instructions()
    assert len(processing) == 1
    assert processing[0]["event_id"] == "EVENT-001"

    # Newly received instruction remains safely queued.
    pending = restarted.list_pending_instructions()
    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-002"
    assert pending[0]["processing_status"] == "RECEIVED"

    # Restart must not allow the queued instruction to proceed.
    assert restarted.mark_instruction_processing("EVENT-002") is False

    restarted._connection.close()


def test_duplicate_instruction_cannot_reenter_processing(tmp_path):
    """Duplicate delivery must not reset an instruction's processing state."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Simulate Jack's engine delivering the same instruction again.
    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is False

    # Duplicate delivery must not reset the processing state.
    processing = receiver.list_processing_instructions()

    assert len(processing) == 1
    assert processing[0]["event_id"] == "EVENT-001"
    assert processing[0]["processing_status"] == "PROCESSING"

    # The duplicate must not appear as new pending work.
    assert receiver.list_pending_instructions() == []

    # It must not be possible to claim the instruction again.
    assert receiver.mark_instruction_processing("EVENT-001") is False


def test_duplicate_processing_instruction_rejected_after_restart(tmp_path):
    """Restart must not allow duplicate delivery to reset processing."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Simulate shutdown.
    receiver._connection.close()

    # Restart with the same durable database.
    restarted = MessageReceiver(database_path=database_path)

    # Jack's engine resends the original instruction.
    assert restarted.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=payload,
    ) is False

    # The original instruction must remain PROCESSING.
    processing = restarted.list_processing_instructions()

    assert len(processing) == 1
    assert processing[0]["event_id"] == "EVENT-001"
    assert processing[0]["processing_status"] == "PROCESSING"

    # No duplicate pending instruction may appear.
    assert restarted.list_pending_instructions() == []

    # A second processing claim must remain prohibited.
    assert restarted.mark_instruction_processing("EVENT-001") is False

    restarted._connection.close()


def test_duplicate_event_id_cannot_overwrite_original_payload(tmp_path):
    """A duplicate event ID must not alter the stored instruction."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"BUY",'
        '"quantity":2}'
    )

    modified_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"SELL",'
        '"quantity":10}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=original_payload,
    ) is True

    # Same event ID, but conflicting trading instructions.
    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=modified_payload,
    ) is False

    # The original instruction must remain unchanged.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-001"
    assert pending[0]["payload_json"] == original_payload
    assert pending[0]["processing_status"] == "RECEIVED"


def test_conflicting_duplicate_preserves_payload_after_restart(tmp_path):
    """Conflicting duplicate delivery must not change durable payload."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"BUY",'
        '"quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"SELL",'
        '"quantity":10}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=original_payload,
    ) is True

    receiver._connection.close()

    # Restart FundX using the same durable database.
    restarted = MessageReceiver(database_path=database_path)

    # A conflicting duplicate must be rejected.
    assert restarted.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=conflicting_payload,
    ) is False

    # The original instruction must remain unchanged.
    pending = restarted.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-001"
    assert pending[0]["payload_json"] == original_payload
    assert pending[0]["processing_status"] == "RECEIVED"

    restarted._connection.close()


def test_detect_conflicting_duplicate_instruction(tmp_path):
    """Detect when an event ID is reused with a different payload."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=original_payload,
    ) is True

    # An identical retransmission is not a conflict.
    assert receiver.is_conflicting_duplicate(
        "EVENT-001", original_payload
    ) is False

    # Same event ID with different instructions is a conflict.
    assert receiver.is_conflicting_duplicate(
        "EVENT-001", conflicting_payload
    ) is True

    # An unknown event ID is not an existing conflict.
    assert receiver.is_conflicting_duplicate(
        "EVENT-999", conflicting_payload
    ) is False

    # Inspection must not alter the original instruction.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["payload_json"] == original_payload
    assert pending[0]["processing_status"] == "RECEIVED"


def test_conflicting_duplicate_detection_survives_restart(tmp_path):
    """Conflicting duplicates must remain detectable after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"FUNDX-SESSION",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="FUNDX-SESSION",
        sequence_number=1,
        payload_json=original_payload,
    ) is True

    receiver._connection.close()

    # Restart using the existing durable database.
    restarted = MessageReceiver(database_path=database_path)

    # Identical retransmission is not a conflict.
    assert restarted.is_conflicting_duplicate(
        "EVENT-001", original_payload
    ) is False

    # Modified instruction with the same event ID is a conflict.
    assert restarted.is_conflicting_duplicate(
        "EVENT-001", conflicting_payload
    ) is True

    # Detection must not modify the original instruction.
    pending = restarted.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["payload_json"] == original_payload
    assert pending[0]["processing_status"] == "RECEIVED"

    restarted._connection.close()


def test_conflicting_duplicate_triggers_global_processing_halt(tmp_path):
    """Conflicting duplicate must halt processing across sessions."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    other_payload = (
        '{"event_id":"EVENT-002",'
        '"session_id":"SESSION-B",'
        '"sequence_number":1}'
    )

    # Accept the original instruction.
    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="SESSION-A",
        sequence_number=1,
        payload_json=original_payload,
    ) is True

    # Accept an independent instruction in another session.
    assert receiver.accept_instruction(
        event_id="EVENT-002",
        session_id="SESSION-B",
        sequence_number=1,
        payload_json=other_payload,
    ) is True

    # Conflicting duplicate must be rejected.
    assert receiver.accept_instruction(
        event_id="EVENT-001",
        session_id="SESSION-A",
        sequence_number=1,
        payload_json=conflicting_payload,
    ) is False

    # The conflict must trigger a global processing halt.
    assert receiver.has_message_integrity_halt() is True

    # Session B must not bypass the halt.
    assert receiver.mark_instruction_processing("EVENT-002") is False

    # The original instruction must remain unchanged.
    pending = receiver.list_pending_instructions()

    original = next(
        item for item in pending
        if item["event_id"] == "EVENT-001"
    )

    assert original["payload_json"] == original_payload
    assert original["processing_status"] == "RECEIVED"


def test_message_integrity_halt_survives_restart(tmp_path):
    """A conflicting-duplicate halt must survive a receiver restart."""
    database_path = tmp_path / "instruction_inbox.db"

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    other_payload = (
        '{"event_id":"EVENT-002",'
        '"session_id":"SESSION-B",'
        '"sequence_number":1}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-002", "SESSION-B", 1, other_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    assert receiver.has_message_integrity_halt() is True

    # Simulate shutting down FundX.
    receiver._connection.close()

    # Start a completely new receiver using the same database.
    restarted = MessageReceiver(database_path=database_path)

    # The halt must still exist.
    assert restarted.has_message_integrity_halt() is True

    # No pending instruction may enter processing.
    assert restarted.mark_instruction_processing("EVENT-002") is False

    # Original instruction must remain untouched.
    pending = restarted.list_pending_instructions()

    original = next(
        item for item in pending
        if item["event_id"] == "EVENT-001"
    )

    assert original["payload_json"] == original_payload
    assert original["processing_status"] == "RECEIVED"

    restarted._connection.close()


def test_message_integrity_halt_blocks_new_instructions(tmp_path):
    """A global integrity halt must block new instructions."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    new_payload = (
        '{"event_id":"EVENT-002",'
        '"session_id":"SESSION-B",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":5}'
    )

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    assert receiver.has_message_integrity_halt() is True

    # New instructions must be rejected while halted.
    assert receiver.accept_instruction(
        "EVENT-002", "SESSION-B", 1, new_payload
    ) is False

    # Rejected instructions must not enter the durable inbox.
    assert len(receiver.list_pending_instructions()) == 1

    # The halt must remain active.
    assert receiver.has_message_integrity_halt() is True

    receiver._connection.close()


def test_message_integrity_halt_blocks_second_receiver(tmp_path):
    """A halt must block another receiver sharing the same database."""
    database_path = tmp_path / "instruction_inbox.db"

    receiver_a = MessageReceiver(database_path=database_path)
    receiver_b = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    other_payload = (
        '{"event_id":"EVENT-002",'
        '"session_id":"SESSION-B",'
        '"sequence_number":1}'
    )

    # Receiver B accepts work before the conflict occurs.
    assert receiver_b.accept_instruction(
        "EVENT-002", "SESSION-B", 1, other_payload
    ) is True

    # Receiver A receives conflicting instructions.
    assert receiver_a.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver_a.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    # Receiver B must observe the same global halt.
    assert receiver_b.has_message_integrity_halt() is True

    # Previously accepted work cannot begin processing.
    assert receiver_b.mark_instruction_processing("EVENT-002") is False

    # New work cannot be accepted either.
    new_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"SESSION-B",'
        '"sequence_number":2}'
    )

    assert receiver_b.accept_instruction(
        "EVENT-003", "SESSION-B", 2, new_payload
    ) is False

    assert len(receiver_b.list_pending_instructions()) == 2

    receiver_a._connection.close()
    receiver_b._connection.close()


def test_simultaneous_conflicting_duplicates_trigger_halt(tmp_path):
    """Concurrent conflicting instructions must produce a durable halt."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "instruction_inbox.db"

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    # Initialize the shared database before starting the workers.
    initializer = MessageReceiver(database_path=database_path)
    initializer._connection.close()

    barrier = Barrier(2)

    def submit(payload):
        receiver = MessageReceiver(database_path=database_path)
        try:
            barrier.wait(timeout=10)
            return receiver.accept_instruction(
                "EVENT-001", "SESSION-A", 1, payload
            )
        finally:
            receiver._connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(submit, original_payload)
        second = executor.submit(submit, conflicting_payload)

        results = [first.result(), second.result()]

    # Exactly one instruction may be accepted.
    assert sorted(results) == [False, True]

    inspector = MessageReceiver(database_path=database_path)

    # The conflicting duplicate must trigger a global halt.
    assert inspector.has_message_integrity_halt() is True

    # Only one instruction may exist in the durable inbox.
    pending = inspector.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-001"
    assert pending[0]["payload_json"] in (
        original_payload,
        conflicting_payload,
    )
    assert pending[0]["processing_status"] == "RECEIVED"

    # Neither version may begin processing after the conflict.
    assert inspector.mark_instruction_processing("EVENT-001") is False

    inspector._connection.close()


def test_message_integrity_halt_preserves_conflict_evidence(tmp_path):
    """Both conflicting payloads must remain available after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    receiver._connection.close()

    restarted = MessageReceiver(database_path=database_path)

    evidence = restarted._connection.execute(
        """
        SELECT reason, event_id, original_payload, conflicting_payload
        FROM message_integrity_halts
        WHERE halt_id = 1
        """
    ).fetchone()

    assert evidence is not None
    assert evidence[0] == "Conflicting duplicate event ID"
    assert evidence[1] == "EVENT-001"
    assert evidence[2] == original_payload
    assert evidence[3] == conflicting_payload

    assert restarted.has_message_integrity_halt() is True

    restarted._connection.close()


def test_message_integrity_halt_evidence_cannot_be_overwritten(tmp_path):
    """Later conflicts must not replace the original halt evidence."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    first_conflict = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    later_conflict = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":20}'
    )

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, first_conflict
    ) is False

    assert receiver.has_message_integrity_halt() is True

    # A subsequent conflicting instruction must also be rejected.
    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, later_conflict
    ) is False

    evidence = receiver._connection.execute(
        """
        SELECT event_id, original_payload, conflicting_payload
        FROM message_integrity_halts
        WHERE halt_id = 1
        """
    ).fetchone()

    assert evidence is not None
    assert evidence[0] == "EVENT-001"
    assert evidence[1] == original_payload
    assert evidence[2] == first_conflict

    # The original accepted instruction remains unchanged.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["payload_json"] == original_payload
    assert pending[0]["processing_status"] == "RECEIVED"

    receiver._connection.close()


def test_get_message_integrity_halt_is_read_only(tmp_path):
    """Halt diagnostics must preserve all stored evidence."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    # No halt should exist initially.
    assert receiver.get_message_integrity_halt() is None

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    expected = {
        "reason": "Conflicting duplicate event ID",
        "event_id": "EVENT-001",
        "original_payload": original_payload,
        "conflicting_payload": conflicting_payload,
    }

    # Repeated diagnostic reads must return the same evidence.
    assert receiver.get_message_integrity_halt() == expected
    assert receiver.get_message_integrity_halt() == expected

    # Reading the evidence must not clear the halt.
    assert receiver.has_message_integrity_halt() is True

    # The original instruction must remain unchanged.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["payload_json"] == original_payload
    assert pending[0]["processing_status"] == "RECEIVED"

    receiver._connection.close()


def test_get_message_integrity_halt_survives_restart(tmp_path):
    """Halt diagnostics must remain available after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    receiver._connection.close()

    # Simulate a complete receiver restart.
    restarted = MessageReceiver(database_path=database_path)

    evidence = restarted.get_message_integrity_halt()

    assert evidence == {
        "reason": "Conflicting duplicate event ID",
        "event_id": "EVENT-001",
        "original_payload": original_payload,
        "conflicting_payload": conflicting_payload,
    }

    # Diagnostics must not release the global halt.
    assert restarted.has_message_integrity_halt() is True

    restarted._connection.close()


def test_message_integrity_halt_report_is_read_only(tmp_path):
    """The operator report must explain the halt without changing state."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    # No report should exist before a safety halt.
    assert receiver.get_message_integrity_halt_report() is None

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    report = receiver.get_message_integrity_halt_report()

    assert report is not None
    assert "FUNDX SAFETY HALT - MESSAGE INTEGRITY" in report
    assert "Status: HALTED" in report
    assert "New instruction acceptance: BLOCKED" in report
    assert "New instruction processing: BLOCKED" in report
    assert "Event ID: EVENT-001" in report
    assert original_payload in report
    assert conflicting_payload in report
    assert "Restarting FundX does not clear this halt." in report

    # Generating the report must not release the halt.
    assert receiver.has_message_integrity_halt() is True

    # The original instruction must remain unchanged.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["payload_json"] == original_payload
    assert pending[0]["processing_status"] == "RECEIVED"

    receiver._connection.close()


def test_message_integrity_halt_report_survives_restart(tmp_path):
    """Operator safety report must remain available after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    original_report = receiver.get_message_integrity_halt_report()

    assert original_report is not None

    receiver._connection.close()

    # Simulate a complete FundX receiver restart.
    restarted = MessageReceiver(database_path=database_path)

    restarted_report = restarted.get_message_integrity_halt_report()

    # The complete report must be identical after restart.
    assert restarted_report == original_report

    assert "Status: HALTED" in restarted_report
    assert "Event ID: EVENT-001" in restarted_report
    assert original_payload in restarted_report
    assert conflicting_payload in restarted_report

    # Restarting must not authorize instruction processing.
    assert restarted.has_message_integrity_halt() is True
    assert restarted.mark_instruction_processing("EVENT-001") is False

    restarted._connection.close()


def test_receiver_safety_status_when_healthy(tmp_path):
    """A new receiver must report no active safety blockers."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    status = receiver.get_receiver_safety_status()

    assert status == {
        "safe_to_process": True,
        "message_integrity_halt": False,
        "sequence_gap": False,
        "interrupted_processing": False,
        "reasons": [],
    }

    # Reading the status must not change receiver state.
    assert receiver.get_receiver_safety_status() == status

    assert receiver.list_pending_instructions() == []
    assert receiver.list_processing_instructions() == []

    receiver._connection.close()


def test_receiver_safety_status_during_integrity_halt(tmp_path):
    """Safety status must identify a conflicting-message halt."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    status = receiver.get_receiver_safety_status()

    assert status == {
        "safe_to_process": False,
        "message_integrity_halt": True,
        "sequence_gap": False,
        "interrupted_processing": False,
        "reasons": ["MESSAGE_INTEGRITY_HALT"],
    }

    # The diagnostic must not release the halt.
    assert receiver.has_message_integrity_halt() is True

    # Processing must remain blocked.
    assert receiver.mark_instruction_processing("EVENT-001") is False

    receiver._connection.close()


def test_receiver_safety_status_during_sequence_gap(tmp_path):
    """Safety status must identify an unresolved sequence gap."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1}'
    )

    gap_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"SESSION-A",'
        '"sequence_number":3}'
    )

    # Accept the first instruction normally.
    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, first_payload
    ) is True

    # Sequence 3 arrives before sequence 2.
    assert receiver.accept_instruction(
        "EVENT-003", "SESSION-A", 3, gap_payload
    ) is False

    status = receiver.get_receiver_safety_status()

    assert status == {
        "safe_to_process": False,
        "message_integrity_halt": False,
        "sequence_gap": True,
        "interrupted_processing": False,
        "reasons": ["SEQUENCE_GAP"],
    }

    # Reading safety status must not clear the gap.
    assert receiver.has_sequence_gap("SESSION-A") is True

    # Previously accepted instructions cannot begin processing.
    assert receiver.mark_instruction_processing("EVENT-001") is False

    # The rejected instruction must not enter the inbox.
    pending = receiver.list_pending_instructions()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "EVENT-001"

    receiver._connection.close()


def test_receiver_safety_status_during_interrupted_processing(tmp_path):
    """Safety status must identify unfinished instruction processing."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    # Accept the instruction normally.
    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, payload
    ) is True

    # Claim the instruction for processing.
    assert receiver.mark_instruction_processing("EVENT-001") is True

    status = receiver.get_receiver_safety_status()

    assert status == {
        "safe_to_process": False,
        "message_integrity_halt": False,
        "sequence_gap": False,
        "interrupted_processing": True,
        "reasons": ["INTERRUPTED_PROCESSING"],
    }

    # Reading safety status must not modify the instruction.
    assert receiver.has_interrupted_processing() is True

    processing = receiver.list_processing_instructions()

    assert len(processing) == 1
    assert processing[0]["event_id"] == "EVENT-001"
    assert processing[0]["processing_status"] == "PROCESSING"

    # The instruction cannot be claimed a second time.
    assert receiver.mark_instruction_processing("EVENT-001") is False

    receiver._connection.close()


def test_receiver_safety_status_with_multiple_blockers(tmp_path):
    """Safety status must report all active blockers together."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    first_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1}'
    )

    gap_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"SESSION-A",'
        '"sequence_number":3}'
    )

    # Accept and claim the first instruction.
    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, first_payload
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # A later instruction arrives with sequence number 2 missing.
    assert receiver.accept_instruction(
        "EVENT-003", "SESSION-A", 3, gap_payload
    ) is False

    status = receiver.get_receiver_safety_status()

    assert status == {
        "safe_to_process": False,
        "message_integrity_halt": False,
        "sequence_gap": True,
        "interrupted_processing": True,
        "reasons": [
            "SEQUENCE_GAP",
            "INTERRUPTED_PROCESSING",
        ],
    }

    # Both safety problems must remain active after inspection.
    assert receiver.has_sequence_gap("SESSION-A") is True
    assert receiver.has_interrupted_processing() is True

    # No additional processing may begin.
    assert receiver.mark_instruction_processing("EVENT-001") is False

    receiver._connection.close()


def test_receiver_safety_status_with_all_three_blockers(tmp_path):
    """Safety status must report all three blockers simultaneously."""
    database_path = tmp_path / "instruction_inbox.db"
    receiver = MessageReceiver(database_path=database_path)

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    gap_payload = (
        '{"event_id":"EVENT-003",'
        '"session_id":"SESSION-A",'
        '"sequence_number":3}'
    )

    # Accept and claim the original instruction.
    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    # Trigger the message-integrity halt first.
    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    assert receiver.has_message_integrity_halt() is True

    # Simulate a separately detected sequence gap in durable state.
    receiver._connection.execute(
        """
        INSERT INTO sequence_gaps (
            session_id,
            missing_sequence_number
        )
        VALUES (?, ?)
        """,
        ("SESSION-A", 2),
    )
    receiver._connection.commit()

    status = receiver.get_receiver_safety_status()

    assert status == {
        "safe_to_process": False,
        "message_integrity_halt": True,
        "sequence_gap": True,
        "interrupted_processing": True,
        "reasons": [
            "MESSAGE_INTEGRITY_HALT",
            "SEQUENCE_GAP",
            "INTERRUPTED_PROCESSING",
        ],
    }

    # Diagnostics must not clear any safety blocker.
    assert receiver.has_message_integrity_halt() is True
    assert receiver.has_sequence_gap("SESSION-A") is True
    assert receiver.has_interrupted_processing() is True

    # Processing remains blocked.
    assert receiver.mark_instruction_processing("EVENT-001") is False

    receiver._connection.close()


def test_receiver_safety_status_all_blockers_survive_restart(tmp_path):
    """All three safety blockers must remain visible after restart."""
    database_path = tmp_path / "instruction_inbox.db"

    original_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"BUY","quantity":2}'
    )

    conflicting_payload = (
        '{"event_id":"EVENT-001",'
        '"session_id":"SESSION-A",'
        '"sequence_number":1,'
        '"action":"SELL","quantity":10}'
    )

    receiver = MessageReceiver(database_path=database_path)

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, original_payload
    ) is True

    assert receiver.mark_instruction_processing("EVENT-001") is True

    assert receiver.accept_instruction(
        "EVENT-001", "SESSION-A", 1, conflicting_payload
    ) is False

    # Simulate an additional unresolved sequence gap.
    receiver._connection.execute(
        """
        INSERT INTO sequence_gaps (
            session_id,
            missing_sequence_number
        )
        VALUES (?, ?)
        """,
        ("SESSION-A", 2),
    )
    receiver._connection.commit()

    original_status = receiver.get_receiver_safety_status()

    assert original_status["safe_to_process"] is False
    assert len(original_status["reasons"]) == 3

    receiver._connection.close()

    # Restart using the same durable SQLite database.
    restarted = MessageReceiver(database_path=database_path)

    restarted_status = restarted.get_receiver_safety_status()

    assert restarted_status == original_status

    assert restarted_status["reasons"] == [
        "MESSAGE_INTEGRITY_HALT",
        "SEQUENCE_GAP",
        "INTERRUPTED_PROCESSING",
    ]

    # Restarting must not allow processing.
    assert restarted.mark_instruction_processing("EVENT-001") is False

    # The previously claimed instruction remains unchanged.
    processing = restarted.list_processing_instructions()

    assert len(processing) == 1
    assert processing[0]["event_id"] == "EVENT-001"
    assert processing[0]["processing_status"] == "PROCESSING"

    restarted._connection.close()