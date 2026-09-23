#!/usr/bin/env bash
# Run experiment arms locally or on a jobd host. Defaults reproduce the approved write-on pair;
# COLLUSION_ARMS_FILE / COLLUSION_ARM_IDS / COLLUSION_SAMPLES / COLLUSION_MAX_TURNS /
# COLLUSION_ENV_MODEL select others,
# and COLLUSION_VALIDATE_WRITE_FLAG='' skips the single-setting write-instruction validation.
set -euo pipefail
collection_id=${1:?Docent collection ID required}
shift
if [ -n "${JOBD_ATTEMPT_DIR:-}" ]; then
  cd "$JOBD_ATTEMPT_DIR/in/repo"
  output_root="$JOBD_ATTEMPT_DIR/out"
else
  cd "$(dirname "$0")/.."
  output_root="$PWD/runs"
fi
run_prefix=${COLLUSION_RUN_PREFIX:-hinted-write-on-100-20260909}
models_file=${COLLUSION_MODELS_FILE:-experiments/hinted-models.yaml}
arms_file=${COLLUSION_ARMS_FILE:-experiments/hinted-write-instructions.yaml}
arm_ids=${COLLUSION_ARM_IDS:-working-write-on empty-success-write-on}
samples=${COLLUSION_SAMPLES:-5}
max_turns=${COLLUSION_MAX_TURNS:-100}
env_model=${COLLUSION_ENV_MODEL:-env-gpt-5.6}
# The validator's write-instruction check only applies when every selected arm shares one setting.
validate_write_flag=${COLLUSION_VALIDATE_WRITE_FLAG---wiki-write-instructions}
# shellcheck disable=SC2206
arm_id_list=($arm_ids)
models=("$@")
if [ "${#models[@]}" -eq 0 ]; then
  models=(gpt-5.6 qwen3.8-27b glm-5.3 kimi-k3)
fi
for model_name in "${models[@]}"; do
  mkdir -p "$output_root/$run_prefix-$model_name"
done
if [ ! -x .venv/bin/ai-collusion-wiki ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install .
fi

run_model() {
  local model_name=$1
  local run_id="$run_prefix-$model_name"
  local run_path="$output_root/$run_id"
  local payload_status validation_status upload_status
  set +e
  .venv/bin/ai-collusion-wiki play \
    --page dse/DataUSAStateSequenceCollab2027 --rev 4 \
    --spec wikitasks/sector61_state_seven.yaml \
    --models "$models_file" \
    --arms "$arms_file" \
    --arm "${arm_id_list[@]}" \
    --env-model "$env_model" --only "$model_name" \
    -n "$samples" --workers 10 --max-turns "$max_turns" \
    --out "$output_root" --run-id "$run_id" \
    2>&1 | tee -a "$run_path/runner.log"
  payload_status=$?
  .venv/bin/python scripts/validate_rollouts.py "$run_path" \
    --model "$model_name" --samples "$samples" --rounds 7 --max-turns "$max_turns" \
    --arm-ids "${arm_id_list[@]}" $validate_write_flag \
    2>&1 | tee "$run_path/validation.txt"
  validation_status=$?
  # Preserve invalid/incomplete episodes for inspection as well as valid ones.
  .venv/bin/ai-collusion-docent --run "$run_path" --collection-id "$collection_id" \
    2>&1 | tee "$run_path/docent-upload.log"
  upload_status=$?
  printf 'payload_status=%s\nvalidation_status=%s\nupload_status=%s\n' \
    "$payload_status" "$validation_status" "$upload_status" > "$run_path/status.txt"
  if [ "$payload_status" -ne 0 ]; then return "$payload_status"; fi
  if [ "$validation_status" -ne 0 ]; then return "$validation_status"; fi
  return "$upload_status"
}

pids=()
for model_name in "${models[@]}"; do
  run_model "$model_name" &
  pids+=("$!")
done
batch_status=0
for child_pid in "${pids[@]}"; do
  if wait "$child_pid"; then
    :
  else
    child_status=$?
    printf 'Model process %s failed with exit %s\n' "$child_pid" "$child_status" >&2
    batch_status=1
  fi
done
exit "$batch_status"
