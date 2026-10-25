
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class TradeInstruction:
    """One trading instruction received from Jack's strategy engine."""

    # Message identity and ordering
    schema_version: str
    event_id: str
    session_id: str
    sequence_number: int

    # Strategy and book identity
    strategy_release_id: str
    strategy_id: str
    book_id: str
    position_id: str
    leg_id: str

    # Order identity
    order_id: str
    replaces_order_id: Optional[str]

    # Trading instruction
    action: str
    intent_role: str
    instrument: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[Decimal]
    time_in_force: Optional[str]
    expire_at_utc: Optional[str]
    reduce_only: bool
    post_only: bool

    # Strategy timing and telemetry
    decision_timestamp_utc: str
    published_timestamp_utc: str
    valid_until_utc: str
    reference_bar_close_utc: Optional[str]
    reason_code: str

    def __post_init__(self):
        """Validate basic order type, quantity, and limit price."""

        if (
            not isinstance(self.order_id, str)
            or not self.order_id.strip()
        ):
            raise ValueError(
                "order_id must be a non-empty string"
            )

        if (
            not isinstance(self.event_id, str)
            or not self.event_id.strip()
        ):
            raise ValueError(
                "event_id must be a non-empty string"
            )

        if (
            not isinstance(self.session_id, str)
            or not self.session_id.strip()
        ):
            raise ValueError(
                "session_id must be a non-empty string"
            )

        if (
            type(self.sequence_number) is not int
            or self.sequence_number <= 0
        ):
            raise ValueError(
                "sequence_number must be a positive integer"
            )

        allowed_actions = {"NEW", "REPLACE", "CANCEL"}

        if self.action not in allowed_actions:
            raise ValueError(
                f"action '{self.action}' is not supported"
            )

        if self.action == "NEW" and self.replaces_order_id is not None:
            raise ValueError(
                "replaces_order_id must be None for NEW instructions"
            )

        if self.action in {"REPLACE", "CANCEL"}:
            if (
                not isinstance(self.replaces_order_id, str)
                or not self.replaces_order_id.strip()
            ):
                raise ValueError(
                    "replaces_order_id is required for REPLACE instructions"
                )
            if self.order_id == self.replaces_order_id:
                    raise ValueError(
                        "order_id must differ from replaces_order_id "
                        "for REPLACE instructions"
                )

        allowed_order_types = {"LIMIT", "MARKET"}

        if self.order_type not in allowed_order_types:
            raise ValueError(
                f"order_type '{self.order_type}' is not supported"
            )

        if self.action == "CANCEL":
            if self.quantity is not None:
                raise ValueError(
                    "quantity must be None for CANCEL instructions"
                )
            if self.limit_price is not None:
                raise ValueError(
                    "limit_price must be None for CANCEL instructions"
                )
        else:
            if type(self.quantity) is not int or self.quantity <= 0:
                raise ValueError(
                    "quantity must be a positive integer"
                )

        if self.action != "CANCEL" and self.order_type == "LIMIT":
            if (
                not isinstance(self.limit_price, Decimal)
                or not self.limit_price.is_finite()
                or self.limit_price <= 0
            ):
                raise ValueError(
                    "limit_price must be a positive, finite Decimal "
                    "for LIMIT orders"
                )
