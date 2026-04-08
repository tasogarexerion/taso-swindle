#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TASO NNUE USI Proxy (YaneuraOu系想定)

設計原則:
- bestmove は原則そのまま（強さを歪めない）。安全フィルタのみ。
- 表示はシンプルに:
    - 現在の局面の段階（game phase）
    - 現在の局面の形勢（stance）
    - intent（内部: MAIN/ATK/DEF / 表示: 本筋・BULL・HEDGE）を“線”として維持
    - 候補手: 本筋 / BULL / HEDGE を軸に最大N手（WATCH）
- 内部では多数の指標を使って選ぶが、ユーザーに出す言葉は少なくする。

重点:
- (1) 候補ごとに PVContactSoon を計算して BULL（=ATK）を“気持ちよく”
- (2) HEDGE（=DEF）トラック（前回HEDGE候補のPV）を保持し、慣性(inertia)で“腰を重く”
- (3) CRISIS では HEDGE（=DEF）を厳格に（低リスク/低分岐を強優先）
- intentsane: intent は「候補タグの“存在”」に従属（存在しないタグへ遷移しない）

地雷潰し / 賢さ復元パッチ:
- [FIX] is_usi_move_token(): special bestmove(resign/win/0000/(none))はUSI指し手トークンとして扱わない
- [FIX] ANALYZE時のinfo multipv垂れ流しを短時間バッファし、1..N順で出力（機能は維持）
- [FIX] Winability: opp_diff(good数由来) と initiative の二重カウントを解消。
        initiative は「bestへの近さ(closeness)」「分岐(divergence)」「詰みの強制力」で定義。
"""

import os
import sys
import subprocess
import threading
import queue
import time
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set
from collections import deque

# =========================
# PATH / CWD 固定
# =========================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def _abspath_under_script(p: str) -> str:
    if not p:
        return p
    if os.path.isabs(p):
        return p
    return os.path.join(SCRIPT_DIR, p)

# =========================
# CONFIG (環境変数で上書き可)
# =========================
ENGINE_PATH = os.environ.get("TASO_ENGINE", "./YaneuraOu")
NNUE_PATH   = os.environ.get("TASO_NNUE", "eval")  # evalフォルダ想定が安全

ENGINE_PATH_ABS = _abspath_under_script(ENGINE_PATH)
NNUE_PATH_ABS   = _abspath_under_script(NNUE_PATH)

ENGINE_CMD = [ENGINE_PATH_ABS, "-eval", NNUE_PATH_ABS]

THREADS = int(os.environ.get("TASO_THREADS", "6"))
HASH_MB = int(os.environ.get("TASO_HASH_MB", "4096"))

DEFAULT_MULTIPV = int(os.environ.get("TASO_MULTIPV", "3"))
PREFIX_K = int(os.environ.get("TASO_PREFIX_K", "6"))

# stability / smoothing
STAB_TAU = float(os.environ.get("TASO_STAB_TAU", "2.0"))
GOOD_CP_MARGIN = int(os.environ.get("TASO_GOOD_CP_MARGIN", "150"))
DROP_SOFT_LIMIT = int(os.environ.get("TASO_DROP_SOFT_LIMIT", "260"))
DROP_DISTURB_LIMIT = int(os.environ.get("TASO_DROP_DISTURB_LIMIT", "520"))
GOOD_COUNT_CAP = int(os.environ.get("TASO_GOOD_COUNT_CAP", "5"))
GOOD_COUNT_CAP = max(2, min(10, GOOD_COUNT_CAP))
GOOD_TAU = float(os.environ.get("TASO_GOOD_TAU", "3.0"))
WINABILITY_SPREAD = float(os.environ.get("TASO_WIN_SPREAD", "0.85"))
DISP_WIN_CONTRAST = float(os.environ.get("TASO_DISP_WIN_CONTRAST", "0.85"))

CP_ALPHA_MIN = float(os.environ.get("TASO_CP_ALPHA_MIN", "0.26"))
CP_ALPHA_MAX = float(os.environ.get("TASO_CP_ALPHA_MAX", "0.96"))
CP_SPREAD_REF = float(os.environ.get("TASO_CP_SPREAD_REF", "260"))

MATE_SHORT_MAX = int(os.environ.get("TASO_SHORT_MATE_MAX", "7"))
MATE_RISK_FLOOR = float(os.environ.get("TASO_MATE_RISK_FLOOR", "0.75"))
MATE_RISK_TAU   = float(os.environ.get("TASO_MATE_RISK_TAU", "10.0"))
MATE_INIT_FLOOR = float(os.environ.get("TASO_MATE_INIT_FLOOR", "0.60"))
MATE_INIT_TAU   = float(os.environ.get("TASO_MATE_INIT_TAU", "8.0"))

SAFETY_MS = int(os.environ.get("TASO_SAFETY_MS", "40"))

SHOW = os.environ.get("TASO_SHOW", "1") == "1"
DEBUG = os.environ.get("TASO_DEBUG", "0") == "1"

READ_TIMEOUT = float(os.environ.get("TASO_READ_TIMEOUT", "0.1"))
LINE_LIMIT = int(os.environ.get("TASO_LINE_LIMIT", "200000"))

GO_HARD_SEC = float(os.environ.get("TASO_GO_HARD_SEC", "60.0"))
GO_STOP_GRACE_SEC = float(os.environ.get("TASO_GO_STOP_GRACE_SEC", "3.0"))
# go infinite / ponder 用。0以下ならハードタイムアウト無効
GO_HARD_SEC_INFINITE = float(os.environ.get("TASO_GO_HARD_SEC_INFINITE", "0.0"))

# safety_check deadline（比例型）
SAFETY_DEADLINE_MIN_SEC = float(os.environ.get("TASO_SAFETY_DEADLINE_MIN_SEC", "0.20"))
SAFETY_DEADLINE_FACTOR  = float(os.environ.get("TASO_SAFETY_DEADLINE_FACTOR", "3.0"))
SAFETY_DEADLINE_PAD_SEC = float(os.environ.get("TASO_SAFETY_DEADLINE_PAD_SEC", "0.05"))
SAFETY_STOP_GRACE_SEC   = float(os.environ.get("TASO_SAFETY_STOP_GRACE_SEC", "0.25"))

# safety_check中だけ短いtimeout
SAFETY_READ_TIMEOUT = float(os.environ.get("TASO_SAFETY_READ_TIMEOUT", "0.02"))
SAFETY_MATE_MAX = int(os.environ.get("TASO_SAFETY_MATE_MAX", str(MATE_SHORT_MAX)))
SAFETY_MATE_REPEAT = int(os.environ.get("TASO_SAFETY_MATE_REPEAT", "2"))

# book が無いなら自動でOFF（ノイズ削減）
AUTO_DISABLE_BOOK = os.environ.get("TASO_AUTO_DISABLE_BOOK", "1") == "1"
BOOK_FILE = os.path.join(SCRIPT_DIR, "book", "standard_book.db")

# 表示: 将棋表記に変換するか
SHOW_KIF = os.environ.get("TASO_SHOW_KIF", "1") == "1"

# engineの info をそのまま転送するか（デフォルト: ANALYZEのみ転送）
FORWARD_ENGINE_INFO_ENV = os.environ.get("TASO_FORWARD_ENGINE_INFO", "").strip()
# "", "auto", "0", "1"
# auto: ANALYZEのみ転送
FORWARD_ENGINE_INFO_MODE = (FORWARD_ENGINE_INFO_ENV.lower() if FORWARD_ENGINE_INFO_ENV else "auto")

# ANALYZE時の multipv info ならべ替え用（地雷潰し）
INFO_MPV_REORDER = os.environ.get("TASO_INFO_MPV_REORDER", "1") == "1"
INFO_MPV_BUF_MS = int(os.environ.get("TASO_INFO_MPV_BUF_MS", "20"))  # 既定 20ms

# --- 勝ち筋ストーリー（Winability側）のヒステリシス（ブレ防止） ---
PH_STABLE_ENTER = float(os.environ.get("TASO_PH_STABLE_ENTER", "0.78"))
PH_STABLE_EXIT  = float(os.environ.get("TASO_PH_STABLE_EXIT",  "0.72"))
PH_BAL_ENTER    = float(os.environ.get("TASO_PH_BAL_ENTER",    "0.58"))
PH_BAL_EXIT     = float(os.environ.get("TASO_PH_BAL_EXIT",     "0.52"))
PH_DIST_ENTER   = float(os.environ.get("TASO_PH_DIST_ENTER",   "0.38"))
PH_DIST_EXIT    = float(os.environ.get("TASO_PH_DIST_EXIT",    "0.32"))

# --- ゲームフェイズ（手数非依存） ---
GP_EWMA_ALPHA = float(os.environ.get("TASO_GP_EWMA_ALPHA", "0.42"))
GP_HYS_GAP    = float(os.environ.get("TASO_GP_HYS_GAP", "0.06"))

GP_PROBE_ENTER  = float(os.environ.get("TASO_GP_PROBE_ENTER",  "0.26"))
GP_TENS_ENTER   = float(os.environ.get("TASO_GP_TENSION_ENTER","0.50"))
GP_CLASH_ENTER  = float(os.environ.get("TASO_GP_CLASH_ENTER",  "0.72"))

GP_CONVERT_CP_ENTER = int(os.environ.get("TASO_GP_CONVERT_CP_ENTER", "900"))
GP_CONVERT_CP_EXIT  = int(os.environ.get("TASO_GP_CONVERT_CP_EXIT",  "700"))
GP_FINISH_CP_ENTER  = int(os.environ.get("TASO_GP_FINISH_CP_ENTER",  "2500"))
GP_FINISH_CP_EXIT   = int(os.environ.get("TASO_GP_FINISH_CP_EXIT",   "1800"))

GP_SHOCK_PVCS  = float(os.environ.get("TASO_GP_SHOCK_PVCS", "0.82"))
GP_SHOCK_CSC   = int(os.environ.get("TASO_GP_SHOCK_CSC", "5"))

GP_PV_PLY = int(os.environ.get("TASO_GP_PV_PLY", "6"))

# --- 形勢（stance） ---
STANCE_EWMA_ALPHA = float(os.environ.get("TASO_STANCE_EWMA_ALPHA", "0.30"))
STANCE_TREND_ALPHA = float(os.environ.get("TASO_STANCE_TREND_ALPHA", "0.30"))

TREND_DEADZONE = int(os.environ.get("TASO_TREND_DEADZONE", "25"))
STANCE_ADV_ENTER = int(os.environ.get("TASO_STANCE_ADV_ENTER", "600"))
STANCE_ADV_EXIT  = int(os.environ.get("TASO_STANCE_ADV_EXIT",  "450"))
STANCE_DEF_ENTER = int(os.environ.get("TASO_STANCE_DEF_ENTER", "-600"))
STANCE_DEF_EXIT  = int(os.environ.get("TASO_STANCE_DEF_EXIT",  "-450"))
STANCE_CRI_ENTER = int(os.environ.get("TASO_STANCE_CRI_ENTER", "-1300"))
STANCE_CRI_EXIT  = int(os.environ.get("TASO_STANCE_CRI_EXIT",  "-1100"))

STANCE_SHOCK_CP = 500

MODE_PLAY = "PLAY"
MODE_WATCH = "WATCH"
MODE_ANALYZE = "ANALYZE"
DEFAULT_MODE = os.environ.get("TASO_MODE", MODE_WATCH).upper()
if DEFAULT_MODE not in (MODE_PLAY, MODE_WATCH, MODE_ANALYZE):
    DEFAULT_MODE = MODE_WATCH

# WATCHの表示候補数
WATCH_SHOW_N = int(os.environ.get("TASO_WATCH_N", "5"))
WATCH_SHOW_N = max(3, min(8, WATCH_SHOW_N))
# WATCH中の途中PV表示（探索の進捗確認用）
WATCH_PROGRESS_ENABLE = os.environ.get("TASO_WATCH_PROGRESS", "1") == "1"
WATCH_PROGRESS_INTERVAL_MS = int(os.environ.get("TASO_WATCH_PROGRESS_INTERVAL_MS", "220"))
WATCH_PROGRESS_MIN_DEPTH = int(os.environ.get("TASO_WATCH_PROGRESS_MIN_DEPTH", "1"))
WATCH_PROGRESS_LINES = int(os.environ.get("TASO_WATCH_PROGRESS_LINES", str(WATCH_SHOW_N)))
WATCH_PROGRESS_LINES = max(1, min(10, WATCH_PROGRESS_LINES))
WATCH_PROGRESS_PV_MOVES = int(os.environ.get("TASO_WATCH_PROGRESS_PV_MOVES", "12"))
WATCH_PROGRESS_PV_MOVES = max(1, min(40, WATCH_PROGRESS_PV_MOVES))

# intentsane: 継続/転換のしきい値
INTENT_KEEP_MARGIN = float(os.environ.get("TASO_INTENT_KEEP_MARGIN", "0.06"))
INTENT_SWITCH_SHOCK = float(os.environ.get("TASO_INTENT_SWITCH_SHOCK", "0.12"))

# =========================
# IO
# =========================
def out(s: str) -> None:
    try:
        sys.stdout.write(s + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        raise SystemExit(0)

def info(s: str) -> None:
    if SHOW:
        out("info string " + s)

def dbg(s: str) -> None:
    if DEBUG:
        try:
            sys.stderr.write("[TASO] " + s + "\n")
            sys.stderr.flush()
        except Exception:
            pass

# =========================
# Stdin Reader (go中 stop/quit 即応)
# =========================
class StdinReader:
    def __init__(self) -> None:
        self.q: queue.Queue[str] = queue.Queue()
        self.alive = True
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self) -> None:
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if not line:
                    continue
                self.q.put(line)
            self.alive = False
        except Exception:
            self.alive = False

    def get(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_nowait(self) -> Optional[str]:
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

# =========================
# Engine Wrapper
# =========================
class Engine:
    def __init__(self):
        if not os.path.isfile(ENGINE_PATH_ABS):
            raise FileNotFoundError(f"Engine not found: {ENGINE_PATH_ABS}")

        self.p = subprocess.Popen(
            ENGINE_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=SCRIPT_DIR,
        )
        self.q: queue.Queue[str] = queue.Queue()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        assert self.p.stdout is not None
        for line in self.p.stdout:
            self.q.put(line.rstrip("\n"))
        self.alive = False

    def send(self, s: str) -> None:
        if self.p.stdin is None:
            return
        try:
            self.p.stdin.write(s + "\n")
            self.p.stdin.flush()
        except Exception:
            self.alive = False

    def recv(self, timeout: float = READ_TIMEOUT) -> Optional[str]:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self, limit: int = 200000) -> int:
        n = 0
        while n < limit:
            try:
                _ = self.q.get_nowait()
                n += 1
            except queue.Empty:
                break
        return n

    def close(self) -> None:
        try:
            self.send("quit")
        except Exception:
            pass
        try:
            self.p.terminate()
        except Exception:
            pass

# =========================
# Parsing helpers
# =========================
def _try_int(x: str) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None

def parse_mate_token(tok: Optional[str]) -> Optional[int]:
    if tok is None:
        return None
    if tok.startswith("+"):
        tok = tok[1:]
    return _try_int(tok)

def is_special_bestmove(tok: str) -> bool:
    t = tok.strip().lower()
    return t in ("resign", "win", "0000", "(none)")

def is_usi_move_token(tok: str) -> bool:
    """
    USI move token:
      - normal: [1-9][a-i][1-9][a-i][+]?  (e.g. 7g7f, 2b3c+)
      - drop:   [PLNSGBRK]\*[1-9][a-i]    (e.g. P*7f)

    FIX: special bestmove(resign/win/0000/(none)) は USI 指し手トークンではない。
         （ここを True にすると将来の分岐で地雷になる）
    """
    s = tok.strip()
    if not s:
        return False
    if is_special_bestmove(s):
        return False

    if "*" in s:
        # drop
        if len(s) != 4:
            return False
        pc, star, dst = s[0], s[1], s[2:]
        if star != "*":
            return False
        if pc.upper() not in ("P", "L", "N", "S", "G", "B", "R", "K"):
            return False
        if len(dst) != 2:
            return False
        f = _try_int(dst[0])
        r = ord(dst[1]) - ord("a") + 1
        return (f is not None) and (1 <= f <= 9) and (1 <= r <= 9)

    prom = s.endswith("+")
    core = s[:-1] if prom else s
    if len(core) != 4:
        return False
    s1, s2 = core[:2], core[2:]
    f1 = _try_int(s1[0]); r1 = ord(s1[1]) - ord("a") + 1
    f2 = _try_int(s2[0]); r2 = ord(s2[1]) - ord("a") + 1
    if f1 is None or f2 is None:
        return False
    return (1 <= f1 <= 9 and 1 <= r1 <= 9 and 1 <= f2 <= 9 and 1 <= r2 <= 9)

def prefix_len(a: str, b: str, k: int = PREFIX_K) -> int:
    aa = a.split()
    bb = b.split()
    n = 0
    for i in range(min(k, len(aa), len(bb))):
        if aa[i] != bb[i]:
            break
        n += 1
    return n

def stability_sim(a: str, b: str, k: int = PREFIX_K) -> float:
    """Weighted PV similarity in [0,1]. Less jumpy than prefix_len/k."""
    aa = a.split()
    bb = b.split()
    if k <= 0:
        return 0.0
    num = 0.0
    den = 0.0
    for i in range(k):
        w = math.exp(-float(i) / max(0.1, STAB_TAU))
        den += w
        eq = 1.0 if (i < len(aa) and i < len(bb) and aa[i] == bb[i]) else 0.0
        num += w * eq
    v = (num / den) if den > 0 else 0.0
    return max(0.0, min(1.0, v))

@dataclass
class Cand:
    mpv: int
    move: str
    pv: str
    cp: int
    mate: Optional[int]
    depth: int = 0

@dataclass
class Features:
    stability: float
    opp_diff: float
    self_risk: float
    initiative: float
    winability: float
    divergence: float

def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

def disp_win(w: float) -> float:
    c = max(0.30, min(1.00, float(DISP_WIN_CONTRAST)))
    return clamp(0.5 + (w - 0.5) * c)

# =========================
# Winability
# =========================
def candidate_dispersion_norm(cands: List[Cand], k: int = 5) -> float:
    xs: List[int] = []
    for c in cands:
        if abs(c.cp) >= 20000:
            continue
        xs.append(c.cp)
    if not xs:
        return 0.0
    xs.sort(reverse=True)
    kk = max(1, min(int(k), len(xs)))
    xs = xs[:kk]
    if len(xs) <= 1:
        return 0.0
    rng = float(xs[0] - xs[-1])
    return clamp(rng / 900.0)

def plan_signal_from_state(cp_ew: float, dcp_ew: float, ew_eng: float, disp_n: float, stance_hint: str, has_mate: bool, has_self_mate: bool) -> float:
    w = 0.5 + 0.33 * math.tanh(cp_ew / 1100.0)
    w += 0.08 * math.tanh(dcp_ew / 260.0)

    if cp_ew >= 0.0:
        w += 0.10 * (disp_n - 0.25)
    else:
        w -= 0.12 * (disp_n - 0.25)
        w -= 0.10 * clamp(ew_eng)
        if disp_n < 0.30:
            w -= 0.10 * (0.30 - disp_n) / 0.30

    if has_mate:
        w = max(w, 0.82)

    if has_self_mate or stance_hint == "CRISIS":
        w = min(w, 0.22)

    return clamp(w)

def mate_risk(d: int) -> float:
    return clamp(MATE_RISK_FLOOR + (1.0 - MATE_RISK_FLOOR) * math.exp(-float(d) / MATE_RISK_TAU))

def mate_initiative(d: int) -> float:
    return clamp(MATE_INIT_FLOOR + (1.0 - MATE_INIT_FLOOR) * math.exp(-float(d) / MATE_INIT_TAU))

def _safe_best_cp(best_cp: int) -> int:
    return 0 if abs(best_cp) >= 20000 else best_cp

def compute_uncertainty_for_display(cands: List[Cand], best: Cand) -> float:
    if not cands:
        return 0.0

    def cp_used(c: Cand) -> int:
        return 0 if abs(c.cp) >= 20000 else c.cp

    top = sorted(cands, key=cp_used, reverse=True)
    M = min(5, len(top))
    top = top[:M]
    if M <= 1:
        return 0.0

    best_cp = _safe_best_cp(best.cp)

    good = 0
    for c in top:
        if cp_used(c) >= best_cp - GOOD_CP_MARGIN:
            good += 1
    good = max(1, good)
    good_unc = clamp((float(good - 1) / float(M - 1)) if M > 1 else 0.0)

    second_cp = cp_used(top[1]) if M >= 2 else best_cp
    spread = max(0.0, float(best_cp - second_cp))
    spread_ref = max(1.0, float(CP_SPREAD_REF))
    spread_unc = 1.0 - clamp(spread / spread_ref)

    pv_div_sum = 0.0
    pv_div_n = 0
    for c in top:
        if c.move == best.move:
            continue
        pl = prefix_len(best.pv, c.pv, PREFIX_K)
        pv_div_sum += clamp((float(PREFIX_K - pl) / float(max(1, PREFIX_K))))
        pv_div_n += 1
    pv_div = (pv_div_sum / float(pv_div_n)) if pv_div_n > 0 else 0.0
    pv_unc = clamp(pv_div)

    return clamp(0.45 * good_unc + 0.35 * spread_unc + 0.20 * pv_unc)

def human_cp_for_display(cp_base: float, uncertainty: float) -> int:
    a0 = float(CP_ALPHA_MIN)
    a1 = float(CP_ALPHA_MAX)
    if a0 > a1:
        a0, a1 = a1, a0
    confidence = 1.0 - clamp(float(uncertainty))
    alpha = a0 + (a1 - a0) * confidence
    return int(round(alpha * float(cp_base)))

def compute_features(cand: Cand, best: Cand, context_cands: List[Cand], mode_hint: str) -> Features:
    best_cp_used = _safe_best_cp(best.cp)
    cand_cp_used = 0 if abs(cand.cp) >= 20000 else cand.cp
    delta = best_cp_used - cand_cp_used

    pv_pref = prefix_len(best.pv, cand.pv, PREFIX_K)
    stability = stability_sim(best.pv, cand.pv, PREFIX_K)
    divergence = clamp((PREFIX_K - pv_pref) / float(max(1, PREFIX_K)))

    # good-count based opp_diff（相手の最善が絞られている＝受けづらい/強制力が高い）
    top_ctx = sorted(
        context_cands,
        key=lambda c: (0 if abs(c.cp) >= 20000 else c.cp),
        reverse=True,
    )
    top_ctx = top_ctx[: max(1, min(GOOD_COUNT_CAP, len(top_ctx)))]

    good = 0
    for c in top_ctx:
        ccp = 0 if abs(c.cp) >= 20000 else c.cp
        if ccp >= best_cp_used - GOOD_CP_MARGIN:
            good += 1
    good = max(1, good)

    tau = max(0.5, float(GOOD_TAU))
    opp_diff = clamp(1.0 / (1.0 + (good - 1.0) / tau))

    # self risk（頓死/大きな損）
    risk = 0.2
    if cand.mate is not None and cand.mate < 0:
        risk = 1.0
    else:
        if delta >= 300:
            risk = max(risk, 0.7)
        elif delta >= 180:
            risk = max(risk, 0.5)

    mate_neg_best = None
    for c in context_cands:
        if c.mate is not None and c.mate < 0:
            d = abs(c.mate)
            if mate_neg_best is None or d < mate_neg_best:
                mate_neg_best = d
    if mate_neg_best is not None:
        risk = max(risk, mate_risk(mate_neg_best))

    self_risk = clamp(risk)

    # ---------------------------------------------------------
    # FIX: initiative を good数由来から外す（opp_diffと二重に食わない）
    #
    # initiative = 「主導権を握れる手か？」
    #  - 詰みが見えているなら強い
    #  - bestに近い（closeness）＋分岐（divergence）がある＝主導権を作りやすい
    #  - 不利側(DISTURB/DESPERATE)では divergence を少し増幅
    # ---------------------------------------------------------
    # bestとの差が小さいほど 1.0 に近づく
    # 600cpでほぼ 0 になる程度のスケール（雑に扱いやすい）
    closeness = clamp(1.0 - max(0.0, float(delta)) / 600.0)

    init = 0.0
    if cand.mate is not None and cand.mate > 0:
        init = max(init, mate_initiative(abs(cand.mate)))

    # 「近いけど線が違う」＝主導権を作りやすい（ただし極端な変化だけを持ち上げすぎない）
    div_eff = clamp((divergence - 0.10) / 0.90)
    init = max(init, clamp(0.65 * closeness + 0.35 * div_eff))

    if mode_hint in ("DISTURB", "DESPERATE"):
        init = clamp(init + 0.18 * div_eff)

    initiative = clamp(init)

    win = (
        0.30 * stability +
        0.25 * opp_diff +
        0.20 * (1.0 - self_risk) +
        0.25 * initiative
    )
    winability_raw = clamp(win)
    spread = clamp(WINABILITY_SPREAD, 0.5, 1.0)
    winability = clamp(0.5 + (winability_raw - 0.5) * spread)

    return Features(stability, opp_diff, self_risk, initiative, winability, divergence)

# =========================
# 勝ち筋ストーリー（Winability側）: ヒステリシス + 文言
# =========================
class WinPhaseHysteresis:
    def __init__(self) -> None:
        self.phase: str = "BALANCE"

    def update(self, w: float) -> str:
        p = self.phase

        if p == "STABLE":
            if w < PH_STABLE_EXIT:
                self.phase = "BALANCE"
        elif p == "BALANCE":
            if w >= PH_STABLE_ENTER:
                self.phase = "STABLE"
            elif w < PH_BAL_EXIT:
                self.phase = "DISTURB"
        elif p == "DISTURB":
            if w >= PH_BAL_ENTER:
                self.phase = "BALANCE"
            elif w < PH_DIST_EXIT:
                self.phase = "DESPERATE"
        else:  # DESPERATE
            if w >= PH_DIST_ENTER:
                self.phase = "DISTURB"

        return self.phase

def win_plan_text(phase: str) -> str:
    if phase == "STABLE":
        return "plan: STABLE"
    if phase == "BALANCE":
        return "plan: BALANCE"
    if phase == "DISTURB":
        return "plan: DISTURB"
    return "plan: DESPERATE"

# =========================
# --- Shogi board: USI move -> 簡易KIF / 指標用 ---
# =========================
FILES_FW = "０１２３４５６７８９"  # index 1..9
RANK_KANJI = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]

PIECE_JP = {
    "P": "歩", "L": "香", "N": "桂", "S": "銀", "G": "金", "B": "角", "R": "飛", "K": "玉",
    "+P": "と", "+L": "成香", "+N": "成桂", "+S": "成銀", "+B": "馬", "+R": "龍",
}

def _sq_to_xy(sq: str) -> Optional[Tuple[int, int]]:
    if len(sq) != 2:
        return None
    f = _try_int(sq[0])
    r = ord(sq[1]) - ord("a") + 1
    if f is None or not (1 <= f <= 9) or not (1 <= r <= 9):
        return None
    return (f, r)

def _xy_to_kif(f: int, r: int) -> str:
    return f"{FILES_FW[f]}{RANK_KANJI[r]}"

def _unpromote(p: str) -> str:
    return p[1:] if p.startswith("+") else p

def _promote(p: str) -> str:
    if p.startswith("+"):
        return p
    if p in ("P", "L", "N", "S", "B", "R"):
        return "+" + p
    return p

def _parse_sfen_board(board_sfen: str) -> List[List[Optional[Tuple[str, str]]]]:
    """
    SFEN ranks are a..i (top to bottom).
    Each rank lists files from 9 to 1.
    We store board[rank 1..9][file 1..9].
    """
    ranks = board_sfen.split("/")
    board: List[List[Optional[Tuple[str, str]]]] = [[None for _ in range(10)] for _ in range(10)]
    if len(ranks) != 9:
        return board

    for r_idx, row in enumerate(ranks, start=1):
        f = 9
        i = 0
        while i < len(row) and f >= 1:
            ch = row[i]
            if ch.isdigit():
                f -= int(ch)
                i += 1
                continue

            promo = False
            if ch == "+":
                promo = True
                i += 1
                if i >= len(row):
                    break
                ch = row[i]

            side = "b" if ch.isupper() else "w"
            pc = ch.upper()
            piece = ("+" + pc) if promo else pc
            board[r_idx][f] = (side, piece)
            f -= 1
            i += 1

    return board

def _init_startpos_board() -> List[List[Optional[Tuple[str, str]]]]:
    sfen = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL"
    return _parse_sfen_board(sfen)

def _clone_board(board: List[List[Optional[Tuple[str, str]]]]) -> List[List[Optional[Tuple[str, str]]]]:
    return [row[:] for row in board]

def _side_to_move_from_position(base_position: str) -> str:
    toks = base_position.split()
    if not toks or toks[0] != "position":
        return "b"
    i = 1
    if i < len(toks) and toks[i] == "startpos":
        if "moves" in toks:
            mc = len(toks) - (toks.index("moves") + 1)
            return "b" if mc % 2 == 0 else "w"
        return "b"
    if i < len(toks) and toks[i] == "sfen":
        if i + 2 < len(toks):
            return "b" if toks[i + 2] == "b" else "w"
    return "b"

def _board_from_position(base_position: str) -> Tuple[List[List[Optional[Tuple[str, str]]]], str]:
    board = _init_startpos_board()
    side = "b"
    toks = base_position.split()
    if not toks or toks[0] != "position":
        return board, side

    i = 1
    if i < len(toks) and toks[i] == "startpos":
        board = _init_startpos_board()
        side = "b"
    elif i < len(toks) and toks[i] == "sfen":
        if i + 4 < len(toks):
            board_sfen = toks[i + 1]
            side_sfen = toks[i + 2]
            board = _parse_sfen_board(board_sfen)
            side = "b" if side_sfen == "b" else "w"
    else:
        return board, side

    if "moves" in toks:
        m_idx = toks.index("moves") + 1
        seq = toks[m_idx:]
        for mv in seq:
            board, _, _ = _apply_usi_move(board, side, mv)
            side = "w" if side == "b" else "b"

    return board, side

def _apply_usi_move(
    board: List[List[Optional[Tuple[str, str]]]],
    side_to_move: str,
    move: str
) -> Tuple[List[List[Optional[Tuple[str, str]]]], str, bool]:
    """
    Apply move to board (軽量・擬似合法)。capture検出あり。
    Returns (new_board, moved_piece_code_after, captured?)
    """
    b2 = _clone_board(board)

    if "*" in move:
        pc = move[0].upper()
        dst = move.split("*", 1)[1]
        xy = _sq_to_xy(dst)
        piece = pc
        if xy:
            f, r = xy
            b2[r][f] = (side_to_move, piece)
        return b2, piece, False

    prom = move.endswith("+")
    core = move[:-1] if prom else move
    if len(core) != 4:
        return b2, "P", False

    src = core[:2]
    dst = core[2:]
    sxy = _sq_to_xy(src)
    dxy = _sq_to_xy(dst)
    if not sxy or not dxy:
        return b2, "P", False

    sf, sr = sxy
    df, dr = dxy

    sp = b2[sr][sf]
    moved = sp[1] if sp else "P"

    captured = False
    dp = b2[dr][df]
    if dp is not None and dp[0] != side_to_move:
        captured = True

    b2[sr][sf] = None
    moved_after = _promote(moved) if prom else moved
    b2[dr][df] = (side_to_move, moved_after)
    return b2, moved_after, captured

def usi_move_to_kif(base_position: str, usi_move: str) -> str:
    if not SHOW_KIF:
        return usi_move

    if (not is_usi_move_token(usi_move)) or is_special_bestmove(usi_move):
        return usi_move

    board, side = _board_from_position(base_position)
    side_to_move = side

    if "*" in usi_move:
        pc = usi_move[0].upper()
        dst = usi_move.split("*", 1)[1]
        dxy = _sq_to_xy(dst)
        if not dxy:
            return usi_move
        df, dr = dxy
        piece_jp = PIECE_JP.get(pc, pc)
        return f"{_xy_to_kif(df, dr)}{piece_jp}打"

    prom = usi_move.endswith("+")
    core = usi_move[:-1] if prom else usi_move
    if len(core) != 4:
        return usi_move
    dst = core[2:]
    dxy = _sq_to_xy(dst)
    if not dxy:
        return usi_move

    _, moved_piece, _ = _apply_usi_move(board, side_to_move, usi_move)

    df, dr = dxy
    base_piece = _unpromote(moved_piece)

    if prom:
        piece_jp = PIECE_JP.get(base_piece, base_piece)
        suffix = "成"
    else:
        piece_jp = PIECE_JP.get(moved_piece, PIECE_JP.get(base_piece, moved_piece))
        suffix = ""

    return f"{_xy_to_kif(df, dr)}{piece_jp}{suffix}"

def usi_move_to_kif_on_board(
    board: List[List[Optional[Tuple[str, str]]]],
    side_to_move: str,
    usi_move: str
) -> str:
    if not SHOW_KIF:
        return usi_move
    if (not is_usi_move_token(usi_move)) or is_special_bestmove(usi_move):
        return usi_move

    if "*" in usi_move:
        pc = usi_move[0].upper()
        dst = usi_move.split("*", 1)[1]
        dxy = _sq_to_xy(dst)
        if not dxy:
            return usi_move
        df, dr = dxy
        piece_jp = PIECE_JP.get(pc, pc)
        return f"{_xy_to_kif(df, dr)}{piece_jp}打"

    prom = usi_move.endswith("+")
    core = usi_move[:-1] if prom else usi_move
    if len(core) != 4:
        return usi_move

    dst = core[2:]
    dxy = _sq_to_xy(dst)
    if not dxy:
        return usi_move

    _, moved_piece, _ = _apply_usi_move(board, side_to_move, usi_move)

    df, dr = dxy
    base_piece = _unpromote(moved_piece)

    if prom:
        piece_jp = PIECE_JP.get(base_piece, base_piece)
        suffix = "成"
    else:
        piece_jp = PIECE_JP.get(moved_piece, PIECE_JP.get(base_piece, moved_piece))
        suffix = ""

    return f"{_xy_to_kif(df, dr)}{piece_jp}{suffix}"

def pv_line_to_kif(base_position: str, pv_line: str, max_moves: int = 12) -> str:
    moves = pv_line.split()
    if not moves:
        return ""

    lim = max(1, int(max_moves))
    show_moves = moves[:lim]
    truncated = len(moves) > lim

    if (not SHOW_KIF) or (not base_position.startswith("position")):
        s = " ".join(show_moves)
        return s + (" ..." if truncated else "")

    board, side = _board_from_position(base_position)
    cur_side = side
    disp: List[str] = []

    for mv in show_moves:
        if (not is_usi_move_token(mv)) or is_special_bestmove(mv):
            break
        disp.append(usi_move_to_kif_on_board(board, cur_side, mv))
        board, _, _ = _apply_usi_move(board, cur_side, mv)
        cur_side = "w" if cur_side == "b" else "b"

    s = " ".join(disp) if disp else " ".join(show_moves)
    return s + (" ..." if truncated else "")

# =========================
# ゲームフェイズ指標
# =========================
def _in_bounds(f: int, r: int) -> bool:
    return 1 <= f <= 9 and 1 <= r <= 9

def _dir(side: str) -> int:
    return -1 if side == "b" else 1

def _gold_moves(side: str) -> List[Tuple[int, int]]:
    d = _dir(side)
    return [(0, d), (-1, d), (1, d), (-1, 0), (1, 0), (0, -d)]

def _silver_moves(side: str) -> List[Tuple[int, int]]:
    d = _dir(side)
    return [(0, d), (-1, d), (1, d), (-1, -d), (1, -d)]

def _knight_moves(side: str) -> List[Tuple[int, int]]:
    d = _dir(side)
    return [(-1, 2*d), (1, 2*d)]

def _king_moves() -> List[Tuple[int, int]]:
    return [(-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)]

def _attack_squares(board: List[List[Optional[Tuple[str,str]]]], side: str, f: int, r: int, piece: str) -> List[Tuple[int,int]]:
    res: List[Tuple[int,int]] = []
    d = _dir(side)

    def add(df: int, dr: int) -> None:
        nf, nr = f + df, r + dr
        if _in_bounds(nf, nr):
            res.append((nf, nr))

    def slide(df: int, dr: int) -> None:
        nf, nr = f + df, r + dr
        while _in_bounds(nf, nr):
            res.append((nf, nr))
            if board[nr][nf] is not None:
                break
            nf += df
            nr += dr

    p = piece
    if p == "P":
        add(0, d)
    elif p == "L":
        slide(0, d)
    elif p == "N":
        for df, dr in _knight_moves(side):
            add(df, dr)
    elif p == "S":
        for df, dr in _silver_moves(side):
            add(df, dr)
    elif p == "G":
        for df, dr in _gold_moves(side):
            add(df, dr)
    elif p == "K":
        for df, dr in _king_moves():
            add(df, dr)
    elif p == "B":
        for df, dr in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            slide(df, dr)
    elif p == "R":
        for df, dr in [(0,-1),(0,1),(-1,0),(1,0)]:
            slide(df, dr)
    elif p in ("+P","+L","+N","+S"):
        for df, dr in _gold_moves(side):
            add(df, dr)
    elif p == "+B":
        for df, dr in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            slide(df, dr)
        for df, dr in [(0,-1),(0,1),(-1,0),(1,0)]:
            add(df, dr)
    elif p == "+R":
        for df, dr in [(0,-1),(0,1),(-1,0),(1,0)]:
            slide(df, dr)
        for df, dr in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            add(df, dr)

    return res

def contact_soon_count(board: List[List[Optional[Tuple[str,str]]]]) -> int:
    targets_b: Set[Tuple[int,int]] = set()
    targets_w: Set[Tuple[int,int]] = set()

    for r in range(1, 10):
        for f in range(1, 10):
            cell = board[r][f]
            if cell is None:
                continue
            side, piece = cell
            att = _attack_squares(board, side, f, r, piece)
            for (af, ar) in att:
                dst = board[ar][af]
                if dst is None:
                    continue
                if dst[0] == side:
                    continue
                if side == "b":
                    targets_b.add((af, ar))
                else:
                    targets_w.add((af, ar))

    return len(targets_b) + len(targets_w)

def _attack_readiness_side(board: List[List[Optional[Tuple[str,str]]]], side: str) -> float:
    minor_adv = 0.0
    minor_total = 0.0
    camp = 0.0
    camp_total = 0.0

    def adv_amount(r: int) -> int:
        return max(0, (7 - r)) if side == "b" else max(0, (r - 3))

    for r in range(1, 10):
        for f in range(1, 10):
            cell = board[r][f]
            if cell is None or cell[0] != side:
                continue
            piece = cell[1]
            if piece == "K":
                continue

            if _unpromote(piece) in ("P","L","N","S","G"):
                minor_total += 1.0
                a = adv_amount(r)
                if a >= 1:
                    minor_adv += 1.0

            camp_total += 1.0
            if side == "b":
                if r <= 3:
                    camp += 1.0
            else:
                if r >= 7:
                    camp += 1.0

    minor = (minor_adv / minor_total) if minor_total > 0 else 0.0
    camp_ratio = (camp / camp_total) if camp_total > 0 else 0.0

    open_rook = 0.0
    if side == "b":
        rook_file = 2
        pawn_start_r = 7
    else:
        rook_file = 8
        pawn_start_r = 3

    pawn_found = False
    for r in range(1, 10):
        cell = board[r][rook_file]
        if cell and cell[0] == side and _unpromote(cell[1]) == "P":
            pawn_found = True
            if r != pawn_start_r:
                open_rook = 1.0
            break
    if not pawn_found:
        open_rook = 1.0

    open_bishop = 0.0
    bishop_sq = (8, 8) if side == "b" else (2, 2)
    bf, br = bishop_sq
    if _in_bounds(bf, br):
        cell = board[br][bf]
        if cell and cell[0] == side and _unpromote(cell[1]) == "B":
            steps = [(-1,-1),(1,-1),(-1,1),(1,1)]
            open_cnt = 0
            for df, dr in steps:
                nf, nr = bf + df, br + dr
                if _in_bounds(nf, nr) and board[nr][nf] is None:
                    open_cnt += 1
            open_bishop = clamp(open_cnt / 4.0)

    readiness = 0.45 * minor + 0.30 * max(open_rook, open_bishop) + 0.25 * camp_ratio
    return clamp(readiness)

def attack_readiness(board: List[List[Optional[Tuple[str,str]]]], side_to_move: str) -> float:
    rb = _attack_readiness_side(board, "b")
    rw = _attack_readiness_side(board, "w")
    return max(rb, rw)

def _norm_csc(csc: int) -> float:
    return clamp(1.0 - math.exp(-float(max(0, csc)) / 4.0))

def pv_contact_soon(base_position: str, pv_line: str, ply: int = GP_PV_PLY) -> float:
    if not base_position.startswith("position") or not pv_line:
        return 0.0

    board, side = _board_from_position(base_position)
    moves = pv_line.split()
    if not moves:
        return 0.0

    t_capture: Optional[int] = None
    t_contact: Optional[int] = None

    cur_board = board
    cur_side = side

    for i, mv in enumerate(moves[:ply], start=1):
        if (not is_usi_move_token(mv)) or is_special_bestmove(mv):
            break
        cur_board, _, captured = _apply_usi_move(cur_board, cur_side, mv)
        cur_side = "w" if cur_side == "b" else "b"

        if captured and t_capture is None:
            t_capture = i

        if t_contact is None:
            csc = contact_soon_count(cur_board)
            if csc >= 1:
                t_contact = i

        if t_capture is not None and t_capture <= 2:
            break

    score_cap = 0.0
    if t_capture is not None:
        score_cap = clamp(math.exp(-float(t_capture - 1) / 1.6))
    score_con = 0.0
    if t_contact is not None:
        score_con = clamp(math.exp(-float(t_contact - 1) / 2.4))

    return clamp(max(score_cap, 0.85 * score_con))

def king_danger_cp_bias(base_position: str) -> int:
    if not base_position:
        return 0

    board, _ = _board_from_position(base_position)

    kb: Optional[Tuple[int, int]] = None
    kw: Optional[Tuple[int, int]] = None
    for r in range(1, 10):
        for f in range(1, 10):
            cell = board[r][f]
            if cell is None:
                continue
            side, piece = cell
            if piece != "K":
                continue
            if side == "b":
                kb = (f, r)
            else:
                kw = (f, r)

    def build_counts(s: str) -> Dict[Tuple[int, int], int]:
        m: Dict[Tuple[int, int], int] = {}
        for r in range(1, 10):
            for f in range(1, 10):
                cell = board[r][f]
                if cell is None or cell[0] != s:
                    continue
                piece = cell[1]
                att = _attack_squares(board, s, f, r, piece)
                for sq in att:
                    m[sq] = m.get(sq, 0) + 1
        return m

    att_b = build_counts("b")
    att_w = build_counts("w")

    def region(kpos: Optional[Tuple[int, int]], side: str) -> Set[Tuple[int, int]]:
        reg: Set[Tuple[int, int]] = set()
        if kpos is None:
            return reg
        f, r = kpos
        for df, dr in _king_moves():
            nf, nr = f + df, r + dr
            if _in_bounds(nf, nr):
                reg.add((nf, nr))
        d = _dir(side)
        nf, nr = f, r + d
        if _in_bounds(nf, nr):
            reg.add((nf, nr))
        return reg

    def danger(side: str, kpos: Optional[Tuple[int, int]]) -> float:
        reg = region(kpos, side)
        if not reg:
            return 0.0
        ally = att_b if side == "b" else att_w
        ene  = att_w if side == "b" else att_b
        e = 0.0
        a = 0.0
        for sq in reg:
            e += float(ene.get(sq, 0))
            a += float(ally.get(sq, 0))
        return clamp(e / (e + a + 2.0))

    danger_b = danger("b", kb)
    danger_w = danger("w", kw)

    stm = _side_to_move_from_position(base_position)
    danger_side_to_move = danger_b if stm == "b" else danger_w
    danger_other = danger_w if stm == "b" else danger_b
    danger_diff = danger_side_to_move - danger_other
    cp_bias = int(round(-600.0 * danger_diff))
    return cp_bias

@dataclass
class GamePhaseMetrics:
    csc: int
    csc_n: float
    ar: float
    pvcs: float
    engage: float

class GamePhaseState:
    ORDER = ["BUILD", "PROBE", "TENSION", "CLASH", "CONVERT", "FINISH"]

    def __init__(self) -> None:
        self.phase: str = "BUILD"
        self.ew_csc: float = 0.0
        self.ew_ar: float = 0.0
        self.ew_pvcs: float = 0.0
        self.ew_eng: float = 0.0

    def _ewma(self, prev: float, x: float) -> float:
        a = clamp(GP_EWMA_ALPHA, 0.05, 0.90)
        return a * x + (1.0 - a) * prev

    def _hys_step(self, current: str, eng: float) -> str:
        probe_exit = GP_PROBE_ENTER - GP_HYS_GAP
        tens_exit  = GP_TENS_ENTER - GP_HYS_GAP
        clash_exit = GP_CLASH_ENTER - GP_HYS_GAP

        if current == "BUILD":
            return "PROBE" if eng >= GP_PROBE_ENTER else "BUILD"
        if current == "PROBE":
            if eng >= GP_TENS_ENTER:
                return "TENSION"
            if eng < probe_exit:
                return "BUILD"
            return "PROBE"
        if current == "TENSION":
            if eng >= GP_CLASH_ENTER:
                return "CLASH"
            if eng < tens_exit:
                return "PROBE"
            return "TENSION"
        if current == "CLASH":
            if eng < clash_exit:
                return "TENSION"
            return "CLASH"
        return current

    def update(self, raw: GamePhaseMetrics, best_cp_abs: int, has_mate: bool) -> str:
        self.ew_csc  = self._ewma(self.ew_csc, float(raw.csc))
        self.ew_ar   = self._ewma(self.ew_ar, raw.ar)
        self.ew_pvcs = self._ewma(self.ew_pvcs, raw.pvcs)
        self.ew_eng  = self._ewma(self.ew_eng, raw.engage)

        if has_mate or best_cp_abs >= GP_FINISH_CP_ENTER:
            self.phase = "FINISH"
            return self.phase

        if best_cp_abs >= GP_CONVERT_CP_ENTER and self.ew_eng >= 0.30:
            if self.phase in ("BUILD","PROBE","TENSION","CLASH","CONVERT"):
                self.phase = "CONVERT"
                return self.phase

        if self.ew_pvcs >= GP_SHOCK_PVCS or int(round(self.ew_csc)) >= GP_SHOCK_CSC:
            if self.phase in ("BUILD","PROBE","TENSION"):
                self.phase = "CLASH"
                return self.phase

        if self.phase == "FINISH":
            if (not has_mate) and best_cp_abs < GP_FINISH_CP_EXIT:
                self.phase = "CONVERT" if best_cp_abs >= GP_CONVERT_CP_ENTER else "CLASH"
            return self.phase

        if self.phase == "CONVERT":
            if best_cp_abs < GP_CONVERT_CP_EXIT:
                self.phase = "CLASH" if self.ew_eng >= (GP_CLASH_ENTER - GP_HYS_GAP) else "TENSION"
            return self.phase

        next_phase = self._hys_step(self.phase, self.ew_eng)

        cur_idx = self.ORDER.index(self.phase)
        nxt_idx = self.ORDER.index(next_phase)
        if nxt_idx < cur_idx - 1:
            next_phase = self.ORDER[cur_idx - 1]

        self.phase = next_phase
        return self.phase

def game_phase_text(phase: str) -> str:
    if phase == "BUILD":
        return "BUILD"
    if phase == "PROBE":
        return "PROBE"
    if phase == "TENSION":
        return "TENSION"
    if phase == "CLASH":
        return "CLASH"
    if phase == "CONVERT":
        return "CONVERT"
    return "FINISH"

# =========================
# 形勢（stance）: cp_ew + hysteresis
# =========================
class StanceState:
    """
    stance: ADV / EVEN / DEFICIT / CRISIS
    cpは“地形”。瞬間値のブレをEWMAで丸める。
    """
    def __init__(self) -> None:
        self.stance: str = "EVEN"
        self.cp_ew: float = 0.0
        self.dcp_ew: float = 0.0
        self._prev_cp_ew: float = 0.0

    def reset(self) -> None:
        self.stance = "EVEN"
        self.cp_ew = 0.0
        self.dcp_ew = 0.0
        self._prev_cp_ew = 0.0

    def update(self, best_cp: int, has_mate: bool, has_self_mate: bool) -> str:
        cp = float(_safe_best_cp(best_cp))
        a = clamp(STANCE_EWMA_ALPHA, 0.05, 0.90)
        if abs(cp - self.cp_ew) >= STANCE_SHOCK_CP:
            a = min(0.90, a * 3.0)
        self._prev_cp_ew = self.cp_ew
        self.cp_ew = a * cp + (1.0 - a) * self.cp_ew

        d = self.cp_ew - self._prev_cp_ew
        ta = clamp(STANCE_TREND_ALPHA, 0.05, 0.90)
        self.dcp_ew = ta * d + (1.0 - ta) * self.dcp_ew

        s = self.stance

        # CRISIS（最優先）
        if s == "CRISIS":
            if self.cp_ew >= STANCE_CRI_EXIT:
                s = "DEFICIT" if self.cp_ew <= STANCE_DEF_EXIT else "EVEN"
        else:
            if self.cp_ew <= STANCE_CRI_ENTER:
                s = "CRISIS"

        # ADV/DEFICIT
        if s != "CRISIS":
            if s == "ADV":
                if self.cp_ew <= STANCE_ADV_EXIT:
                    s = "EVEN"
            elif s == "DEFICIT":
                if self.cp_ew >= STANCE_DEF_EXIT:
                    s = "EVEN"
            else:  # EVEN
                if self.cp_ew >= STANCE_ADV_ENTER:
                    s = "ADV"
                elif self.cp_ew <= STANCE_DEF_ENTER:
                    s = "DEFICIT"

        if has_self_mate:
            s = "CRISIS"
        elif has_mate and s in ("EVEN", "DEFICIT", "CRISIS"):
            s = "ADV"

        self.stance = s
        return self.stance

def stance_text(stance: str) -> str:
    return stance  # 英語1語

# =========================
# Candidate ranking（本筋用）
# =========================
def rank_candidates(cands: List[Cand], feats: Dict[str, Features], best: Cand, phase_mode: str) -> List[Cand]:
    best_cp_used = _safe_best_cp(best.cp)

    def key(c: Cand) -> Tuple[float, float, float]:
        f = feats[c.move]
        ccp = 0 if abs(c.cp) >= 20000 else c.cp
        delta = float(best_cp_used - ccp)
        pref = float(prefix_len(best.pv, c.pv, PREFIX_K))

        if phase_mode == "STABLE":
            return (f.winability, -delta, pref)
        if phase_mode == "BALANCE":
            return (f.winability, -pref, -delta)
        if phase_mode == "DISTURB":
            return (0.5 * f.initiative + 0.3 * f.opp_diff + 0.2 * f.winability, -pref, -delta)
        return (0.7 * f.initiative + 0.2 * f.opp_diff + 0.1 * f.winability, -pref, -delta)

    hard_limit = DROP_SOFT_LIMIT if phase_mode in ("STABLE", "BALANCE") else DROP_DISTURB_LIMIT

    kept: List[Cand] = []
    for c in cands:
        ccp = 0 if abs(c.cp) >= 20000 else c.cp
        delta = best_cp_used - ccp
        if delta <= hard_limit:
            kept.append(c)
    if not kept:
        kept = list(cands)

    kept.sort(key=key, reverse=True)
    return kept

# =========================
# ATK/DEF scoring（1/2/3）
# =========================
def delta_cp(best: Cand, c: Cand) -> int:
    bcp = _safe_best_cp(best.cp)
    ccp = 0 if abs(c.cp) >= 20000 else c.cp
    return bcp - ccp

def _delta_norm(best: Cand, c: Cand) -> float:
    d = float(max(0, delta_cp(best, c)))
    return clamp(d / 800.0)

def atk_score(f: Features, pvcs: float, gphase: str, stance: str, best: Cand, c: Cand) -> float:
    dn = _delta_norm(best, c)

    if gphase in ("CLASH", "FINISH"):
        s = 0.55 * pvcs + 0.20 * f.initiative + 0.15 * f.divergence + 0.10 * f.opp_diff
    elif gphase == "TENSION":
        s = 0.45 * pvcs + 0.25 * f.initiative + 0.15 * f.divergence + 0.15 * f.opp_diff
    else:  # BUILD/PROBE/CONVERT
        s = 0.28 * pvcs + 0.37 * f.initiative + 0.15 * f.divergence + 0.20 * f.opp_diff

    if stance in ("DEFICIT", "CRISIS"):
        s -= 0.05 * dn
    else:
        s -= 0.10 * dn

    if f.self_risk >= 0.92:
        s -= 0.20
    elif f.self_risk >= 0.85:
        s -= 0.10

    return clamp(s)

def def_score(f: Features, inertia: float, gphase: str, stance: str, best: Cand, c: Cand) -> float:
    dn = _delta_norm(best, c)

    heaviness = (
        0.40 * f.stability +
        0.35 * (1.0 - f.self_risk) +
        0.15 * (1.0 - f.divergence) +
        0.10 * f.opp_diff
    )
    anti_rush = -0.15 * max(0.0, f.initiative - 0.55)
    inertia_bonus = 0.25 * inertia
    delta_penalty = -0.10 * dn

    s = heaviness + anti_rush + inertia_bonus + delta_penalty

    if stance == "CRISIS":
        if f.self_risk > 0.65:
            s -= 0.18
        if f.divergence > 0.60:
            s -= 0.12

    return clamp(s)

def combined_display_score(tag: str, f: Features, atk: float, deff: float) -> float:
    if tag == "MAIN":
        return disp_win(f.winability)
    if tag == "ATK":
        return disp_win(0.55 * f.winability + 0.45 * atk)
    if tag == "DEF":
        return disp_win(0.60 * f.winability + 0.40 * deff)
    return disp_win(f.winability)

# =========================
# intentsane: IntentState（存在チェック込み）
# =========================
class IntentState:
    """
    intent: MAIN / ATK / DEF
    - 原則: 現 intent を維持（線を太く）
    - 例外: shock / CRISIS / 明確な優位
    - 重要: 候補タグが存在しない intent へは遷移しない（存在チェック）
    """
    def __init__(self) -> None:
        self.intent: str = "MAIN"
        self.reason: str = "init"

    def update(
        self,
        gphase: str,
        stance: str,
        scores: Dict[str, float],
        available: Dict[str, bool],
    ) -> Tuple[str, str]:
        def is_av(tag: str) -> bool:
            return bool(available.get(tag, False))

        def best_available() -> Tuple[str, float]:
            best_t = "MAIN"
            best_s = float("-inf")
            for t, sc in scores.items():
                if not is_av(t):
                    continue
                if sc > best_s:
                    best_s = sc
                    best_t = t
            if not is_av(best_t):
                # 最終安全弁
                if is_av("MAIN"):
                    return "MAIN", scores.get("MAIN", 0.0)
                for t in ("ATK", "DEF"):
                    if is_av(t):
                        return t, scores.get(t, 0.0)
                return "MAIN", float("-inf")
            return best_t, best_s

        # 現intentが死んでるなら即座に救済
        if not is_av(self.intent):
            t, _ = best_available()
            self.reason = f"switch: {self.intent} not available -> {t}"
            self.intent = t
            return self.intent, self.reason

        cur = self.intent
        cur_s = scores.get(cur, float("-inf"))
        best_t, best_s = best_available()

        # CRISIS規則（ただし存在チェック付き）
        if stance == "CRISIS":
            if is_av("DEF"):
                # ATKへ振るのは明確にATKが勝るときだけ
                if is_av("ATK") and scores.get("ATK", float("-inf")) >= scores.get("DEF", float("-inf")) + 0.08:
                    self.intent = "ATK"
                    self.reason = "switch: CRISIS but ATK clearly stronger"
                else:
                    self.intent = "DEF"
                    self.reason = "keep/switch: CRISIS -> DEF"
                return self.intent, self.reason
            # DEFが無いCRISISは嘘をつけないので残り最善へ
            if is_av("ATK"):
                self.intent = "ATK"
                self.reason = "switch: CRISIS but DEF missing -> ATK"
                return self.intent, self.reason
            self.intent = "MAIN"
            self.reason = "switch: CRISIS but DEF/ATK missing -> MAIN"
            return self.intent, self.reason

        # 通常: まず維持（線を太く）
        keep_margin = max(0.0, float(INTENT_KEEP_MARGIN))
        if best_s <= cur_s + keep_margin:
            self.reason = "keep: within margin"
            return self.intent, self.reason

        # 明確な差（shock）なら転換
        shock = max(0.0, float(INTENT_SWITCH_SHOCK))
        if best_s >= cur_s + shock:
            self.intent = best_t
            self.reason = f"switch: shock ({cur_s:.2f}->{best_s:.2f})"
            return self.intent, self.reason

        # それ以外は軽い転換（ただし序盤はMAINへ寄せる）
        if gphase in ("BUILD", "PROBE") and best_t != "MAIN":
            self.intent = "MAIN" if is_av("MAIN") else best_t
            self.reason = "keep: early phase prefers MAIN"
            return self.intent, self.reason

        self.intent = best_t
        self.reason = f"switch: best ({cur_s:.2f}->{best_s:.2f})"
        return self.intent, self.reason

# =========================
# Safety check (post-move)
# =========================
def safety_check_after_move(eng: Engine, base_position: str, move: str) -> bool:
    # 重要: 特殊bestmoveや不正トークンを踏まない（プロトコル汚染防止）
    if not base_position.startswith("position"):
        return False
    if (not move) or is_special_bestmove(move) or (not is_usi_move_token(move)):
        return False

    eng.drain()

    toks = base_position.split()
    if "moves" in toks:
        newpos = base_position + " " + move
    else:
        newpos = base_position + " moves " + move

    eng.send(newpos)
    eng.send(f"go movetime {SAFETY_MS}")

    unsafe = False
    t0 = time.time()
    deadline = t0 + max(
        SAFETY_DEADLINE_MIN_SEC,
        (max(1, SAFETY_MS) / 1000.0) * SAFETY_DEADLINE_FACTOR
    ) + SAFETY_DEADLINE_PAD_SEC

    got_bestmove = False
    mate_pos_hits = 0

    rto = min(READ_TIMEOUT, max(0.001, SAFETY_READ_TIMEOUT))

    while time.time() < deadline:
        o = eng.recv(rto)
        if o is None:
            continue

        if o.startswith("info") and " mate " in o:
            tt = o.split()
            if "mate" in tt:
                idx = tt.index("mate")
                m = parse_mate_token(tt[idx + 1] if idx + 1 < len(tt) else None)
                # 相手番なので mate+ は危険
                if m is not None and m > 0:
                    if m <= max(1, SAFETY_MATE_MAX):
                        unsafe = True
                    else:
                        mate_pos_hits += 1
                        if mate_pos_hits >= max(1, SAFETY_MATE_REPEAT):
                            unsafe = True

        if o.startswith("bestmove"):
            got_bestmove = True
            break

        if unsafe:
            break

    if not got_bestmove:
        eng.send("stop")
        t_stop = time.time() + SAFETY_STOP_GRACE_SEC
        while time.time() < t_stop:
            o = eng.recv(rto)
            if o is None:
                continue
            if o.startswith("bestmove"):
                break

    eng.send(base_position)  # 局面復元
    eng.drain()
    return unsafe

# =========================
# USI option collector
# =========================
def _parse_option_name(line: str) -> Optional[str]:
    # option name <...> type <...>
    if not line.startswith("option "):
        return None
    tt = line.split()
    if "name" not in tt:
        return None
    i = tt.index("name") + 1
    if i >= len(tt):
        return None
    # name はスペースを含みうる。typeの直前までを連結する
    j = tt.index("type") if "type" in tt and tt.index("type") > i else len(tt)
    name = " ".join(tt[i:j]).strip()
    return name or None

# =========================
# Main USI loop
# =========================
def main() -> None:
    eng = Engine()
    stdin = StdinReader()

    current_position = ""
    user_mode = DEFAULT_MODE
    multipv = DEFAULT_MULTIPV

    supported_opts: Set[str] = set()
    win_hys = WinPhaseHysteresis()
    game_state = GamePhaseState()
    stance_state = StanceState()
    intent_state = IntentState()

    # DEFの“線”を太くするためのトラック（前回PV）
    def_track_pv: Optional[str] = None
    def_track_age: int = 0
    atk_track_pv: Optional[str] = None
    atk_track_age: int = 0

    def send_opt(name: str, value: str) -> None:
        if name in supported_opts:
            eng.send(f"setoption name {name} value {value}")

    def apply_engine_opts():
        send_opt("Threads", str(THREADS))
        send_opt("USI_Hash", str(HASH_MB))
        send_opt("MultiPV", str(multipv))

        if AUTO_DISABLE_BOOK and not os.path.isfile(BOOK_FILE):
            send_opt("USI_OwnBook", "false")

    def handle_setoption(line: str) -> bool:
        nonlocal user_mode, multipv
        toks = line.split()
        if "name" in toks:
            i = toks.index("name")
            name = toks[i + 1] if i + 1 < len(toks) else ""
            value = ""
            if "value" in toks:
                j = toks.index("value")
                value = " ".join(toks[j + 1 :]) if j + 1 < len(toks) else ""

            if name == "TASO_Mode":
                v = value.strip().upper()
                if v in (MODE_PLAY, MODE_WATCH, MODE_ANALYZE):
                    user_mode = v
                    info(f"mode set => {user_mode}")
                return True

            if name == "TASO_MultiPV":
                mv = _try_int(value.strip())
                if mv is not None and 1 <= mv <= 10:
                    multipv = mv
                    info(f"multipv set => {multipv}")
                    apply_engine_opts()
                return True

            if name == "TASO_SafetyMs":
                info("TASO_SafetyMs is fixed via env TASO_SAFETY_MS (restart required)")
                return True

        return False

    def fmt_move(mv: str) -> str:
        if not current_position:
            return mv
        kif = usi_move_to_kif(current_position, mv)
        if kif == mv:
            return mv
        return f"{kif}({mv})"

    def should_forward_engine_info() -> bool:
        if FORWARD_ENGINE_INFO_MODE in ("1", "true", "yes"):
            return True
        if FORWARD_ENGINE_INFO_MODE in ("0", "false", "no"):
            return False
        # auto
        return user_mode == MODE_ANALYZE

    def hard_timeout_for_go(go_line: str) -> Optional[float]:
        tt = go_line.split()
        if "infinite" in tt or "ponder" in tt:
            return GO_HARD_SEC_INFINITE if GO_HARD_SEC_INFINITE > 0.0 else None
        return GO_HARD_SEC if GO_HARD_SEC > 0.0 else None

    NEG_INF = float("-inf")

    # ANALYZE用: info multipv を短時間バッファして並べ替える（機能維持）
    mpv_buf: Dict[int, deque[str]] = {}
    mpv_buf_start: Optional[float] = None

    def _reset_mpv_buf() -> None:
        nonlocal mpv_buf, mpv_buf_start
        mpv_buf = {}
        mpv_buf_start = None

    def _flush_mpv_buf() -> None:
        nonlocal mpv_buf, mpv_buf_start
        if not mpv_buf:
            mpv_buf_start = None
            return
        # 1..N の順に吐く
        for k in sorted(mpv_buf.keys()):
            dq = mpv_buf[k]
            while dq:
                out(dq.popleft())
        mpv_buf = {}
        mpv_buf_start = None

    def _buf_or_forward_info(line: str, tt: List[str], fwd_info: bool) -> None:
        nonlocal mpv_buf_start, mpv_buf

        if not fwd_info:
            return

        # 並べ替えOFFなら従来通り即転送
        if (not INFO_MPV_REORDER) or (user_mode != MODE_ANALYZE):
            out(line)
            return

        # multipv 付きだけバッファ対象。無いinfoは即転送（機能削除なし）
        if "multipv" not in tt:
            # バッファ中に非multipvを混ぜると読みにくいので、先に吐いてからバッファを維持
            out(line)
            return

        m = 1
        idx = tt.index("multipv")
        mv = _try_int(tt[idx + 1] if idx + 1 < len(tt) else "")
        if mv is not None:
            m = mv

        now = time.time()
        if mpv_buf_start is None:
            mpv_buf_start = now

        if m not in mpv_buf:
            mpv_buf[m] = deque()
        mpv_buf[m].append(line)

        # 1) mpv=1 が来たら、だいたい1サイクル終わりとみなしてflush
        if m == 1:
            _flush_mpv_buf()
            return

        # 2) タイムアウト（既定20ms）でflush
        buf_sec = max(0.0, float(INFO_MPV_BUF_MS) / 1000.0)
        if mpv_buf_start is not None and (now - mpv_buf_start) >= buf_sec:
            _flush_mpv_buf()

    try:
        while True:
            line = stdin.get(timeout=0.1)
            if line is None:
                if not stdin.alive and stdin.q.empty():
                    break
                # バッファが溜まってて時間経過したらflush（ANALYZE読みやすさ改善）
                if mpv_buf_start is not None and mpv_buf:
                    buf_sec = max(0.0, float(INFO_MPV_BUF_MS) / 1000.0)
                    if (time.time() - mpv_buf_start) >= buf_sec:
                        _flush_mpv_buf()
                continue

            if line == "usi":
                supported_opts.clear()
                eng.send("usi")
                while True:
                    o = eng.recv(READ_TIMEOUT)
                    if o is None:
                        continue

                    if o.startswith("option "):
                        name = _parse_option_name(o)
                        if name:
                            supported_opts.add(name)

                    if o == "usiok":
                        out(f'option name TASO_Mode type combo default {DEFAULT_MODE} var PLAY var WATCH var ANALYZE')
                        out(f'option name TASO_MultiPV type spin default {DEFAULT_MULTIPV} min 1 max 10')
                        out(f'option name TASO_SafetyMs type spin default {SAFETY_MS} min 0 max 500')
                        out("usiok")
                        break
                    out(o)

                apply_engine_opts()
                continue

            if line.startswith("setoption"):
                if handle_setoption(line):
                    continue
                eng.send(line)
                continue

            if line == "isready":
                eng.send("isready")
                t0 = time.time()
                while True:
                    o = eng.recv(READ_TIMEOUT)
                    if o is None:
                        if time.time() - t0 > 10.0:
                            out("readyok")
                            break
                        continue
                    if o == "readyok":
                        out("readyok")
                        break
                    if o.startswith("info") and should_forward_engine_info():
                        out(o)
                continue

            if line == "usinewgame":
                stance_state.reset()
                eng.send(line)
                continue

            if line.startswith("position"):
                current_position = line
                stance_state.reset()
                eng.send(line)
                continue

            if line.startswith("go"):
                eng.drain()
                apply_engine_opts()
                eng.send(line)

                # go開始時にバッファを初期化
                _reset_mpv_buf()

                # DEF inertia の参照は「前回トラック」で行う（盛り防止）
                prev_def_track_pv = def_track_pv
                prev_def_track_age = def_track_age
                prev_atk_track_pv = atk_track_pv
                prev_atk_track_age = atk_track_age

                cands_map: Dict[int, Cand] = {}
                cands_by_move: Dict[str, Cand] = {}
                best_engine_move: Optional[str] = None

                t_go = time.time()
                hard_timeout_sec = hard_timeout_for_go(line)
                hard_deadline = (t_go + hard_timeout_sec) if hard_timeout_sec is not None else None
                stop_deadline: Optional[float] = None
                sent_stop = False
                already_resigned = False
                deferred: List[str] = []

                fwd_info = should_forward_engine_info()
                watch_progress_last_ts = 0.0
                watch_progress_last_sig: Optional[Tuple[Tuple[int, int, int, int, str], ...]] = None
                watch_progress_interval_sec = max(0.03, float(WATCH_PROGRESS_INTERVAL_MS) / 1000.0)
                watch_progress_min_depth = max(1, int(WATCH_PROGRESS_MIN_DEPTH))
                watch_progress_lines = max(1, min(10, int(WATCH_PROGRESS_LINES)))

                def maybe_emit_watch_progress(now: float) -> None:
                    nonlocal watch_progress_last_ts, watch_progress_last_sig

                    if user_mode != MODE_WATCH or (not WATCH_PROGRESS_ENABLE):
                        return
                    if (now - watch_progress_last_ts) < watch_progress_interval_sec:
                        return
                    if not cands_map:
                        return

                    ordered = [cands_map[k] for k in sorted(cands_map.keys())]
                    ordered = [c for c in ordered if c.depth >= watch_progress_min_depth]
                    if not ordered:
                        return
                    ordered = ordered[:watch_progress_lines]

                    sig = tuple(
                        (c.mpv, c.depth, c.cp, (c.mate if c.mate is not None else 99999), c.pv)
                        for c in ordered
                    )
                    if sig == watch_progress_last_sig:
                        return

                    for c in ordered:
                        mv_disp = fmt_move(c.move)
                        pv_disp = pv_line_to_kif(current_position, c.pv, WATCH_PROGRESS_PV_MOVES)
                        if c.mate is not None:
                            out(f"info string [WATCH-PROGRESS] mpv={c.mpv} d={c.depth} {mv_disp} score=mate {c.mate} pv={pv_disp}")
                        else:
                            out(f"info string [WATCH-PROGRESS] mpv={c.mpv} d={c.depth} {mv_disp} score={c.cp:+d} pv={pv_disp}")

                    watch_progress_last_ts = now
                    watch_progress_last_sig = sig

                while True:
                    while True:
                        cmd = stdin.get_nowait()
                        if cmd is None:
                            break
                        if cmd == "stop":
                            eng.send("stop")
                            sent_stop = True
                        elif cmd == "ponderhit":
                            eng.send("ponderhit")
                        elif cmd == "quit":
                            eng.send("quit")
                            return
                        else:
                            deferred.append(cmd)

                    now = time.time()
                    if hard_deadline is not None and now > hard_deadline:
                        if not sent_stop:
                            eng.send("stop")
                            sent_stop = True
                        if stop_deadline is None:
                            stop_deadline = now + GO_STOP_GRACE_SEC
                        elif now > stop_deadline:
                            info("⚠ go timeout: bestmove not returned -> resign")
                            # バッファ残があれば吐いてから終える
                            _flush_mpv_buf()
                            out("bestmove resign")
                            already_resigned = True
                            break

                    o = eng.recv(READ_TIMEOUT)
                    if o is None:
                        # バッファが溜まってて時間経過したらflush
                        if mpv_buf_start is not None and mpv_buf:
                            buf_sec = max(0.0, float(INFO_MPV_BUF_MS) / 1000.0)
                            if (time.time() - mpv_buf_start) >= buf_sec:
                                _flush_mpv_buf()
                        continue

                    if o.startswith("info"):
                        tt = o.split()

                        # engine info は垂れ流さない（デフォルトautoでANALYZEのみ）
                        _buf_or_forward_info(o, tt, fwd_info)

                        depth = 0
                        if "depth" in tt:
                            idp = tt.index("depth")
                            d = _try_int(tt[idp + 1] if idp + 1 < len(tt) else "")
                            if d is not None:
                                depth = d

                        mpv = 1
                        if "multipv" in tt:
                            idx = tt.index("multipv")
                            m = _try_int(tt[idx + 1] if idx + 1 < len(tt) else "")
                            if m is not None:
                                mpv = m

                        cp = None
                        if "score" in tt and "cp" in tt:
                            idx = tt.index("cp")
                            cp = _try_int(tt[idx + 1] if idx + 1 < len(tt) else "")

                        mate = None
                        if "mate" in tt:
                            idx = tt.index("mate")
                            mate = parse_mate_token(tt[idx + 1] if idx + 1 < len(tt) else None)

                        cand_updated = False
                        if "pv" in tt:
                            i_pv = tt.index("pv")
                            if i_pv + 1 < len(tt):
                                pv = " ".join(tt[i_pv + 1 :])
                                mv = tt[i_pv + 1]
                                if not mv:
                                    continue

                                if cp is None:
                                    if mate is not None:
                                        cp = 30000 if mate > 0 else -30000
                                    else:
                                        cp = 0

                                new_c = Cand(mpv=mpv, move=mv, pv=pv, cp=cp, mate=mate, depth=depth)
                                prev = cands_map.get(mpv)
                                # depth最大優先（浅いinfoで上書きされない）
                                if prev is None or new_c.depth >= prev.depth:
                                    cands_map[mpv] = new_c
                                    cand_updated = True

                                # move単位の最新候補も保持（終盤でmpvが単一化しても候補の多様性を維持）
                                pm = cands_by_move.get(new_c.move)
                                if pm is None:
                                    cands_by_move[new_c.move] = new_c
                                elif new_c.depth > pm.depth:
                                    cands_by_move[new_c.move] = new_c
                                elif new_c.depth == pm.depth:
                                    pcp = 0 if abs(pm.cp) >= 20000 else pm.cp
                                    ncp = 0 if abs(new_c.cp) >= 20000 else new_c.cp
                                    if ncp > pcp:
                                        cands_by_move[new_c.move] = new_c

                        if cand_updated:
                            maybe_emit_watch_progress(time.time())
                        continue

                    if o.startswith("bestmove"):
                        # bestmove前にバッファ残を吐いておく（ANALYZE整列）
                        _flush_mpv_buf()
                        parts = o.split()
                        best_engine_move = parts[1] if len(parts) >= 2 else None
                        break

                    out(o)

                # deferred 処理（go握りつぶし防止）
                for cmd in deferred:
                    if cmd.startswith("setoption"):
                        if not handle_setoption(cmd):
                            eng.send(cmd)
                    elif cmd.startswith("position"):
                        current_position = cmd
                        eng.send(cmd)
                    elif cmd == "usinewgame":
                        eng.send(cmd)
                    elif cmd.startswith("go"):
                        # 重要: 消さずにキューへ戻す
                        stdin.q.put(cmd)
                    elif cmd == "quit":
                        eng.send("quit")
                        return
                    else:
                        # それ以外は素直に流す
                        eng.send(cmd)

                if already_resigned:
                    continue

                if best_engine_move is None:
                    out("bestmove resign")
                    continue

                cands = list(cands_map.values())
                if not cands:
                    out(f"bestmove {best_engine_move}")
                    continue

                best = cands_map[1] if 1 in cands_map else max(cands, key=lambda c: c.cp)

                # 頓死回避 第1段：見えてる自玉mate負は候補から落とす（表示用候補）
                safe_cands = [c for c in cands if not (c.mate is not None and c.mate < 0)]
                if not safe_cands:
                    safe_cands = cands

                # move重複を圧縮（同じ指し手がmpvに複数あると候補が痩せるため）
                safe_by_move: Dict[str, Cand] = {}
                for c in safe_cands:
                    p = safe_by_move.get(c.move)
                    if p is None:
                        safe_by_move[c.move] = c
                    elif c.depth > p.depth:
                        safe_by_move[c.move] = c
                    elif c.depth == p.depth:
                        pcp = 0 if abs(p.cp) >= 20000 else p.cp
                        ccp = 0 if abs(c.cp) >= 20000 else c.cp
                        if ccp > pcp:
                            safe_by_move[c.move] = c
                safe_cands = list(safe_by_move.values())

                # 深さが進むとmpvが実質1本化することがあるため、探索中に観測した別手で補完
                if len(safe_cands) < WATCH_SHOW_N and cands_by_move:
                    extra_pool = sorted(
                        cands_by_move.values(),
                        key=lambda c: (c.depth, (0 if abs(c.cp) >= 20000 else c.cp)),
                        reverse=True,
                    )
                    for c in extra_pool:
                        if c.move in safe_by_move:
                            continue
                        if c.mate is not None and c.mate < 0:
                            continue
                        safe_by_move[c.move] = c
                        safe_cands.append(c)
                        if len(safe_cands) >= WATCH_SHOW_N:
                            break

                short_mate = None
                has_mate = False
                for c in safe_cands:
                    if c.mate is not None and c.mate > 0:
                        has_mate = True
                        if short_mate is None or c.mate < short_mate[0]:
                            short_mate = (c.mate, c.move)

                has_self_mate = False
                for c in cands:
                    if c.mate is not None and c.mate < 0:
                        has_self_mate = True
                        break

                # --- 勝ち筋ストーリー（Winability側） ---
                disp_n = candidate_dispersion_norm(safe_cands, min(5, len(safe_cands)))
                plan_w = plan_signal_from_state(stance_state.cp_ew, stance_state.dcp_ew, game_state.ew_eng, disp_n, stance_state.stance, has_mate, has_self_mate)
                win_phase = win_hys.update(plan_w)
                win_text = win_plan_text(win_phase)

                # --- 形勢（stance） ---
                cp_bias = king_danger_cp_bias(current_position) if current_position else 0
                stance = stance_state.update(best.cp + cp_bias, has_mate, has_self_mate)

                # --- Features ---
                feats_by_move: Dict[str, Features] = {}
                for c in safe_cands:
                    feats_by_move[c.move] = compute_features(c, best, cands, win_phase)

                # --- ゲームフェイズ ---
                gp_phase = game_state.phase
                if current_position:
                    board, _ = _board_from_position(current_position)
                    csc = contact_soon_count(board)
                    ar = attack_readiness(board, _side_to_move_from_position(current_position))
                    pvcs_best = pv_contact_soon(current_position, best.pv, GP_PV_PLY)
                    csc_n = _norm_csc(csc)
                    engage = clamp(0.45 * pvcs_best + 0.35 * csc_n + 0.20 * ar)
                    raw = GamePhaseMetrics(csc, csc_n, ar, pvcs_best, engage)

                    best_cp_abs = abs(_safe_best_cp(best.cp))
                    gp_phase = game_state.update(raw, best_cp_abs, has_mate)

                gphase = gp_phase

                # --- 候補ごとのPVContactSoon ---
                pvcs_by_move: Dict[str, float] = {}
                if current_position:
                    for c in safe_cands:
                        pvcs_by_move[c.move] = pv_contact_soon(current_position, c.pv, GP_PV_PLY)
                else:
                    for c in safe_cands:
                        pvcs_by_move[c.move] = 0.0

                # --- 本筋ランキング ---
                ranked_main = rank_candidates(safe_cands, feats_by_move, best, win_phase)

                # ===== bestmove（指す手）は強さ維持 =====
                chosen = best_engine_move

                for c in cands:
                    if c.move == chosen and (c.mate is not None and c.mate < 0):
                        info("⚠ bestmove shows self-mate in PV -> fallback to safest")
                        chosen = max(safe_cands, key=lambda x: x.cp).move
                        break

                # safety_check は特殊bestmove/不正トークンを踏まない
                if SAFETY_MS > 0 and current_position and is_usi_move_token(chosen) and (not is_special_bestmove(chosen)):
                    unsafe = safety_check_after_move(eng, current_position, chosen)
                    if unsafe:
                        info(f"☠ safety-check failed for {chosen} -> fallback")
                        alt = None
                        for c in ranked_main[: min(7, len(ranked_main))]:
                            if c.move == chosen:
                                continue
                            if (not is_usi_move_token(c.move)) or is_special_bestmove(c.move):
                                continue
                            if safety_check_after_move(eng, current_position, c.move):
                                continue
                            alt = c.move
                            break
                        if alt is not None:
                            chosen = alt
                        else:
                            info("☠ no alternative passed safety-check (keep chosen)")

                # ===== 候補の“配膳”: 本筋 + ATK + DEF（最大N手） =====
                used: Set[str] = set()
                picks: List[Tuple[str, Cand]] = []

                if any(c.move == best.move for c in safe_cands) and best.move not in used:
                    picks.append(("MAIN", best))
                    used.add(best.move)

                # 1) 本筋をまず3つ
                main_target = 3 if WATCH_SHOW_N >= 5 else 2
                for c in ranked_main:
                    if c.move in used:
                        continue
                    picks.append(("MAIN", c))
                    used.add(c.move)
                    if len([t for t, _ in picks if t == "MAIN"]) >= main_target:
                        break

                # 2) ATK候補
                atk_best: Optional[Cand] = None
                atk_best_score = -1.0

                atk_delta_cap = 1100 if stance in ("DEFICIT", "CRISIS") else 800
                for c in safe_cands:
                    if c.move in used:
                        continue
                    f = feats_by_move[c.move]
                    if f.self_risk >= 0.98:
                        continue

                    # Δキャップ（cp差）で“指し継ぎ”最低ラインを守る
                    if delta_cp(best, c) > atk_delta_cap:
                        continue

                    inertia = stability_sim(prev_atk_track_pv, c.pv, PREFIX_K) if prev_atk_track_pv else 0.0
                    s_atk = atk_score(f, pvcs_by_move.get(c.move, 0.0), gphase, stance, best, c)
                    s_atk = clamp(s_atk + 0.12 * inertia)

                    if gphase in ("BUILD", "PROBE") and s_atk < 0.42:
                        continue
                    if s_atk > atk_best_score:
                        atk_best_score = s_atk
                        atk_best = c

                if atk_best is not None:
                    picks.append(("ATK", atk_best))
                    used.add(atk_best.move)

                # 3) DEF候補（前回トラックで inertia）
                def_best: Optional[Cand] = None
                def_best_score = -1.0
                def_delta_cap = 700 if stance in ("ADV",) else 900

                for c in safe_cands:
                    if c.move in used:
                        continue
                    f = feats_by_move[c.move]

                    if stance == "CRISIS":
                        if f.self_risk > 0.65:
                            continue
                        if f.divergence > 0.62:
                            continue

                    if delta_cp(best, c) > def_delta_cap:
                        continue

                    inertia = 0.0
                    if prev_def_track_pv:
                        inertia = stability_sim(prev_def_track_pv, c.pv, PREFIX_K)

                    s_def = def_score(f, inertia, gphase, stance, best, c)

                    if f.self_risk >= 0.92:
                        continue

                    if s_def > def_best_score:
                        def_best_score = s_def
                        def_best = c

                if def_best is not None:
                    picks.append(("DEF", def_best))
                    used.add(def_best.move)

                # 4) 残りは本筋で埋める
                for c in ranked_main:
                    if len(picks) >= WATCH_SHOW_N:
                        break
                    if c.move in used:
                        continue
                    picks.append(("MAIN", c))
                    used.add(c.move)

                # 補助: まだ空きがあれば safe_cands から未使用の別手で埋める（同一手の重複はしない）
                if len(picks) < WATCH_SHOW_N:
                    extra_pool = sorted(
                        safe_cands,
                        key=lambda c: (c.depth, (0 if abs(c.cp) >= 20000 else c.cp)),
                        reverse=True,
                    )
                    for c in extra_pool:
                        if len(picks) >= WATCH_SHOW_N:
                            break
                        if c.move in used:
                            continue
                        picks.append(("MAIN", c))
                        used.add(c.move)

                # ===== intentsane スコア作成（存在チェック用 -inf 付き） =====
                # MAINスコア: ranked_main先頭を代表に
                main_rep = best
                f_main = feats_by_move.get(main_rep.move)
                if f_main is None:
                    main_rep = picks[0][1] if picks else ranked_main[0]
                    f_main = feats_by_move.get(main_rep.move)
                main_best_score = combined_display_score("MAIN", f_main, 0.0, 0.0) if f_main else 0.0

                atk_best_disp = NEG_INF
                if atk_best is not None:
                    f = feats_by_move[atk_best.move]
                    inertia = stability_sim(prev_atk_track_pv, atk_best.pv, PREFIX_K) if prev_atk_track_pv else 0.0
                    a = atk_score(f, pvcs_by_move.get(atk_best.move, 0.0), gphase, stance, best, atk_best)
                    a = clamp(a + 0.12 * inertia)
                    atk_best_disp = combined_display_score("ATK", f, a, 0.0)

                def_best_disp = NEG_INF
                if def_best is not None:
                    f = feats_by_move[def_best.move]
                    inertia = stability_sim(prev_def_track_pv, def_best.pv, PREFIX_K) if prev_def_track_pv else 0.0
                    d = def_score(f, inertia, gphase, stance, best, def_best)
                    def_best_disp = combined_display_score("DEF", f, 0.0, d)

                intent_scores = {
                    "MAIN": main_best_score,
                    "ATK": atk_best_disp if atk_best is not None else NEG_INF,
                    "DEF": def_best_disp if def_best is not None else NEG_INF,
                }
                available = {
                    "MAIN": True,               # MAINは常にある前提
                    "ATK": atk_best is not None,
                    "DEF": def_best is not None,
                }
                intent, intent_reason = intent_state.update(gphase, stance, intent_scores, available)
                info("⚠ CRISIS: DEF候補なし（受け筋なし）") if (stance == "CRISIS" and not available.get("DEF", False)) else None

                # ===== 表示 =====
                f_best = feats_by_move.get(best.move)
                if f_best:
                    unc = compute_uncertainty_for_display(cands, best)
                    cp_h = human_cp_for_display(stance_state.cp_ew, unc)
                    feel = "△=mate" if has_mate else f"△={cp_h:+d}"
                    trend_int = int(round(stance_state.dcp_ew))
                    trend_arrow = "↑" if trend_int >= TREND_DEADZONE else ("↓" if trend_int <= -TREND_DEADZONE else "→")
                    flow = f"flow={trend_arrow}{trend_int:+d}"
                    stance_disp = "WIN" if (has_mate or (abs(best.cp) >= 20000 and best.cp > 0)) else stance_text(stance)
                    info(
                        f"[TASO] stance={stance_disp}  "
                        f"intent={('本筋' if intent=='MAIN' else ('BULL' if intent=='ATK' else ('HEDGE' if intent=='DEF' else intent)))} ({intent_reason})  "
                        f"{feel}  {flow}"
                    )

                if short_mate and short_mate[0] <= MATE_SHORT_MAX:
                    info(f"⚡ short-mate seen: mate {short_mate[0]} (pv move {fmt_move(short_mate[1])})")

                if user_mode == MODE_PLAY:
                    pass

                elif user_mode == MODE_WATCH:
                    slot_map: Dict[int, Tuple[str, Cand]] = {
                        i: pair for i, pair in enumerate(picks[:WATCH_SHOW_N], start=1)
                    }

                    # 将棋HOMEでは後着行が上に積まれるため、逆順送信で画面上を 1..N にそろえる。
                    for i in range(WATCH_SHOW_N, 0, -1):
                        pair = slot_map.get(i)
                        if pair is None:
                            out(f"info multipv {i} string 候補{i}: (候補なし)")
                            continue

                        tag, c = pair
                        f = feats_by_move[c.move]
                        pvcs = pvcs_by_move.get(c.move, 0.0)

                        # inertia は「前回トラック」で表示（盛り防止）
                        inertia = stability_sim(prev_def_track_pv, c.pv, PREFIX_K) if prev_def_track_pv else 0.0

                        a = atk_score(f, pvcs, gphase, stance, best, c) if tag == "ATK" else 0.0
                        d = def_score(f, inertia, gphase, stance, best, c) if tag == "DEF" else 0.0
                        score = combined_display_score(tag, f, a, d)

                        mv_disp = fmt_move(c.move)
                        label = "本筋" if tag == "MAIN" else ("BULL" if tag == "ATK" else ("HEDGE" if tag == "DEF" else tag))

                        if abs(c.cp) >= 20000:
                            out(f"info multipv {i} string 候補{i}: {mv_disp}  tag={label}  score={score:.2f}  cp=mate-only")
                        else:
                            dc = delta_cp(best, c)
                            out(f"info multipv {i} string 候補{i}: {mv_disp}  tag={label}  score={score:.2f}  cp={c.cp:+d} Δ={-dc:+d}")

                    # --- WATCH: 読み筋タブ用にPVを候補数ぶんだけ1回だけ吐く（info stringは禁止） ---
                    for i in range(WATCH_SHOW_N, 0, -1):
                        pair = slot_map.get(i)
                        if pair is None:
                            if best.mate is not None:
                                out(f"info depth {best.depth} multipv {i} score mate {best.mate} pv {best.pv}")
                            else:
                                out(f"info depth {best.depth} multipv {i} score cp {best.cp} pv {best.pv}")
                            continue

                        _, c = pair
                        if c.mate is not None:
                            out(f"info depth {c.depth} multipv {i} score mate {c.mate} pv {c.pv}")
                        else:
                            out(f"info depth {c.depth} multipv {i} score cp {c.cp} pv {c.pv}")

                else:  # ANALYZE
                    for i, (tag, c) in enumerate(picks, start=1):
                        f = feats_by_move[c.move]
                        pvcs = pvcs_by_move.get(c.move, 0.0)
                        inertia = stability_sim(prev_def_track_pv, c.pv, PREFIX_K) if prev_def_track_pv else 0.0
                        a = atk_score(f, pvcs, gphase, stance, best, c)
                        d = def_score(f, inertia, gphase, stance, best, c)
                        score = combined_display_score(tag, f, a, d)

                        mv_disp = fmt_move(c.move)
                        label = "本筋" if tag == "MAIN" else ("BULL" if tag == "ATK" else ("HEDGE" if tag == "DEF" else tag))

                        if abs(c.cp) >= 20000:
                            info(
                                f"[{i}] {mv_disp} tag={label} score={score:.2f} cp=mate-only "
                                f"(win={disp_win(f.winability):.2f} atk={a:.2f} def={d:.2f} pvcs={pvcs:.2f} inertia={inertia:.2f} "
                                f"stab={f.stability:.2f} diff={f.opp_diff:.2f} risk={f.self_risk:.2f} init={f.initiative:.2f} div={f.divergence:.2f})"
                            )
                        else:
                            dc = delta_cp(best, c)
                            info(
                                f"[{i}] {mv_disp} tag={label} score={score:.2f} cp={c.cp:+d} Δ={-dc:+d} "
                                f"(win={disp_win(f.winability):.2f} atk={a:.2f} def={d:.2f} pvcs={pvcs:.2f} inertia={inertia:.2f} "
                                f"stab={f.stability:.2f} diff={f.opp_diff:.2f} risk={f.self_risk:.2f} init={f.initiative:.2f} div={f.divergence:.2f})"
                            )

                # ===== DEFトラック更新（表示後に更新する） =====
                if def_best is not None:
                    def_track_pv = def_best.pv
                    def_track_age = 0
                else:
                    def_track_age = prev_def_track_age + 1
                    if def_track_age >= 6:
                        def_track_pv = None

                if atk_best is not None:
                    atk_track_pv = atk_best.pv
                    atk_track_age = 0
                else:
                    atk_track_age = prev_atk_track_age + 1
                    if atk_track_age >= 6:
                        atk_track_pv = None

                out(f"bestmove {chosen}")
                continue

            if line == "quit":
                break

            eng.send(line)

    finally:
        eng.close()

if __name__ == "__main__":
    main()
