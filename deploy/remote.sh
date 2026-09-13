#!/usr/bin/env bash
# MCS remote driver: `deploy/remote.sh <host> <verb>`; profiles in deploy/hosts/<host>.env.
#
# Small on purpose. Each verb rsyncs this checkout to the host and runs this same Makefile there
# with the profile's variables. The host lease (one GPU, one podman store) is MURRiX's
# `make remote-lock HOST=<host>`; take it before build/run/stop here — this script only reminds.
set -euo pipefail

HOST_PROFILE=${1:?usage: remote.sh <host> <verb>}
VERB=${2:?usage: remote.sh <host> <verb>}
HERE=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$HERE/deploy/hosts/$HOST_PROFILE.env"
[ -f "$ENV_FILE" ] || { echo "remote: no profile $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${REMOTE_HOST:?}" "${REMOTE_USER:?}" "${REMOTE_DIR:?}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
TARGET="$REMOTE_USER@$REMOTE_HOST"

# Every variable the Makefile reads, forwarded as `make VAR=value` on the host.
FORWARD=(IMAGE_NAME CONTAINER_NAME MCS_PORT MCS_KEY MCS_ROOTS FILES_PATH OLD_PATH VOLATILE_PATH TMP_PATH MCS_MEMORY GPU TZ
         TORCH_CUDA INSTRUCT_MODEL VLM_MODEL FFMPEG_BUILD FFMPEG_ASSET
         MCS_MODEL_PINNED MCS_MODEL_IDLE_EVICT MCS_MODEL_MAX_RSS MCS_INSTRUCT_DEVICE MCS_VLM_QUANT MCS_WHISPER_LANGUAGE
         MCS_LIMIT_TOOLS MCS_LIMIT_MODELS MCS_LIMIT_ENCODES MCS_CPU_ENCODE_MAX_MB MCS_CPU_ENCODE_MB_PER_1K_FRAMES
         MCS_CPU_ENCODE_MAX_SEGMENTS MCS_VIDEO_ENCODER MCS_ENCODE_TIMEOUT)
make_args=()
for name in "${FORWARD[@]}"; do
  if [ -n "${!name:-}" ]; then make_args+=("$name=${!name}"); fi
done

remote() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }
remote_make() { remote "cd '$REMOTE_DIR' && make $(printf '%q ' "${make_args[@]}") $*"; }

sync() {
  echo "==> syncing to $TARGET:$REMOTE_DIR"
  remote "mkdir -p '$REMOTE_DIR'"
  rsync -az --delete --info=stats1 -e "ssh ${SSH_OPTS[*]}" \
    --exclude '/.git' --exclude '/.venv' --exclude '__pycache__/' --exclude '/.pytest_cache' \
    "$HERE/" "$TARGET:$REMOTE_DIR/"
}

case "$VERB" in
  sync) sync ;;
  build) echo "==> reminder: hold the host lease (MURRiX: make remote-lock HOST=$HOST_PROFILE)"; sync; remote_make build ;;
  run) sync; remote_make run ;;
  stop) remote_make stop ;;
  restart) remote_make stop || true; sync; remote_make run ;;
  logs) remote "podman logs -f '${CONTAINER_NAME:-mcs}'" ;;
  status) remote "podman ps --filter name='${CONTAINER_NAME:-mcs}' --format '{{.Names}} {{.Status}}'; nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null || true" ;;
  shell) remote -t "podman exec -it '${CONTAINER_NAME:-mcs}' bash" ;;
  gpu-check) remote_make gpu-check ;;
  health) remote "curl -s -H 'Authorization: Bearer ${MCS_KEY:-let-me-in}' http://localhost:${MCS_PORT:-8181}/v2/health" | python3 -m json.tool ;;
  *) echo "remote: unknown verb $VERB" >&2; exit 1 ;;
esac
