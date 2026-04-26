# shellcheck shell=bash
# M2 acceptance test helpers — sourced by test_m2_end_to_end.sh.
#
# Step bookkeeping: step_begin → ... → step_pass | step_fail.
# Curl helpers: admin_curl / user_curl emit two lines (status, body).
# Assertions: assert_status, assert_jq, poll_until.

# ── Color (respect NO_COLOR + non-TTY) ────────────────────────
if [ -z "${NO_COLOR:-}" ] && [ -t 1 ]; then
    _C_GREEN=$'\033[32m'
    _C_RED=$'\033[31m'
    _C_YELLOW=$'\033[33m'
    _C_RESET=$'\033[0m'
else
    _C_GREEN=""; _C_RED=""; _C_YELLOW=""; _C_RESET=""
fi

_step_count=0
_step_pass=0
_step_fail=0
_step_skip=0
_step_start_ts=0
_step_current_name=""

# ── Step bookkeeping ──────────────────────────────────────────
step_begin() {
    local name="$1"
    _step_count=$((_step_count + 1))
    _step_current_name="$name"
    _step_start_ts=$(date +%s)
    echo
    echo "─── step ${_step_count}: ${name}"
}

step_pass() {
    local elapsed=$(( $(date +%s) - _step_start_ts ))
    _step_pass=$((_step_pass + 1))
    echo "    ${_C_GREEN}PASS${_C_RESET}  (${elapsed}s)"
}

step_skip() {
    local reason="${1:-}"
    local elapsed=$(( $(date +%s) - _step_start_ts ))
    _step_skip=$((_step_skip + 1))
    echo "    ${_C_YELLOW}SKIP${_C_RESET}  (${elapsed}s)  ${reason}"
}

# Print a FAIL line and exit unless M2_CONTINUE_ON_FAILURE=1.
step_fail() {
    local msg="$1"
    local elapsed=$(( $(date +%s) - _step_start_ts ))
    _step_fail=$((_step_fail + 1))
    echo "    ${_C_RED}FAIL${_C_RESET}  (${elapsed}s)  ${msg}"
    if [ "${M2_CONTINUE_ON_FAILURE:-0}" != "1" ]; then
        echo
        echo "aborting run; set M2_CONTINUE_ON_FAILURE=1 to keep going"
        exit 1
    fi
}

# ── Curl wrappers ────────────────────────────────────────────
# Both emit:
#   line 1: HTTP status code
#   line 2..: response body
# Use head/tail to split.

admin_curl() {
    local method="$1"; local path="$2"; shift 2
    local body_arg=()
    if [ $# -gt 0 ]; then body_arg=(-d "$1"); fi

    local raw
    raw=$(curl -sS -X "$method" \
        -H "Content-Type: application/json" \
        -H "X-Dev-User: ${OPENVDI_ADMIN_USER}" \
        -H "X-Dev-Role: admin" \
        -w '\n__STATUS__:%{http_code}' \
        "${body_arg[@]}" \
        "${OPENVDI_BROKER_URL}${path}")

    local status
    status=$(echo "$raw" | awk -F':' '/^__STATUS__:/{print $2}')
    local body
    body=$(echo "$raw" | sed '/^__STATUS__:/d')
    echo "$status"
    echo "$body"
}

user_curl() {
    local method="$1"; local path="$2"; shift 2
    local body_arg=()
    if [ $# -gt 0 ]; then body_arg=(-d "$1"); fi

    local raw
    raw=$(curl -sS -X "$method" \
        -H "Content-Type: application/json" \
        -H "X-Dev-User: ${OPENVDI_REGULAR_USER}" \
        -H "X-Dev-Role: user" \
        -H "X-Dev-Groups: ${OPENVDI_REGULAR_GROUPS}" \
        -w '\n__STATUS__:%{http_code}' \
        "${body_arg[@]}" \
        "${OPENVDI_BROKER_URL}${path}")

    local status
    status=$(echo "$raw" | awk -F':' '/^__STATUS__:/{print $2}')
    local body
    body=$(echo "$raw" | sed '/^__STATUS__:/d')
    echo "$status"
    echo "$body"
}

# ── Assertions ───────────────────────────────────────────────
# Each prints a step_fail line on mismatch; caller decides whether to
# `return 1` (M2_CONTINUE_ON_FAILURE=1) or rely on step_fail's exit.

assert_status() {
    local expected="$1"; local actual="$2"; local desc="$3"
    if [ "$actual" != "$expected" ]; then
        step_fail "expected HTTP ${expected}, got ${actual} — ${desc}"
        return 1
    fi
}

# Assert one of N status codes. Useful where the prompt + provider
# semantics permit either of two answers (e.g. M2-14's
# nonexistent-VMID returns either 400 or 502 depending on whether the
# Proxmox client translates the underlying 500 — see
# providers/proxmox-md-patch.md).
assert_status_in() {
    local actual="$1"; local desc="$2"; shift 2
    local s
    for s in "$@"; do
        if [ "$actual" = "$s" ]; then return 0; fi
    done
    step_fail "expected HTTP one of [$*], got ${actual} — ${desc}"
    return 1
}

assert_jq() {
    local expr="$1"; local expected="$2"; local body="$3"; local desc="$4"
    local actual
    actual=$(echo "$body" | jq -r "$expr" 2>/dev/null) || actual="<jq error>"
    if [ "$actual" != "$expected" ]; then
        step_fail "jq '${expr}' expected '${expected}', got '${actual}' — ${desc}"
        return 1
    fi
}

# Poll an admin GET endpoint until a jq expression matches expected,
# or timeout elapses.
poll_until() {
    local path="$1"; local expr="$2"; local expected="$3"
    local timeout="$4"; local desc="$5"
    local deadline=$(( $(date +%s) + timeout ))

    while [ "$(date +%s)" -lt "$deadline" ]; do
        local out
        out=$(admin_curl GET "$path")
        local status
        status=$(echo "$out" | head -1)
        local body
        body=$(echo "$out" | tail -n +2)
        if [ "$status" = "200" ]; then
            local actual
            actual=$(echo "$body" | jq -r "$expr" 2>/dev/null) || actual=""
            if [ "$actual" = "$expected" ]; then
                return 0
            fi
        fi
        sleep 2
    done

    step_fail "poll timed out after ${timeout}s — ${desc} did not reach '${expected}'"
    return 1
}

# Poll an admin GET endpoint until it returns a specific HTTP status.
# Useful for "wait until 404" (resource deleted).
poll_until_status() {
    local path="$1"; local expected_status="$2"
    local timeout="$3"; local desc="$4"
    local deadline=$(( $(date +%s) + timeout ))

    while [ "$(date +%s)" -lt "$deadline" ]; do
        local out
        out=$(admin_curl GET "$path")
        local status
        status=$(echo "$out" | head -1)
        if [ "$status" = "$expected_status" ]; then
            return 0
        fi
        sleep 2
    done

    step_fail "poll timed out after ${timeout}s — ${desc} did not return ${expected_status}"
    return 1
}
