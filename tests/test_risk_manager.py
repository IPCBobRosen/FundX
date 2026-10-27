import pytest

from app.risk_manager import RiskLimits


def test_risk_limits_accept_valid_position_limit():
    """IPC can configure a positive maximum position."""
    limits = RiskLimits(max_absolute_position=10)

    assert limits.max_absolute_position == 10


@pytest.mark.parametrize(
    "invalid_limit",
    [0, -1, -10, 1.5, "10", None, True],
)
def test_risk_limits_reject_invalid_position_limit(invalid_limit):
    """Invalid risk limits must be rejected."""
    with pytest.raises(ValueError):
        RiskLimits(max_absolute_position=invalid_limit)


def test_risk_limits_cannot_be_modified():
    """An established risk-limit object must be immutable."""
    from dataclasses import FrozenInstanceError

    limits = RiskLimits(max_absolute_position=10)

    with pytest.raises(FrozenInstanceError):
        limits.max_absolute_position = 20


from app.risk_manager import calculate_approved_quantity


@pytest.mark.parametrize(
    "current_position,side,requested,expected",
    [
        (0, "BUY", 7, 7),
        (6, "BUY", 7, 4),
        (10, "BUY", 5, 0),
        (-6, "BUY", 7, 7),
        (-6, "SELL", 7, 4),
        (6, "SELL", 15, 15),
        (0, "SELL", 12, 10),
        (-10, "SELL", 5, 0),
    ],
)
def test_calculate_approved_quantity(
    current_position, side, requested, expected
):
    limits = RiskLimits(max_absolute_position=10)

    approved = calculate_approved_quantity(
        limits=limits,
        current_position=current_position,
        side=side,
        requested_quantity=requested,
    )

    assert approved == expected


@pytest.mark.parametrize(
    "current_position,side,requested",
    [
        (0, "HOLD", 5),
        (0, "BUY", 0),
        (0, "SELL", -1),
        (0, "BUY", True),
        (1.5, "BUY", 5),
        (11, "BUY", 5),
        (-11, "SELL", 5),
    ],
)
def test_calculate_approved_quantity_rejects_invalid_inputs(
    current_position, side, requested
):
    limits = RiskLimits(max_absolute_position=10)

    with pytest.raises(ValueError):
        calculate_approved_quantity(
            limits=limits,
            current_position=current_position,
            side=side,
            requested_quantity=requested,
        )


from app.risk_manager import (
    calculate_approved_quantity_with_working_orders,
)


@pytest.mark.parametrize(
    "position,working_buy,working_sell,side,requested,expected",
    [
        (4, 3, 0, "BUY", 6, 3),
        (0, 6, 6, "BUY", 8, 4),
        (-5, 0, 3, "SELL", 5, 2),
        (8, 2, 4, "BUY", 3, 0),
        (0, 0, 0, "BUY", 7, 7),
        (0, 0, 0, "SELL", 12, 10),
        (6, 0, 0, "SELL", 15, 15),
        (-6, 0, 0, "BUY", 15, 15),
    ],
)
def test_approved_quantity_with_working_orders(
    position, working_buy, working_sell, side, requested, expected
):
    limits = RiskLimits(max_absolute_position=10)

    approved = calculate_approved_quantity_with_working_orders(
        limits=limits,
        current_position=position,
        working_buy_quantity=working_buy,
        working_sell_quantity=working_sell,
        side=side,
        requested_quantity=requested,
    )

    assert approved == expected


@pytest.mark.parametrize(
    "position,working_buy,working_sell,side,requested",
    [
        (0, -1, 0, "BUY", 5),
        (0, 0, -1, "SELL", 5),
        (0, True, 0, "BUY", 5),
        (0, 0, 1.5, "SELL", 5),
        (0, 0, 0, "HOLD", 5),
        (0, 0, 0, "BUY", 0),
        (0, 0, 0, "SELL", -1),
        (0, 0, 0, "BUY", True),
        (11, 0, 0, "BUY", 5),
        (-11, 0, 0, "SELL", 5),
        (8, 3, 0, "BUY", 5),
        (-8, 0, 3, "SELL", 5),
    ],
)
def test_working_order_risk_rejects_invalid_exposure(
    position, working_buy, working_sell, side, requested
):
    limits = RiskLimits(max_absolute_position=10)

    with pytest.raises(ValueError):
        calculate_approved_quantity_with_working_orders(
            limits=limits,
            current_position=position,
            working_buy_quantity=working_buy,
            working_sell_quantity=working_sell,
            side=side,
            requested_quantity=requested,
        )


from app.risk_manager import RiskReservationStore


def test_risk_reservation_table_created():
    """Risk reservation database must contain the required table."""
    store = RiskReservationStore()

    row = store._connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'risk_reservations'
        """
    ).fetchone()

    assert row is not None
    assert row[0] == "risk_reservations"

    store._connection.close()


def test_risk_reservation_table_survives_restart(tmp_path):
    """Reservation table must persist across database restarts."""
    database_path = tmp_path / "fundx_risk.db"

    first_store = RiskReservationStore(
        database_path=database_path
    )

    first_store._connection.close()

    restarted_store = RiskReservationStore(
        database_path=database_path
    )

    row = restarted_store._connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'risk_reservations'
        """
    ).fetchone()

    assert row is not None
    assert row[0] == "risk_reservations"

    restarted_store._connection.close()


def test_risk_reservation_record_survives_restart(tmp_path):
    """An active reservation must survive closing and reopening SQLite."""
    database_path = tmp_path / "fundx_risk.db"

    first_store = RiskReservationStore(database_path)

    first_store.record_reservation(
        order_id="ORDER-001",
        book_id="BOOK-A",
        instrument="BIP",
        side="BUY",
        requested_quantity=5,
        approved_quantity=5,
    )

    first_store._connection.close()

    restarted_store = RiskReservationStore(database_path)

    row = restarted_store._connection.execute(
        """
        SELECT order_id, book_id, instrument, side,
               requested_quantity, approved_quantity,
               reservation_status
        FROM risk_reservations
        WHERE order_id = ?
        """,
        ("ORDER-001",),
    ).fetchone()

    assert row == (
        "ORDER-001",
        "BOOK-A",
        "BIP",
        "BUY",
        5,
        5,
        "ACTIVE",
    )

    restarted_store._connection.close()


def test_duplicate_risk_reservation_is_rejected():
    """The same order must not create a second reservation."""
    import sqlite3

    store = RiskReservationStore()

    reservation = dict(
        order_id="ORDER-001",
        book_id="BOOK-A",
        instrument="BIP",
        side="BUY",
        requested_quantity=5,
        approved_quantity=5,
    )

    store.record_reservation(**reservation)

    with pytest.raises(sqlite3.IntegrityError):
        store.record_reservation(**reservation)

    count = store._connection.execute(
        """
        SELECT COUNT(*)
        FROM risk_reservations
        WHERE order_id = ?
        """,
        ("ORDER-001",),
    ).fetchone()[0]

    assert count == 1

    store._connection.close()


def test_atomic_reservations_share_available_capacity(tmp_path):
    """Two strategy books must not reserve the same capacity."""
    database_path = tmp_path / "fundx_risk.db"

    store = RiskReservationStore(database_path)
    limits = RiskLimits(max_absolute_position=10)

    first_approved = store.approve_and_reserve(
        limits=limits,
        current_position=4,
        order_id="ORDER-A",
        book_id="BOOK-A",
        instrument="BIP",
        side="BUY",
        requested_quantity=5,
    )

    second_approved = store.approve_and_reserve(
        limits=limits,
        current_position=4,
        order_id="ORDER-B",
        book_id="BOOK-B",
        instrument="BIP",
        side="BUY",
        requested_quantity=5,
    )

    assert first_approved == 5
    assert second_approved == 1

    rows = store._connection.execute(
        """
        SELECT order_id, approved_quantity
        FROM risk_reservations
        WHERE reservation_status = 'ACTIVE'
        ORDER BY order_id
        """
    ).fetchall()

    assert rows == [
        ("ORDER-A", 5),
        ("ORDER-B", 1),
    ]

    store._connection.close()


def test_separate_connections_share_risk_reservations(tmp_path):
    """Separate database connections must share reserved capacity."""
    database_path = tmp_path / "fundx_risk.db"

    first_store = RiskReservationStore(database_path)
    second_store = RiskReservationStore(database_path)

    limits = RiskLimits(max_absolute_position=10)

    first_approved = first_store.approve_and_reserve(
        limits=limits,
        current_position=4,
        order_id="ORDER-A",
        book_id="BOOK-A",
        instrument="BIP",
        side="BUY",
        requested_quantity=5,
    )

    second_approved = second_store.approve_and_reserve(
        limits=limits,
        current_position=4,
        order_id="ORDER-B",
        book_id="BOOK-B",
        instrument="BIP",
        side="BUY",
        requested_quantity=5,
    )

    assert first_approved == 5
    assert second_approved == 1

    first_store._connection.close()
    second_store._connection.close()


def test_concurrent_risk_reservations_do_not_exceed_limit(tmp_path):
    """Concurrent requests must not reserve the same available capacity.

    Each worker uses a separate SQLite connection, simulating
    independent FundX processes competing for IPC risk capacity.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database_path = tmp_path / "fundx_risk.db"
    limits = RiskLimits(max_absolute_position=10)

    # Initialize the shared database before starting either worker.
    initial_store = RiskReservationStore(database_path)
    initial_store._connection.close()

    # Ensure both workers are ready before they attempt approval.
    # This increases the chance of competing database transactions.
    start_barrier = Barrier(2)

    def reserve_for_book(order_id, book_id):
        """Request five contracts through an independent connection."""
        store = RiskReservationStore(database_path)

        try:
            start_barrier.wait(timeout=10)

            # Approval and reservation must happen atomically.
            return store.approve_and_reserve(
                limits=limits,
                current_position=4,
                order_id=order_id,
                book_id=book_id,
                instrument="BIP",
                side="BUY",
                requested_quantity=5,
            )
        finally:
            store._connection.close()

    # Launch competing requests from two strategy books.
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            reserve_for_book, "ORDER-A", "BOOK-A"
        )
        second = executor.submit(
            reserve_for_book, "ORDER-B", "BOOK-B"
        )

        approvals = [first.result(), second.result()]

    # Either book may win the race, but total approval must
    # never exceed the six contracts remaining under IPC's limit.
    assert sorted(approvals) == [1, 5]
    assert sum(approvals) == 6

    # Independently verify the durable database records.
    verification_store = RiskReservationStore(database_path)

    rows = verification_store._connection.execute(
        """
        SELECT approved_quantity
        FROM risk_reservations
        WHERE reservation_status = 'ACTIVE'
        """
    ).fetchall()

    assert len(rows) == 2
    assert sum(quantity for (quantity,) in rows) == 6

    verification_store._connection.close()


def test_risk_reservation_waits_for_existing_write_transaction(tmp_path):
    """A competing approval must wait for an existing SQLite writer.

    This test deliberately holds the database write lock, then
    verifies that another connection cannot approve and reserve
    contracts until the first transaction has committed.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    database_path = tmp_path / "fundx_risk.db"
    limits = RiskLimits(max_absolute_position=10)

    # Create two independent connections to the same database.
    first_store = RiskReservationStore(database_path)
    

    # The first connection acquires SQLite's write lock.
    first_store._connection.execute("BEGIN IMMEDIATE")

    worker_started = Event()
    worker_finished = Event()

    def competing_approval():
        """Attempt approval while another connection holds the lock."""
        second_store = RiskReservationStore(database_path)
        worker_started.set()

        try:
            # This call must wait for the first transaction to finish.
            return second_store.approve_and_reserve(
                limits=limits,
                current_position=4,
                order_id="ORDER-B",
                book_id="BOOK-B",
                instrument="BIP",
                side="BUY",
                requested_quantity=5,
            )
        finally:
            # Signal that the worker has finished its approval attempt.
            worker_finished.set()

            # Close the SQLite connection in the same thread
            # that created it, avoiding cross-thread access.
            second_store._connection.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(competing_approval)

            assert worker_started.wait(timeout=5)

            # The competing approval must not finish while the
            # first connection holds SQLite's write transaction.
            assert not worker_finished.wait(timeout=0.2)

            # Release the write lock so the competing request
            # can calculate capacity and commit its reservation.
            first_store._connection.execute("COMMIT")

            assert future.result(timeout=5) == 5

    finally:
        if first_store._connection.in_transaction:
            first_store._connection.execute("ROLLBACK")

        # The worker closes its own connection.
        # Only the main-thread connection remains to be closed.
        first_store._connection.close()


def test_failed_risk_approval_rolls_back_transaction(tmp_path):
    """A failed approval must not leave an open transaction or reservation.

    This protects FundX from retaining incomplete risk state after
    an invalid instruction. A subsequent valid instruction must
    still be able to reserve capacity normally.
    """
    database_path = tmp_path / "fundx_risk.db"
    store = RiskReservationStore(database_path)
    limits = RiskLimits(max_absolute_position=10)

    try:
        # An invalid order direction must fail risk validation.
        with pytest.raises(ValueError):
            store.approve_and_reserve(
                limits=limits,
                current_position=4,
                order_id="ORDER-INVALID",
                book_id="BOOK-A",
                instrument="BIP",
                side="HOLD",
                requested_quantity=5,
            )

        # A failed approval must release the transaction lock.
        assert not store._connection.in_transaction

        # No reservation should have been created.
        count = store._connection.execute(
            """
            SELECT COUNT(*)
            FROM risk_reservations
            """
        ).fetchone()[0]

        assert count == 0

        # Confirm the database remains usable after rollback.
        approved = store.approve_and_reserve(
            limits=limits,
            current_position=4,
            order_id="ORDER-VALID",
            book_id="BOOK-A",
            instrument="BIP",
            side="BUY",
            requested_quantity=5,
        )

        assert approved == 5

    finally:
        # Always close the connection, even if an assertion fails.
        store._connection.close()


def test_reservation_write_failure_rolls_back(tmp_path):
    """A database write failure must not produce an approval.

    Simulate a SQLite storage error during reservation creation.
    FundX must roll back the transaction, retain no reservation,
    and propagate the failure rather than return an approval.
    """
    import sqlite3

    database_path = tmp_path / "fundx_risk.db"
    store = RiskReservationStore(database_path)
    limits = RiskLimits(max_absolute_position=10)

    try:
        # This test-only trigger forces every reservation insert
        # to fail, simulating a database write problem.
        store._connection.execute(
            """
            CREATE TRIGGER reject_risk_reservation_insert
            BEFORE INSERT ON risk_reservations
            BEGIN
                SELECT RAISE(ABORT, 'simulated reservation write failure');
            END
            """
        )

        # Risk approval must fail when its reservation cannot
        # be committed to the durable database.
        with pytest.raises(
            sqlite3.IntegrityError,
            match="simulated reservation write failure",
        ):
            store.approve_and_reserve(
                limits=limits,
                current_position=4,
                order_id="ORDER-FAIL",
                book_id="BOOK-A",
                instrument="BIP",
                side="BUY",
                requested_quantity=5,
            )

        # The failed operation must not leave a transaction open.
        assert not store._connection.in_transaction

        # No reservation may exist for the failed instruction.
        count = store._connection.execute(
            """
            SELECT COUNT(*)
            FROM risk_reservations
            WHERE order_id = ?
            """,
            ("ORDER-FAIL",),
        ).fetchone()[0]

        assert count == 0

    finally:
        # Close the database even if an assertion fails.
        store._connection.close()