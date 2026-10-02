"""Health check route registration for FastAPI apps.

The public entry point is `add_health_check(app, ...)`: it registers a `GET`
endpoint (by default `/healthz`) that runs optional dependency probes and answers
`200` with `{"status": "ok", ...}` when everything is healthy, or `503` with
`{"status": "degraded", ...}` when any probe fails, errors out or times out.

The app is always passed in explicitly, so nothing is shared between app
instances, and importing this module has no side effects: no app is created,
mutated or registered until `add_health_check()` is called.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, Literal

from annotated_doc import Doc
from fastapi import FastAPI, Response, status
from fastapi.exceptions import FastAPIError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.routing import BaseRoute

__all__ = [
    "DEFAULT_HEALTH_CHECK_METHODS",
    "DEFAULT_HEALTH_CHECK_PATH",
    "DEFAULT_PROBE_TIMEOUT",
    "DegradedResponse",
    "HealthPayload",
    "HealthResponse",
    "Probe",
    "ProbeOutcome",
    "ProbeResult",
    "add_health_check",
    "build_payload",
    "register_route",
    "remove_health_check",
    "run_probes",
]

DEFAULT_HEALTH_CHECK_PATH = "/healthz"
DEFAULT_HEALTH_CHECK_METHODS = ["GET"]
DEFAULT_PROBE_TIMEOUT = 5.0
MAX_DETAIL_LENGTH = 200
HEALTH_CHECK_ROUTE_MARKER = "fastapi_health_check"


class ProbeResult(BaseModel):
    """A single check entry, as returned by a probe and as reported in the payload."""

    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    """Payload returned when every probe passes (or when no probe is configured)."""

    status: Literal["ok"] = "ok"
    checks: dict[str, ProbeResult] = Field(default_factory=dict)


class DegradedResponse(BaseModel):
    """Payload returned when at least one probe fails, errors or times out."""

    status: Literal["degraded"] = "degraded"
    checks: dict[str, ProbeResult] = Field(default_factory=dict)


HealthPayload = Annotated[
    HealthResponse | DegradedResponse, Field(discriminator="status")
]
"""Response model for the health check route: a discriminated union on `status`."""

ProbeOutcome = bool | ProbeResult | None
"""What a probe may report: `True`/`False`, a `ProbeResult`, or `None` (means ok)."""

Probe = Callable[[], Awaitable[ProbeOutcome]]
"""An awaitable, side effect free dependency check."""


async def run_probes(
    probes: Mapping[str, Probe],
    *,
    timeout: float | None = DEFAULT_PROBE_TIMEOUT,
    expose_error_messages: bool = False,
) -> dict[str, ProbeResult]:
    """Run every probe concurrently and collect one `ProbeResult` per probe.

    Probes run in declaration order through `asyncio.gather`, so a slow dependency
    does not add up to the latency of the others. A probe that raises, or that
    does not finish within `timeout` seconds, is reported as a failed check
    instead of breaking the whole health check.

    Exceptions are reported by type name only; pass `expose_error_messages=True`
    to also surface the exception message, truncated to `MAX_DETAIL_LENGTH`.
    """
    if not probes:
        return {}
    items = list(probes.items())
    results = await asyncio.gather(
        *(
            _run_probe(name, probe, timeout, expose_error_messages)
            for name, probe in items
        )
    )
    return {
        name: result
        for (name, _probe), result in zip(items, results, strict=True)
    }


def build_payload(checks: Mapping[str, ProbeResult]) -> HealthPayload:
    """Map probe results to the `ok` or `degraded` payload for the response."""
    healthy = all(check.ok for check in checks.values())
    return HealthResponse(checks=dict(checks)) if healthy else DegradedResponse(
        checks=dict(checks)
    )


def register_route(
    app: FastAPI,
    *,
    path: str,
    probes: Mapping[str, Probe],
    timeout: float | None = DEFAULT_PROBE_TIMEOUT,
    expose_error_messages: bool = False,
    include_in_schema: bool = False,
    methods: list[str] | None = None,
    tags: list[str] | None = None,
) -> APIRoute:
    """Register the health check route on `app` and return the created `APIRoute`.

    Raises `FastAPIError` if the app already routes any of `methods` on `path`, so
    a second registration (or a pre-existing user route) is reported instead of
    being silently shadowed by route matching order.
    """
    route_methods = list(methods or DEFAULT_HEALTH_CHECK_METHODS)
    _validate_path(path)
    _validate_probes(probes)
    conflicting = _find_routes(app, path, route_methods)
    if conflicting:
        raise FastAPIError(
            f"Cannot add the health check route {route_methods} {path!r}, "
            f"{app.title!r} already routes it with: "
            f"{[route.name for route in conflicting]}. "
            f"Call remove_health_check({app.title!r}, {path!r}) first, "
            f"or pass a different path=."
        )
    endpoint = _build_endpoint(
        probes, timeout=timeout, expose_error_messages=expose_error_messages
    )
    app.add_api_route(
        path,
        endpoint,
        methods=route_methods,
        response_model=HealthPayload,
        responses={
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "model": DegradedResponse,
                "description": "At least one dependency probe failed.",
            }
        },
        include_in_schema=include_in_schema,
        tags=tags,
    )
    return _mark_route(app, endpoint, path)


def remove_health_check(
    app: FastAPI, path: str = DEFAULT_HEALTH_CHECK_PATH
) -> APIRoute | None:
    """Remove a health check route previously added by `add_health_check()`.

    Returns the removed route, or `None` if `path` holds no health check route.
    Routes that were not registered by `add_health_check()` are never removed.
    """
    for route in list(app.router.routes):
        if not getattr(route, HEALTH_CHECK_ROUTE_MARKER, False):
            continue
        if getattr(route, "path", None) == path:
            app.router.routes.remove(route)
            return route
    return None


def add_health_check(
    app: FastAPI,
    *,
    path: Annotated[
        str,
        Doc("Path the health check is served on, by default `/healthz`."),
    ] = DEFAULT_HEALTH_CHECK_PATH,
    probes: Annotated[
        Mapping[str, Probe] | None,
        Doc(
            """
            Dependency probes to run on every request, keyed by the name reported
            in the `checks` field of the payload.

            Each probe is an async callable returning `True`, `False`, a
            `ProbeResult`, or `None` (meaning ok). Probes that raise or time out
            are reported as failed checks.
            """
        ),
    ] = None,
    timeout: Annotated[
        float | None,
        Doc(
            """
            Seconds a single probe may take before it is reported as failed, or
            `None` to wait indefinitely.
            """
        ),
    ] = DEFAULT_PROBE_TIMEOUT,
    expose_error_messages: Annotated[
        bool,
        Doc(
            """
            Include the (truncated) message of a failed probe exception in the
            response, instead of only its type name. Off by default because
            exception messages can leak internal details.
            """
        ),
    ] = False,
    include_in_schema: Annotated[
        bool,
        Doc(
            """
            Include the route in the OpenAPI schema. Off by default, health checks
            are infrastructure endpoints, not part of the API contract.
            """
        ),
    ] = False,
    methods: Annotated[
        list[str] | None,
        Doc("HTTP methods the health check answers, by default only `GET`."),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Doc("OpenAPI tags for the route, only used with `include_in_schema=True`."),
    ] = None,
) -> APIRoute:
    """Register a dependency aware health check endpoint on `app`.

    The route answers `GET /healthz` with `200` and
    `{"status": "ok", "checks": {...}}` while every probe passes, and `503` with
    `{"status": "degraded", "checks": {...}}` as soon as one of them fails, so an
    orchestrator can take an unhealthy instance out of rotation.

    Example:

    ```python
    from fastapi import FastAPI

    from features import add_health_check

    app = FastAPI()


    async def database() -> bool:
        return await database_is_reachable()


    add_health_check(app, probes={"database": database})
    ```

    A `FastAPIError` is raised if `path` is already routed on the app; call
    `remove_health_check(app, path)` first to move an existing registration to a
    different path.
    """
    return register_route(
        app,
        path=path,
        probes=probes or {},
        timeout=timeout,
        expose_error_messages=expose_error_messages,
        include_in_schema=include_in_schema,
        methods=methods,
        tags=tags,
    )


def _build_endpoint(
    probes: Mapping[str, Probe],
    *,
    timeout: float | None,
    expose_error_messages: bool,
) -> Callable[..., Awaitable[HealthPayload]]:
    async def health_check(response: Response) -> HealthPayload:
        checks = await run_probes(
            probes, timeout=timeout, expose_error_messages=expose_error_messages
        )
        payload = build_payload(checks)
        response.status_code = (
            status.HTTP_200_OK
            if isinstance(payload, HealthResponse)
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return payload

    return health_check


async def _run_probe(
    name: str, probe: Probe, timeout: float | None, expose_error_messages: bool
) -> ProbeResult:
    try:
        if timeout is None:
            outcome = await probe()
        else:
            outcome = await asyncio.wait_for(probe(), timeout)
    except asyncio.TimeoutError:
        return ProbeResult(ok=False, detail=f"timeout after {timeout}s")
    except Exception as exc:
        return ProbeResult(ok=False, detail=_describe_error(exc, expose_error_messages))
    return _coerce_outcome(outcome)


def _coerce_outcome(outcome: ProbeOutcome) -> ProbeResult:
    if isinstance(outcome, ProbeResult):
        return outcome
    if outcome is None:
        return ProbeResult(ok=True)
    return ProbeResult(ok=bool(outcome))


def _describe_error(exc: Exception, expose_error_messages: bool) -> str:
    detail = type(exc).__name__
    if not expose_error_messages:
        return detail
    message = str(exc)
    if not message:
        return detail
    return f"{detail}: {message[:MAX_DETAIL_LENGTH]}"


def _find_routes(app: FastAPI, path: str, methods: list[str]) -> list[BaseRoute]:
    conflicting: list[BaseRoute] = []
    for route in app.router.routes:
        if getattr(route, "path", None) != path:
            continue
        route_methods = getattr(route, "methods", None)
        if route_methods is None or set(methods) & set(route_methods):
            conflicting.append(route)
    return conflicting


def _mark_route(app: FastAPI, endpoint: Callable[..., Any], path: str) -> APIRoute:
    for route in reversed(app.router.routes):
        if getattr(route, "endpoint", None) is endpoint:
            setattr(route, HEALTH_CHECK_ROUTE_MARKER, True)
            return route
    raise FastAPIError(
        f"The health check route {path!r} could not be found on "
        f"{app.title!r} after registration."
    )


def _validate_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise FastAPIError(
            f"The health check path must be a string starting with '/', got {path!r}."
        )


def _validate_probes(probes: Mapping[str, Probe]) -> None:
    for name, probe in probes.items():
        if not isinstance(name, str) or not name:
            raise FastAPIError(
                f"Health check probe names must be non empty strings, got {name!r}."
            )
        if not callable(probe):
            raise FastAPIError(
                f"Health check probe {name!r} must be callable, got {probe!r}."
            )
    if len(set(probes)) != len(probes):
        raise FastAPIError(f"Duplicate health check probe names: {sorted(probes)}.")