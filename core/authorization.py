import hmac
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional


class AuthorizationDenied(Exception):
    pass


class AuthorizationExpired(AuthorizationDenied):
    pass


_ISSUER = object()
_CONFIRMATION_PREFIX = b"AUTHORIZE "
_MAX_RESPONSE_BYTES = 160

# Contract: docs/SAFETY_CONTRACTS.md 6.1. A caller-supplied ttl may only shorten
# this ceiling, never raise it, or an authorized agent could hand itself more time.
DEFAULT_TTL_SECONDS = 120


def _validate_ttl(ttl_seconds):
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, (int, float))
        or not 0 < ttl_seconds <= DEFAULT_TTL_SECONDS
    ):
        raise AuthorizationDenied("Authorization lifetime must be within the fixed ceiling")
    return float(ttl_seconds)


class PlanAuthorization:
    __slots__ = (
        "_plan_sha256",
        "_proposal_sha256",
        "_intervention_sha256",
        "_used",
        "_lock",
        "_ttl_seconds",
        "_issued_monotonic",
        "_issued_at",
        "_expires_at",
        "_clock",
    )

    def __init__(
        self,
        plan_sha256: str,
        proposal_sha256: str,
        *,
        _issuer=None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock=time.monotonic,
        intervention_sha256: Optional[str] = None,
    ):
        if _issuer is not _ISSUER:
            raise AuthorizationDenied("Authorization must be granted by the local TTY")
        self._plan_sha256 = plan_sha256
        self._proposal_sha256 = proposal_sha256
        if intervention_sha256 is not None and (
            not isinstance(intervention_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", intervention_sha256)
        ):
            raise AuthorizationDenied("Invalid canonical intervention digest")
        self._intervention_sha256 = intervention_sha256
        self._used = False
        self._lock = threading.Lock()
        self._ttl_seconds = _validate_ttl(ttl_seconds)
        if not callable(clock):
            raise AuthorizationDenied("Authorization clock must be callable")
        self._clock = clock
        self._issued_monotonic = self._clock()
        # Reporting fields only; the validity decision never reads wall-clock time.
        issued_wall = datetime.now(timezone.utc)
        self._issued_at = issued_wall.isoformat(timespec="seconds")
        self._expires_at = (
            issued_wall + timedelta(seconds=self._ttl_seconds)
        ).isoformat(timespec="seconds")

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    @property
    def issued_at(self) -> str:
        return self._issued_at

    @property
    def expires_at(self) -> str:
        return self._expires_at

    @property
    def intervention_sha256(self) -> Optional[str]:
        return self._intervention_sha256

    @property
    def used(self) -> bool:
        with self._lock:
            return self._used

    def binds(
        self,
        plan_sha256: str,
        proposal_sha256: str,
        intervention_sha256: Optional[str] = None,
    ) -> bool:
        """Non-consuming check that this grant was minted for exactly these bytes.

        A gate that only needs to look must not burn the ceremony: consume() stays
        the single point where a grant becomes invalid.
        """
        if self.used or self.expired():
            return False
        # The intervention slot must be present on both sides or absent on both,
        # so a generic check can never accept an intervention-bound grant.
        if (intervention_sha256 is None) != (self._intervention_sha256 is None):
            return False
        if intervention_sha256 is not None and not hmac.compare_digest(
            self._intervention_sha256, intervention_sha256
        ):
            return False
        return hmac.compare_digest(self._plan_sha256, plan_sha256) and hmac.compare_digest(
            self._proposal_sha256, proposal_sha256
        )

    def remaining_seconds(self) -> float:
        # A clock reading earlier than issuance is treated as spent, so an injected
        # clock can only shorten an authorization's life, never extend it.
        elapsed = self._clock() - self._issued_monotonic
        if elapsed < 0:
            return 0.0
        return self._ttl_seconds - elapsed

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0

    def to_evidence_record(self) -> dict:
        record = {
            "method": "local_tty_confirmation",
            "plan_sha256": self._plan_sha256,
            "proposal_sha256": self._proposal_sha256,
            "ttl_seconds": self._ttl_seconds,
            "issued_at": self._issued_at,
            "expires_at": self._expires_at,
        }
        # Synthetic campaigns keep their historical evidence bytes unchanged, so
        # the intervention slot is recorded only when the ceremony bound one.
        if self._intervention_sha256 is not None:
            record["intervention_sha256"] = self._intervention_sha256
        return record

    def consume(
        self,
        plan_sha256: str,
        proposal_sha256: str,
        intervention_sha256: Optional[str] = None,
    ) -> str:
        # Checked before the used flag so an expired grant cannot burn the ceremony.
        if self.expired():
            raise AuthorizationExpired("TTY authorization has expired")
        with self._lock:
            if self._used:
                raise AuthorizationDenied("TTY authorization has already been used")
            self._used = True
        if not hmac.compare_digest(self._plan_sha256, plan_sha256):
            raise AuthorizationDenied("Authorized plan digest does not match")
        if not hmac.compare_digest(self._proposal_sha256, proposal_sha256):
            raise AuthorizationDenied("Authorized proposal digest does not match")
        # A grant minted for one intervention cannot be spent by a generic path,
        # and a generic grant cannot be spent on an intervention.
        if (self._intervention_sha256 is None) != (intervention_sha256 is None) or not (
            hmac.compare_digest(self._intervention_sha256 or "", intervention_sha256 or "")
        ):
            raise AuthorizationDenied("Authorized intervention digest does not match")
        return self._proposal_sha256


def _read_confirmation(tty_fd: int, plan_sha256: str) -> bool:
    warning = (
        "Local TTY confirmation is operator consent, not cryptographic identity; "
        "same-UID processes are outside this protection.\n"
        f"To authorize this exact plan, type: AUTHORIZE {plan_sha256}\n> "
    ).encode("ascii")
    _write_all(tty_fd, warning)
    response = bytearray()
    while len(response) <= _MAX_RESPONSE_BYTES:
        char = os.read(tty_fd, 1)
        if not char:
            return False
        if char == b"\n":
            break
        if char not in (b"\r",):
            response.extend(char)
    else:
        return False
    expected = _CONFIRMATION_PREFIX + plan_sha256.encode("ascii")
    return hmac.compare_digest(bytes(response), expected)


def _write_all(fd: int, data: bytes):
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("Could not write complete TTY authorization prompt")
        remaining = remaining[written:]


def authorize_plan_from_tty(
    plan_sha256: str,
    proposal_sha256: str,
    proposal_summary: str,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    clock=time.monotonic,
    intervention_sha256: Optional[str] = None,
) -> PlanAuthorization:
    ttl_seconds = _validate_ttl(ttl_seconds)
    if not isinstance(plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_sha256):
        raise AuthorizationDenied("Invalid canonical plan digest")
    if not isinstance(proposal_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", proposal_sha256
    ):
        raise AuthorizationDenied("Invalid canonical proposal digest")
    if intervention_sha256 is not None and (
        not isinstance(intervention_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", intervention_sha256)
    ):
        raise AuthorizationDenied("Invalid canonical intervention digest")
    if not isinstance(proposal_summary, str) or len(proposal_summary) > 16 * 1024:
        raise AuthorizationDenied("Invalid proposal summary")
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise AuthorizationDenied("No controlling TTY is available") from exc
    try:
        if not os.isatty(tty_fd):
            raise AuthorizationDenied("The authorization device is not a TTY")
        prompt = (
            f"Proposal SHA-256: {proposal_sha256}\n"
            f"Plan SHA-256: {plan_sha256}\n"
        )
        if intervention_sha256 is not None:
            prompt += (
                f"Intervention SHA-256: {intervention_sha256}\n"
                "Approving this line approves exactly that host intervention "
                "and its rollback.\n"
            )
        _write_all(
            tty_fd,
            (prompt + "Exact proposal to authorize:\n" + proposal_summary + "\n").encode(
                "ascii"
            ),
        )
        if not _read_confirmation(tty_fd, plan_sha256):
            raise AuthorizationDenied("TTY confirmation was absent or did not match")
    finally:
        os.close(tty_fd)
    return PlanAuthorization(
        plan_sha256,
        proposal_sha256,
        _issuer=_ISSUER,
        ttl_seconds=ttl_seconds,
        clock=clock,
        intervention_sha256=intervention_sha256,
    )
