"""Every request commits before its response is sent.

With FastAPI's default scope, a yield dependency's exit code runs after the response has gone
out. The commit in get_conn would then race the client's next request: the screen refetches
straight after a change and can read the data from before it (seen with a new rate-card
version showing "priced 0 orders"). scope="function" commits first.
"""

from fastapi.routing import APIRoute

from app.db import get_conn
from app.main import API_ROUTERS


def _connections(dependant):
    for sub in dependant.dependencies:
        if sub.call is get_conn:
            yield sub
        yield from _connections(sub)


def test_every_route_commits_before_responding():
    routes = [r for router in API_ROUTERS for r in router.routes if isinstance(r, APIRoute)]
    assert routes
    found = [(r, dep) for r in routes for dep in _connections(r.dependant)]
    assert len(found) >= len(routes) // 2  # the check really sees the connections
    late = [f"{sorted(r.methods)} {r.path}" for r, dep in found if dep.scope != "function"]
    assert late == []
