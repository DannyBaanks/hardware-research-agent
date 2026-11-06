import os
import pty
import threading
import time
from unittest.mock import patch

from core.authorization import authorize_plan_from_tty


def authorize_in_pty(
    plan_sha256,
    proposal_sha256,
    proposal_summary="{}",
    answer=None,
    capture=False,
    ttl_seconds=None,
    clock=None,
):
    master_fd, slave_fd = pty.openpty()
    expected = f"AUTHORIZE {plan_sha256}"
    supplied = expected if answer is None else answer
    response_sent = threading.Event()
    errors = []
    output = bytearray()

    def respond_to_prompt():
        try:
            deadline = time.monotonic() + 5
            while b"> " not in output and time.monotonic() < deadline:
                try:
                    output.extend(os.read(master_fd, 4096))
                except BlockingIOError:
                    time.sleep(0.005)
            if b"> " not in output:
                raise TimeoutError("TTY authorization prompt was not emitted")
            os.write(master_fd, supplied.encode("ascii") + b"\n")
        except Exception as exc:
            errors.append(exc)
        finally:
            response_sent.set()

    os.set_blocking(master_fd, False)
    responder = threading.Thread(target=respond_to_prompt)
    responder.start()
    issued = {}
    if ttl_seconds is not None:
        issued["ttl_seconds"] = ttl_seconds
    if clock is not None:
        issued["clock"] = clock
    try:
        with patch("core.authorization.os.open", return_value=os.dup(slave_fd)):
            result = authorize_plan_from_tty(
                plan_sha256, proposal_sha256, proposal_summary, **issued
            )
    finally:
        response_sent.wait(5)
        responder.join(timeout=5)
        os.close(master_fd)
        os.close(slave_fd)
    if errors:
        raise errors[0]
    return (result, bytes(output)) if capture else result
