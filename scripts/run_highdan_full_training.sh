#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR_DEFAULT="$ROOT_DIR/artifacts/external_kifu/highdan_corpus/highdan-20260225-092752"
RUN_DIR="${1:-$RUN_DIR_DEFAULT}"
RUN_DIR="$(cd "$RUN_DIR" && pwd)"

echo "[policy_blocked] 外部棋譜取得は禁止のため run_highdan_full_training.sh は使用できません。" >&2
echo "[policy_blocked] ローカル自己対局データのみで学習を実行してください。" >&2
exit 3

TS="$(date -u +%Y%m%d-%H%M%S)"
PIPE_DIR="$RUN_DIR/full_pipeline_$TS"
mkdir -p "$PIPE_DIR"

COLLECT_LOG="$PIPE_DIR/collect.log"
CONVERT_LOG="$PIPE_DIR/convert.log"
LEARN_LOG="$PIPE_DIR/learn.log"
SUMMARY_JSON="$PIPE_DIR/summary.json"

echo "[full] run_dir=$RUN_DIR"
echo "[full] pipe_dir=$PIPE_DIR"

zsh "$ROOT_DIR/scripts/keep_mac_awake.sh" start || true

echo "[full] step=collect_start"
python3 -u "$ROOT_DIR/scripts/collect_shogi_extend_highdan_corpus.py" \
  --resume \
  --run-dir "$RUN_DIR" \
  --seed-user K_Yamawasabi \
  --min-dan 6 \
  --exclude-user K_Yamawasabi \
  --per 200 \
  --max-users 0 \
  --max-games-per-user 0 \
  --sleep-sec 0.0 >"$COLLECT_LOG" 2>&1
echo "[full] step=collect_done"

LEARNING_INPUT_DIR="$RUN_DIR/learning_input"
mkdir -p "$LEARNING_INPUT_DIR"
DECISION_JSONL="$LEARNING_INPUT_DIR/highdan_decision_events_min6_excl_yamawasabi_full_${TS}.jsonl"

echo "[full] step=convert_start"
python3 "$ROOT_DIR/scripts/convert_shogi_extend_highdan_records_to_decision_jsonl.py" \
  --input "$RUN_DIR/records_dedup.jsonl" \
  --output "$DECISION_JSONL" \
  --min-dan 6 \
  --exclude-user K_Yamawasabi >"$CONVERT_LOG" 2>&1
echo "[full] step=convert_done"

LEARNING_ROOT="$ROOT_DIR/artifacts/learning_runs_highdan"
echo "[full] step=learn_start"
python3 "$ROOT_DIR/scripts/run_learning_pipeline.py" \
  --logs "$DECISION_JSONL" \
  --output-root "$LEARNING_ROOT" \
  --skip-fill-outcomes \
  --train-hybrid \
  --no-train-ponder \
  --prefer-labeled-outcome \
  --min-outcome-confidence 0.7 \
  --min-outcome-match-confidence 0.7 \
  --ponder-label-mode heuristic_only \
  --min-ponder-label-confidence 0.0 >"$LEARN_LOG" 2>&1
echo "[full] step=learn_done"

LATEST_RUN="$(ls -1dt "$LEARNING_ROOT"/* | head -n1)"
LATEST_WEIGHT="$LATEST_RUN/weights/hybrid_weights.json"
if [[ ! -f "$LATEST_WEIGHT" ]]; then
  echo "weight_not_found: $LATEST_WEIGHT" >&2
  exit 3
fi

cp -f "$LATEST_WEIGHT" "$ROOT_DIR/models/hybrid_weights.json"
echo "[full] installed=$ROOT_DIR/models/hybrid_weights.json"

python3 - "$RUN_DIR" "$PIPE_DIR" "$DECISION_JSONL" "$LATEST_RUN" "$LATEST_WEIGHT" "$SUMMARY_JSON" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
pipe_dir = Path(sys.argv[2])
decision_jsonl = Path(sys.argv[3])
latest_run = Path(sys.argv[4])
latest_weight = Path(sys.argv[5])
summary_json = Path(sys.argv[6])

records = 0
with decision_jsonl.open("r", encoding="utf-8") as fh:
    for raw in fh:
        if raw.strip():
            records += 1

summary = {
    "run_dir": str(run_dir),
    "pipeline_dir": str(pipe_dir),
    "decision_jsonl": str(decision_jsonl),
    "decision_records": records,
    "learning_run": str(latest_run),
    "learning_weight": str(latest_weight),
    "installed_weight": str(Path("models/hybrid_weights.json")),
}
summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
PY

echo "[full] done"
