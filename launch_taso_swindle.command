#!/bin/zsh
set -euo pipefail
cd /Users/taso/開発/逆転のメカニズム

# Balanced high-performance preset (Apple Silicon 32GB class).
export TASO_SWINDLE_BACKEND_ENGINE="/Users/taso/開発/逆転のメカニズム/YaneuraOu"
export TASO_SWINDLE_BACKEND_ARGS=""
export TASO_SWINDLE_BACKEND_OPTION_PASSTHROUGH="Threads=8;Hash=8192;EvalDir=/Users/taso/開発/逆転のメカニズム/eval;BookFile=no_book"

exec python3 -m taso_swindle.main "$@"
