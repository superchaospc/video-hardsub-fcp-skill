#!/usr/bin/env bash
set -eu

OFFICIAL_URL='https://github.com/superchaospc/video-hardsub-fcp-skill.git'
SKILL_NAME='video-hardsub-fcp'
LOCK_OWNED=0
LOCK_PATH=''
LOCK_TOKEN=''
LOCK_OWNER_FILE=''
LOCK_OWNER_PID=''
LOCK_OWNER_START=''
LOCK_KEEPER_PID=''
LOCK_KEEPER_FIFO=''
PUBLICATION_ARGS=()

usage() {
  cat <<'EOF'
Usage:
  install.sh
  install.sh --source DIR
  install.sh --help
EOF
}

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
require_tool() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"; }
physical_dir() { (cd "$1" 2>/dev/null && pwd -P); }

path_identity() {
  python3 - "$1" <<'PY'
import os, sys
try: value = os.lstat(sys.argv[1])
except OSError: raise SystemExit(1)
print(f"{value.st_dev}:{value.st_ino}")
PY
}

resolved_path() {
  python3 - "$1" <<'PY'
import os, sys
print(os.path.realpath(os.path.abspath(sys.argv[1])))
PY
}

symlink_matches() {
  local link_path expected_target raw_target actual_target
  link_path=$1; expected_target=$2
  [ -L "$link_path" ] || return 1
  raw_target=$(readlink "$link_path") || return 1
  case $raw_target in /*) actual_target=$raw_target ;; *) actual_target="$(dirname "$link_path")/$raw_target" ;; esac
  [ "$(resolved_path "$actual_target")" = "$(resolved_path "$expected_target")" ]
}

validate_guard_fd() {
  /usr/bin/python3 - "$1" "$2" "$3" <<'PY'
import os
import stat
import sys

guard_path, fd_text, expected_identity = sys.argv[1:]
try:
    guard_fd = int(fd_text)
    opened = os.fstat(guard_fd)
    current = os.stat(guard_path, follow_symlinks=False)
except (OSError, TypeError, ValueError) as exc:
    print(f"install.sh: installer guard descriptor is unavailable or invalid: {exc}", file=sys.stderr)
    raise SystemExit(1)
identity = f"{opened.st_dev}:{opened.st_ino}"
valid = (
    stat.S_ISREG(opened.st_mode)
    and opened.st_uid == os.getuid()
    and opened.st_nlink == 1
    and stat.S_IMODE(opened.st_mode) == 0o600
    and stat.S_ISREG(current.st_mode)
    and (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
    and identity == expected_identity
)
if not valid:
    print("install.sh: installer guard descriptor or pathname identity is unsafe", file=sys.stderr)
    raise SystemExit(1)
PY
}

release_lock() {
  [ "$LOCK_OWNED" -eq 1 ] || return 0
  exec 9>&-
  if ! wait "$LOCK_KEEPER_PID"; then
    printf 'install.sh: lock keeper could not safely release; retained lock directory: %s\n' "$LOCK_PATH" >&2
  fi
  LOCK_OWNED=0
}

cleanup() {
  CLEANUP_RC=$?; trap - EXIT HUP INT TERM
  release_lock
  exit "$CLEANUP_RC"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

nearest_existing_ancestor() {
  local cursor parent
  cursor=$1
  while [ ! -e "$cursor" ] && [ ! -L "$cursor" ]; do
    parent=$(dirname "$cursor"); [ "$parent" != "$cursor" ] || break; cursor=$parent
  done
  [ -d "$cursor" ] || die "unsafe parent component is not a directory: $cursor"
}
check_parent_safe() { nearest_existing_ancestor "$(dirname "$1")"; }

ensure_dir_tree() {
  local requested
  requested=$1
  mkdir -p "$requested" || die "could not create directory: $requested"
  [ -d "$requested" ] || die "directory path is unsafe: $requested"
}

pin_parent() {
  local requested
  requested=$1
  PINNED_LOGICAL_PARENT=$(dirname "$requested")
  PINNED_REAL_PARENT=$(physical_dir "$PINNED_LOGICAL_PARENT") \
    || die "could not resolve link parent: $PINNED_LOGICAL_PARENT"
  PINNED_PARENT_IDENTITY=$(path_identity "$PINNED_REAL_PARENT") \
    || die "could not identify link parent: $PINNED_LOGICAL_PARENT"
  PINNED_FINAL_NAME=$(basename "$requested")
}

queue_link() {
  local kind logical_parent real_parent identity final_name target target_check_path target_identity
  kind=$1; logical_parent=$2; real_parent=$3; identity=$4; final_name=$5; target=$6; target_check_path=$7; target_identity=$8
  PUBLICATION_ARGS+=("$kind" "$logical_parent" "$real_parent" "$identity" "$final_name" "$target" "$target_check_path" "$target_identity")
}

acquire_lock() {
  local pause attempts owner_pid owner_start owner_token extra live_start stale_path
  LOCK_PATH="/tmp/video-hardsub-fcp-install.${UID}.lock"
  LOCK_TOKEN="$$-${RANDOM}-${RANDOM}-${SECONDS:-0}"
  LOCK_OWNER_FILE="$LOCK_PATH/owner"
  attempts=0
  while [ "$attempts" -lt 20 ]; do
    attempts=$((attempts + 1))
    if /bin/mkdir "$LOCK_PATH" 2>/dev/null; then
      LOCK_KEEPER_FIFO="$LOCK_PATH/lifetime"
      if ! /usr/bin/mkfifo -m 600 "$LOCK_KEEPER_FIFO"; then
        /bin/rmdir "$LOCK_PATH" 2>/dev/null || true
        die "could not create installer lock lifetime channel: $LOCK_PATH"
      fi
      /bin/bash -c '
        trap "" HUP INT TERM
        fifo=$1; owner_file=$2; lock_path=$3; expected_token=$4
        while IFS= read -r _; do :; done <"$fifo"
        owner_pid=""; owner_start=""; owner_token=""; extra=""
        current_start=$(LC_ALL=C TZ=UTC /bin/ps -o lstart= -p "$$" 2>/dev/null) || current_start=""
        if [ -d "$lock_path" ] && [ ! -L "$lock_path" ] && [ -f "$owner_file" ] && [ ! -L "$owner_file" ] \
          && { IFS= read -r owner_pid && IFS= read -r owner_start && IFS= read -r owner_token && ! IFS= read -r extra; } <"$owner_file" \
          && [ -z "$extra" ] && [ "$owner_pid" = "$$" ] && [ "$owner_start" = "$current_start" ] \
          && [ "$owner_token" = "$expected_token" ]; then
          /bin/rm -f -- "$fifo" "$owner_file" 2>/dev/null || exit 1
          /bin/rmdir "$lock_path" 2>/dev/null || exit 1
        else
          printf "install.sh: lock ownership became ambiguous; retained lock directory: %s\n" "$lock_path" >&2
          exit 1
        fi
      ' lock-keeper "$LOCK_KEEPER_FIFO" "$LOCK_OWNER_FILE" "$LOCK_PATH" "$LOCK_TOKEN" &
      LOCK_KEEPER_PID=$!
      exec 9>"$LOCK_KEEPER_FIFO"
      LOCK_OWNED=1
      LOCK_OWNER_PID=$LOCK_KEEPER_PID
      LOCK_OWNER_START=$(LC_ALL=C TZ=UTC /bin/ps -o lstart= -p "$LOCK_OWNER_PID") \
        || die 'could not determine lock keeper process start identity'
      [ -n "$LOCK_OWNER_START" ] || die 'could not determine lock keeper process start identity'
      if ! (umask 077; printf '%s\n%s\n%s\n' "$LOCK_OWNER_PID" "$LOCK_OWNER_START" "$LOCK_TOKEN" >"$LOCK_OWNER_FILE"); then
        die "could not record lock ownership: $LOCK_PATH"
      fi
      break
    fi
    [ -d "$LOCK_PATH" ] && [ ! -L "$LOCK_PATH" ] \
      || die "installer lock path is external or ambiguous; inspect manually: $LOCK_PATH"
    [ -f "$LOCK_OWNER_FILE" ] && [ ! -L "$LOCK_OWNER_FILE" ] \
      || die "installer lock owner record is missing or ambiguous; inspect manually: $LOCK_PATH"
    owner_pid=''; owner_start=''; owner_token=''; extra=''
    if ! { IFS= read -r owner_pid && IFS= read -r owner_start && IFS= read -r owner_token && ! IFS= read -r extra; } <"$LOCK_OWNER_FILE"; then
      die "installer lock owner record is malformed; inspect manually: $LOCK_PATH"
    fi
    [ -z "$extra" ] || die "installer lock owner record has trailing data; inspect manually: $LOCK_PATH"
    case $owner_pid in ''|*[!0-9]*) die "installer lock owner PID is malformed; inspect manually: $LOCK_PATH" ;; esac
    [ -n "$owner_start" ] && [ -n "$owner_token" ] \
      || die "installer lock owner record is incomplete; inspect manually: $LOCK_PATH"
    if kill -0 "$owner_pid" 2>/dev/null; then
      if live_start=$(LC_ALL=C TZ=UTC /bin/ps -o lstart= -p "$owner_pid" 2>/dev/null); then
        if [ "$live_start" = "$owner_start" ]; then
          die "another video-hardsub-fcp installer is active (pid $owner_pid); wait and retry: $LOCK_PATH"
        fi
      elif kill -0 "$owner_pid" 2>/dev/null; then
        die "installer lock owner is live but its start identity is ambiguous; inspect manually: $LOCK_PATH"
      fi
    fi
    stale_path="$LOCK_PATH.stale-$owner_pid-$$-${RANDOM}-$attempts"
    [ ! -e "$stale_path" ] && [ ! -L "$stale_path" ] || continue
    if /bin/mv -- "$LOCK_PATH" "$stale_path" 2>/dev/null; then
      printf 'install.sh: quarantined demonstrably stale installer lock; retained for inspection: %s\n' "$stale_path" >&2
    fi
  done
  [ "$LOCK_OWNED" -eq 1 ] || die "could not acquire installer lock after stale-lock contention: $LOCK_PATH"
  pause=${VIDEO_HARDSUB_FCP_INSTALL_TEST_PAUSE_AFTER_LOCK_SECONDS:-0}
  case $pause in ''|*[!0-9]*) die 'invalid test lock pause' ;; esac
  [ "$pause" -le 30 ] || die 'test lock pause exceeds 30 seconds'
  if [ "$pause" -gt 0 ]; then /bin/sleep "$pause"; fi
}

validate_source() {
  local candidate first_name helper
  candidate=$1; [ -d "$candidate" ] || die '--source must resolve to a directory'
  VALID_SOURCE_REAL=$(physical_dir "$candidate") || die 'could not resolve --source directory'
  [ -f "$VALID_SOURCE_REAL/SKILL.md" ] || die '--source is not a valid video-hardsub-fcp Skill'
  first_name=$(awk '/^name:[[:space:]]*/ { sub(/^name:[[:space:]]*/, ""); print; exit }' "$VALID_SOURCE_REAL/SKILL.md")
  [ "$first_name" = "$SKILL_NAME" ] || die '--source is not a valid video-hardsub-fcp Skill'
  for helper in inspect-video.sh fetch-hitpaw-result.sh verify-video.sh conform-vertical.sh analyze-cuts.py build-fcpxml.py package-deliverables.sh; do
    [ -f "$VALID_SOURCE_REAL/scripts/$helper" ] || die "--source is missing scripts/$helper"
  done
}

validate_official_checkout() {
  local checkout expected_identity expected_real actual_identity actual_real origin checkout_status current_branch upstream_remote upstream_merge upstream_branch
  checkout=$1; expected_identity=$2; expected_real=$3
  [ -d "$checkout" ] && [ ! -L "$checkout" ] && [ -d "$checkout/.git" ] \
    || die 'canonical checkout topology changed or is invalid'
  actual_identity=$(path_identity "$checkout") || die 'could not identify canonical checkout'
  [ "$actual_identity" = "$expected_identity" ] || die 'canonical checkout identity changed during install'
  actual_real=$(physical_dir "$checkout") || die 'could not resolve canonical checkout'
  [ "$actual_real" = "$expected_real" ] || die 'canonical checkout path changed during install'
  validate_source "$checkout"
  origin=$(git -C "$checkout" remote get-url origin 2>/dev/null) || die 'canonical checkout has no readable origin'
  [ "$origin" = "$OFFICIAL_URL" ] || die 'canonical checkout origin is not the official repository'
  if ! checkout_status=$(git -C "$checkout" status --porcelain --untracked-files=normal); then
    die 'could not verify canonical checkout cleanliness'
  fi
  [ -z "$checkout_status" ] || die 'canonical checkout has local changes or untracked files; refusing update'
  current_branch=$(git -C "$checkout" symbolic-ref --quiet --short HEAD) || die 'canonical checkout is detached; refusing update'
  upstream_remote=$(git -C "$checkout" config --get "branch.$current_branch.remote" 2>/dev/null) || die 'canonical checkout branch has no upstream remote'
  [ "$upstream_remote" = origin ] || die 'canonical checkout branch does not track official origin'
  upstream_merge=$(git -C "$checkout" config --get "branch.$current_branch.merge" 2>/dev/null) || die 'canonical checkout branch has no upstream branch'
  case $upstream_merge in refs/heads/*) upstream_branch=${upstream_merge#refs/heads/} ;; *) die 'canonical checkout has an invalid upstream branch' ;; esac
  [ "$current_branch" = "$upstream_branch" ] || die 'canonical checkout branch name does not match its official upstream branch'
  VALID_OFFICIAL_BRANCH=$current_branch
  VALID_OFFICIAL_UPSTREAM_BRANCH=$upstream_branch
}

preflight_discovery_link() {
  local link_path expected_target
  link_path=$1; expected_target=$2
  if [ -L "$link_path" ]; then symlink_matches "$link_path" "$expected_target" || die "refusing wrong symlink: $link_path"
  elif [ -e "$link_path" ]; then die "refusing to replace real path: $link_path"; fi
}

publish_links_transaction() {
  [ "$#" -gt 0 ] || return 0
  python3 - "$LOCK_TOKEN" "$@" <<'PY'
import ctypes
import os
import signal
import stat
import sys
import time


class InstallError(RuntimeError):
    pass


class InstallSignal(BaseException):
    def __init__(self, signum):
        super().__init__(f"interrupted by signal {signum}")
        self.signum = signum


def interrupt(signum, _frame):
    raise InstallSignal(signum)


token = sys.argv[1]
raw = sys.argv[2:]
if len(raw) % 8:
    raise SystemExit("install.sh: invalid private publication transaction")

handled_signals = [signal.SIGINT, signal.SIGTERM]
if hasattr(signal, "SIGHUP"):
    handled_signals.append(signal.SIGHUP)
for handled_signal in handled_signals:
    signal.signal(handled_signal, interrupt)

libc = ctypes.CDLL(None, use_errno=True)
if sys.platform == "darwin":
    rename_call = libc.renameatx_np
    rename_call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename_call.restype = ctypes.c_int

    def rename_noreplace(fd, source, destination):
        result = rename_call(fd, os.fsencode(source), fd, os.fsencode(destination), 0x00000004)
        if result:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), source, destination)
else:
    rename_call = libc.renameat2
    rename_call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename_call.restype = ctypes.c_int

    def rename_noreplace(fd, source, destination):
        result = rename_call(fd, os.fsencode(source), fd, os.fsencode(destination), 1)
        if result:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), source, destination)


def parse_identity(value):
    try:
        device, inode = value.split(":", 1)
        return int(device), int(inode)
    except (TypeError, ValueError) as exc:
        raise InstallError("invalid pinned parent identity") from exc


def lstat_at(record, name):
    return os.stat(name, dir_fd=record["fd"], follow_symlinks=False)


def path_exists_at(record, name):
    try:
        lstat_at(record, name)
    except FileNotFoundError:
        return False
    return True


def resolved_link(record, name):
    target = os.readlink(name, dir_fd=record["fd"])
    if not os.path.isabs(target):
        target = os.path.join(record["expected_real"], target)
    return os.path.realpath(os.path.abspath(target))


def verify_parent(record):
    try:
        current = os.stat(record["logical_parent"])
        current_real = os.path.realpath(os.path.abspath(record["logical_parent"]))
    except OSError as exc:
        raise InstallError(f"logical link parent became unavailable: {record['logical_parent']}") from exc
    if (current.st_dev, current.st_ino) != record["identity"] or current_real != record["expected_real"]:
        raise InstallError(f"logical link parent changed during install: {record['logical_parent']}")


def verify_target(record):
    try:
        pinned = os.fstat(record["target_fd"])
        target = os.stat(os.path.realpath(os.path.abspath(record["target_check_path"])))
    except OSError as exc:
        raise InstallError(f"validated link target became unavailable: {record['target_check_path']}") from exc
    if (
        not stat.S_ISDIR(pinned.st_mode)
        or (pinned.st_dev, pinned.st_ino) != record["target_identity"]
        or not stat.S_ISDIR(target.st_mode)
        or (target.st_dev, target.st_ino) != record["target_identity"]
    ):
        raise InstallError(f"validated link target changed during install: {record['target_check_path']}")


def verify_final(record):
    try:
        current = lstat_at(record, record["final_name"])
    except FileNotFoundError as exc:
        raise InstallError(
            f"accepted final path disappeared during install: {record['expected_real']}/{record['final_name']}"
        ) from exc
    identity = (current.st_dev, current.st_ino)
    state = record["state"]
    if state == "existing-link":
        valid = (
            stat.S_ISLNK(current.st_mode)
            and identity == record["existing_identity"]
            and resolved_link(record, record["final_name"])
            == os.path.realpath(os.path.abspath(record["target"]))
        )
    elif state == "existing-real":
        valid = (
            stat.S_ISDIR(current.st_mode)
            and not stat.S_ISLNK(current.st_mode)
            and identity == record["existing_identity"]
            and os.path.realpath(os.path.join(record["expected_real"], record["final_name"]))
            == os.path.realpath(os.path.abspath(record["target"]))
        )
    elif state == "published":
        valid = (
            stat.S_ISLNK(current.st_mode)
            and identity == record["staged_identity"]
            and resolved_link(record, record["final_name"])
            == os.path.realpath(os.path.abspath(record["target"]))
        )
    else:
        raise InstallError(f"invalid final verification state: {state}")
    if not valid:
        raise InstallError(
            f"accepted final path changed during install: {record['expected_real']}/{record['final_name']}"
        )


recovery_counter = 0


def retain(record, current_name, purpose):
    global recovery_counter
    if not path_exists_at(record, current_name):
        return
    for _ in range(20):
        recovery_counter += 1
        recovery = f".video-hardsub-fcp-recovery-{token}-{recovery_counter}"
        try:
            rename_noreplace(record["fd"], current_name, recovery)
        except FileNotFoundError:
            return
        except FileExistsError:
            continue
        except OSError:
            if not path_exists_at(record, current_name):
                return
            print(
                f"install.sh: could not atomically retain {purpose}; left current path untouched: "
                f"{record['expected_real']}/{current_name}",
                file=sys.stderr,
            )
            return
        recovered = lstat_at(record, recovery)
        owned = (
            stat.S_ISLNK(recovered.st_mode)
            and (recovered.st_dev, recovered.st_ino) == record.get("staged_identity")
            and resolved_link(record, recovery) == os.path.realpath(os.path.abspath(record["target"]))
        )
        classification = "owned" if owned else "external or ambiguous"
        print(
            f"install.sh: retained {classification} {purpose} recovery artifact; inspect manually: "
            f"{record['expected_real']}/{recovery}",
            file=sys.stderr,
        )
        return
    print(
        f"install.sh: could not reserve recovery name for {purpose}; left current path untouched: "
        f"{record['expected_real']}/{current_name}",
        file=sys.stderr,
    )


def parse_pause(name):
    value = os.environ.get(name, "0")
    if not value.isdigit() or int(value) > 30:
        raise InstallError(f"invalid private test pause: {name}")
    return int(value)


records = []
opened = []
published = []
try:
    for offset in range(0, len(raw), 8):
        kind, logical_parent, expected_real, identity_text, final_name, target, target_check_path, target_identity_text = raw[offset:offset + 8]
        if kind not in ("link", "real"):
            raise InstallError(f"invalid publication kind: {kind!r}")
        if not final_name or final_name in (".", "..") or os.sep in final_name:
            raise InstallError(f"invalid final link name: {final_name!r}")
        identity = parse_identity(identity_text)
        target_identity = parse_identity(target_identity_text)
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(expected_real, flags)
        opened.append(fd)
        pinned = os.fstat(fd)
        if (pinned.st_dev, pinned.st_ino) != identity:
            raise InstallError(f"pinned link parent identity changed: {logical_parent}")
        target_fd = os.open(target_check_path, flags)
        opened.append(target_fd)
        pinned_target = os.fstat(target_fd)
        if (pinned_target.st_dev, pinned_target.st_ino) != target_identity:
            raise InstallError(f"validated link target identity changed: {target_check_path}")
        record = {
            "logical_parent": logical_parent,
            "expected_real": expected_real,
            "identity": identity,
            "final_name": final_name,
            "target": target,
            "target_check_path": target_check_path,
            "target_identity": target_identity,
            "target_fd": target_fd,
            "kind": kind,
            "fd": fd,
            "state": "pending",
        }
        verify_parent(record)
        verify_target(record)
        records.append(record)

    for index, record in enumerate(records, 1):
        try:
            existing = lstat_at(record, record["final_name"])
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if record["kind"] == "link":
                if not stat.S_ISLNK(existing.st_mode) or resolved_link(record, record["final_name"]) != os.path.realpath(os.path.abspath(record["target"])):
                    raise InstallError(f"refusing existing path in pinned parent: {record['expected_real']}/{record['final_name']}")
                record["state"] = "existing-link"
            else:
                final_path = os.path.join(record["expected_real"], record["final_name"])
                if not stat.S_ISDIR(existing.st_mode) or stat.S_ISLNK(existing.st_mode) or os.path.realpath(final_path) != os.path.realpath(os.path.abspath(record["target"])):
                    raise InstallError(f"refusing existing real path in pinned parent: {final_path}")
                record["state"] = "existing-real"
            record["existing_identity"] = (existing.st_dev, existing.st_ino)
            continue
        if record["kind"] == "real":
            raise InstallError(f"accepted real path disappeared before transaction: {record['expected_real']}/{record['final_name']}")
        staged_name = f".video-hardsub-fcp-link-{token}-{index}"
        if path_exists_at(record, staged_name):
            raise InstallError(f"temporary symlink path already exists: {record['expected_real']}/{staged_name}")
        os.symlink(record["target"], staged_name, dir_fd=record["fd"])
        staged = lstat_at(record, staged_name)
        if not stat.S_ISLNK(staged.st_mode) or os.readlink(staged_name, dir_fd=record["fd"]) != record["target"]:
            raise InstallError(f"could not verify staged symlink: {record['expected_real']}/{staged_name}")
        record["staged_name"] = staged_name
        record["staged_identity"] = (staged.st_dev, staged.st_ino)
        record["state"] = "staged"

    marker = os.environ.get("VIDEO_HARDSUB_FCP_INSTALL_TEST_PARENT_PIN_MARKER")
    if marker:
        marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(marker_fd)
    pause = parse_pause("VIDEO_HARDSUB_FCP_INSTALL_TEST_PAUSE_BEFORE_FIRST_LINK_SECONDS")
    if pause:
        time.sleep(pause)

    publication_count = 0
    for record in records:
        if record["state"] in ("existing-link", "existing-real"):
            try:
                verify_parent(record)
                verify_target(record)
                verify_final(record)
            except InstallError:
                retain(record, record["final_name"], "substituted accepted path")
                record["state"] = "retained"
                raise
            continue
        try:
            rename_noreplace(record["fd"], record["staged_name"], record["final_name"])
        except OSError as exc:
            retain(record, record["staged_name"], "unpublished link")
            record["state"] = "retained"
            raise InstallError(f"could not publish symlink without replacement: {record['expected_real']}/{record['final_name']}") from exc
        record["state"] = "published"
        published.append(record)
        publication_count += 1
        try:
            verify_parent(record)
            verify_target(record)
            verify_final(record)
        except InstallError:
            retain(record, record["final_name"], "published link")
            record["state"] = "retained"
            raise
        if publication_count == 1 and "VIDEO_HARDSUB_FCP_INSTALL_TEST_FAIL_AFTER_FIRST_LINK_DELAY_SECONDS" in os.environ:
            delay = parse_pause("VIDEO_HARDSUB_FCP_INSTALL_TEST_FAIL_AFTER_FIRST_LINK_DELAY_SECONDS")
            if delay:
                time.sleep(delay)
            raise InstallError("injected test failure after first link publication")

    for record in records:
        try:
            verify_parent(record)
            verify_target(record)
            verify_final(record)
        except InstallError:
            if record.get("state") in ("existing-link", "existing-real", "published"):
                retain(record, record["final_name"], "changed final path")
                record["state"] = "retained"
            raise
except BaseException as exc:
    for handled_signal in handled_signals:
        signal.signal(handled_signal, signal.SIG_IGN)
    for record in reversed(records):
        if record.get("state") == "staged":
            retain(record, record["staged_name"], "unpublished link")
            record["state"] = "retained"
    for record in reversed(published):
        if record.get("state") == "published":
            retain(record, record["final_name"], "published link")
            record["state"] = "retained"
    if isinstance(exc, InstallSignal):
        exit_status = 128 + exc.signum
    elif isinstance(exc, KeyboardInterrupt):
        exit_status = 130
    else:
        exit_status = 1
    print(f"install.sh: {exc}", file=sys.stderr)
    raise SystemExit(exit_status)
finally:
    for fd in opened:
        os.close(fd)
PY
}

case $# in
  0) mode=remote; source_arg='' ;;
  1) case $1 in --help|-h) usage; exit 0 ;; *) usage >&2; exit 2 ;; esac ;;
  2) [ "$1" = '--source' ] || { usage >&2; exit 2; }; mode=source; source_arg=$2 ;;
  *) usage >&2; exit 2 ;;
esac

canonical_requested="${CLAUDE_SKILLS_DIR:-"$HOME/.claude/skills"}/$SKILL_NAME"
codex_requested="${CODEX_HOME:-"$HOME/.codex"}/skills/$SKILL_NAME"
agents_requested="$HOME/.agents/skills/$SKILL_NAME"

# A stable advisory guard serializes inspection, stale quarantine, directory-lock
# acquisition, all topology mutation, and rollback. The guard file is never
# removed, so a crash releases the kernel lock without creating a stale-guard ABA.
guard_path="/tmp/video-hardsub-fcp-install.${UID}.guard"
if [ -z "${VIDEO_HARDSUB_FCP_INSTALL_GUARD_FD:-}" ]; then
  exec /usr/bin/python3 - "$guard_path" "$0" "$@" <<'PY'
import errno
import os
import stat
import sys
import time

guard_path, installer, *installer_args = sys.argv[1:]
if not hasattr(os, "O_NOFOLLOW"):
    print("install.sh: O_NOFOLLOW is required for the installer guard", file=sys.stderr)
    raise SystemExit(1)

marker = os.environ.get("VIDEO_HARDSUB_FCP_INSTALL_TEST_GUARD_CREATE_MARKER")
pause_text = os.environ.get("VIDEO_HARDSUB_FCP_INSTALL_TEST_PAUSE_BEFORE_GUARD_CREATE_SECONDS", "0")
if not pause_text.isdigit() or int(pause_text) > 30:
    print("install.sh: invalid private guard-create pause", file=sys.stderr)
    raise SystemExit(1)
if marker:
    marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(marker_fd)
if int(pause_text):
    time.sleep(int(pause_text))

flags = os.O_RDWR | os.O_NOFOLLOW
guard_fd = None
for _ in range(100):
    try:
        previous_umask = os.umask(0o077)
        try:
            guard_fd = os.open(guard_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        finally:
            os.umask(previous_umask)
    except FileExistsError:
        try:
            guard_fd = os.open(guard_path, flags)
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"install.sh: existing installer guard is unsafe: {guard_path}: {exc}", file=sys.stderr)
            raise SystemExit(1)
    except OSError as exc:
        print(f"install.sh: could not atomically create installer guard: {guard_path}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    try:
        opened = os.fstat(guard_fd)
        current = os.stat(guard_path, follow_symlinks=False)
    except OSError as exc:
        os.close(guard_fd)
        guard_fd = None
        if exc.errno == errno.ENOENT:
            continue
        print(f"install.sh: could not validate installer guard: {guard_path}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    valid = (
        stat.S_ISREG(opened.st_mode)
        and opened.st_uid == os.getuid()
        and opened.st_nlink == 1
        and stat.S_IMODE(opened.st_mode) == 0o600
        and stat.S_ISREG(current.st_mode)
        and (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
    )
    if not valid:
        os.close(guard_fd)
        print(f"install.sh: installer guard ownership, mode, link count, or identity is unsafe: {guard_path}", file=sys.stderr)
        raise SystemExit(1)
    break
if guard_fd is None:
    print(f"install.sh: installer guard changed repeatedly during atomic open: {guard_path}", file=sys.stderr)
    raise SystemExit(1)

fixed_fd = 8
if guard_fd != fixed_fd:
    os.dup2(guard_fd, fixed_fd, inheritable=True)
    os.close(guard_fd)
else:
    os.set_inheritable(fixed_fd, True)
opened = os.fstat(fixed_fd)
environment = os.environ.copy()
environment["VIDEO_HARDSUB_FCP_INSTALL_GUARD_FD"] = str(fixed_fd)
environment["VIDEO_HARDSUB_FCP_INSTALL_GUARD_IDENTITY"] = f"{opened.st_dev}:{opened.st_ino}"
os.execve("/bin/bash", ["bash", installer, *installer_args], environment)
PY
fi
guard_fd=$VIDEO_HARDSUB_FCP_INSTALL_GUARD_FD
guard_identity=${VIDEO_HARDSUB_FCP_INSTALL_GUARD_IDENTITY:-}
case $guard_fd in ''|*[!0-9]*) die 'installer guard descriptor is invalid' ;; esac
[ -n "$guard_identity" ] || die 'installer guard identity is missing'
validate_guard_fd "$guard_path" "$guard_fd" "$guard_identity" \
  || die "installer guard descriptor failed validation: $guard_path"
if ! /usr/bin/lockf -t 0 "$guard_fd"; then
  die "another video-hardsub-fcp installer holds the per-user guard; wait and retry: $guard_path"
fi
validate_guard_fd "$guard_path" "$guard_fd" "$guard_identity" \
  || die "installer guard changed while locked; inspect manually: $guard_path"
acquire_lock

[ "$(uname -s)" = 'Darwin' ] || die 'macOS is required'
require_tool git; require_tool python3; require_tool ffmpeg; require_tool ffprobe
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || die 'Python 3.10 or newer is required'

if [ "$mode" = source ]; then
  validate_source "$source_arg"; source_real=$VALID_SOURCE_REAL
  source_identity=$(path_identity "$source_real") || die 'could not identify validated --source directory'
  if [ -L "$canonical_requested" ]; then
    symlink_matches "$canonical_requested" "$source_real" || die "refusing wrong symlink: $canonical_requested"; canonical_kind='link'
  elif [ -e "$canonical_requested" ]; then
    [ -d "$canonical_requested" ] && [ "$(physical_dir "$canonical_requested")" = "$source_real" ] \
      || die "refusing arbitrary real path at canonical location: $canonical_requested"; canonical_kind='real'
  else canonical_kind='link'; fi
else
  if [ -L "$canonical_requested" ]; then die "refusing existing symlink at canonical path: $canonical_requested"
  elif [ -e "$canonical_requested" ]; then [ -d "$canonical_requested/.git" ] || die "refusing arbitrary real directory at canonical path: $canonical_requested"; fi
fi

preflight_discovery_link "$codex_requested" "$canonical_requested"
preflight_discovery_link "$agents_requested" "$canonical_requested"
check_parent_safe "$canonical_requested"; check_parent_safe "$codex_requested"; check_parent_safe "$agents_requested"
ensure_dir_tree "$(dirname "$canonical_requested")"; ensure_dir_tree "$(dirname "$codex_requested")"; ensure_dir_tree "$(dirname "$agents_requested")"

pin_parent "$canonical_requested"
canonical_parent_logical=$PINNED_LOGICAL_PARENT; canonical_parent_real=$PINNED_REAL_PARENT
canonical_parent_identity=$PINNED_PARENT_IDENTITY; canonical_name=$PINNED_FINAL_NAME
canonical="$canonical_parent_real/$canonical_name"
pin_parent "$codex_requested"
codex_parent_logical=$PINNED_LOGICAL_PARENT; codex_parent_real=$PINNED_REAL_PARENT
codex_parent_identity=$PINNED_PARENT_IDENTITY; codex_name=$PINNED_FINAL_NAME
codex_link="$codex_parent_real/$codex_name"
pin_parent "$agents_requested"
agents_parent_logical=$PINNED_LOGICAL_PARENT; agents_parent_real=$PINNED_REAL_PARENT
agents_parent_identity=$PINNED_PARENT_IDENTITY; agents_name=$PINNED_FINAL_NAME
agents_link="$agents_parent_real/$agents_name"

if [ "$mode" = source ]; then
  canonical_target_identity=$source_identity
  canonical_target_path=$source_real
  queue_link "$canonical_kind" "$canonical_parent_logical" "$canonical_parent_real" "$canonical_parent_identity" "$canonical_name" "$source_real" "$canonical_target_path" "$canonical_target_identity"
else
  if [ -e "$canonical" ]; then
    checkout_identity=$(path_identity "$canonical") || die 'could not identify canonical checkout'
    checkout_real=$(physical_dir "$canonical") || die 'could not resolve canonical checkout'
    validate_official_checkout "$canonical" "$checkout_identity" "$checkout_real"
    pre_pull_branch=$VALID_OFFICIAL_BRANCH
    pre_pull_upstream_branch=$VALID_OFFICIAL_UPSTREAM_BRANCH
    git -C "$canonical" pull --ff-only origin "$pre_pull_upstream_branch" || die 'fast-forward update from official origin failed'
    validate_official_checkout "$canonical" "$checkout_identity" "$checkout_real"
    [ "$VALID_OFFICIAL_BRANCH" = "$pre_pull_branch" ] || die 'canonical checkout branch changed during update'
    [ "$VALID_OFFICIAL_UPSTREAM_BRANCH" = "$pre_pull_upstream_branch" ] || die 'canonical checkout upstream changed during update'
    checkout_head=$(git -C "$canonical" rev-parse --verify HEAD 2>/dev/null) || die 'could not resolve canonical checkout HEAD after update'
    fetched_head=$(git -C "$canonical" rev-parse --verify FETCH_HEAD 2>/dev/null) || die 'could not resolve fetched official revision after update'
    [ "$checkout_head" = "$fetched_head" ] || die 'canonical checkout contains commits not present in the fetched official branch'
  else
    git clone "$OFFICIAL_URL" "$canonical" || die 'clone failed'
    checkout_identity=$(path_identity "$canonical") || die 'could not identify cloned canonical checkout'
    checkout_real=$(physical_dir "$canonical") || die 'could not resolve cloned canonical checkout'
    validate_official_checkout "$canonical" "$checkout_identity" "$checkout_real"
  fi
  canonical_target_identity=$checkout_identity
  canonical_target_path=$checkout_real
  queue_link real "$canonical_parent_logical" "$canonical_parent_real" "$canonical_parent_identity" "$canonical_name" "$checkout_real" "$canonical_target_path" "$canonical_target_identity"
fi

queue_link link "$codex_parent_logical" "$codex_parent_real" "$codex_parent_identity" "$codex_name" "$canonical" "$canonical_target_path" "$canonical_target_identity"
queue_link link "$agents_parent_logical" "$agents_parent_real" "$agents_parent_identity" "$agents_name" "$canonical" "$canonical_target_path" "$canonical_target_identity"
publish_links_transaction "${PUBLICATION_ARGS[@]}"

physical_dir "$canonical" >/dev/null || die 'canonical Skill is unavailable'
[ -L "$codex_link" ] && [ -L "$agents_link" ] || die 'Skill discovery links are unavailable after publication'
if [ ! -d '/Applications/HitPaw Edimakor.app' ] && [ ! -d "$HOME/Applications/HitPaw Edimakor.app" ]; then
  printf '%s\n' 'Note: HitPaw is a separate GUI prerequisite. Install it before paid removal, or use its actual app name/location if it differs.' >&2
fi
printf 'Installed %s at %s\n' "$SKILL_NAME" "$canonical"
