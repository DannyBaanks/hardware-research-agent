import hmac
import os
import re
import threading


class AuthorizationDenied(Exception):
    pass


_ISSUER = object()
_CONFIRMATION_PREFIX = b"AUTHORIZE "
_MAX_RESPONSE_BYTES = 160


class PlanAuthorization:
    __slots__ = ("_plan_sha256", "_proposal_sha256", "_used", "_lock")

    def __init__(
        self,
        plan_sha256: str,
        proposal_sha256: str,
        *,
        _issuer=None,
    ):
        if _issuer is not _ISSUER:
            raise AuthorizationDenied("Authorization must be granted by the local TTY")
        self._plan_sha256 = plan_sha256
        self._proposal_sha256 = proposal_sha256
        self._used = False
        self._lock = threading.Lock()

    def consume(self, plan_sha256: str, proposal_sha256: str) -> str:
        with self._lock:
            if self._used:
                raise AuthorizationDenied("TTY authorization has already been used")
            self._used = True
        if not hmac.compare_digest(self._plan_sha256, plan_sha256):
            raise AuthorizationDenied("Authorized plan digest does not match")
        if not hmac.compare_digest(self._proposal_sha256, proposal_sha256):
            raise AuthorizationDenied("Authorized proposal digest does not match")
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
) -> PlanAuthorization:
    if not isinstance(plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_sha256):
        raise AuthorizationDenied("Invalid canonical plan digest")
    if not isinstance(proposal_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", proposal_sha256
    ):
        raise AuthorizationDenied("Invalid canonical proposal digest")
    if not isinstance(proposal_summary, str) or len(proposal_summary) > 16 * 1024:
        raise AuthorizationDenied("Invalid proposal summary")
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise AuthorizationDenied("No controlling TTY is available") from exc
    try:
        if not os.isatty(tty_fd):
            raise AuthorizationDenied("The authorization device is not a TTY")
        _write_all(
            tty_fd,
            (
                f"Proposal SHA-256: {proposal_sha256}\n"
                f"Plan SHA-256: {plan_sha256}\n"
                "Exact proposal to authorize:\n"
                + proposal_summary
                + "\n"
            ).encode("ascii"),
        )
        if not _read_confirmation(tty_fd, plan_sha256):
            raise AuthorizationDenied("TTY confirmation was absent or did not match")
    finally:
        os.close(tty_fd)
    return PlanAuthorization(plan_sha256, proposal_sha256, _issuer=_ISSUER)
