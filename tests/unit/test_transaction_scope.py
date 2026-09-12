"""Route transaction lifetimes are part of the HTTP consistency contract."""

from typing import get_args

from fastapi.params import Depends

from accessforge_api.routes import (
    exports,
    findings,
    grants,
    journeys,
    projects,
    runners,
    runs,
    schedules,
    settings,
    stream,
)


def _connection_dependency(module: object) -> Depends:
    dependency = get_args(module.Conn)[1]  # type: ignore[attr-defined]
    assert isinstance(dependency, Depends)
    return dependency


def test_non_streaming_transactions_finish_before_the_response_is_sent() -> None:
    """A returned mutation identifier must already be visible to a new connection."""
    for module in (
        exports,
        findings,
        grants,
        journeys,
        projects,
        runners,
        runs,
        schedules,
        settings,
    ):
        assert _connection_dependency(module).scope == "function", module.__name__


def test_event_stream_keeps_its_connection_for_the_response_lifetime() -> None:
    """A StreamingResponse consumes its connection after the route function returns."""
    assert _connection_dependency(stream).scope in {None, "request"}
