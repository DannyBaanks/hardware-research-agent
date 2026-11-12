# Sourced by CI steps. "! command" cannot be used for this: under "set -e" a
# negated command never fails the step, so a wrongly successful command would pass.

expect_exit() {
  local want="$1" got=0
  shift
  "$@" || got=$?
  if [ "$got" -ne "$want" ]; then
    echo "expected exit $want but got $got: $*" >&2
    exit 1
  fi
}

expect_nonzero() {
  local got=0
  "$@" || got=$?
  if [ "$got" -eq 0 ]; then
    echo "expected a refusal but the command succeeded: $*" >&2
    exit 1
  fi
}
