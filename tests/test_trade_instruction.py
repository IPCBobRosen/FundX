
from app.models.trade_instruction import TradeInstruction
from decimal import Decimal


def make_valid_instruction(**overrides):
    """Create a valid sample instruction for automated tests."""

    from dataclasses import replace

    instruction = TradeInstruction(
        schema_version="1.0",
        event_id="J-EVT-000184",
        session_id="FUNDX-20261022-01",
        sequence_number=184,
        strategy_release_id="X_HB-1.0",
        strategy_id="X_HB_CAP2",
        book_id="X_HB_CAP2_A",
        position_id="3f9c...",
        leg_id="RUNG-1",
        order_id="J-10482",
        replaces_order_id=None,
        action="NEW",
        intent_role="ENTRY",
        instrument="COINBASE_BTC_CONTRACT_TBD",
        side="BUY",
        quantity=10,
        order_type="LIMIT",
        limit_price=Decimal("70000.00"),
        time_in_force="GTC",
        expire_at_utc="2026-10-23T02:05:00.000Z",
        reduce_only=False,
        post_only=False,
        decision_timestamp_utc="2026-10-22T08:00:00.000Z",
        published_timestamp_utc="2026-10-22T08:00:00.125Z",
        valid_until_utc="2026-10-22T08:00:05.000Z",
        reference_bar_close_utc="2026-10-22T08:00:00.000Z",
        reason_code="ENTRY_RUNG",
    )

    return replace(instruction, **overrides)


def test_entry_rung_instruction():
    """Verify Jack's entry-rung instruction fields."""

    instruction = TradeInstruction(
        schema_version="1.0",
        event_id="J-EVT-000184",
        session_id="FUNDX-20261022-01",
        sequence_number=184,
        strategy_release_id="X_HB-1.0",
        strategy_id="X_HB_CAP2",
        book_id="X_HB_CAP2_A",
        position_id="3f9c...",
        leg_id="RUNG-1",
        order_id="J-10482",
        replaces_order_id=None,
        action="NEW",
        intent_role="ENTRY",
        instrument="COINBASE_BTC_CONTRACT_TBD",
        side="BUY",
        quantity=10,
        order_type="LIMIT",
        limit_price=Decimal("70000.00"),
        time_in_force="GTC",
        expire_at_utc="2026-10-23T02:05:00.000Z",
        reduce_only=False,
        post_only=False,
        decision_timestamp_utc="2026-10-22T08:00:00.000Z",
        published_timestamp_utc="2026-10-22T08:00:00.125Z",
        valid_until_utc="2026-10-22T08:00:05.000Z",
        reference_bar_close_utc="2026-10-22T08:00:00.000Z",
        reason_code="ENTRY_RUNG",
    )

    assert instruction.strategy_id == "X_HB_CAP2"
    assert instruction.book_id == "X_HB_CAP2_A"
    assert instruction.leg_id == "RUNG-1"
    assert instruction.order_id == "J-10482"
    assert instruction.quantity == 10
    assert instruction.limit_price == Decimal("70000.00")
    assert isinstance(instruction.limit_price, Decimal)
    assert instruction.action == "NEW"
    assert instruction.order_type == "LIMIT"
    assert instruction.reduce_only is False


def test_reject_zero_quantity():
    """FundX must reject an order requesting zero contracts."""

    import pytest

    with pytest.raises(ValueError, match="quantity"):
        make_valid_instruction(quantity=0)



def test_reject_negative_quantity():
    """FundX must reject an order requesting negative contracts."""

    import pytest

    with pytest.raises(ValueError, match="quantity"):
        make_valid_instruction(quantity=-5)


def test_reject_fractional_quantity():
    """FundX must reject fractional contract quantities."""

    import pytest

    with pytest.raises(ValueError, match="quantity"):
        make_valid_instruction(quantity=2.5)


def test_reject_zero_limit_price():
    """FundX must reject a LIMIT order with a zero price."""

    import pytest

    with pytest.raises(ValueError, match="limit_price"):
        make_valid_instruction(limit_price=Decimal("0"))


def test_reject_negative_limit_price():
    """FundX must reject a LIMIT order with a negative price."""

    import pytest

    with pytest.raises(ValueError, match="limit_price"):
        make_valid_instruction(limit_price=Decimal("-100.00"))

def test_reject_missing_limit_price():
    """FundX must reject a LIMIT order without a price."""

    import pytest

    with pytest.raises(ValueError, match="limit_price"):
        make_valid_instruction(limit_price=None)


def test_market_order_without_limit_price():
    """A MARKET order can omit its limit price."""

    instruction = make_valid_instruction(
        order_type="MARKET",
        limit_price=None,
    )

    assert instruction.order_type == "MARKET"
    assert instruction.limit_price is None


def test_reject_unknown_order_type():
    """FundX must reject unrecognized order types."""

    import pytest

    with pytest.raises(ValueError, match="order_type"):
        make_valid_instruction(
            order_type="LIMT",
        )


def test_reject_unknown_action():
    """FundX must reject unrecognized order actions."""

    import pytest

    with pytest.raises(ValueError, match="action"):
        make_valid_instruction(action="NEWW")


def test_replace_requires_previous_order_id():
    """A REPLACE instruction must identify the previous order."""

    import pytest

    with pytest.raises(ValueError, match="replaces_order_id"):
        make_valid_instruction(
            action="REPLACE",
            order_id="J-10482-REV1",
            replaces_order_id=None,
        )


def test_replace_rejects_empty_previous_order_id():
    """A REPLACE instruction must not have an empty previous order ID."""

    import pytest

    with pytest.raises(ValueError, match="replaces_order_id"):
        make_valid_instruction(
            action="REPLACE",
            order_id="J-10482-REV1",
            replaces_order_id="",
        )


def test_replace_rejects_identical_order_ids():
    """A replacement must use a different order ID."""

    import pytest

    with pytest.raises(ValueError, match="order_id"):
        make_valid_instruction(
            action="REPLACE",
            order_id="J-10482",
            replaces_order_id="J-10482",
        )


def test_accept_valid_replace_instruction():
    """A valid REPLACE instruction must pass model validation."""

    instruction = make_valid_instruction(
        action="REPLACE",
        order_id="J-10482-REV1",
        replaces_order_id="J-10482",
        limit_price=Decimal("69950.00"),
    )

    assert instruction.action == "REPLACE"
    assert instruction.order_id == "J-10482-REV1"
    assert instruction.replaces_order_id == "J-10482"
    assert instruction.limit_price == Decimal("69950.00")


def test_accept_cancel_action():
    """FundX must recognize a CANCEL with a valid target order ID."""

    instruction = make_valid_instruction(
        action="CANCEL",
        order_id="J-CANCEL-10482",
        replaces_order_id="J-10482",
        quantity=None,
        limit_price=None,
    )

    assert instruction.action == "CANCEL"
    assert instruction.order_id == "J-CANCEL-10482"
    assert instruction.replaces_order_id == "J-10482"


def test_cancel_requires_target_order_id():
    """A CANCEL instruction must identify the order to cancel."""

    import pytest

    with pytest.raises(ValueError, match="replaces_order_id"):
        make_valid_instruction(
            action="CANCEL",
            order_id="J-CANCEL-10482",
            replaces_order_id=None,
        )


def test_cancel_can_omit_quantity():
    """A CANCEL instruction does not require a trade quantity."""

    instruction = make_valid_instruction(
        action="CANCEL",
        order_id="J-CANCEL-10482",
        replaces_order_id="J-10482",
        quantity=None,
        limit_price=None,
    )

    assert instruction.action == "CANCEL"
    assert instruction.quantity is None


def test_cancel_can_omit_limit_price():
    """A CANCEL instruction does not require a limit price."""

    instruction = make_valid_instruction(
        action="CANCEL",
        order_id="J-CANCEL-10482",
        replaces_order_id="J-10482",
        quantity=None,
        limit_price=None,
    )

    assert instruction.action == "CANCEL"
    assert instruction.limit_price is None


def test_cancel_rejects_limit_price():
    """A CANCEL instruction must not specify a limit price."""

    import pytest

    with pytest.raises(ValueError, match="limit_price"):
        make_valid_instruction(
            action="CANCEL",
            order_id="J-CANCEL-10482",
            replaces_order_id="J-10482",
            quantity=None,
            limit_price=Decimal("70000.00"),
        )


def test_new_rejects_previous_order_id():
    """A NEW instruction must not reference an existing order."""

    import pytest

    with pytest.raises(ValueError, match="replaces_order_id"):
        make_valid_instruction(
            action="NEW",
            replaces_order_id="J-10482",
        )


def test_reject_empty_order_id():
    """FundX must reject an instruction with an empty order ID."""

    import pytest

    with pytest.raises(ValueError, match="order_id"):
        make_valid_instruction(
            order_id="",
        )


def test_reject_whitespace_order_id():
    """FundX must reject an order ID containing only whitespace."""

    import pytest

    with pytest.raises(ValueError, match="order_id"):
        make_valid_instruction(
            order_id="   ",
        )


def test_reject_non_string_order_id():
    """FundX must reject an order ID that is not a string."""

    import pytest

    with pytest.raises(ValueError, match="order_id"):
        make_valid_instruction(
            order_id=10482,
        )


def test_reject_empty_event_id():
    """FundX must reject an instruction with an empty event ID."""

    import pytest

    with pytest.raises(ValueError, match="event_id"):
        make_valid_instruction(
            event_id="",
        )


def test_reject_whitespace_event_id():
    """FundX must reject an event ID containing only whitespace."""

    import pytest

    with pytest.raises(ValueError, match="event_id"):
        make_valid_instruction(
            event_id="   ",
        )


def test_reject_empty_session_id():
    """FundX must reject an instruction with an empty session ID."""

    import pytest

    with pytest.raises(ValueError, match="session_id"):
        make_valid_instruction(
            session_id="",
        )


def test_reject_zero_sequence_number():
    """FundX must reject a sequence number of zero."""

    import pytest

    with pytest.raises(ValueError, match="sequence_number"):
        make_valid_instruction(
            sequence_number=0,
        )


def test_reject_negative_sequence_number():
    """FundX must reject negative message sequence numbers."""

    import pytest

    with pytest.raises(ValueError, match="sequence_number"):
        make_valid_instruction(
            sequence_number=-1,
        )


def test_reject_fractional_sequence_number():
    """FundX must reject fractional message sequence numbers."""

    import pytest

    with pytest.raises(ValueError, match="sequence_number"):
        make_valid_instruction(
            sequence_number=184.5,
        )


def test_reject_boolean_sequence_number():
    """FundX must reject Boolean message sequence numbers."""

    import pytest

    with pytest.raises(ValueError, match="sequence_number"):
        make_valid_instruction(
            sequence_number=True,
        )


def test_reject_whitespace_session_id():
    """FundX must reject session IDs containing only whitespace."""

    import pytest

    with pytest.raises(ValueError, match="session_id"):
        make_valid_instruction(
            session_id="   ",
        )


def test_reject_non_string_session_id():
    """FundX must reject session IDs that are not strings."""

    import pytest

    with pytest.raises(ValueError, match="session_id"):
        make_valid_instruction(
            session_id=12345,
        )