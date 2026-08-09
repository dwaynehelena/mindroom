"""Skill Foundry marketplace server and API endpoints.

The marketplace is a lightweight single-page app served over HTTP.  Two entry
points are provided:

* ``install_marketplace_routes`` — registers a FastAPI router exposing
  ``/api/marketplace/*`` for browsing, installing, updating, and uninstalling
  skills.  All mutations delegate to the ``SkillInstaller`` / ``SkillRegistry``
  modules so the UI and the CLI share one code path.
* ``run_marketplace_server`` — a standalone threaded HTTP server that serves
  the static SPA (``marketplace/index.html`` + ``app.js``) and exposes the same
  JSON API for local-only use (``mindroom marketplace``).
"""

from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mindroom.tool_system.index import SkillIndex, SkillSearchResult

_MARKETPLACE_DIR = Path(__file__).resolve().parents[2] / "marketplace"

_OPENCLAW_HOME = Path.home() / ".openclaw"
_SKILLS_DIR = _OPENCLAW_HOME / "skills"
_REGISTRY_DB = _OPENCLAW_HOME / "skill-registry.db"
_INSTALLED_INDEX = _OPENCLAW_HOME / "installed-skills.json"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _build_index() -> "SkillIndex":
    """Build a searchable ``SkillIndex`` over the default registry + installed state."""
    from mindroom.tool_system.index import InstalledSkillsIndex, RegistryIndexFetcher, SkillIndex
    from mindroom.tool_system.registry import SkillRegistry

    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(_REGISTRY_DB)
    return SkillIndex(
        registry,
        index_fetcher=RegistryIndexFetcher(),
        installed_index=InstalledSkillsIndex(_INSTALLED_INDEX),
    )


def _build_installer() -> tuple[Any, Any]:
    """Build a ``SkillRegistry`` and ``SkillInstaller`` bound to the default paths."""
    from mindroom.tool_system.installer import SkillInstaller
    from mindroom.tool_system.registry import SkillRegistry

    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(_REGISTRY_DB)
    installer = SkillInstaller(
        registry,
        skills_dir=_SKILLS_DIR,
        all_skill_roots=[_SKILLS_DIR],
        installed_index_path=_INSTALLED_INDEX,
    )
    return registry, installer


def _skill_to_dict(skill: "SkillSearchResult") -> dict[str, object]:
    """Convert a ``SkillSearchResult`` into a JSON-safe mapping."""
    return {
        "name": skill.name,
        "description": skill.description,
        "author": skill.author,
        "license": skill.license,
        "tags": list(skill.tags),
        "latest_version": skill.latest_version,
        "all_versions": list(skill.all_versions),
        "installed_version": skill.installed_version,
        "installed_path": str(skill.installed_path) if skill.installed_path is not None else None,
        "is_up_to_date": skill.is_up_to_date,
    }


class _MarketplaceError(RuntimeError):
    """Base error carrying an HTTP status code for marketplace API surfaces."""

    status: int = HTTPStatus.BAD_REQUEST


class _NotFoundError(_MarketplaceError):
    status = HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Core API handlers (shared by FastAPI router and standalone HTTP server)
# ---------------------------------------------------------------------------


class MarketplaceApi:
    """Stateless handler bundle wrapping the shared installer + index."""

    def __init__(self) -> None:
        self._index = _build_index()
        self._registry, self._installer = _build_installer()

    def list_skills(self) -> list[dict[str, object]]:
        return [_skill_to_dict(s) for s in self._index.search()]

    def skill_detail(self, name: str) -> dict[str, object]:
        result = self._index.get_detail(name)
        if result is None:
            raise _NotFoundError(f"Skill {name!r} not found in the registry")
        return _skill_to_dict(result)

    def skill_skillmd(self, name: str) -> dict[str, str]:
        from mindroom.tool_system.skills import get_registry_cache_dir

        if not _SAFE_NAME.fullmatch(name):
            raise _NotFoundError(f"Skill {name!r} has no local SKILL.md")
        for candidate in (_SKILLS_DIR / name, get_registry_cache_dir() / name):
            skill_file = candidate / "SKILL.md"
            if skill_file.is_file():
                return {"content": skill_file.read_text(encoding="utf-8")}
        raise _NotFoundError(f"Skill {name!r} has no local SKILL.md")

    def list_installed(self) -> list[dict[str, object]]:
        return [_skill_to_dict(s) for s in self._index.list_installed()]

    def install(self, name: str, version: str | None = None) -> dict[str, object]:
        try:
            result = self._installer.install_from_registry(name, version=version)
        except Exception as exc:  # noqa: BLE001 — surfaced as a 400 detail
            raise _MarketplaceError(str(exc) or exc.__class__.__name__) from exc
        return {
            "name": result.name,
            "version": str(result.version),
            "install_path": str(result.install_path),
            "dependencies": list(result.dependencies),
        }

    def uninstall(self, name: str) -> dict[str, object]:
        if not self._installer.uninstall(name):
            raise _NotFoundError(f"Skill {name!r} is not installed")
        return {"name": name}

    def update(self, name: str) -> dict[str, object]:
        try:
            result = self._installer.update(name)
        except Exception as exc:  # noqa: BLE001
            raise _MarketplaceError(str(exc) or exc.__class__.__name__) from exc
        if result is None:
            return {"name": name, "updated": False, "version": None}
        return {"name": result.name, "updated": True, "version": str(result.version)}

    def update_all(self) -> dict[str, object]:
        updated: list[str] = []
        for installed in self._registry.list_installed():
            try:
                result = self._installer.update(installed.name)
            except Exception:  # noqa: BLE001 — one failure must not abort the rest
                continue
            if result is not None:
                updated.append(result.name)
        return {"updated": updated}


def _error_response(exc: BaseException) -> tuple[int, dict[str, str]]:
    status = exc.status if isinstance(exc, _MarketplaceError) else HTTPStatus.BAD_REQUEST
    return status, {"detail": str(exc) or exc.__class__.__name__}


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------


def install_marketplace_routes(app: object) -> None:
    """Register the marketplace JSON API onto a FastAPI router/app."""
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field

    router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

    def _as_http(exc: BaseException) -> HTTPException:
        status, detail = _error_response(exc)
        return HTTPException(status_code=status, detail=detail["detail"])

    api = MarketplaceApi()

    class _MutationRequest(BaseModel):
        name: str = Field(min_length=1)
        version: str | None = None

    @router.get("/skills")
    async def list_skills() -> list[dict[str, object]]:
        return api.list_skills()

    @router.get("/installed")
    async def list_installed() -> list[dict[str, object]]:
        return api.list_installed()

    @router.get("/skill/{name}")
    async def skill_detail(name: str) -> dict[str, object]:
        try:
            return api.skill_detail(name)
        except BaseException as exc:  # noqa: BLE001
            raise _as_http(exc) from exc

    @router.get("/skill/{name}/skillmd")
    async def skill_skillmd(name: str) -> dict[str, str]:
        try:
            return api.skill_skillmd(name)
        except BaseException as exc:  # noqa: BLE001
            raise _as_http(exc) from exc

    @router.post("/install")
    async def install(payload: _MutationRequest) -> dict[str, object]:
        try:
            return api.install(payload.name, payload.version)
        except BaseException as exc:  # noqa: BLE001
            raise _as_http(exc) from exc

    @router.post("/uninstall")
    async def uninstall(payload: _MutationRequest) -> dict[str, object]:
        try:
            return api.uninstall(payload.name)
        except BaseException as exc:  # noqa: BLE001
            raise _as_http(exc) from exc

    @router.post("/update")
    async def update(payload: _MutationRequest) -> dict[str, object]:
        try:
            return api.update(payload.name)
        except BaseException as exc:  # noqa: BLE001
            raise _as_http(exc) from exc

    @router.post("/update-all")
    async def update_all() -> dict[str, object]:
        return api.update_all()

    app.include_router(router)


# ---------------------------------------------------------------------------
# Standalone threaded HTTP server
# ---------------------------------------------------------------------------


def _static_asset(path: str) -> tuple[int, str, bytes] | None:
    """Return (status, content_type, body) for a static SPA asset, else None."""
    if path in ("/", "/index.html"):
        asset = _MARKETPLACE_DIR / "index.html"
        content_type = "text/html; charset=utf-8"
    elif path == "/app.js":
        asset = _MARKETPLACE_DIR / "app.js"
        content_type = "application/javascript; charset=utf-8"
    else:
        return None
    if not asset.is_file():
        return None
    return HTTPStatus.OK, content_type, asset.read_bytes()


def _marketplace_handler(api: MarketplaceApi) -> type[BaseHTTPRequestHandler]:
    """Return a request handler bound to one ``MarketplaceApi`` instance."""

    class _Handler(BaseHTTPRequestHandler):
        # Silence default logging to stderr.
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _route_get(self, path: str) -> None:
            if path == "/skills":
                self._send_json(HTTPStatus.OK, api.list_skills())
                return
            if path == "/installed":
                self._send_json(HTTPStatus.OK, api.list_installed())
                return
            m = re.fullmatch(r"/skill/([^/]+)(/skillmd)?", path)
            if m:
                name, has_skillmd = m.group(1), m.group(2)
                try:
                    if has_skillmd:
                        self._send_json(HTTPStatus.OK, api.skill_skillmd(name))
                    else:
                        self._send_json(HTTPStatus.OK, api.skill_detail(name))
                except BaseException as exc:  # noqa: BLE001
                    status, detail = _error_response(exc)
                    self._send_json(status, detail)
                return
            asset = _static_asset(path)
            if asset is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})
                return
            status, content_type, content = asset
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _route_post(self, path: str, body: dict[str, object]) -> None:
            name = str(body.get("name", ""))
            try:
                if path == "/install":
                    payload: object = api.install(name, body.get("version"))
                elif path == "/uninstall":
                    payload = api.uninstall(name)
                elif path == "/update":
                    payload = api.update(name)
                elif path == "/update-all":
                    payload = api.update_all()
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})
                    return
            except BaseException as exc:  # noqa: BLE001
                status, detail = _error_response(exc)
                self._send_json(status, detail)
                return
            self._send_json(HTTPStatus.OK, payload)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path.startswith("/api/marketplace"):
                sub_path = path.removeprefix("/api/marketplace") or "/"
                self._route_get(sub_path)
                return
            self._route_get(path)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0].rstrip("/")
            if not path.startswith("/api/marketplace"):
                self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})
                return
            sub_path = path.removeprefix("/api/marketplace")
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            try:
                body: dict[str, object] = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(body, dict):
                    body = {}
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Invalid JSON body"})
                return
            self._route_post(sub_path, body)

    return _Handler


def run_marketplace_server(
    *,
    host: str = "127.0.0.1",
    port: int = 9876,
    ready_event: threading.Event | None = None,
) -> ThreadingHTTPServer:
    """Start a blocking marketplace HTTP server and return the bound server.

    The server runs in the calling thread; ``ready_event`` (if provided) is
    set once the socket is bound so callers can wait before opening a browser.
    """
    api = MarketplaceApi()
    server = ThreadingHTTPServer((host, port), _marketplace_handler(api))
    if ready_event is not None:
        ready_event.set()
    server.serve_forever()
    return server