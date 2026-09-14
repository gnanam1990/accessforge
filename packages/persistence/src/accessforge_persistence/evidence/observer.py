"""The completion observer, implemented against a real application database.

Module 11's `CompletionObserver` protocol, backed by PostgreSQL. This is the component that reads
the application under test's own durable state, and the reason a screen-reader result means
anything.

Two implementation decisions carry the protocol's guarantees into practice.

**A read-only connection, enforced by the session.** `SET TRANSACTION READ ONLY` makes an attempted
write fail at the database rather than relying on this class not containing one. The difference
matters because the realistic way an observer gains a write is someone adding a convenience helper
to it -- it already has a connection to the application, and resetting a fixture from here looks
tidy. A read-only transaction makes that attempt fail instead of succeed.

**It counts rows, never reads a receipt.** The application's own `service_request` table, filtered
to this run's fixture nonce. Not an HTTP endpoint the application serves, because an endpoint
returns
what the application chooses to say; not the DOM, because the DOM is what the page chose to display.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from accessforge_domain.evaluation.observer import EffectCount, ObserverError
from accessforge_domain.timestamps import to_rfc3339_utc

#: Effects this observer knows how to count, mapped to the application table that holds them.
#:
#: A closed mapping, because the alternative is accepting a table name from a caller and running a
#: query built from it. An observer that could be pointed at an arbitrary table by its configuration
#: would be one configuration mistake away from counting rows in the product's own database and
#: reporting them as the application's.
_EFFECT_TABLES: dict[str, str] = {
    "CREATE_TEST_REQUEST": "public.service_request",
}


class ApplicationObserver:
    """Counts effects in the application under test. Read-only, by transaction."""

    def __init__(self, application_database_url: str) -> None:
        self._url = application_database_url

    def inspect_fixture(self, *, fixture_nonce: str) -> dict[str, Any]:
        """One read-only snapshot of original fixture metadata and its durable effect count."""
        try:
            with psycopg.connect(
                self._url,
                row_factory=dict_row,
                autocommit=False,
                connect_timeout=5,
                options="-c statement_timeout=5000 -c lock_timeout=1000",
            ) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                row = conn.execute(
                    "SELECT f.nonce,f.template_digest,f.variant,f.created_at,"
                    "(SELECT count(*) FROM public.service_request r WHERE r.fixture_nonce=f.nonce) "
                    "AS effect_count FROM public.fixture_instance f WHERE f.nonce=%s",
                    (fixture_nonce,),
                ).fetchone()
                if row is None:
                    raise ObserverError("application fixture is unavailable")
                return {
                    "nonce": row["nonce"],
                    "templateDigest": row["template_digest"],
                    "variant": row["variant"],
                    "createdAt": to_rfc3339_utc(row["created_at"]),
                    "effectCount": int(row["effect_count"]),
                    "observedAt": to_rfc3339_utc(datetime.now(UTC)),
                }
        except psycopg.Error as exc:
            raise ObserverError(
                "application fixture inspection unavailable, not an empty fixture"
            ) from exc

    def count_effects(
        self, *, fixture_nonce: str, effect: str, expected_template_digest: str | None = None
    ) -> EffectCount:
        table = _EFFECT_TABLES.get(effect)
        if table is None:
            # Not an unknown observation -- an unknown *question*. The distinction matters: a caller
            # asking about an effect nobody implemented has a configuration error, and reporting it
            # as "we could not observe" would send someone to look at the application instead.
            raise ObserverError(
                f"{effect!r} is not an effect this observer knows how to count. The known effects "
                f"are {', '.join(sorted(_EFFECT_TABLES))}. This is a configuration error, not an "
                "unobservable state."
            )

        try:
            with psycopg.connect(
                self._url,
                row_factory=dict_row,
                autocommit=False,
                connect_timeout=5,
                options="-c statement_timeout=5000 -c lock_timeout=1000",
            ) as conn:
                # Read-only at the database, not by convention. An attempted write inside this
                # transaction fails even if someone adds one to this class.
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                if expected_template_digest is not None:
                    fixture = conn.execute(
                        "SELECT template_digest FROM public.fixture_instance WHERE nonce=%s",
                        (fixture_nonce,),
                    ).fetchone()
                    if fixture is None or fixture["template_digest"] != expected_template_digest:
                        raise ObserverError(
                            "application fixture identity is unavailable or mismatched"
                        )
                row: dict[str, Any] | None = conn.execute(
                    # The table name comes from the closed mapping above, never from a parameter.
                    # The nonce is bound.
                    f"SELECT count(*) AS n FROM {table} WHERE fixture_nonce = %s",  # noqa: S608
                    (fixture_nonce,),
                ).fetchone()
        except psycopg.Error as exc:
            # Every database failure is an unknown, never a zero. A connection refused and "the
            # application holds no requests" are opposite conclusions, and returning 0 here would
            # report a confirmed task failure every time the observer could not connect.
            raise ObserverError(
                "could not read the application's state. This is an unknown, not a count "
                "of zero: being unable to look is not evidence that nothing happened."
            ) from exc

        return EffectCount(
            fixture_nonce=fixture_nonce,
            effect=effect,
            count=int(row["n"]) if row else 0,
            observed_at=to_rfc3339_utc(datetime.now(UTC)),
        )
