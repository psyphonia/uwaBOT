"""OAuth structure only: callers inject transport and approved registration."""
import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlencode, urlsplit


class BlackboardError(ValueError):
    pass


@dataclass(frozen=True)
class Response:
    status: int
    body: object
    headers: dict[str, str] = field(default_factory=dict)


class Transport(Protocol):
    async def request(self, method: str, url: str, *, headers: dict[str, str],
                      body: dict[str, str] | None = None) -> Response: ...


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise BlackboardError("Blackboard requires an HTTPS origin.")
    return value.rstrip("/")


@dataclass(frozen=True)
class OAuthConfig:
    base_url: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    offline: bool = False
    timeout: float = 15


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str = field(repr=False)
    verifier: str = field(repr=False)


@dataclass(frozen=True)
class Token:
    access_token: str = field(repr=False)
    expires_at: datetime
    user_id: str
    refresh_token: str | None = field(default=None, repr=False)


class BlackboardOAuth:
    def __init__(self, config: OAuthConfig, transport: Transport):
        self.config, self.transport = config, transport
        origin(config.base_url)
        callback = urlsplit(config.redirect_uri)
        if (not config.client_id.strip() or not config.client_secret.strip()
                or callback.scheme != "https" or not callback.netloc or callback.fragment
                or callback.username or callback.password or config.timeout <= 0):
            raise BlackboardError("Approved OAuth registration and HTTPS callback are required.")
        self.token: Token | None = None
        self._pending: AuthorizationRequest | None = None
        self._lock = asyncio.Lock()

    def begin(self) -> AuthorizationRequest:
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = urlencode(dict(client_id=self.config.client_id, redirect_uri=self.config.redirect_uri,
                               response_type="code", scope="read offline" if self.config.offline else "read",
                               state=state, code_challenge=challenge, code_challenge_method="S256"))
        self._pending = AuthorizationRequest(origin(self.config.base_url) +
            "/learn/api/public/v1/oauth2/authorizationcode?" + query, state, verifier)
        return self._pending

    async def complete(self, *, code: str, state: str) -> Token:
        request = self._pending
        if request is None or not secrets.compare_digest(request.state, state) or not code:
            raise BlackboardError("Invalid OAuth callback state or code.")
        self._pending = None
        self.token = await self._exchange(dict(grant_type="authorization_code", code=code,
                                               code_verifier=request.verifier))
        return self.token

    async def _exchange(self, body: dict[str, str]) -> Token:
        credential = base64.b64encode(f"{self.config.client_id}:{self.config.client_secret}".encode()).decode()
        try:
            async with asyncio.timeout(self.config.timeout):
                response = await self.transport.request("POST", origin(self.config.base_url) +
                    "/learn/api/public/v1/oauth2/token", headers={"Authorization": "Basic " + credential,
                    "Content-Type": "application/x-www-form-urlencoded"},
                    body={**body, "redirect_uri": self.config.redirect_uri})
        except Exception:
            raise BlackboardError("Blackboard token request failed or timed out.") from None
        if not isinstance(response, Response) or response.status != 200:
            raise BlackboardError("Blackboard token exchange rejected.")
        data = response.body
        if (not isinstance(data, dict) or not isinstance(data.get("access_token"), str)
                or not data["access_token"] or any(c in data["access_token"] for c in "\r\n")
                or not isinstance(data.get("token_type"), str) or data["token_type"].lower() != "bearer"
                or type(data.get("expires_in")) is not int or data["expires_in"] <= 0
                or not isinstance(data.get("user_id"), str) or not data["user_id"]
                or not isinstance(data.get("scope"), str) or "read" not in data["scope"].split()
                or set(data["scope"].split()) - {"read", "offline"}):
            raise BlackboardError("Malformed Blackboard token response.")
        refresh = data.get("refresh_token") if self.config.offline else None
        if refresh is not None and (not isinstance(refresh, str) or not refresh):
            raise BlackboardError("Malformed refresh credential.")
        return Token(data["access_token"], datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"]),
                     data["user_id"], refresh)

    async def valid_token(self, *, force_refresh: bool = False) -> Token:
        async with self._lock:
            token = self.token
            if token is None:
                raise BlackboardError("Blackboard authorization is unavailable; student consent is required.")
            if not force_refresh and token.expires_at > datetime.now(timezone.utc) + timedelta(seconds=30):
                return token
            if not self.config.offline or not token.refresh_token:
                raise BlackboardError("Blackboard token expired/rejected; reauthorization is required.")
            renewed = await self._exchange(dict(grant_type="refresh_token", refresh_token=token.refresh_token))
            if renewed.user_id != token.user_id:
                raise BlackboardError("Refresh changed the authorized student.")
            self.token = Token(renewed.access_token, renewed.expires_at, renewed.user_id,
                               renewed.refresh_token or token.refresh_token)
            return self.token
