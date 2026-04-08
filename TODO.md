# TASO-SWINDLE TODO (after Phase13 increment)

## Compliance update

- External kifu acquisition from third-party services is disabled by project policy.
- Learning sample outputs must anonymize personal information fields by default.
- One-time remediation:
  - Delete previously fetched external kifu originals.
  - Re-anonymize external-derived JSONL datasets with `scripts/redact_jsonl_pii.py`.

## Phase11 completed

- Added strict multi-file KIF/CSA matching with confidence metadata:
  - `outcome_match_source`
  - `outcome_match_confidence`
  - `outcome_match_candidates`
- Added multi-log source breakdown in learning pipeline summary:
  - `input_sources[]`
  - `failed_stage`
  - `partial_outputs`
- Added `--min-outcome-match-confidence` filtering in label builder.
- Improved df-pn candidate proposal quality:
  - language-aware token extraction
  - negation/distance candidate classes
  - evidence fields (`token_class`, `sample_count`, `examples`, `confidence_hint`)

## Phase12 completed

- Added site-aware `game_id` normalization and source detection:
  - new `scripts/game_id_normalizer.py`
  - `game_id_normalized_exact` matching path
  - labeled outputs now include `game_id_raw`, `game_id_normalized`, `game_id_source_detected`
- Added pipeline resume/retry controls:
  - `--resume`
  - `--retry`
  - `--force-stage`
  - summary fields: `resume_used`, `retry_count_by_stage`, `skipped_stages`, `executed_stages`
- Added weights A/B comparison report:
  - new `scripts/report_weights_ab.py`
  - JSON/Markdown diff with safety notes
  - optional pipeline integration via `--compare-prev-weights`
- Added df-pn candidate feature export foundation:
  - new `scripts/export_dfpn_candidate_features.py`
  - `build_dfpn_pack_candidates.py --features-out`

## Phase13 completed

- Expanded site-aware `game_id` normalization dictionary and rules:
  - wars / 81dojo / lishogi / shogiclub24 / shogiquest / kifudb
  - URL/query extraction + separator/noise cleanup + source-aware prefix strip
- Added content-hash strict resume:
  - `stage_manifest.json`
  - skip requires artifact validity + hash match
  - `stage_hash_mismatch_stages` reporting
- Added df-pn token meaning supervised path:
  - new `scripts/dfpn_token_classifier.py`
  - new `scripts/train_dfpn_token_classifier.py`
  - `build_dfpn_pack_candidates.py --classifier-model`
- Extended weights A/B report with actual-game eval diff:
  - `report_weights_ab.py --eval-log-a --eval-log-b`
  - pipeline compare path now passes eval logs when available

## Mate / df-pn

- Expand external df-pn dialect packs with real binary sample corpora.
- Expand game_id normalization dictionaries for additional site-specific KIF/CSA variants.
- Improve df-pn supervised classifier with reviewed labels and threshold calibration.
- Add TOML loader and optional hot-reload for dialect pack updates.
- Automate df-pn candidate proposal review workflow (collect -> candidates -> validate -> human review).
- Add dedicated mate-engine option presets beyond generic `Threads/Hash`.
- Add dedicated mate engine args support (path + args split) when needed.

## Hybrid confidence

- Improve KIF/CSA outcome fill robustness for ambiguous move notation and transpositions.
- Monitor runtime vs heuristic ponder-label ratio continuously in production logs.
- Add per-policy telemetry and auto-policy recommendation from logs.
- Add conflict replay tooling for `verify_conflict_count` hotspots.
- Add model selection/rollback by `features_version`.
- Add A/B comparison script for newly trained weights vs current production weights.

## Ponder

- Improve ponder learned-gate trainer labels beyond heuristic (`reuse_good/reuse_bad` ground truth).
- Add periodic quality report for `reuse_then_bestmove_changed` and label confidence drift.
- Improve `ponderhit` transition quality under short byoyomi with lower latency.
- Expand ponder E2E to cover repeated `ponderhit`/`stop` races.
- Add scheduled learning pipeline execution (cron/launchd) with retention policy.

## Learning / Weight tuning

- Add automatic periodic training pipeline and model version rollover.
- Add content-hash manifest pruning/GC and stage-level invalidation tooling.
- Add offline feature ablation report generation and drift checks.
- Add weights A/B report extension with real-match-log outcome deltas.

## Testing / operations

- Expand mock E2E restart-chain coverage with verifier + df-pn enabled.
- Add optional pytest workflow while keeping direct-run compatibility.
- Expand real-engine smoke matrix:
  - verify mode sweep
  - dfpn dialect sweep
  - hybrid policy sweep
  - mate profile sweep
  - optional external df-pn binary checks

## Discord配布運用（追加）

- `scripts/build_discord_release.py` を標準入口として利用し、`flavor_with_hybrid` を既定運用にする。
- 配布前に `scripts/scan_release_privacy.py --strict` を必須化する。
- 受信側復元は `scripts/restore_discord_parts.py` を案内する。
- 将来拡張:
  - `anonymous_min` プロファイル追加（重み非同梱）
  - zip暗号化オプション（配布チャネル要件に応じて）
