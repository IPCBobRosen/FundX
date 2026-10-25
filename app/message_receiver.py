import json
import sqlite3


class MessageReceiver:
    """Durably track instruction events, sequences, and sequence gaps."""

    def __init__(self, database_path=":memory:"):
        self._connection = sqlite3.connect(
            str(database_path),
            timeout=30,
            isolation_level=None,
        )

        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS received_events (
                event_id TEXT PRIMARY KEY
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS message_sequences (
                session_id TEXT PRIMARY KEY,
                last_sequence_number INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sequence_gaps (
                session_id TEXT PRIMARY KEY,
                missing_sequence_number INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS instruction_inbox (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                processing_status TEXT NOT NULL
                    DEFAULT 'RECEIVED',
                UNIQUE(session_id, sequence_number)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS message_integrity_halts (
                halt_id INTEGER PRIMARY KEY CHECK (halt_id = 1),
                reason TEXT NOT NULL,
                event_id TEXT NOT NULL,
                original_payload TEXT NOT NULL,
                conflicting_payload TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-empty string")

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")

    @staticmethod
    def _validate_sequence_number(sequence_number: int) -> None:
        if type(sequence_number) is not int or sequence_number <= 0:
            raise ValueError("sequence_number must be a positive integer")

    def _global_gap_exists(self) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM sequence_gaps LIMIT 1"
        ).fetchone() is not None

    def _advance_sequence(self, session_id: str, sequence_number: int) -> bool:
        """Advance the sequence within an already-open write transaction."""
        cursor = self._connection.execute(
            """
            INSERT INTO message_sequences
                (session_id, last_sequence_number)
            SELECT ?, ?
            WHERE ? = 1
               OR EXISTS (
                   SELECT 1
                   FROM message_sequences
                   WHERE session_id = ?
               )
            ON CONFLICT(session_id)
            DO UPDATE SET
                last_sequence_number = excluded.last_sequence_number
            WHERE message_sequences.last_sequence_number
                  = excluded.last_sequence_number - 1
            """,
            (session_id, sequence_number, sequence_number, session_id),
        )
        return cursor.rowcount == 1

    def _record_gap_if_needed(self, session_id: str, sequence_number: int) -> None:
        """Persist the first missing number, if an existing session has a gap."""
        row = self._connection.execute(
            """
            SELECT last_sequence_number
            FROM message_sequences
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is not None and sequence_number > row[0] + 1:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO sequence_gaps
                    (session_id, missing_sequence_number)
                VALUES (?, ?)
                """,
                (session_id, row[0] + 1),
            )

    def accept_event(self, event_id: str) -> bool:
        """Accept a new event unless duplicated or globally halted."""
        self._validate_event_id(event_id)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if self._global_gap_exists():
                self._connection.execute("COMMIT")
                return False

            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO received_events (event_id)
                VALUES (?)
                """,
                (event_id,),
            )
            accepted = cursor.rowcount == 1
            self._connection.execute("COMMIT")
            return accepted
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def accept_sequence(self, session_id: str, sequence_number: int) -> bool:
        """Accept the next consecutive sequence unless globally halted."""
        self._validate_session_id(session_id)
        self._validate_sequence_number(sequence_number)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if self._global_gap_exists():
                self._connection.execute("COMMIT")
                return False

            accepted = self._advance_sequence(session_id, sequence_number)
            if not accepted:
                self._record_gap_if_needed(session_id, sequence_number)
            self._connection.execute("COMMIT")
            return accepted
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def has_sequence_gap(self, session_id: str) -> bool:
        """Return whether the specified session has a recorded gap."""
        self._validate_session_id(session_id)
        return self._connection.execute(
            """
            SELECT 1
            FROM sequence_gaps
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone() is not None

    def clear_sequence_gap(
        self,
        session_id: str,
        authorized: bool = False,
    ) -> None:
        """Remain fail-closed until verified recovery is implemented."""
        self._validate_session_id(session_id)
        if authorized is not True:
            raise PermissionError(
                "Sequence gap recovery requires authorization"
            )
        raise NotImplementedError(
            "Authorized recovery requires reconciliation "
            "and is not yet implemented"
        )

    def accept_instruction(
        self,
        event_id: str,
        session_id: str,
        sequence_number: int,
        payload_json: str | None = None,
    ) -> bool:
        """Atomically record an instruction and advance its sequence."""
        self._validate_event_id(event_id)
        self._validate_session_id(session_id)
        self._validate_sequence_number(sequence_number)

        if not isinstance(payload_json, str) or not payload_json.strip():
            raise ValueError(
                "payload_json must be a non-empty string"
            )

        if payload_json is not None:
            try:
                payload = json.loads(payload_json)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError(
                    "payload_json must contain valid JSON"
                ) from exc

            if not isinstance(payload, dict):
                raise ValueError(
                    "payload_json must contain a JSON object"
                )

            if (
                payload.get("event_id") != event_id
                or payload.get("session_id") != session_id
                or type(payload.get("sequence_number")) is not int
                or payload["sequence_number"] != sequence_number
            ):
                raise ValueError(
                    "Payload identifiers do not match instruction arguments"
                )

        self._connection.execute("BEGIN IMMEDIATE")

        try:
            # Reject new instructions during a global integrity halt.
            if self.has_message_integrity_halt():
                self._connection.execute("ROLLBACK")
                return False
            
            # No new instructions during an unresolved sequence gap.
            if self._global_gap_exists():
                self._connection.execute("ROLLBACK")
                return False

            # Reject duplicate event IDs.
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO received_events (event_id)
                VALUES (?)
                """,
                (event_id,),
            )

            if cursor.rowcount != 1:
                original = self._connection.execute(
                    """
                    SELECT payload_json
                    FROM instruction_inbox
                    WHERE event_id = ?
                    """,
                    (event_id,),
                ).fetchone()

                if original is not None and original[0] != payload_json:
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO message_integrity_halts (
                            halt_id,
                            reason,
                            event_id,
                            original_payload,
                            conflicting_payload
                        )
                        VALUES (1, ?, ?, ?, ?)
                        """,
                        (
                            "Conflicting duplicate event ID",
                            event_id,
                            original[0],
                            payload_json,
                        ),
                    )

                    # Preserve the halt even though the duplicate is rejected.
                    self._connection.execute("COMMIT")
                    return False

                self._connection.execute("ROLLBACK")
                return False

            # Advance the sequence number.
            if not self._advance_sequence(session_id, sequence_number):
                self._record_gap_if_needed(session_id, sequence_number)

                # Do not consume the rejected event ID.
                self._connection.execute(
                    """
                    DELETE FROM received_events
                    WHERE event_id = ?
                    """,
                    (event_id,),
                )

                # Preserve a newly discovered sequence gap.
                self._connection.execute("COMMIT")
                return False

            # Store the complete instruction when a payload is supplied.
            if payload_json is not None:
                self._connection.execute(
                    """
                    INSERT INTO instruction_inbox (
                        event_id,
                        session_id,
                        sequence_number,
                        payload_json,
                        processing_status
                    )
                    VALUES (?, ?, ?, ?, 'RECEIVED')
                    """,
                    (
                        event_id,
                        session_id,
                        sequence_number,
                        payload_json,
                    ),
                )

            # Event ID, sequence, and payload commit together.
            self._connection.execute("COMMIT")
            return True

        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise


    def list_pending_instructions(self) -> list[dict]:
        """Return received instructions in durable sequence order."""
        rows = self._connection.execute(
            """
            SELECT
                event_id,
                session_id,
                sequence_number,
                payload_json,
                processing_status
            FROM instruction_inbox
            WHERE processing_status = 'RECEIVED'
            ORDER BY session_id, sequence_number
            """
        ).fetchall()

        return [
            {
                "event_id": row[0],
                "session_id": row[1],
                "sequence_number": row[2],
                "payload_json": row[3],
                "processing_status": row[4],
            }
            for row in rows
        ]


    def mark_instruction_processing(self, event_id: str) -> bool:
        """Atomically claim a received instruction for internal processing."""
        self._validate_event_id(event_id)

        self._connection.execute("BEGIN IMMEDIATE")

        try:
            # Never process instructions during a message-integrity halt.
            if self.has_message_integrity_halt():
                self._connection.execute("ROLLBACK")
                return False
            
            # Never begin processing while any sequence gap exists.
            if self._global_gap_exists():
                self._connection.execute("ROLLBACK")
                return False

            # Do not claim new work while another instruction
            # remains in PROCESSING and may require reconciliation.
            if self.has_interrupted_processing():
                self._connection.execute("ROLLBACK")
                return False

            cursor = self._connection.execute(
                """
                UPDATE instruction_inbox
                SET processing_status = 'PROCESSING'
                WHERE event_id = ?
                  AND processing_status = 'RECEIVED'
                """,
                (event_id,),
            )

            self._connection.execute("COMMIT")
            return cursor.rowcount == 1

        except Exception:
            self._connection.execute("ROLLBACK")
            raise


    def list_processing_instructions(self) -> list[dict]:
        """Return instructions claimed for processing, without modifying them."""
        rows = self._connection.execute(
            """
            SELECT
                event_id,
                session_id,
                sequence_number,
                payload_json,
                processing_status
            FROM instruction_inbox
            WHERE processing_status = 'PROCESSING'
            ORDER BY session_id, sequence_number
            """
        ).fetchall()

        return [
            {
                "event_id": row[0],
                "session_id": row[1],
                "sequence_number": row[2],
                "payload_json": row[3],
                "processing_status": row[4],
            }
            for row in rows
        ]


    def has_interrupted_processing(self) -> bool:
        """Detect instructions requiring recovery inspection."""
        row = self._connection.execute(
            """
            SELECT 1
            FROM instruction_inbox
            WHERE processing_status = 'PROCESSING'
            LIMIT 1
            """
        ).fetchone()

        return row is not None


    def is_conflicting_duplicate(
        self, event_id: str, payload_json: str
    ) -> bool:
        """Detect an event ID reused with a different original payload."""
        self._validate_event_id(event_id)

        if not isinstance(payload_json, str):
            raise ValueError("payload_json must be a string")

        row = self._connection.execute(
            """
            SELECT payload_json
            FROM instruction_inbox
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if row is None:
            return False

        return row[0] != payload_json


    def has_message_integrity_halt(self) -> bool:
        """Check whether a durable message-integrity halt exists."""
        row = self._connection.execute(
            """
            SELECT 1
            FROM message_integrity_halts
            WHERE halt_id = 1
            LIMIT 1
            """
        ).fetchone()

        return row is not None


    def get_message_integrity_halt(self) -> dict | None:
        """Return durable halt evidence without modifying the database."""
        row = self._connection.execute(
            """
            SELECT
                reason,
                event_id,
                original_payload,
                conflicting_payload
            FROM message_integrity_halts
            WHERE halt_id = 1
            """
        ).fetchone()

        if row is None:
            return None

        return {
            "reason": row[0],
            "event_id": row[1],
            "original_payload": row[2],
            "conflicting_payload": row[3],
        }


    def get_message_integrity_halt_report(self) -> str | None:
        """Return a read-only, operator-friendly integrity halt report."""
        evidence = self.get_message_integrity_halt()

        if evidence is None:
            return None

        return (
            "FUNDX SAFETY HALT - MESSAGE INTEGRITY\n"
            "Status: HALTED\n"
            "New instruction acceptance: BLOCKED\n"
            "New instruction processing: BLOCKED\n"
            f"Reason: {evidence['reason']}\n"
            f"Event ID: {evidence['event_id']}\n"
            "\n"
            "Original instruction:\n"
            f"{evidence['original_payload']}\n"
            "\n"
            "Conflicting instruction:\n"
            f"{evidence['conflicting_payload']}\n"
            "\n"
            "Operator action: Investigate the conflicting instructions.\n"
            "Do not resume processing until the conflict is reconciled.\n"
            "Restarting FundX does not clear this halt."
        )


    def get_receiver_safety_status(self) -> dict:
        """Return the current receiver safety state without changing it."""
        integrity_halt = self.has_message_integrity_halt()
        sequence_gap = self._global_gap_exists()
        interrupted_processing = self.has_interrupted_processing()

        reasons = []

        if integrity_halt:
            reasons.append("MESSAGE_INTEGRITY_HALT")

        if sequence_gap:
            reasons.append("SEQUENCE_GAP")

        if interrupted_processing:
            reasons.append("INTERRUPTED_PROCESSING")

        return {
            "safe_to_process": not reasons,
            "message_integrity_halt": integrity_halt,
            "sequence_gap": sequence_gap,
            "interrupted_processing": interrupted_processing,
            "reasons": reasons,
        }