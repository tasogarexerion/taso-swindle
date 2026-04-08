# TASO-SWINDLE (Phase13 completed)

TASO-SWINDLE is a standalone USI wrapper that targets reversal value against humans in losing positions.
It keeps USI compatibility while selecting moves by REV (Reversal Expectation Value) instead of pure eval max.

## Requirements

- Python 3.11+
- Backend USI engine executable (YaneuraOu family expected)

## Project Rules

- 外部からの棋譜取得は禁止
  - `scripts/extract_shogi_extend_user_kifu.py`
  - `scripts/collect_shogi_extend_highdan_corpus.py`
  - 上記は実行時にポリシーで停止します（ローカル棋譜のみ利用）
- 学習サンプル内の個人情報は匿名化
  - `scripts/build_training_labels.py` は既定で `game_id*` / `source_log_path` を匿名化して出力します
  - 必要に応じて `--anonymize-salt` で匿名化トークンの塩を指定できます
- 学習データは個人使用のみを前提（外部公開しない）
  - `scripts/run_learning_pipeline.py` の summary に `compliance_mode=personal_use_only` を出力します
  - 既存データの再匿名化は `scripts/redact_jsonl_pii.py` を使用します

## Launch

```bash
python3 -m taso_swindle.main
```

## Minimum USI setup

```text
setoption name BackendEnginePath value /absolute/path/to/YaneuraOu
setoption name BackendEngineArgs value -eval /absolute/path/to/eval
isready
```

Then use normal `position` and `go`.

## Phase2/Phase6 behavior

- Stage1: root MultiPV candidate extraction (`score mate` priority + delta-cap gate)
- Stage2: per-candidate opponent reply probe (`SwindleReply*`)
- Mate verification with reusable verifier process (lazy start + reuse)
- Verify modes: `VERIFY_ONLY` / `TOP_CANDIDATES` / `AGGRESSIVE`
- Verify hybrid policy: `CONSERVATIVE` / `BALANCED` / `MATE_ENGINE_FIRST` / `DFPN_FIRST`
- Optional learned hybrid confidence adjustment (offline-trained JSON weights)
- Dedicated mate engine support (optional) with backend fallback
- Dedicated mate engine profile: `AUTO` / `SAFE` / `FAST_VERIFY`
- df-pn optional assist path (non-blocking, parser mode aware)
- Lightweight pseudo-hisshi with per-round budget / per-candidate probe cap
- Features:
  - `OnlyMovePressure` (`gap12`, `gap13`)
  - `ReplyEntropyScore`
  - heuristic `HumanTrapScore` (not fixed zero)
  - `SelfRisk`, `SurvivalScore`
  - lightweight `PseudoHisshiScore`
- REV integration:
  - Mate/Threat/OnlyMove/Entropy/Trap/Risk/Survival (+ pseudo hisshi)
  - `score mate +N` is always prioritized over cp-only ranking

## SwindleMode

- `TACTICAL`: mate / threat / only-move heavy
- `MURKY`: trap / entropy heavy
- `HYBRID`: balanced default
- `AUTO`: quick heuristic switch
  - mate/check-like signals -> `TACTICAL`
  - tight candidate spread / high entropy hint -> `MURKY`
  - otherwise -> `HYBRID`

## DryRun vs live selection

- `SwindleDryRun=true`
  - computes full Stage2 + REV
  - returns backend `bestmove`
  - logs candidate ranking and REV details
- `SwindleDryRun=false`
  - returns REV top candidate (mate-priority and fallback rules still apply)

## Reply options used in Phase2

- `SwindleReplyMultiPV`: MultiPV used during Stage2 opponent probe
- `SwindleReplyDepth`: probe depth when `SwindleReplyNodes=0`
- `SwindleReplyNodes`: probe nodes limit (if `>0`, depth is not used)
- `SwindleReplyTopK`: number of reply lines consumed for feature extraction
- `SwindleUseAdaptiveReplyBudget`: adaptive reduction of Stage2 scope under low time
- `SwindleMaxCandidates`: upper bound of Stage2 probe targets

## Mate verify / df-pn options

- `SwindlePseudoHisshiDetect`
- `SwindlePseudoHisshiWindowPly`
- `SwindleUseMateEngineVerification`
- `SwindleMateVerifyTimeMs`
- `SwindleVerifyMode` (`VERIFY_ONLY` / `TOP_CANDIDATES` / `AGGRESSIVE`)
- `SwindleVerifyMaxCandidates`
- `SwindleVerifyAggressiveExtraMs`
- `SwindleMateEnginePath`
- `SwindleMateEngineEvalDir`
- `SwindleUseDfPn`
- `SwindleDfPnPath`
- `SwindleDfPnTimeMs`
- `SwindleDfPnParserMode` (`AUTO` / `STRICT` / `LOOSE`)
- `SwindleDfPnDialect` (`AUTO` / `GENERIC_EN` / `GENERIC_JA` / `LEGACY_CLI` / `COMPACT`)
- `SwindleDfPnDialectPackPath` (external JSON path for dialect packs)
- `SwindleVerifyHybridPolicy`
- `SwindleMateEngineProfile`
- `SwindlePonderEnable`
- `SwindlePonderVerify`
- `SwindlePonderDfPn`
- `SwindlePonderMaxMs`
- `SwindlePonderReuseMinScore`
- `SwindlePonderCacheMaxAgeMs`
- `SwindlePonderRequireVerifyForMateCache`
- `SwindlePonderGateWeightsPath`
- `SwindleUsePonderGateLearnedAdjustment`
- `SwindlePonderReuseLearnedAdjustmentCapPct`
- `SwindleHybridWeightsPath`
- `SwindleUseHybridLearnedAdjustment`
- `SwindleHybridAdjustmentCapPct`
- `SwindleHybridLabelMode` (`PSEUDO` / `SUPERVISED` / `MIXED`)
- `SwindleHybridRequireFeatureVersionMatch`

## Info string output

When `SwindleVerboseInfo=true`, wrapper emits compact summaries:

- `SWINDLE ON/OFF`, mode, dryrun, emergency flag
- root eval and drop-cap
- top ranking lines (`REV`, `gap12`, mate, short breakdown)
- important events (for example `MATE DETECTED`, `VERIFY timeout`, `dfpn_timeout`, `PSEUDO_HISSHI skipped_budget`, `BACKEND restart`)

## Logs

- Default path: `./logs/taso-swindle/`
- Default format: JSONL
- One record per move with:
  - mode
  - candidates + features
  - `reply_topk`, `gap12`, `gap13`, `reply_entropy`
  - `pseudo_hisshi_score`
  - `rev_breakdown`
  - `selected_reason`
  - event-level `events` and `option_restore_failed`
  - `mate_verify_status`
  - `verify_status_summary`
  - `verify_mode_used`
  - `verify_engine_kind`
  - `mate_verify_candidates_count`
  - `dfpn_used`
  - `dfpn_status_summary`
  - `dfpn_parser_hits`
  - `dfpn_parse_unknown_count`
  - `dfpn_distance_available_count`
  - `dfpn_dialect_used`
  - `dfpn_dialect_candidates`
  - `dfpn_source_detail_normalized`
  - `verify_conflict_count`
  - `verify_unknown_count`
  - `dfpn_parser_mode`
  - `verify_hybrid_policy`
  - `hybrid_learned_adjustment_used`
  - `hybrid_adjustment_delta`
  - `hybrid_adjustment_source`
  - `pseudo_hisshi_status`
  - `ponder_status_summary`
  - `ponder_cache_used`
  - `ponder_cache_hit`
  - `ponder_used_budget_ms`
  - `ponder_fallback_reason`
  - `ponder_reuse_score`
  - `ponder_cache_age_ms`
  - `ponder_cache_gate_reason`
  - `ponder_gate_learned_adjustment_used`
  - `ponder_gate_adjustment_delta`
  - `ponder_gate_adjustment_source`
  - `reuse_then_bestmove_changed`
  - `ponder_reuse_decision_id`
  - `ponder_reuse_parent_position_key`
  - `ponder_label_source`
  - `ponder_label_confidence`
  - `actual_opponent_move`
  - `actual_move_in_reply_topk`
  - `actual_move_rank_in_reply_topk`
  - `outcome_tag`
  - `outcome_confidence`
  - `backend_restart_count`

## Local real-engine smoke (Codex App / local dev)

Run backend direct + TASO-SWINDLE wrapper smoke in one command:

```bash
python3 scripts/smoke_real_engine.py \
  --engine ./YaneuraOu \
  --eval ./eval \
  --verify-mode VERIFY_ONLY \
  --verify-hybrid-policy CONSERVATIVE \
  --mate-profile AUTO \
  --wrapper "python3 -m taso_swindle.main"
```

Notes:

- `--wrapper` is a single string and is executed via internal `shlex.split()`.
- The smoke script checks:
  - backend `usi/isready/go`
  - wrapper `usi/isready/go`
  - at least one `bestmove` response in both paths

Optional smoke variants:

```bash
python3 scripts/smoke_real_engine.py \
  --engine ./YaneuraOu \
  --eval ./eval \
  --verify-mode TOP_CANDIDATES \
  --verify-hybrid-policy BALANCED \
  --mate-engine ./YaneuraOu \
  --mate-eval ./eval \
  --wrapper "python3 -m taso_swindle.main"
```

## Hands-free self-play dataset generation

Use one command to run self-play, emit KIF, and optionally run the learning pipeline:

```bash
python3 scripts/run_selfplay_dataset.py \
  --games 200 \
  --nodes 400 \
  --max-plies 120 \
  --output-root ./artifacts/selfplay_runs
```

Generated artifacts:

- `artifacts/selfplay_runs/<run_id>/games_kif/*.kif`
- `artifacts/selfplay_runs/<run_id>/games.jsonl`
- `artifacts/selfplay_runs/<run_id>/wrapper_logs/taso-swindle-YYYYMMDD.jsonl`
- `artifacts/selfplay_runs/<run_id>/summary.json`

By default, self-play also runs `run_learning_pipeline.py` automatically.
Disable it when you only want raw game generation:

```bash
python3 scripts/run_selfplay_dataset.py \
  --games 1000 \
  --nodes 400 \
  --no-auto-pipeline
```

Recommended presets:

- Throughput-first:
  - `--nodes 200 --max-plies 120 --no-auto-pipeline`
- Quality-first (longer games / denser labels):
  - `--nodes 1000 --max-plies 160`

Useful options:

- `--swap-colors` (default on): alternates wrapper side per game.
- `--opening-file <path>`: custom opening lines (USI moves, one line per opening).
- `--disable-resign` (default on): sets `ResignValue=32767` when engine supports it.
- `--resign-fallback` (default on): if `bestmove resign`, tries latest PV-head move.
- `--train-ponder` / `--no-train-ponder`: control ponder gate weight training in auto pipeline.
- `--train-hybrid`: also run hybrid training inside auto pipeline.
- `--min-ponder-label-confidence` (default `0.0` here): keep heuristic ponder labels during bootstrap runs.

## Hybrid trainer / labels (Phase7)

Build label records from decision logs (pseudo/supervised/mixed):

```bash
python3 scripts/build_training_labels.py \
  --input ./logs/taso-swindle/taso-swindle-YYYYMMDD.jsonl \
  --output ./logs/taso-swindle/hybrid_labels.jsonl \
  --label-mode mixed \
  --ponder-label-mode runtime_first \
  --min-ponder-label-confidence 0.5
```

Train a lightweight hybrid adjustment model from JSONL logs:

```bash
python3 scripts/train_hybrid_confidence.py \
  --input ./logs/taso-swindle/taso-swindle-YYYYMMDD.jsonl \
  --label-mode mixed \
  --output ./logs/taso-swindle/hybrid_weights.json
```

Export a default seed weight file:

```bash
python3 scripts/export_hybrid_weights.py \
  --output ./logs/taso-swindle/hybrid_weights.json
```

Validate external df-pn dialect pack JSON:

```bash
python3 scripts/validate_dfpn_dialect_pack.py \
  --pack ./dfpn_dialects/default_packs.json
```

Fill `actual_opponent_move/outcome_tag` from KIF or CSA into a new labeled JSONL:

```bash
python3 scripts/fill_outcomes_from_kif.py \
  --input ./logs/taso-swindle/taso-swindle-YYYYMMDD.jsonl \
  --kif-dir ./kif_archive \
  --output ./logs/taso-swindle/taso-swindle-YYYYMMDD.labeled.jsonl
```

```bash
python3 scripts/fill_outcomes_from_csa.py \
  --input ./logs/taso-swindle/taso-swindle-YYYYMMDD.jsonl \
  --csa-dir ./csa_archive \
  --output ./logs/taso-swindle/taso-swindle-YYYYMMDD.labeled.jsonl
```

When multiple KIF/CSA files are present, matcher now scores candidates with strict priority:

1. `game_id` exact match
2. strong metadata + ply/final-move consistency
3. weak metadata fallback
4. otherwise `unmatched`

Game ID matching also uses site-aware normalization:

- URL/query extraction (`?game_id=...`, `?gid=...`, path tail fallback)
- separator absorption (`- _ / : .`)
- source detection (`wars`, `81dojo`, `lishogi`, `shogiclub24`, `shogiquest`, `kifudb`, `unknown`)
- normalized exact matching (`game_id_normalized_exact`)

Labeled outputs include:

- `outcome_match_source` (`game_id_exact` / `game_id_normalized_exact` / `meta_strong` / `meta_weak` / `unmatched`)
- `outcome_match_confidence` (`0..1`)
- `outcome_match_candidates` (number of candidate game files)
- `game_id_raw`
- `game_id_normalized`
- `game_id_source_detected`

For supervised labels, you can filter low-confidence outcome matches:

```bash
python3 scripts/build_training_labels.py \
  --input ./logs/taso-swindle/taso-swindle-YYYYMMDD.labeled.jsonl \
  --output ./logs/taso-swindle/training_labels.jsonl \
  --label-mode mixed \
  --min-outcome-match-confidence 0.6
```

Train ponder gate learned-adjustment weights:

```bash
python3 scripts/train_ponder_gate.py \
  --input ./logs/taso-swindle/taso-swindle-YYYYMMDD.labeled.jsonl \
  --label-mode mixed \
  --output ./logs/taso-swindle/ponder_gate_weights.json
```

Export default ponder gate seed weights:

```bash
python3 scripts/export_ponder_gate_weights.py \
  --output ./logs/taso-swindle/ponder_gate_weights.json
```

Collect raw df-pn samples for dialect tuning:

```bash
python3 scripts/collect_dfpn_samples.py \
  --dfpn \"./your-dfpn-binary\" \
  --position \"position startpos\" \
  --move 7g7f \
  --repeat 20 \
  --unknown-only \
  --output ./logs/taso-swindle/dfpn-samples.jsonl
```

Build proposal-only regex candidates from unknown df-pn samples:

```bash
python3 scripts/build_dfpn_pack_candidates.py \
  --input ./logs/taso-swindle/dfpn-samples.jsonl \
  --output ./dfpn_dialects/candidates_latest.json \
  --min-support 2 \
  --language mixed \
  --with-negation \
  --with-distance \
  --features-out ./dfpn_dialects/candidate_features.jsonl
```

Export candidate-token features directly (Phase13 supervised preparation):

```bash
python3 scripts/export_dfpn_candidate_features.py \
  --input ./logs/taso-swindle/dfpn-samples.jsonl \
  --output ./dfpn_dialects/candidate_features.csv \
  --format csv
```

Train supervised token classifier and apply it during proposal generation:

```bash
python3 scripts/train_dfpn_token_classifier.py \
  --input ./dfpn_dialects/candidate_features_labeled.jsonl \
  --output ./dfpn_dialects/dfpn_token_classifier.json \
  --label-field token_class_label \
  --min-samples 2
```

```bash
python3 scripts/build_dfpn_pack_candidates.py \
  --input ./logs/taso-swindle/dfpn-samples.jsonl \
  --output ./dfpn_dialects/candidates_latest.json \
  --classifier-model ./dfpn_dialects/dfpn_token_classifier.json \
  --classifier-min-confidence 0.7
```

Run data-quality report before training:

```bash
python3 scripts/report_learning_data_quality.py \
  --input ./logs/taso-swindle/taso-swindle-YYYYMMDD.labeled.jsonl \
  --output-json ./artifacts/learning_runs/latest/reports/quality_report.json \
  --output-text ./artifacts/learning_runs/latest/reports/quality_report.txt \
  --min-ponder-label-confidence 0.5 \
  --min-outcome-confidence 0.6
```

Run semi-automated learning pipeline end-to-end:

```bash
python3 scripts/run_learning_pipeline.py \
  --logs ./logs/taso-swindle/taso-swindle-20260225-a.jsonl ./logs/taso-swindle/taso-swindle-20260225-b.jsonl \
  --kif-dir ./kif \
  --csa-dir ./csa \
  --output-root ./artifacts/learning_runs \
  --resume \
  --retry 1 \
  --compare-prev-weights \
  --train-ponder \
  --train-hybrid \
  --prefer-labeled-outcome \
  --min-outcome-confidence 0.6 \
  --min-outcome-match-confidence 0.6 \
  --ponder-label-mode runtime_first \
  --min-ponder-label-confidence 0.5
```

`summary.json` now includes per-source aggregation:

- `input_sources[]` with `path`, `record_count`, `labeled_count`, `training_count`
- `failed_stage` (`fill_kif`, `fill_csa`, `quality_report`, `build_labels`, `train_ponder`, `train_hybrid`, `export_weights`)
- `partial_outputs` (`stage/path` mapping) for generated artifacts before failure
- `resume_used`, `retry_count_by_stage`, `skipped_stages`, `executed_stages`

Resume/retry execution controls:

```bash
python3 scripts/run_learning_pipeline.py \
  --logs ./logs/taso-swindle/a.jsonl ./logs/taso-swindle/b.jsonl \
  --output-root ./artifacts/learning_runs \
  --resume \
  --retry 1 \
  --force-stage build_labels \
  --train-ponder \
  --no-train-hybrid
```

Notes:

- `--resume` uses `stage_manifest.json` content hashes; stage skip requires both artifact validity and hash match.
- changed inputs are surfaced in `stage_hash_mismatch_stages`.
- `--force-stage` bypasses hash skip only for specified stages.

`quality_report.json` now includes source breakdown:

- `by_source` section with per-log coverage/eligibility/drop reasons

Install latest learned weights into runtime paths:

```bash
python3 scripts/install_latest_weights.py \
  --artifacts-root ./artifacts/learning_runs \
  --ponder-dst ./models/ponder_gate_weights.json \
  --hybrid-dst ./models/hybrid_weights.json \
  --dry-run
```

Compare old/new weights (A/B) without applying them:

```bash
python3 scripts/report_weights_ab.py \
  --a ./artifacts/learning_runs/<old>/weights/ponder_gate_weights.json \
  --b ./artifacts/learning_runs/<new>/weights/ponder_gate_weights.json \
  --type ponder \
  --out ./artifacts/learning_runs/<new>/reports/weights_ab_ponder.json \
  --md-out ./artifacts/learning_runs/<new>/reports/weights_ab_ponder.md \
  --eval-log-a ./artifacts/learning_runs/<old>/training_labels/training_labels.jsonl \
  --eval-log-b ./artifacts/learning_runs/<new>/training_labels/training_labels.jsonl
```

Daily operation (ShogiGUI/ShogiHome-style):

1. Play games with normal wrapper settings and logging enabled.
2. Periodically run `run_learning_pipeline.py` on collected JSONL.
3. Check `reports/quality_report.json` and `summary.json`.
4. Apply weights via `install_latest_weights.py`.
5. Remove `--dry-run` after confirming generated paths.

Troubleshooting:

- No KIF/CSA available: use `--skip-fill-outcomes`; pipeline still works with runtime/heuristic labels.
- Runtime label ratio too low: confirm ponder cache reuse is enabled and inspect `ponder_label_source`.
- Too few training samples: lower confidence thresholds or merge more logs.
- Low-quality KIF/CSA matches: increase `--min-outcome-match-confidence` (recommended `0.55~0.75`).
- Version mismatch/no-op: verify `features_version` inside generated weight JSON.

df-pn candidate proposal workflow (proposal only):

1. collect unknown samples (`collect_dfpn_samples.py`)
2. build candidates (`build_dfpn_pack_candidates.py`)
3. validate dialect pack (`validate_dfpn_dialect_pack.py --json`)
4. human review, then manual update of pack JSON

Example:

```bash
python3 scripts/build_dfpn_pack_candidates.py \
  --input ./logs/taso-swindle/dfpn-samples.jsonl \
  --output ./dfpn_dialects/candidates_latest.json \
  --min-support 2 \
  --language mixed \
  --with-negation \
  --with-distance
```

```bash
python3 scripts/smoke_real_engine.py \
  --engine ./YaneuraOu \
  --eval ./eval \
  --verify-mode AGGRESSIVE \
  --mate-profile FAST_VERIFY \
  --ponder \
  --dfpn "/bin/echo" \
  --wrapper "python3 -m taso_swindle.main"
```

## Direct test run (no pytest required)

```bash
python3 tests/run_phase2_checks.py
python3 tests/run_phase3_checks.py
python3 tests/run_phase4_checks.py
python3 tests/run_phase5_checks.py
python3 tests/run_phase6_checks.py
python3 tests/run_phase7_checks.py
python3 tests/run_phase8_checks.py
python3 tests/run_phase9_checks.py
python3 tests/run_phase10_checks.py
python3 tests/run_phase11_checks.py
python3 tests/run_phase12_checks.py
python3 tests/run_phase13_checks.py
```

or run each file directly under `tests/`.

## Current limitations

- df-pn parser now supports dialect packs, but per-binary tuning still needs more real samples.
- Supervised/mixed training is available, but quality depends on `actual_opponent_move/outcome_tag` coverage.
- Ponder reuse now has quality gating; aggressive reuse is intentionally blocked by default.
- Pseudo hisshi is still lightweight and budget-limited by design.
- Weight tuning is connected as no-op (extension point only).

## Discord release bundle (hybrid included)

Build a Discord-friendly package with split parts and strict privacy scan:

```bash
python3 scripts/build_discord_release.py \
  --profile flavor_with_hybrid \
  --output-root ./artifacts_local/discord_release \
  --part-size-mb 29 \
  --strict-scan
```

Artifacts:

- `bundle/` distributable folder
- `zip/` full archive
- `parts/` split files for Discord upload
- `manifest.json`
- `privacy_audit.json`
- `SUMMARY.md`

Restore from parts:

```bash
python3 scripts/restore_discord_parts.py \
  --parts-dir ./artifacts_local/discord_release/<run_id>/parts \
  --manifest ./artifacts_local/discord_release/<run_id>/manifest.json
```

Run standalone strict scan if needed:

```bash
python3 scripts/scan_release_privacy.py \
  --target-dir ./artifacts_local/discord_release/<run_id>/bundle \
  --strict
```
