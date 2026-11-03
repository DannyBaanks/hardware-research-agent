import hashlib
import json
import resource
import time

if hasattr(resource, "RLIMIT_NPROC"):
    _, hard_limit = resource.getrlimit(resource.RLIMIT_NPROC)
    process_limit = 4 if hard_limit == resource.RLIM_INFINITY else min(4, hard_limit)
    resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))


WORKLOAD_ID = "sha256-chain-v1"
ITERATIONS = 20000


def main():
    state = b"IHRA synthetic workload v1"
    started = time.monotonic()
    for index in range(ITERATIONS):
        state = hashlib.sha256(state + index.to_bytes(4, "big")).digest()

    print(json.dumps({
        "workload_id": WORKLOAD_ID,
        "iterations": ITERATIONS,
        "output_sha256": state.hex(),
        "elapsed_seconds": time.monotonic() - started,
    }, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
