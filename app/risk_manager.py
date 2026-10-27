from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    """IPC-controlled limits for FundX trading."""

    max_absolute_position: int

    def __post_init__(self):
        if (
            type(self.max_absolute_position) is not int
            or self.max_absolute_position <= 0
        ):
            raise ValueError(
                "max_absolute_position must be a positive integer"
            )


def calculate_approved_quantity(
    limits: RiskLimits,
    current_position: int,
    side: str,
    requested_quantity: int,
) -> int:
    """Calculate quantity allowed by the absolute position limit.

    Positive positions are long; negative positions are short.
    This calculation does not include working orders or reservations.
    """

    if type(current_position) is not int:
        raise ValueError("current_position must be an integer")

    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    if type(requested_quantity) is not int or requested_quantity <= 0:
        raise ValueError("requested_quantity must be a positive integer")

    if abs(current_position) > limits.max_absolute_position:
        raise ValueError("current position exceeds IPC risk limit")

    if side == "BUY":
        available_quantity = limits.max_absolute_position - current_position
    else:
        available_quantity = limits.max_absolute_position + current_position

    return min(requested_quantity, available_quantity)

def calculate_approved_quantity_with_working_orders(
    limits: RiskLimits,
    current_position: int,
    working_buy_quantity: int,
    working_sell_quantity: int,
    side: str,
    requested_quantity: int,
) -> int:
    """Calculate allowable quantity including outstanding orders.

    Working quantities represent remaining unfilled contracts.
    Opposing working orders are not netted against each other.
    This is a calculation only, not a durable risk reservation.
    """

    if type(current_position) is not int:
        raise ValueError("current_position must be an integer")

    if abs(current_position) > limits.max_absolute_position:
        raise ValueError("current position exceeds IPC risk limit")

    for name, quantity in (
        ("working_buy_quantity", working_buy_quantity),
        ("working_sell_quantity", working_sell_quantity),
    ):
        if type(quantity) is not int or quantity < 0:
            raise ValueError(
                f"{name} must be a non-negative integer"
            )

    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")

    if type(requested_quantity) is not int or requested_quantity <= 0:
        raise ValueError(
            "requested_quantity must be a positive integer"
        )

    max_possible_long = current_position + working_buy_quantity
    max_possible_short = current_position - working_sell_quantity

    if (
        max_possible_long > limits.max_absolute_position
        or max_possible_short < -limits.max_absolute_position
    ):
        raise ValueError(
            "existing working orders exceed IPC risk limit"
        )

    if side == "BUY":
        available_quantity = (
            limits.max_absolute_position - max_possible_long
        )
    else:
        available_quantity = (
            limits.max_absolute_position + max_possible_short
        )

    return min(requested_quantity, available_quantity)


import sqlite3


class RiskReservationStore:
    """Durably store IPC risk reservations."""

    def __init__(self, database_path=":memory:"):
        self._connection = sqlite3.connect(
            str(database_path),
            timeout=30,
            isolation_level=None,
        )

        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_reservations (
                order_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                instrument TEXT NOT NULL,
                side TEXT NOT NULL
                    CHECK (side IN ('BUY', 'SELL')),
                requested_quantity INTEGER NOT NULL
                    CHECK (requested_quantity > 0),
                approved_quantity INTEGER NOT NULL
                    CHECK (approved_quantity >= 0),
                reservation_status TEXT NOT NULL
                    CHECK (
                        reservation_status IN (
                            'ACTIVE',
                            'RELEASED'
                        )
                    )
            )
            """
        )

    def record_reservation(
        self,
        order_id: str,
        book_id: str,
        instrument: str,
        side: str,
        requested_quantity: int,
        approved_quantity: int,
    ) -> None:
        """Persist an already-authorized reservation.

        This method does not perform risk approval and must not
        be used directly by the live order-execution path.
        """
        for name, value in (
            ("order_id", order_id),
            ("book_id", book_id),
            ("instrument", instrument),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")

        if (
            type(requested_quantity) is not int
            or requested_quantity <= 0
        ):
            raise ValueError(
                "requested_quantity must be a positive integer"
            )

        if (
            type(approved_quantity) is not int
            or approved_quantity <= 0
            or approved_quantity > requested_quantity
        ):
            raise ValueError(
                "approved_quantity must be positive and "
                "cannot exceed requested_quantity"
            )

        self._connection.execute(
            """
            INSERT INTO risk_reservations (
                order_id,
                book_id,
                instrument,
                side,
                requested_quantity,
                approved_quantity,
                reservation_status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                order_id,
                book_id,
                instrument,
                side,
                requested_quantity,
                approved_quantity,
            ),
        )


    def approve_and_reserve(
        self,
        limits: RiskLimits,
        current_position: int,
        order_id: str,
        book_id: str,
        instrument: str,
        side: str,
        requested_quantity: int,
    ) -> int:
        """Atomically approve and reserve available position capacity.

        Prototype only: current_position must eventually come from
        verified broker reconciliation, not an untrusted caller.
        """
        connection = self._connection

        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                """
                SELECT 1
                FROM risk_reservations
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()

            if existing is not None:
                raise ValueError("order_id already has a reservation")

            rows = connection.execute(
                """
                SELECT side, approved_quantity
                FROM risk_reservations
                WHERE instrument = ?
                  AND reservation_status = 'ACTIVE'
                """,
                (instrument,),
            ).fetchall()

            working_buy = sum(
                quantity
                for reserved_side, quantity in rows
                if reserved_side == "BUY"
            )
            working_sell = sum(
                quantity
                for reserved_side, quantity in rows
                if reserved_side == "SELL"
            )

            approved = calculate_approved_quantity_with_working_orders(
                limits=limits,
                current_position=current_position,
                working_buy_quantity=working_buy,
                working_sell_quantity=working_sell,
                side=side,
                requested_quantity=requested_quantity,
            )

            if approved > 0:
                self.record_reservation(
                    order_id=order_id,
                    book_id=book_id,
                    instrument=instrument,
                    side=side,
                    requested_quantity=requested_quantity,
                    approved_quantity=approved,
                )

            connection.execute("COMMIT")
            return approved

        except BaseException:
            connection.execute("ROLLBACK")
            raise