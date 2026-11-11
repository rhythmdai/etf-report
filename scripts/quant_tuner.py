#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REQ-179: Quant Tuner - 本地调参工具

临时 Flask 服务，提供滑块调参 + 一键回测 + NAV 曲线对比。
调完关掉，把最佳参数写回 YAML，正式页面继续走 file://。

用法:
    python scripts/quant_tuner.py
    # -> http://localhost:5181（端口见 config/tuner_config.json）
"""
import gc
import io
import json
import math
import re
import socket
import subprocess
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT_FALLBACK = next(parent for parent in Path(__file__).resolve().parents if (parent / "config").is_dir() and (parent / "scripts").is_dir())
SRC_DIR = PROJECT_ROOT_FALLBACK / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from etf_report.core.paths import ASSETS_DIR, CONFIG_DIR, DATA_DIR, PROJECT_ROOT, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from quant_factors import calc_ema, calc_rsi
from tuner_config import DEV_TUNER_PORT
from etf_report.core.quant_data_utils import load_etf_data, rebuild_weekly_from_daily
from etf_report.core import quant_contract as qc
from benchmark_data import load_hs300_daily_cached, build_hs300_pct, build_hs300_weekly, build_ma_trend_cache
from trading_calendar import load_trading_calendar, is_trading_day, last_trading_day

CONFIG_PATH = CONFIG_DIR / "quant_universe.yaml"
OVERRIDES_PATH = CONFIG_DIR / "quant_user_overrides.yaml"

# ============================================================
# Global preloaded data (loaded once at startup)
# ============================================================
CACHE = {
    "cfg": None,
    "all_daily": {},
    "all_weekly": {},
    "hs300_pct": None,
    "eq_weight_pct": None,
    "hs300_above_ma": {},  # {period: {date_str: bool}}
    "hs300_weekly_df": None,
    # Intraday cache: {code: {date, open, close, high, low, volume, amount}}
    # Single entry per code, always overwritten with latest.
    # Only populated during trading hours; cleared after CSV is updated with confirmed close.
    # Never written to CSV — keeps CSV pure (confirmed close data only).
    "intraday_cache": {},
    "intraday_date": None,  # date string the cache is for
    "ready": False,  # True after preload() completes — frontend polls this
}

# ── Async task progress ──
TASK_STORE = {}
TASK_LOCK = threading.Lock()

def _task_start(task_id, total, message=""):
    with TASK_LOCK:
        TASK_STORE[task_id] = {"pct": 0, "message": message, "status": "running", "total": total, "current": 0, "elapsed": 0, "started": time.time()}

def _task_update(task_id, current, message=""):
    with TASK_LOCK:
        t = TASK_STORE.get(task_id)
        if t:
            t["current"] = current
            t["pct"] = round(current / max(t["total"], 1) * 100, 1)
            t["message"] = message
            t["elapsed"] = round(time.time() - t["started"], 1)

def _task_done(task_id, result=None, error=None):
    with TASK_LOCK:
        t = TASK_STORE.get(task_id)
        if t:
            t["pct"] = 100
            t["status"] = "done" if not error else "error"
            t["message"] = error or "完成"
            t["elapsed"] = round(time.time() - t["started"], 1)
            t["result"] = result


def _deep_merge(base, override):
    """Merge override dict into base dict recursively (in-place)."""
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg.get("scoring") is None:
        cfg["scoring"] = {}

    # Merge user overrides (tuner /api/save output) — preserves base YAML comments
    if OVERRIDES_PATH.exists():
        with OVERRIDES_PATH.open("r", encoding="utf-8") as f:
            overrides = yaml.safe_load(f)
        if overrides:
            _deep_merge(cfg, overrides)

    return cfg


def _df_to_cleaning_input(daily_df):
    """Convert pandas daily DataFrame → dict format expected by data_cleaning."""
    return {
        "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in daily_df["date"]],
        "kline": [[float(r["open"]), float(r["close"]), float(r["low"]), float(r["high"])] for _, r in daily_df.iterrows()],
        "volumes": [int(v) for v in daily_df["volume"]] if "volume" in daily_df.columns else [],
        "amounts": [float(v) for v in daily_df["amount"]] if "amount" in daily_df.columns else [],
    }


def _apply_cleaning_to_df(daily_df, cleaned):
    """Write cleaned kline/volumes back into the DataFrame (in-place copy)."""
    out = daily_df.copy().reset_index(drop=True)
    kline = cleaned.get("kline", [])
    volumes = cleaned.get("volumes", [])
    for idx in range(len(out)):
        if idx < len(kline):
            o, c, l, h = kline[idx]
            out.at[idx, "open"]  = o
            out.at[idx, "close"] = c
            out.at[idx, "low"]   = l
            out.at[idx, "high"]  = h
        if idx < len(volumes) and "volume" in out.columns:
            out.at[idx, "volume"] = volumes[idx]
    # Note: "amount" (成交额) is intentionally NOT scaled.
    # 价格÷ratio × 成交量×ratio = 原始成交额，所以 amount 字段保持不变即可还原"金额视角"。
    return out


def _load_corporate_action_events():
    """Load auto-detected events file. Returns dict code→[events] or {}."""
    path = DATA_DIR / "corporate_action_events.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("events_by_code", {}) or {}
    except Exception as e:
        print(f"  [WARN] failed to load corporate_action_events.json: {e}")
        return {}


def preload():
    """One-time data loading at startup."""
    print("Preloading data...")
    cfg = load_config()
    CACHE["cfg"] = cfg

    load_trading_calendar()

    events_by_code = _load_corporate_action_events()

    for etf in cfg["universe"]:
        code = etf["code"]
        daily, weekly = load_etf_data(code)
        if daily is None:
            continue

        # NOTE: 数据源已切换为前复权(qfq)，价格已包含拆分调整，
        # 无需再对价格做 data_cleaning 的份额变动清洗（否则双重复权）。
        # data_cleaning 仅适用于不复权原始数据。
        # events = events_by_code.get(code) or []
        # if events:
        #     cleaning_input = _df_to_cleaning_input(daily)
        #     cleaned = run_data_cleaning_pipeline(cleaning_input, events)
        #     daily = _apply_cleaning_to_df(daily, cleaned)
        #     weekly = rebuild_weekly_from_daily(daily)

        CACHE["all_daily"][code] = daily
        CACHE["all_weekly"][code] = weekly

    print(f"  Loaded {len(CACHE['all_daily'])}/{len(cfg['universe'])} ETFs")

    # Precompute benchmarks
    _precompute_benchmarks()

    # Precompute F4 valuation scores from local CSV (no network)
    _precompute_valuation_scores()

    # Load market regimes for F4 regime-aware mapping
    _load_market_regimes()

    # Precompute heatmap returns (5d / 20d rolling pct_change)
    _precompute_heatmap_returns()

    # Load ETF metadata (AUM + top10 holdings)
    _load_etf_metadata()

    # Precompute factor caches for production preset (gam-0).
    # Parameters must match gam-0 preset exactly, otherwise the cache key
    # won't match and the first backtest will rebuild all caches from scratch.
    print("  Precomputing factor caches (gam-0)...")
    try:
        from quant_backtest import _precompute_factors, load_config as _load_cfg
        _gam0_cfg = _load_cfg("gam-0")
        _fcfg = _gam0_cfg.get("factors", {})
        _precompute_factors(
            dict(CACHE["all_daily"]), dict(CACHE["all_weekly"]),
            ema_period=_fcfg["ema"]["period_weeks"],
            vol_window=_fcfg["volume_ratio"]["window_days"],
            f7_window=_fcfg.get("log_return_deviation", {}).get("window_days", 17),
            f7_lookback=_fcfg.get("log_return_deviation", {}).get("lookback_days", 250),
            f7_min_days=_fcfg.get("log_return_deviation", {}).get("min_days", 60),
            f7_sigma_floor=_fcfg.get("log_return_deviation", {}).get("sigma_floor", 0.01),
            f1_active_days=_fcfg.get("f1_active_days", 1),
        )
        print("  Factor caches ready.")
    except Exception as e:
        print(f"  Factor cache precompute skipped: {e}")

    CACHE["ready"] = True
    print("Preload complete.\n")


# ============================================================
# Data refresh: intraday cache + post-market CSV update
# ============================================================

SINA_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

TENCENT_URL = "http://qt.gtimg.cn/q="
TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}

# A-share trading schedule (minutes from midnight)
MORNING_OPEN = 570    # 9:30
MORNING_CLOSE = 690   # 11:30
AFTERNOON_OPEN = 780  # 13:00
AFTERNOON_CLOSE = 900 # 15:00
TOTAL_TRADING_MINUTES = 240  # 4 hours
COOL_OFF_TIME = 910   # 15:10 — confirmed data available after this

# ── DM panel constants (single source of truth) ──
# Anomaly detection: single-day return threshold.  Daily return exceeding
# this absolute value is flagged as a surge (>0) or plunge (<0) anomaly.
# Synced to: /api/data_status.anomalyThreshold, tuner-right.html legend,
#            docs/design/data-management-panel.md
DM_ANOMALY_THRESHOLD = 0.25

def _trading_elapsed_minutes(now=None):
    """How many trading minutes have elapsed today. 0 before open, 240 after close."""
    n = now or datetime.now()
    minutes = n.hour * 60 + n.minute
    if minutes <= MORNING_OPEN:
        return 0
    if minutes <= MORNING_CLOSE:
        return minutes - MORNING_OPEN
    if minutes <= AFTERNOON_OPEN:
        return MORNING_CLOSE - MORNING_OPEN
    if minutes <= AFTERNOON_CLOSE:
        return (MORNING_CLOSE - MORNING_OPEN) + (minutes - AFTERNOON_OPEN)
    return TOTAL_TRADING_MINUTES


def _is_post_market(now=None):
    """True after 15:10 — confirmed close data is available from Tencent API."""
    n = now or datetime.now()
    return n.hour * 60 + n.minute >= COOL_OFF_TIME



def _fetch_sina_realtime(cfg):
    """Fetch real-time quotes for all ETFs from Sina API. One HTTP request, ~2s.
    Returns dict: {code: {name, open, prev_close, price, high, low, volume, amount, possibly_halted}}
    """
    symbols = []
    code_list = []
    for etf in cfg["universe"]:
        code = etf["code"]
        m = etf.get("market", "sz")
        symbols.append(f"{m}{code}")
        code_list.append(code)
    # BUG-060: also fetch benchmark ETFs for intraday data
    for code, mkt in [("510050","sh"),("510300","sh"),("510500","sh"),("159915","sz")]:
        symbols.append(f"{mkt}{code}")
        code_list.append(code)

    url = f"{SINA_URL}{','.join(symbols)}"
    try:
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
    except Exception as e:
        print(f"  [Sina] API failed: {e}")
        return {}

    results = {}
    lines = resp.text.strip().split("\n")
    for i, line in enumerate(lines):
        if "=" not in line:
            continue
        match = re.search(r'"([^"]*)"', line)
        if not match:
            continue
        data = match.group(1).split(",")
        if len(data) < 10:
            continue
        code = code_list[i] if i < len(code_list) else None
        if not code:
            continue
        try:
            opn = float(data[1]) if data[1] else 0
            prev = float(data[2]) if data[2] else 0
            price = float(data[3]) if data[3] else 0
            vol = int(float(data[8])) if data[8] else 0
            results[code] = {
                "name": data[0],
                "open": opn,
                "prev_close": prev,
                "price": price,
                "high": float(data[4]) if data[4] else 0,
                "low": float(data[5]) if data[5] else 0,
                "volume": vol,
                "amount": float(data[9]) if data[9] else 0,
                # L1 halt detection: open==prev_close and zero volume → hasn't started trading
                "possibly_halted": (opn > 0 and abs(opn - prev) < 0.001 and vol == 0),
            }
        except (ValueError, IndexError):
            continue
    return results


def _fetch_tencent_realtime(cfg):
    """Fetch real-time quotes from Tencent API (qt.gtimg.cn).
    Fallback when Sina (hq.sinajs.cn) is unreachable (e.g., CI runners outside China).
    Returns dict: {code: {name, open, prev_close, price, high, low, volume, amount, possibly_halted}}
    Same return contract as _fetch_sina_realtime — drop-in replacement.
    """
    symbols = []
    code_list = []
    for etf in cfg["universe"]:
        code = etf["code"]
        m = etf.get("market", "sz")
        symbols.append(f"{m}{code}")
        code_list.append(code)
    # Also fetch benchmark ETFs (same as Sina path)
    for code, mkt in [("510050", "sh"), ("510300", "sh"), ("510500", "sh"), ("159915", "sz")]:
        symbols.append(f"{mkt}{code}")
        code_list.append(code)

    url = f"{TENCENT_URL}{','.join(symbols)}"
    try:
        resp = requests.get(url, headers=TENCENT_HEADERS, timeout=10)
        resp.encoding = "gbk"
    except Exception as e:
        print(f"  [Tencent] API failed: {e}")
        return {}

    results = {}
    lines = resp.text.strip().split(";")
    for i, line in enumerate(lines):
        line = line.strip()
        if not line or "=" not in line:
            continue
        match = re.search(r'"([^"]*)"', line)
        if not match:
            continue
        data = match.group(1).split("~")
        if len(data) < 7:  # need at least through volume field
            continue
        code = code_list[i] if i < len(code_list) else None
        if not code:
            continue
        try:
            opn = float(data[5]) if data[5] else 0
            prev = float(data[4]) if data[4] else 0
            price = float(data[3]) if data[3] else 0
            # Tencent volume: 手 → 股 (×100)
            vol = int(float(data[6]) * 100) if data[6] else 0
            # Tencent amount: 万元 → 元 (×10000)
            amt = float(data[37]) * 10000 if len(data) > 37 and data[37] else 0
            # high/low: guarded access, fallback to price
            high = float(data[33]) if len(data) > 33 and data[33] else price
            low = float(data[34]) if len(data) > 34 and data[34] else price

            results[code] = {
                "name": data[1],
                "open": opn,
                "prev_close": prev,
                "price": price,
                "high": high,
                "low": low,
                "volume": vol,
                "amount": amt,
                # Same halt-detection heuristic as Sina: open==prev_close + zero volume
                "possibly_halted": (opn > 0 and abs(opn - prev) < 0.001 and vol == 0),
            }
        except (ValueError, IndexError):
            continue
    return results


# A-share intraday volume profile template (30-min slots, 8 slots for 4h session)
# Source: composite of A-share market studies — typical cumulative % at slot boundary.
# W-shape: heavy open, morning fade, lunch dip, afternoon trough, close peak.
# Slot:  9:30-10:00  10:00-10:30  10:30-11:00  11:00-11:30  13:00-13:30  13:30-14:00  14:00-14:30  14:30-15:00
_INTRADAY_SLOT_PCT = [0.28, 0.12, 0.08, 0.06, 0.10, 0.07, 0.09, 0.20]
# Cumulative profile: _INTRADAY_CUM[t] = sum of slots 0..t inclusive
_INTRADAY_CUM = []
_cum = 0.0
for _p in _INTRADAY_SLOT_PCT:
    _cum += _p
    _INTRADAY_CUM.append(_cum)
# _INTRADAY_CUM = [0.28, 0.40, 0.48, 0.54, 0.64, 0.71, 0.80, 1.00]


def _intraday_cumulative_pct(now=None):
    """Return the estimated cumulative volume fraction (0..1) at the current time.

    Interpolates linearly within the current 30-min slot using the profile template.
    """
    n = now or datetime.now()
    minutes = n.hour * 60 + n.minute + n.second / 60.0

    if minutes <= MORNING_OPEN:
        return 0.0
    if minutes >= AFTERNOON_CLOSE:
        return 1.0

    # Map current time to a slot index + fractional offset
    if minutes <= MORNING_CLOSE:
        # Morning session: 9:30 - 11:30 → slots 0-3
        elapsed = minutes - MORNING_OPEN
    elif minutes <= AFTERNOON_OPEN:
        # Lunch break — use morning close cumulative
        return _INTRADAY_CUM[3]
    else:
        # Afternoon session: 13:00 - 15:00 → slots 4-7
        elapsed = (MORNING_CLOSE - MORNING_OPEN) + (minutes - AFTERNOON_OPEN)

    slot = min(int(elapsed / 30), 7)
    frac = (elapsed - slot * 30) / 30.0

    prev_cum = _INTRADAY_CUM[slot - 1] if slot > 0 else 0.0
    curr_cum = _INTRADAY_CUM[slot]
    return prev_cum + (curr_cum - prev_cum) * frac


def _estimate_eod_volume(current_volume, now=None):
    """Estimate end-of-day volume from intraday volume using A-share profile template.

    Uses historical intraday volume distribution (W-shape) instead of naive linear
    proportion. The template captures the typical open peak, lunch dip, and close
    surge that linear extrapolation misses.
    """
    cum_pct = _intraday_cumulative_pct(now)
    if cum_pct <= 0.001 or cum_pct >= 0.999:
        return current_volume
    return int(current_volume / cum_pct)


def _run_incremental_fetch(cfg, end_date=None):
    """Run quant_data_fetcher incrementally for all ETFs. Updates CSV files on disk.
    end_date: passed through to fetch_etf_kline to cap the data range.
    Returns (ok_count, fail_count).
    """
    from quant_data_fetcher import update_single, FRESH_MARKER, _latest_allowed_date
    from datetime import datetime as _dt
    import time as _time

    # Global freshness check: require data up to latest allowed close date
    if FRESH_MARKER.exists():
        try:
            expected = _latest_allowed_date()
            if FRESH_MARKER.read_text().strip() >= expected:
                print(f"  (All {len(cfg['universe'])} ETFs already fresh, skipping fetch)")
                return len(cfg["universe"]), 0
        except Exception:
            pass

    ok, fail, fresh = 0, 0, 0
    for etf in cfg["universe"]:
        try:
            _, _, mode = update_single(etf, full=False, end_date=end_date)
            if mode == "fresh":
                fresh += 1
            else:
                ok += 1
                _time.sleep(1.0)  # only sleep when we actually hit the API
        except Exception as e:
            print(f"  [Fetch] {etf['code']} failed: {e}")
            fail += 1
    if fresh > 0:
        print(f"  (Skipped {fresh} already-fresh ETFs)")
    # Write freshness marker if all succeeded
    if fail == 0:
        try:
            FRESH_MARKER.parent.mkdir(parents=True, exist_ok=True)
            FRESH_MARKER.write_text(_dt.now().strftime("%Y-%m-%d"))
        except Exception:
            pass
    return ok, fail


def _reload_csv_to_cache(cfg):
    """Reload all CSV data from disk into CACHE (after fetcher has updated them)."""
    for etf in cfg["universe"]:
        code = etf["code"]
        daily, weekly = load_etf_data(code)
        if daily is not None:
            CACHE["all_daily"][code] = daily
            CACHE["all_weekly"][code] = weekly
    # Also reload benchmark indices (daily + weekly)
    for bm_code in ("000300", "000016", "000905", "399006"):
        bm_path = DATA_DIR / "quant" / f"{bm_code}_daily.csv"
        if bm_path.exists():
            try:
                bm_df = pd.read_csv(bm_path, parse_dates=["date"])
                if len(bm_df) > 0:
                    CACHE["all_daily"][bm_code] = bm_df
            except Exception:
                pass
        # Also load weekly for benchmarks
        bm_wpath = DATA_DIR / "quant" / f"{bm_code}_weekly.csv"
        if bm_wpath.exists():
            try:
                bm_wdf = pd.read_csv(bm_wpath, parse_dates=["date"])
                if len(bm_wdf) > 0:
                    CACHE["all_weekly"][bm_code] = bm_wdf
            except Exception:
                pass


def _sina_batch_append(cfg, date_str, rt_prices):
    """Append one day's Sina realtime data to all ETF CSVs (single-day only).
    Covers both universe ETFs AND benchmark ETFs (510050/510300/510500/159915).
    DESIGN PRINCIPLE: only call this post-market (>=15:10) with confirmed close data.
    Refuses to write if current time < COOL_OFF_TIME as defense-in-depth.
    """
    BM_CODES = ("510050", "510300", "510500", "159915")
    total_count = len(cfg["universe"]) + len(BM_CODES)
    uni_count = len(cfg["universe"])
    now = datetime.now()
    if now.hour * 60 + now.minute < COOL_OFF_TIME:
        print(f"  [Sina] REFUSED: intraday data must never touch CSV (current time < 15:10)")
        return 0, uni_count, 0, len(BM_CODES)
    # Validate date_str is a real date, not a time string
    if not __import__('re').match(r'^\d{4}-\d{2}-\d{2}$', str(date_str)):
        print(f"  [Sina] REFUSED: date_str is not a valid date: {date_str}")
        return 0, uni_count, 0, len(BM_CODES)
    # Audit log
    log_path = DATA_DIR / ".sina_write_log.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{now.isoformat()}] date={date_str} etfs={total_count}\n")
    except Exception:
        pass
    from quant_data_fetcher import append_csv, save_csv, DATA_DIR as QDATA_DIR
    import pandas as _pd

    def _append_one(code, rt):
        """Append one day's Sina data to daily CSV, rebuild weekly.

        UNIT CONTRACT: CSV `volume` is in LOTS (手, 1 lot = 100 shares) — see
        quant_data_fetcher._parse_tx_rows where `amount = close * vol * 100`.
        Sina realtime `rt["volume"]` is in SHARES (股), hence the /100 here.
        The ratio check refuses to write a row whose implied unit is wrong.
        """
        daily_path = QDATA_DIR / f"{code}_daily.csv"
        weekly_path = QDATA_DIR / f"{code}_weekly.csv"
        vol_lots = int(round(float(rt.get("volume", 0) or 0) / 100.0))
        amt = float(rt.get("amount", 0) or 0)
        px = float(rt.get("price", 0) or 0)
        if vol_lots > 0 and amt > 0 and px > 0:
            implied = amt / (vol_lots * px)
            if not (50.0 <= implied <= 200.0):
                raise ValueError(
                    f"volume unit check failed: amount/(volume*close)={implied:.2f}, expected ~100"
                )
        new_row = _pd.DataFrame([{
            "date": date_str, "open": rt["open"], "close": rt["price"],
            "high": rt["high"], "low": rt["low"],
            "volume": vol_lots, "amount": amt,
        }])
        append_csv(new_row, daily_path)
        full_daily = _pd.read_csv(daily_path)
        weekly = rebuild_weekly_from_daily(full_daily)
        save_csv(weekly, weekly_path)

    uni_ok, uni_fail = 0, 0
    for etf in cfg["universe"]:
        code = etf["code"]
        rt = rt_prices.get(code)
        if not rt or rt.get("price", 0) <= 0:
            uni_fail += 1
            continue
        try:
            _append_one(code, rt)
            uni_ok += 1
        except Exception as e:
            print(f"  [Sina] FAIL {code}: {e}")
            uni_fail += 1

    # BUG-061: also write benchmark ETF close to CSV (same Sina request,
    # data already in rt_prices — just wasn't being written).
    bm_ok, bm_fail = 0, 0
    for bm_code in BM_CODES:
        rt = rt_prices.get(bm_code)
        if not rt or rt.get("price", 0) <= 0:
            bm_fail += 1
            continue
        try:
            _append_one(bm_code, rt)
            bm_ok += 1
        except Exception as e:
            print(f"  [Sina] FAIL {bm_code}: {e}")
            bm_fail += 1

    return uni_ok, uni_fail, bm_ok, bm_fail


_SPLIT_EVENTS = {}       # code → [events], populated once per session
_SPLIT_CHECKED = False
_SPLIT_PENDING_REPAIR = set()  # codes where bridge detected a split that needs CSV repair


def _ensure_splits_detected(cfg):
    """First call: detect split events via AKShare + merge with registered JSON events.
    Subsequent calls: no-op (cached)."""
    global _SPLIT_EVENTS, _SPLIT_CHECKED
    if _SPLIT_CHECKED:
        return
    _SPLIT_CHECKED = True
    _SPLIT_EVENTS = _load_corporate_action_events()
    try:
        from etf_report.core.corporate_action_source import detect_corporate_action_events
        from datetime import date as _date
        today = _date.today()
        fresh = detect_corporate_action_events(
            [e["code"] for e in cfg["universe"]],
            _date(today.year, 1, 1), today
        )
        for code, evts in fresh.get("events_by_code", {}).items():
            existing = _SPLIT_EVENTS.setdefault(code, [])
            known = {(e.get("action"), e.get("ex_date")) for e in existing}
            for evt in evts:
                key = (evt.get("action"), evt.get("ex_date"))
                if key not in known:
                    existing.append(evt)
                    print(f"  [Split] DETECTED: {code} ratio={evt['ratio']} ex={evt['ex_date']} ({evt.get('note','')})")
    except Exception as exc:
        print(f"  [Split] detection skipped: {exc}")


def _detect_pending_splits_from_cache(cfg, today_str=None):
    """REQ-360: detect split ETFs that need CSV repair, without cleaning memory.
    Compares CSV last close vs intraday close. If ratio matches known split ratio,
    marks code in _SPLIT_PENDING_REPAIR for DM panel ⚠ icon.
    Does NOT modify CACHE — user sees the raw jump and clicks repair manually."""
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    intraday = bool(CACHE.get("intraday_cache"))
    if not intraday:
        return
    for etf in cfg["universe"]:
        code = etf["code"]
        splits = sorted(
            [e for e in _SPLIT_EVENTS.get(code, [])
             if e.get("action") == "share_split" and e.get("ex_date", "") <= today_str],
            key=lambda e: e.get("ex_date", ""), reverse=True
        )
        if not splits:
            continue
        split_ratio = splits[0]["ratio"]
        daily = CACHE["all_daily"].get(code)
        if daily is None:
            continue
        csv_close = float(daily["close"].iloc[-1])
        cached = CACHE["intraday_cache"].get(code)
        if not cached or cached.get("close", 0) <= 0:
            continue
        rt_close = float(cached["close"])
        r = csv_close / rt_close if rt_close > 0 else 1.0
        if 0.85 * split_ratio <= r <= 1.15 * split_ratio:
            _SPLIT_PENDING_REPAIR.add(code)
            print(f"  [Split] pending repair: {code} csv={csv_close:.3f} rt={rt_close:.3f} ratio={split_ratio}")


def _apply_split_memory_bridge(cfg):
    """In-memory cleaning for ETFs with unprocessed split events.
    Self-healing: compares CSV last close vs intraday close.
    If ratio ≈ split_ratio → need cleaning. If ratio ≈ 1.0 → already adjusted, skip."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    from etf_report.core.data_cleaning import run_data_cleaning_pipeline
    intraday = bool(CACHE.get("intraday_cache"))
    for etf in cfg["universe"]:
        code = etf["code"]
        splits = sorted(
            [e for e in _SPLIT_EVENTS.get(code, [])
             if e.get("action") == "share_split" and e.get("ex_date", "") <= today_str],
            key=lambda e: e.get("ex_date", ""), reverse=True
        )
        if not splits:
            continue
        split_ratio = splits[0]["ratio"]  # most recent split
        daily = CACHE["all_daily"].get(code)
        if daily is None:
            continue
        csv_close = float(daily["close"].iloc[-1])
        rt_close = 0.0
        # Compare with intraday close to decide if cleaning is needed
        need_clean = False
        if intraday:
            cached = CACHE["intraday_cache"].get(code)
            if cached and cached.get("close", 0) > 0:
                rt_close = float(cached["close"])
                r = csv_close / rt_close if rt_close > 0 else 1.0
                if 0.85 * split_ratio <= r <= 1.15 * split_ratio:
                    need_clean = True  # CSV still pre-split, need bridge
        if not need_clean:
            continue  # already adjusted or can't determine → skip (self-healing)
        print(f"  [Bridge] {code} split bridge (ratio={split_ratio}, csv={csv_close:.3f} rt={rt_close:.3f})")
        _SPLIT_PENDING_REPAIR.add(code)  # REQ-360: mark for DM panel ⚠ icon
        ci = _df_to_cleaning_input(daily)
        # Append intraday bar so boundary detection sees weekend gaps
        if cached and cached.get("date") == today_str:
            ci["dates"].append(cached["date"])
            ci["kline"].append([cached["open"], cached["close"], cached["low"], cached["high"]])
            if "volume" in daily.columns:
                ci["volumes"].append(int(cached.get("volume", 0) or 0))
        cleaned = run_data_cleaning_pipeline(ci, splits[:1])  # only the most recent split
        CACHE["all_daily"][code] = _apply_cleaning_to_df(daily, cleaned)
        CACHE["all_weekly"][code] = rebuild_weekly_from_daily(CACHE["all_daily"][code])


def _full_refetch_split_etfs(cfg):
    """Post-market: full refetch for ETFs with split events (qfq may have adjusted history by now).
    Only refetches if CSV last date < split ex_date + 1 (avoids redundant refetches)."""
    from quant_data_fetcher import update_single, get_last_date, DATA_DIR as QDATA_DIR
    import time as _time
    today_str = datetime.now().strftime("%Y-%m-%d")
    for etf in cfg["universe"]:
        code = etf["code"]
        splits = sorted(
            [e for e in _SPLIT_EVENTS.get(code, [])
             if e.get("action") == "share_split" and e.get("ex_date", "") <= today_str],
            key=lambda e: e.get("ex_date", ""), reverse=True
        )
        if not splits:
            continue
        split_date = splits[0]["ex_date"]
        from quant_data_fetcher import get_last_date, DATA_DIR as QDATA_DIR
        last = get_last_date(QDATA_DIR / f"{code}_daily.csv")
        if last and last >= split_date:
            continue  # already have data on/after split date
        print(f"  [Split] full refetch {code} (ex={split_date} ratio={splits[0]['ratio']})...")
        try:
            update_single(etf, full=True)
            _time.sleep(1.0)
            print(f"  [Split] {code} refetch OK")
        except Exception as exc:
            print(f"  [Split] {code} refetch FAILED: {exc}")
def _populate_intraday_cache(cfg, now, today_str, time_label, codes=None):
    """Populate intraday cache from Sina real-time API.
    codes: optional list of ETF codes to update; None = all universe ETFs.
    Returns (uni_updated, halted_count, bm_updated).
    """
    rt_prices = _fetch_sina_realtime(cfg)
    source_name = "Sina"
    if not rt_prices:
        print("  [Intraday] Sina returned empty, trying Tencent fallback...")
        rt_prices = _fetch_tencent_realtime(cfg)
        source_name = "Tencent"
        if not rt_prices:
            print("  [Intraday] Tencent also returned empty — no intraday data available")
            return 0, 0, 0, source_name

    # Retry missing ETFs only when Sina was the primary source.
    # Tencent is single-shot — retrying with the same source that already failed is pointless.
    if source_name == "Sina":
        missing = [e for e in cfg["universe"] if e["code"] not in rt_prices or rt_prices[e["code"]]["price"] <= 0]
        if missing:
            time.sleep(2)
            retry_cfg = dict(cfg)
            retry_cfg["universe"] = missing
            retry_rt = _fetch_sina_realtime(retry_cfg)
            for code, data in retry_rt.items():
                if data.get("price", 0) > 0:
                    rt_prices[code] = data

    qdii_codes = {e["code"] for e in cfg["universe"] if e.get("qdii")}
    prev_ic = CACHE.get("intraday_cache", {})
    code_set = set(codes) if codes else None

    if not CACHE.get("intraday_cache"):
        CACHE["intraday_cache"] = {}
    CACHE["intraday_date"] = today_str

    updated = 0
    halted_count = 0
    for etf in cfg["universe"]:
        code = etf["code"]
        if code_set and code not in code_set:
            continue
        rt = rt_prices.get(code)
        if not rt or rt["price"] <= 0:
            continue

        is_qdii = code in qdii_codes
        vol = rt["volume"]
        amt = rt["amount"]

        halted = False
        if is_qdii:
            if rt.get("possibly_halted"):
                halted = True
            elif code in prev_ic:
                prev = prev_ic[code]
                if (prev.get("halted") and
                    abs(rt["price"] - prev["close"]) < 0.001 and
                    vol == prev.get("raw_volume", 0)):
                    halted = True

        raw_vol = vol
        raw_amt = amt
        if not halted and vol > 0:
            vol = _estimate_eod_volume(vol, now)
            if amt > 0:
                amt = _estimate_eod_volume(int(amt), now)

        if halted:
            halted_count += 1

        CACHE["intraday_cache"][code] = {
            "date": today_str, "time": time_label,
            "open": rt["open"], "close": rt["price"],
            "high": rt["high"], "low": rt["low"],
            "volume": vol, "amount": amt,
            "raw_volume": raw_vol, "raw_amount": raw_amt,
            "halted": halted,
        }
        updated += 1

    # BUG-061: also store benchmark ETF (510050/510300/510500/159915) intraday data.
    # _fetch_sina_realtime already fetches them; _populate_intraday_cache just needs to write.
    bm_updated = 0
    BM_CODES = ("510050", "510300", "510500", "159915")
    for bm_code in BM_CODES:
        rt = rt_prices.get(bm_code)
        if not rt or rt["price"] <= 0:
            continue
        raw_vol = rt.get("volume", 0)
        vol = _estimate_eod_volume(raw_vol, now) if raw_vol > 0 else 0
        CACHE["intraday_cache"][bm_code] = {
            "date": today_str, "time": time_label,
            "open": rt.get("open", rt["price"]),
            "close": rt["price"],
            "high": rt.get("high", rt["price"]),
            "low": rt.get("low", rt["price"]),
            "volume": vol, "amount": rt.get("amount", 0),
            "raw_volume": raw_vol, "raw_amount": rt.get("amount", 0),
            "halted": False,
        }
        bm_updated += 1

    return updated, halted_count, bm_updated, source_name


def refresh_data():
    """Main refresh entry point. Called by /api/refresh_data.

    Rule (REQ-196):
      - Pre-market (before 09:30): same as non-trading — incremental fetch to backfill
        historical gaps, no intraday data yet.
      - Intraday (09:30–15:10, trading day): incremental fetch with end_date=today
        (excludes today's incomplete bar from the API), then add today's live data
        via intraday cache.  CSV stays clean.
      - Post-market (>=15:10): incremental fetch → CSV (now has confirmed close).
        Clear intraday cache.
      - Non-trading day: incremental fetch to fill historical gaps.  No intraday data.

    Intraday cache is a single flat dict, always overwritten.
    Computation code merges cache into daily_df on-the-fly (see _get_daily_with_cache).
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    cfg = CACHE.get("cfg") or load_config()
    time_label = now.strftime("%H:%M")

    post_market = _is_post_market(now)
    trading = is_trading_day(now)
    pre_market = trading and now.hour * 60 + now.minute < MORNING_OPEN
    intraday = trading and not pre_market and not post_market

    ok, fail = 0, 0
    uni_ok = bm_ok = 0
    ran_fetch = False
    used_sina = False

    # ── Single-day Sina fast path (post-market only) ──
    # DESIGN PRINCIPLE: intraday data must NEVER touch CSV. Only post-market confirmed close.
    # Always try Sina — _fetch_sina_realtime pulls all 58 ETFs in one request.
    # If it succeeds, gap detection below uses prev_td (yesterday) as the target, so only
    # ETFs with multi-day gaps trigger Tencent.  If Sina fails, Tencent fills everything.
    prev_td = last_trading_day(now) if post_market else None
    if post_market:
        now_minutes = now.hour * 60 + now.minute
        if now_minutes >= COOL_OFF_TIME:
            print(f"  [Refresh] Post-market — trying Sina fast path...")
            try:
                rt = _fetch_sina_realtime(cfg)
                if rt:
                    uni_ok, uni_fail, bm_ok, bm_fail = _sina_batch_append(cfg, today_str, rt)
                    fail = uni_fail + bm_fail
                    if fail == 0:
                        used_sina = True
                        ok = uni_ok + bm_ok
                        _reload_csv_to_cache(cfg)
                        print(f"  [Refresh] Sina fast path: {uni_ok}+{bm_ok} OK, ~2s")
                    else:
                        ok = uni_ok + bm_ok
                        print(f"  [Refresh] Sina fast path partial: {uni_ok}+{bm_ok} OK, {fail} fail — falling back")
            except Exception as e:
                print(f"  [Refresh] Sina fast path crashed: {e} — falling back to incremental")
            else:
                print(f"  [Refresh] Sina API failed — falling back to incremental")

    # ── Step 1: Fetch today's data (Sina fast path or intraday cache) ──
    # ── Step 2: Always check for historical gaps and fill them ──
    from quant_data_fetcher import get_last_date, DATA_DIR as QDATA_DIR, _latest_allowed_date, update_single, FRESH_MARKER
    gap_ok, gap_fail = 0, 0
    bm_gap_ok, bm_gap_fail = 0, 0
    BM_CODES = ("510050", "510300", "510500", "159915")
    _bm_entries = [
        {"code": "510050", "name": "上证50", "market": "sh"},
        {"code": "510300", "name": "沪深300", "market": "sh"},
        {"code": "510500", "name": "中证500", "market": "sh"},
        {"code": "159915", "name": "创业板指", "market": "sz"},
    ]

    need_gap_fill = False
    # Target date for gap detection:
    #   Used Sina → today's close written to all 58 CSVs; only detect gaps BEFORE prev_td
    #   No Sina    → need Tencent to fill today's data (and any earlier gaps)
    _gap_target = prev_td if (used_sina and prev_td) else (_latest_allowed_date(now) if intraday else today_str)
    for etf in cfg["universe"]:
        last = get_last_date(QDATA_DIR / f"{etf['code']}_daily.csv")
        if last is None or last < _gap_target:
            need_gap_fill = True
            break
    if not need_gap_fill:
        for bm_code in BM_CODES:
            last = get_last_date(QDATA_DIR / f"{bm_code}_daily.csv")
            if last is None or last < _gap_target:
                need_gap_fill = True
                break

    if need_gap_fill:
        # Clear freshness marker — we just detected gaps, so it's stale.
        # _run_incremental_fetch trusts the marker and would skip everything.
        if FRESH_MARKER.exists():
            FRESH_MARKER.unlink()
        print(f"  [Refresh] Historical gaps detected — running incremental fetch...")
        gap_ok, gap_fail = _run_incremental_fetch(cfg)
        ran_fetch = True
        _reload_csv_to_cache(cfg)
        # Also update benchmark ETFs (unified with universe gap fill)
        for bm in _bm_entries:
            last = get_last_date(QDATA_DIR / f"{bm['code']}_daily.csv")
            if last is not None and last >= _gap_target:
                continue
            try:
                _, _, _ = update_single(bm, full=False)
                bm_gap_ok += 1
            except Exception as e:
                print(f"    {bm['code']}: FAIL ({e})")
                bm_gap_fail += 1
    elif not used_sina and not intraday:
        # No gaps, not intraday, not already fetched via Sina → still need fetch
        print(f"  [Refresh] Running incremental fetch...")
        gap_ok, gap_fail = _run_incremental_fetch(cfg)
        ran_fetch = True
        _reload_csv_to_cache(cfg)
        for bm in _bm_entries:
            try:
                _, _, _ = update_single(bm, full=False)
                bm_gap_ok += 1
            except Exception as e:
                print(f"    {bm['code']}: FAIL ({e})")
                bm_gap_fail += 1
    elif intraday:
        print(f"  [Refresh] Intraday — CSV write skipped (live data → cache only)")
        _reload_csv_to_cache(cfg)

    if used_sina:
        # uni_ok/bm_ok already set from _sina_batch_append unpacking
        gap_msg = f"Sina收盘 | {uni_ok}+{bm_ok} OK, {fail} fail"
    elif ran_fetch:
        gap_msg = f"Tencent增量 | {gap_ok}+{bm_gap_ok} OK, {gap_fail+bm_gap_fail} fail"
    else:
        gap_msg = ""

    _ensure_splits_detected(cfg)
    _apply_split_memory_bridge(cfg)

    if post_market:
        _full_refetch_split_etfs(cfg)
        _reload_csv_to_cache(cfg)

    _precompute_benchmarks()
    _precompute_valuation_scores()
    _load_market_regimes()
    _precompute_heatmap_returns()

    # BUG-057: refresh index data too (not just ETF). Missing index CSVs → stale MA signal.
    try:
        hs = load_hs300_daily_cached()
        CACHE["hs300_daily_df"] = hs
        CACHE["hs300_weekly_df"] = build_hs300_weekly(hs)
        CACHE["hs300_above_ma"] = {}  # force rebuild on next backtest
        print("  [Refresh] HS300 index data refreshed")
    except Exception as e:
        print(f"  [Refresh] HS300 refresh failed (non-fatal): {e}")

    # ── Post-market (>=15:10) ──
    if post_market:
        CACHE["intraday_cache"] = {}
        CACHE["intraday_date"] = None
        msg = f"{time_label} | {gap_msg}"
        print(f"  [Refresh] Post-market — {msg}")
        return {
            "status": "confirmed",
            "message": msg,
            "fetchOk": ok,
            "fetchFail": fail,
            "date": today_str,
            "time": time_label,
        }

    # ── Non-trading day / Pre-market ──
    if not trading or pre_market:
        ltd = last_trading_day(now)
        tag = "Pre-market" if pre_market else "Non-trading day"
        msg = f"{time_label} | {tag} | CSV up to {ltd} | {gap_msg}"
        print(f"  [Refresh] {msg}")
        return {
            "status": "confirmed",
            "message": msg,
            "fetchOk": ok,
            "fetchFail": fail,
            "date": ltd,
            "time": time_label,
        }

    # ── Intraday (09:30–15:10): historical gaps filled + intraday cache ──
    uni_updated, halted_count, bm_updated, source_name = _populate_intraday_cache(cfg, now, today_str, time_label)
    vol_note = " (vol est. EOD)" if _trading_elapsed_minutes(now) < TOTAL_TRADING_MINUTES else ""
    halt_note = f" | {halted_count} halted" if halted_count > 0 else ""
    msg = f"{time_label} | {source_name}实时 | {uni_updated}+{bm_updated} ETFs{vol_note}{halt_note} | {gap_msg}"
    print(f"  [Refresh] {msg}")

    return {
        "status": "intraday",
        "message": msg,
        "count": uni_updated + bm_updated,
        "date": today_str,
        "time": time_label,
        "haltedCount": halted_count,
    }


def _get_daily_with_cache(code):
    """Get daily DataFrame for an ETF, with intraday cache appended if available.
    Does NOT modify CACHE["all_daily"] — returns a new DataFrame.
    If cache exists and its date is newer than CSV's last date, appends one row.
    If cache date equals CSV's last date, replaces the last row.
    """
    daily_df = CACHE["all_daily"].get(code)
    if daily_df is None:
        return None

    cached = CACHE["intraday_cache"].get(code)
    if not cached:
        return daily_df

    cache_date = cached["date"]
    last_date = daily_df["date"].iloc[-1]
    last_str = last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date)[:10]

    df = daily_df.copy()

    if last_str == cache_date:
        # Replace last row with cache data
        df.at[df.index[-1], "open"] = cached["open"]
        df.at[df.index[-1], "close"] = cached["close"]
        df.at[df.index[-1], "high"] = cached["high"]
        df.at[df.index[-1], "low"] = cached["low"]
        if "volume" in df.columns:
            df.at[df.index[-1], "volume"] = cached["volume"]
        if "amount" in df.columns:
            df.at[df.index[-1], "amount"] = cached["amount"]
    elif last_str < cache_date:
        # Append new row
        new_row = pd.DataFrame([{
            "date": pd.Timestamp(cache_date),
            "open": cached["open"],
            "close": cached["close"],
            "high": cached["high"],
            "low": cached["low"],
            "volume": cached["volume"],
            "amount": cached["amount"],
        }])
        df = pd.concat([df, new_row], ignore_index=True)

    return df


def _get_benchmark_daily_with_cache(code):
    """Like _get_daily_with_cache but for benchmark ETFs not in CACHE['all_daily'].
    Loads daily from CSV, then merges intraday cache if available.
    BUG-061: gives HS300 MA trend calculation access to today's intraday price."""
    import pandas as _pd
    from quant_data_fetcher import DATA_DIR as _QDATA_DIR

    csv_path = _QDATA_DIR / f"{code}_daily.csv"
    if not csv_path.exists():
        return None

    daily_df = _pd.read_csv(csv_path, parse_dates=["date"])

    cached = CACHE.get("intraday_cache", {}).get(code)
    if not cached:
        return daily_df

    cache_date = cached["date"]
    last_date = daily_df["date"].iloc[-1]
    last_str = last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date)[:10]

    df = daily_df.copy()

    if last_str == cache_date:
        df.at[df.index[-1], "open"] = cached["open"]
        df.at[df.index[-1], "close"] = cached["close"]
        df.at[df.index[-1], "high"] = cached["high"]
        df.at[df.index[-1], "low"] = cached["low"]
        if "volume" in df.columns:
            df.at[df.index[-1], "volume"] = cached["volume"]
        if "amount" in df.columns:
            df.at[df.index[-1], "amount"] = cached["amount"]
    elif last_str < cache_date:
        new_row = _pd.DataFrame([{
            "date": _pd.Timestamp(cache_date),
            "open": cached["open"],
            "close": cached["close"],
            "high": cached["high"],
            "low": cached["low"],
            "volume": cached["volume"],
            "amount": cached["amount"],
        }])
        df = _pd.concat([df, new_row], ignore_index=True)

    return df


def _get_weekly_with_cache(code):
    """Get weekly DataFrame for an ETF, rebuilt from _get_daily_with_cache if cache exists."""
    if code not in CACHE["intraday_cache"]:
        return CACHE["all_weekly"].get(code)

    daily_df = _get_daily_with_cache(code)
    if daily_df is None or len(daily_df) == 0:
        return CACHE["all_weekly"].get(code)

    return rebuild_weekly_from_daily(daily_df)


def _precompute_benchmarks():
    """Compute HS300 + equal-weight benchmarks once."""
    all_daily = CACHE["all_daily"]
    if not all_daily:
        return

    close_series = []
    for code, df in all_daily.items():
        s = df.set_index("date")["close"].astype(float).sort_index().pct_change()
        s.name = code
        close_series.append(s)
    if not close_series:
        return

    returns_df = pd.concat(close_series, axis=1).sort_index()
    eq_returns = returns_df.mean(axis=1, skipna=True).fillna(0.0)
    eq_nav_series = (1.0 + eq_returns).cumprod() * 100.0
    date_strs = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in eq_nav_series.index]

    CACHE["eq_weight_pct"] = [round(float(v), 2) for v in eq_nav_series]
    CACHE["eq_dates"] = date_strs

    try:
        hs = load_hs300_daily_cached()
        CACHE["hs300_pct"] = build_hs300_pct(hs, date_strs)
        CACHE["hs300_daily_df"] = hs
        CACHE["hs300_weekly_df"] = build_hs300_weekly(hs)
        _build_ma_trend_cache(20)
    except Exception as e:
        print(f"  [WARN] HS300 data load failed: {e}")
        CACHE["hs300_pct"] = None
        CACHE["hs300_daily_df"] = None
        CACHE["hs300_weekly_df"] = None

    # HS300 weekly DataFrame for residual momentum (reuse if available)
    if CACHE.get("hs300_weekly_df") is not None:
        CACHE["hs300_weekly"] = CACHE["hs300_weekly_df"].rename(columns={"close": "close"})
        print(f"  HS300 weekly: {len(CACHE['hs300_weekly'])} bars for residual momentum")
    else:
        CACHE["hs300_weekly"] = None


def _build_ma_trend_cache(period):
    """Build or retrieve HS300 MA trend lookup for a given period.

    Weekly bars are dated to the last trading day of their ISO week
    (see build_index_weekly), so incomplete current weeks naturally
    map into the future and merge_asof skips them — regime is stable
    within a week regardless of intraday price movements.
    """
    daily = CACHE.get("hs300_daily_df")
    weekly = CACHE.get("hs300_weekly_df")

    ma_cache = CACHE.get("hs300_above_ma", {})
    if period in ma_cache:
        return ma_cache[period]
    result = build_ma_trend_cache(daily, weekly, period)
    if result is None:
        return None
    ma_cache[period] = result
    CACHE["hs300_above_ma"] = ma_cache
    above_map = result.get("above", {})
    rising_map = result.get("ma_rising", {})
    above_count = sum(above_map.values())
    rising_count = sum(v for v in rising_map.values() if v is not None)
    print(f"  HS300 MA{period} trend: {len(above_map)} days, above={above_count}, below={len(above_map)-above_count}, rising={rising_count}")
    return result


def _precompute_valuation_scores():
    """Compute F4 valuation scores from local CSV + anchors (no network).
    Score = 100 - historical_percentile (low valuation = high score)."""
    try:
        from valuation_engine import ValuationEngine
        engine = ValuationEngine()
        val_scores = {}
        history_dir = DATA_DIR / "valuation_history"

        for etf_code in engine.list_etfs():
            cfg = engine.get_etf_config(etf_code)
            if cfg is None:
                continue

            metric = cfg["primary_metric"]
            # Read latest value from local CSV (no network)
            current_value = None

            if metric == "pb":
                csv_path = history_dir / f"{etf_code}_pb.csv"
            else:
                csv_path = history_dir / f"{etf_code}.csv"

            if csv_path.exists():
                try:
                    with csv_path.open("r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in reversed(lines):
                        parts = line.strip().split(",")
                        if len(parts) >= 2 and parts[1]:
                            try:
                                v = float(parts[1])
                                if v > 0:
                                    current_value = v
                                    break
                            except ValueError:
                                continue
                except Exception:
                    pass

            if current_value is not None:
                result = engine.evaluate(etf_code, current_value)
                if result and result.get("percentile") is not None:
                    val_scores[etf_code] = 100.0 - result["percentile"]
                    continue

            val_scores[etf_code] = 50.0  # neutral fallback

        CACHE["val_scores"] = val_scores
        print(f"  F4 valuation scores: {len(val_scores)} ETFs precomputed")
    except Exception as e:
        print(f"  [WARN] F4 valuation precompute failed: {e}")
        CACHE["val_scores"] = {}


def _load_market_regimes():
    """Load market_regimes.json for F4 regime-aware mapping.
    Format: {"regimes": [{"date": "2024-01-02", "regime": "choppy_range"}, ...]}
    Stored as dict: date_str -> regime_str for fast lookup.
    """
    path = DATA_DIR / "market_regimes.json"
    if not path.exists():
        print("  [WARN] market_regimes.json not found, F4 will use default regime")
        CACHE["market_regimes"] = {}
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        regimes_list = data.get("regimes", [])
        CACHE["market_regimes"] = {r["date"]: r["regime"] for r in regimes_list}
        print(f"  Market regimes: {len(CACHE['market_regimes'])} days loaded")
    except Exception as e:
        print(f"  [WARN] Failed to load market_regimes.json: {e}")
        CACHE["market_regimes"] = {}


def _load_etf_metadata():
    """Load ETF metadata (AUM + top10 holdings) from disk into CACHE."""
    path = DATA_DIR / "quant" / "etf_metadata.json"
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                CACHE["etf_metadata"] = json.load(f)
            print(f"  ETF metadata: {len(CACHE['etf_metadata'])} ETFs loaded")
        else:
            CACHE["etf_metadata"] = {}
            print("  ETF metadata: no file, skipping")
    except Exception as e:
        CACHE["etf_metadata"] = {}
        print(f"  ETF metadata: load failed ({e})")
    _load_stock_metadata()


def _load_stock_metadata():
    """Load stock business descriptions from disk into CACHE."""
    path = DATA_DIR / "quant" / "stock_metadata.json"
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                CACHE["stock_metadata"] = json.load(f)
            print(f"  Stock metadata: {len(CACHE['stock_metadata'])} stocks loaded")
        else:
            CACHE["stock_metadata"] = {}
    except Exception as e:
        CACHE["stock_metadata"] = {}
        print(f"  Stock metadata: load failed ({e})")


def _precompute_heatmap_returns(lookbacks=(5, 20)):
    """Precompute N-day rolling returns for all ETFs.
    Called once at startup; subsequent requests read CACHE["heatmap"].
    Set CACHE["heatmap"] = { "5": {code: {date_str: ret}}, "20": {...} }
    Uses date column as index so pct_change aligns on calendar dates.
    """
    all_daily = CACHE.get("all_daily", {})
    if not all_daily:
        return
    result = {}
    for lb in lookbacks:
        lb_key = str(lb)
        result[lb_key] = {}
        for code, df in all_daily.items():
            # Set date as index so pct_change aligns on trading days
            close = df.set_index("date")["close"].astype(float)
            ret = close.pct_change(lb).dropna()
            # Convert to dict {date_str: return_value}
            result[lb_key][code] = {str(d): float(v) for d, v in zip(ret.index, ret.values)}
    CACHE["heatmap"] = result
    total = sum(len(v) for v in result.values())
    print(f"  Heatmap returns precomputed: {len(result)} lookbacks, {total} series")


def _weight_total_pct(params):
    return qc.weight_total_pct(params)


def _parse_universe_filter(params):
    return qc.parse_universe_filter(params)


def _validate_tuner_params(params):
    return qc.validate_tuner_params(params)


def _compute_etf_contributions(trade_log, signal_history, etf_name_map, etf_sector_map):
    """Aggregate per-ETF contribution metrics from trade log and signal history.

    Returns dict: {code: {name, sector, selectedCount, selectionRate, avgWeight,
                          avgHoldDays, firstSelected, totalPnlPct, avgTradePnl,
                          tradeCount, winRate, payoffRatio, sectorShare,
                          topCooccurrence, cooccurrenceCount, phaseRates, trend}}
    """
    total_signals = max(len(signal_history), 1)
    all_codes = set(etf_name_map.keys())  # all ETFs in universe, including never-selected
    for s in signal_history:
        for code in s.get("top6", []):
            all_codes.add(code)
    for t in trade_log:
        all_codes.add(t["code"])

    # ── Phase definitions ──
    phases = [
        ("2020-2021", "2020-01-01", "2021-12-31"),
        ("2022",      "2022-01-01", "2022-12-31"),
        ("2023",      "2023-01-01", "2023-12-31"),
        ("2024",      "2024-01-01", "2024-12-31"),
        ("2025",      "2025-01-01", "2025-12-31"),
        ("2026",      "2026-01-01", "2026-12-31"),
    ]

    # ── Per-code aggregation ──
    result = {}
    for code in sorted(all_codes):
        name = etf_name_map.get(code, code)
        sector = etf_sector_map.get(code, "")

        # --- Participation (from signal_history) ---
        selected_count = 0
        available_signals = 0
        weight_sum = 0.0
        weight_n = 0
        hold_streaks = []
        current_streak = 0
        first_selected = None

        for s in signal_history:
            top6 = s.get("top6", [])
            positions = s.get("positions", {})
            scores = s.get("scores", {})
            if code in scores:
                available_signals += 1
            date_str = str(s.get("date", ""))[:10]
            in_top6 = code in top6
            if in_top6:
                selected_count += 1
                w = positions.get(code, 0)
                weight_sum += w * 100  # convert to %
                weight_n += 1
                current_streak += 1
                if first_selected is None:
                    first_selected = date_str
            else:
                if current_streak > 0:
                    hold_streaks.append(current_streak)
                current_streak = 0
        if current_streak > 0:
            hold_streaks.append(current_streak)

        effective = max(available_signals, 1)
        selection_rate = round(selected_count / effective * 100, 1)
        avg_weight = round(weight_sum / max(weight_n, 1), 1)
        avg_hold = round(sum(hold_streaks) / max(len(hold_streaks), 1), 1)

        # --- Performance (from trade_log) ---
        code_trades = [t for t in trade_log if t["code"] == code]
        if code_trades:
            winners = [t for t in code_trades if t["pnl_pct"] > 0]
            losers = [t for t in code_trades if t["pnl_pct"] <= 0]
            total_pnl = round(sum(t["pnl_pct"] for t in code_trades), 2)
            avg_pnl = round(total_pnl / len(code_trades), 2)
            wr = round(len(winners) / len(code_trades) * 100, 1)
            aw = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
            al = abs(sum(t["pnl_pct"] for t in losers) / len(losers)) if losers else 0
            payoff = round(aw / al, 2) if al > 0 else 999.0
        else:
            total_pnl, avg_pnl, wr, payoff = 0.0, 0.0, 0.0, 0.0

        # --- Structural ---
        # Sector share: this ETF's selection count / total selections in its sector
        sector_total = sum(
            1 for s in signal_history
            for c in s.get("top6", [])
            if etf_sector_map.get(c, "") == sector
        )
        sector_share = round(selected_count / max(sector_total, 1) * 100, 1)

        # Co-occurrence: most common co-selected ETF
        co_occur = {}
        for s in signal_history:
            top6 = s.get("top6", [])
            if code in top6:
                for other in top6:
                    if other != code:
                        co_occur[other] = co_occur.get(other, 0) + 1
        top_co = max(co_occur, key=co_occur.get) if co_occur else ""
        top_co_name = etf_name_map.get(top_co, top_co) if top_co else ""
        top_co_n = co_occur.get(top_co, 0) if top_co else 0

        # --- Lifecycle (phase rates) ---
        phase_rates = {}
        for phase_label, p_start, p_end in phases:
            n = sum(1 for s in signal_history
                    if p_start <= str(s.get("date", ""))[:10] <= p_end)
            c = sum(1 for s in signal_history
                    if p_start <= str(s.get("date", ""))[:10] <= p_end
                    and code in s.get("top6", []))
            phase_rates[phase_label] = round(c / max(n, 1) * 100, 1) if n > 0 else 0

        # Trend: compare last 2 phases with first 2
        recent = sum(phase_rates.get(p[0], 0) for p in phases[-2:])
        early = sum(phase_rates.get(p[0], 0) for p in phases[:2])
        if recent > early + 5:
            trend = "rising"
        elif recent < early - 5:
            trend = "declining"
        else:
            trend = "stable"

        # Observation period: < 80 trading days since listing
        meta = (CACHE.get("etf_metadata") or {}).get(code, {})
        listing_str = meta.get("listing_date", "")
        trading_days = 0
        observation = False
        if listing_str:
            try:
                listing_dt = pd.Timestamp(listing_str)
                end_dt = pd.Timestamp(end_date_str) if (end_date_str := str(signal_history[-1].get("date", ""))[:10]) else None
                if end_dt:
                    trading_days = max(0, len([d for d in pd.date_range(listing_dt, end_dt) if d.weekday() < 5]))
                observation = trading_days < 80
            except Exception:
                pass

        result[code] = {
            "name": name,
            "sector": sector,
            "observation": observation,
            "tradingDays": trading_days,
            "selectedCount": selected_count,
            "availableSignals": available_signals,
            "selectionRate": selection_rate,
            "avgWeight": avg_weight,
            "avgHoldDays": avg_hold,
            "firstSelected": first_selected or "",
            "totalPnlPct": total_pnl,
            "avgTradePnl": avg_pnl,
            "tradeCount": len(code_trades),
            "winRate": wr,
            "payoffRatio": payoff,
            "sectorShare": sector_share,
            "topCooccurrence": top_co,
            "topCoName": top_co_name,
            "cooccurrenceCount": top_co_n,
            "phaseRates": phase_rates,
            "trend": trend,
        }
    return result


def run_tuner_backtest(params, progress_callback=None):
    """Tuner wrapper → unified quant_backtest.run_backtest().

    Builds config_override from frontend params, prepares preloaded data from
    CACHE, calls the shared backtest engine, and formats output for the frontend.
    """
    validation_error = _validate_tuner_params(params)
    if validation_error:
        return {"error": validation_error}

    t0 = time.time()
    from quant_backtest import run_backtest as _run_backtest

    # ── Build config_override from shared parameter contract ──
    config_override = qc.tuner_params_to_config_override(params)

    # ── Prepare preloaded data ──
    all_daily = dict(CACHE["all_daily"])
    all_weekly = dict(CACHE["all_weekly"])
    all_daily_exec = None
    if CACHE.get("intraday_cache") and not _is_post_market():
        # Merge intraday bars into daily data for both factor computation and execution
        # (only during actual intraday hours; stale cache after close is invalid)
        all_daily_exec = {}
        for code in CACHE["all_daily"]:
            merged = _get_daily_with_cache(code)
            all_daily_exec[code] = merged
        # Use merged data for factor precomputation too (BUG-041)
        all_daily = all_daily_exec
        all_daily_exec = None  # engine falls back to all_daily when None

    # BUG-061: merge benchmark ETF (510050/510300/510500/159915) intraday data
    # so HS300 MA trend calculation sees today's intraday price instead of yesterday's close.
    _bm_merged = {}
    if CACHE.get("intraday_cache") and not _is_post_market():
        for _bm_code in ("510050", "510300", "510500", "159915"):
            _merged = _get_benchmark_daily_with_cache(_bm_code)
            if _merged is not None:
                _bm_merged[_bm_code] = _merged

    universe_filter, _universe_mode = _parse_universe_filter(params)
    if universe_filter is not None:
        all_daily = {k: v for k, v in all_daily.items() if k in universe_filter}
        all_weekly = {k: v for k, v in all_weekly.items() if k in universe_filter}

    available = len(all_daily)
    if available < 6:
        return {"error": f"Only {available} ETFs with data, need at least 6"}

    ma_trend_period = config_override["confidence"]["ma_trend_period"]
    ma_cache = _build_ma_trend_cache(ma_trend_period) or {}
    preloaded = {
        "all_daily": all_daily,
        "all_weekly": all_weekly,
        "market_regimes": CACHE.get("market_regimes", {}),
        "hs300_above_ma": ma_cache.get("above", {}),
        "hs300_ma_rising": ma_cache.get("ma_rising", {}),
        "benchmark_daily_merged": _bm_merged,  # BUG-061
    }

    # Factor precomputation and price lookup now handled inside run_backtest()

    # ── Call unified backtest engine ──
    return_debug = bool(params.get("debug", False))
    nav_df, signal_history, extra = _run_backtest(
        start_date=params.get("start_date"),
        end_date=params.get("end_date"),
        preset=qc.DEFAULT_PRESET,
        universe_filter=universe_filter,
        preloaded=preloaded,
        config_override=config_override,
        return_details=True,
        return_debug=return_debug,
        progress_callback=progress_callback,
        all_daily_exec=all_daily_exec,
        verbose=False,
    )
    total_commission = extra.get("total_commission", 0)

    if nav_df is None:
        return {"error": "Backtest produced no results"}

    elapsed = time.time() - t0
    initial_cap = 1000000.0
    # Base metrics from shared compute_metrics (single source of truth)
    from quant_backtest import compute_metrics as _cm
    _base = _cm(nav_df, signal_history, extra)
    total_return = _base["total_return"]
    annual_return = _base["annual_return"]
    max_drawdown = _base["max_drawdown"]
    sharpe = _base["sharpe"]
    sortino = _base["sortino"]
    # Drawdown series for frontend chart
    cummax = nav_df["nav"].cummax()
    dd = (nav_df["nav"] - cummax) / cummax * 100

    monthly_groups = nav_df.groupby(nav_df["date"].dt.to_period("M"))
    monthly_rets = monthly_groups["nav"].last().pct_change().dropna()
    monthly_win_rate = float((monthly_rets > 0).sum() / max(len(monthly_rets), 1) * 100) if len(monthly_rets) > 0 else 0.0

    # ── Per-trade win rate & payoff ratio from trade log ──
    trade_log = extra.get("trade_log", [])
    if trade_log:
        winning_trades = [t for t in trade_log if t["pnl_pct"] > 0]
        losing_trades = [t for t in trade_log if t["pnl_pct"] <= 0]
        win_rate = round(len(winning_trades) / len(trade_log) * 100, 1)
        avg_win = sum(t["pnl_pct"] for t in winning_trades) / len(winning_trades) if winning_trades else 0.0
        avg_loss = abs(sum(t["pnl_pct"] for t in losing_trades) / len(losing_trades)) if losing_trades else 0.0
        payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 999.0
    else:
        win_rate = 0.0
        payoff_ratio = 0.0
        avg_win = 0.0
        avg_loss = 0.0
    best_month = float(monthly_rets.max() * 100) if len(monthly_rets) > 0 else 0.0
    worst_month = float(monthly_rets.min() * 100) if len(monthly_rets) > 0 else 0.0
    calmar = annual_return / abs(max_drawdown) if abs(max_drawdown) > 0 else 0.0

    # Win/loss streaks from daily returns
    daily_rets = nav_df["nav"].pct_change().dropna()
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    for r in daily_rets:
        if r > 0.02 / 252:
            current_streak = current_streak + 1 if current_streak > 0 else 1
            max_win_streak = max(max_win_streak, current_streak)
        elif r < -0.02 / 252:
            current_streak = current_streak - 1 if current_streak < 0 else -1
            max_loss_streak = min(max_loss_streak, current_streak)
        else:
            current_streak = 0

    yearly_groups = nav_df.groupby(nav_df["date"].dt.year)
    annual_returns = {}
    for yr, grp in yearly_groups:
        first_nav = grp["nav"].iloc[0]
        last_nav = grp["nav"].iloc[-1]
        annual_returns[str(yr)] = round((last_nav / first_nav - 1) * 100, 1)

    # Benchmarks: rebase to 100 at start, align to NAV dates
    eq_dates = CACHE.get("eq_dates", [])
    eq_full = CACHE.get("eq_weight_pct") or []
    hs_full = CACHE.get("hs300_pct") or []
    nav_date_strs = [d.strftime("%Y-%m-%d") for d in nav_df["date"]]

    def _rebase_slice(full_series, full_dates, target_dates):
        if not full_series or len(full_series) != len(full_dates):
            return None
        idx_map = {d: i for i, d in enumerate(full_dates)}
        out = []
        anchor = None
        for ds in target_dates:
            if ds in idx_map:
                v = full_series[idx_map[ds]]
                if anchor is None:
                    anchor = v
                out.append(round(v / anchor * 100, 2) if anchor else 100.0)
            else:
                out.append(out[-1] if out else 100.0)
        return out

    hs_sliced = _rebase_slice(hs_full, eq_dates, nav_date_strs)
    eq_sliced = _rebase_slice(eq_full, eq_dates, nav_date_strs)
    # Excess return vs HS300 (both rebased to 100)
    final_nav_val = _base["final_nav"]
    excess_return = round((final_nav_val / initial_cap) - (hs_sliced[-1] / 100 if hs_sliced else 1), 4) * 100
    excess_return = round(excess_return, 1)

    latest_holdings = []
    if signal_history:
        last = signal_history[-1]
        etf_map = {e["code"]: e for e in CACHE["cfg"]["universe"]}
        for code in last.get("top6", [])[:6]:
            info = etf_map.get(code, {})
            latest_holdings.append({
                "code": code, "name": info.get("name", code),
                "sector": info.get("sector", ""),
                "position": round(last.get("actual_positions", last.get("positions", {})).get(code, 0) * 100, 1),
                "score": round(last.get("scores", {}).get(code, 0) * 100, 1),
            })

    conf_type = config_override["confidence"]["type"]
    strategy_label = {"regime": "Regime-aware", "dd_trigger": "DD Trigger",
                      "momentum_crash": "Mom Crash", "always_full": "Always 95%",
                      "ma_trend": "MA Trend"}.get(conf_type, conf_type or "unknown")

    user_start_str = params.get("start_date", "")
    user_end_str = params.get("end_date", "")
    try:
        start_dt = pd.Timestamp(user_start_str) if user_start_str else nav_df["date"].iloc[0]
        end_dt = pd.Timestamp(user_end_str) if user_end_str else nav_df["date"].iloc[-1]
    except Exception:
        start_dt = nav_df["date"].iloc[0]
        end_dt = nav_df["date"].iloc[-1]

    etf_name_map = {e["code"]: e.get("name", e["code"]) for e in CACHE["cfg"].get("universe", [])}
    etf_sector_map = {e["code"]: e.get("sector", "") for e in CACHE["cfg"].get("universe", [])}

    # Enrich detail with "action" by comparing consecutive positions
    prev_positions = {}
    enriched_history = []
    actual_rebalance_count = 0
    for s in signal_history:
        detail = s.get("detail", {})
        had_action = False
        for code, d in detail.items():
            cur_pos = d.get("position", 0)
            prev_pos = prev_positions.get(code, 0)
            if cur_pos > 0 and prev_pos == 0:
                d["action"] = "new"
                had_action = True
            elif cur_pos > 0 and prev_pos > 0 and abs(cur_pos - prev_pos) > 0.01:
                d["action"] = "adj_up" if cur_pos > prev_pos else "adj_down"
                d["delta"] = round(cur_pos - prev_pos, 1)
                had_action = True
            elif cur_pos > 0 and prev_pos > 0:
                d["action"] = "hold"
            elif cur_pos == 0 and prev_pos > 0:
                d["action"] = "out"
                had_action = True
            else:
                d["action"] = ""
        prev_positions = {code: d.get("position", 0) for code, d in detail.items()}
        if had_action:
            actual_rebalance_count += 1

        sig_date = s["date"]
        # Compute C_eff for this rebalance
        pos_cfg = config_override.get("position", {})
        base_c = float(pos_cfg.get("concentration", 2.0))
        cs_val = float(pos_cfg.get("c_sensitivity", 0.0))
        c_eff = base_c
        if cs_val > 0:
            all_scores = np.array(list(s["scores"].values()))
            top6_scores = np.array([s["scores"][c] for c in s.get("top6", []) if c in s["scores"]])
            if len(all_scores) > 5 and len(top6_scores) > 1:
                mu, sigma = float(all_scores.mean()), max(float(all_scores.std()), 0.02)
                z_top6 = (top6_scores - mu) / sigma
                disp = float(z_top6.std())
                c_mult = 1.0 + cs_val * (disp - 0.5)
                c_eff = round(base_c * max(c_mult, 0.1), 2)

        enriched_entry = {
            "date": sig_date.strftime("%Y-%m-%d") if hasattr(sig_date, "strftime") else str(sig_date)[:10],
            "signalDate": s.get("signal_date", sig_date).strftime("%Y-%m-%d") if hasattr(s.get("signal_date", sig_date), "strftime") else str(s.get("signal_date", sig_date))[:10],
            "executionDate": sig_date.strftime("%Y-%m-%d") if hasattr(sig_date, "strftime") else str(sig_date)[:10],
            "scores": s["scores"],
            "topN": s.get("top6", []),
            "top_n": s.get("top6", []),
            "positions": s.get("actual_positions", s.get("positions", {})),
            "detail": detail,
            "avgConfidence": round(s.get("total_target", 0) * 100, 0),
            "totalPosition": round(s.get("total_target", 0) * 100, 1),
            "cashPct": round((1.0 - s.get("total_target", 0)) * 100, 1),
            "regime": s.get("regime", ""),
            "cEff": c_eff,
            "actualLeverage": s.get("actual_leverage", 0),
            "suspendedCodes": s.get("suspended_codes", []),
            "targetExposure": round(s.get("total_target", 0) * 100, 0),
        }
        if s.get("benchmark_votes") is not None:
            enriched_entry["benchmark_votes"] = s["benchmark_votes"]
        # REQ-349: 替换因果链（加 ETF 名称便于前端展示）
        raw_swaps = s.get("swap_pairs", [])
        if raw_swaps:
            enriched_swaps = []
            for sp in raw_swaps:
                enriched_swaps.append({
                    "in": sp["in"], "in_name": etf_name_map.get(sp["in"], sp["in"]),
                    "in_score": sp["in_score"],
                    "out": sp["out"], "out_name": etf_name_map.get(sp["out"], sp["out"]),
                    "out_score": sp["out_score"],
                    "gap": sp["gap"], "band": sp["band"], "passed": sp["passed"],
                })
            enriched_entry["swap_pairs"] = enriched_swaps
        enriched_history.append(enriched_entry)

    # Detect intraday estimate: last NAV date matches intraday cache date
    ic_date = CACHE.get("intraday_date")
    ic_times = []
    for v in CACHE.get("intraday_cache", {}).values():
        if v.get("time"):
            ic_times.append(v["time"])
    ic_time = ic_times[-1] if ic_times else None
    has_intraday = bool(ic_date and nav_date_strs and nav_date_strs[-1] == ic_date)

    # Write debug snapshots if requested
    if return_debug:
        import json as _json
        snaps = extra.get("debug_snapshots", [])
        debug_path = DATA_DIR / "debug_tuner.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        with debug_path.open("w", encoding="utf-8") as _f:
            _json.dump({
                "count": len(snaps), "snapshots": snaps,
                "_params": {k: v for k, v in params.items() if not k.startswith('_')},
                "_config_override": config_override,
                "_ar": round(_base["annual_return"], 1),
                "_mdd": round(_base["max_drawdown"], 1),
                "_sortino": round(_base["sortino"], 2),
            }, _f, ensure_ascii=False, indent=2)
        print(f"DEBUG: {len(snaps)} snapshots saved → {debug_path}")

    return {
        "strategy": strategy_label,
        "etfNameMap": etf_name_map,
        "etfHoldings": {code: (meta.get("top10", []) if isinstance(meta, dict) else []) for code, meta in CACHE.get("etf_metadata", {}).items()},
        "stockMetadata": CACHE.get("stock_metadata", {}),
        "hs300": hs_sliced,
        "eqWeight": eq_sliced,
        "drawdown": [round(v, 2) for v in dd],
        "summary": {
            "totalReturn": round(total_return, 2),
            "annualReturn": round(annual_return, 2),
            "maxDrawdown": round(max_drawdown, 2),
            "sharpe": round(float(sharpe), 2),
            "sortino": round(float(sortino), 2),
            "calmar": round(float(calmar), 2),
            "excessReturn": excess_return,
            "winRate": round(win_rate, 1),
            "payoffRatio": round(payoff_ratio, 2),
            "avgWin": round(avg_win, 2) if isinstance(avg_win, (int, float)) else 0.0,
            "avgLoss": round(avg_loss, 2) if isinstance(avg_loss, (int, float)) else 0.0,
            "tradeCount": len(trade_log),
            "monthlyWinRate": round(monthly_win_rate, 1),
            "bestMonth": round(best_month, 2),
            "worstMonth": round(worst_month, 2),
            "maxWinStreak": max_win_streak,
            "maxLossStreak": abs(max_loss_streak),
            "startDate": start_dt.strftime("%Y-%m-%d") if hasattr(start_dt, "strftime") else str(start_dt)[:10],
            "endDate": end_dt.strftime("%Y-%m-%d") if hasattr(end_dt, "strftime") else str(end_dt)[:10],
            "tradingDays": len(nav_df),
            "rebalanceCount": actual_rebalance_count,
            "rebalanceDays": len(enriched_history),
            "commissionPct": round(total_commission / initial_cap * 100, 2),
            "elapsed": round(elapsed, 1),
            "initialCapital": initial_cap,
            "finalNav": round(final_nav_val, 0),
            "annualReturns": annual_returns,
            "hasIntradayEstimate": has_intraday,
            "intradayDate": ic_date if has_intraday else None,
            "intradayTime": ic_time if has_intraday else None,
            "intradayCount": len(CACHE.get("intraday_cache", {})) if has_intraday else 0,
            "haltedEtfs": [code for code, v in CACHE.get("intraday_cache", {}).items() if v.get("halted")],
            "exposureSummary": extra.get("exposure_summary", {}),
        },
        "nav": {
            "dates": nav_date_strs,
            "pct": [round(float(v), 2) for v in nav_df["nav_pct"]],
            "exposure": [round(float(e), 3) for e in nav_df["exposure"]] if "exposure" in nav_df.columns else [],
        },
        "benchmarks": {
            "hs300": hs_sliced,
            "eqWeight": eq_sliced,
            "holdings": latest_holdings,
            "etfNameMap": etf_name_map,
            "etfSectorMap": etf_sector_map,
        },
        "signalHistory": enriched_history,
        "etfContributions": _compute_etf_contributions(trade_log, signal_history, etf_name_map, etf_sector_map),
        "factorDispersion": extra.get("factor_dispersion", {}),
    }



# ============================================================
# Flask app
# ============================================================
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
# Preserve YAML insertion order in API responses (Flask 3.x)
app.json.sort_keys = False

# ── NaN-safe JSON: Python nan/inf → null (valid JSON) ──
import math as _math
def _sanitize_json(obj):
    """Recursively replace float('nan')/inf with None in dicts/lists."""
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float) and (_math.isnan(obj) or _math.isinf(obj)):
        return None
    return obj

_safe_json_dumps_orig = app.json.dumps
def _safe_json_dumps(obj, **kwargs):
    return _safe_json_dumps_orig(_sanitize_json(obj), **kwargs)
app.json.dumps = _safe_json_dumps

# Force GC after each request to prevent memory buildup on long backtests
@app.after_request
def gc_after_request(response):
    gc.collect()
    try:
        import psutil
        mem_mb = psutil.Process().memory_info().rss / 1024 / 1024
        if mem_mb > 800:
            print(f"  [GC] Memory {mem_mb:.0f}MB > 800MB, aggressive cleanup")
            gc.collect(2)
    except Exception:
        pass
    return response

@app.route("/")
def index():
    # Server-side assembly: tuner.html skeleton + left/right panel fragments.
    # Fragment files in assets/ — changes take effect immediately, no restart.
    skeleton = (ASSETS_DIR / "tuner.html").read_text(encoding="utf-8")
    left = (ASSETS_DIR / "tuner-left.html").read_text(encoding="utf-8")
    right = (ASSETS_DIR / "tuner-right.html").read_text(encoding="utf-8")
    right = right.replace("{DM_ANOMALY_THRESHOLD_PCT}", str(int(DM_ANOMALY_THRESHOLD * 100)))
    html = skeleton.replace("<!-- LEFT_PANEL -->", left).replace("<!-- RIGHT_PANEL -->", right)
    from flask import make_response
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


@app.route("/assets/<path:filepath>")
def serve_assets(filepath):
    """Serve static assets (CSS/JS) from the project's assets directory."""
    return send_from_directory(ASSETS_DIR, filepath)


def _require_ready():
    """Return (json_error, 503) if CACHE not ready, otherwise None."""
    if not CACHE.get("ready"):
        return jsonify({"error": "Server is still loading data, please wait"}), 503
    return None


_BACKTEST_CACHE_PATH = DATA_DIR / "quant" / "cache" / "last_backtest.json"


def _cache_version_hash():
    """Hash key source files so cached results invalidate on code change."""
    import hashlib
    h = hashlib.md5()
    for fname in ["quant_tuner.py", "quant_backtest.py", "quant_factors.py",
                  "quant_contract.py", "quant_data_utils.py"]:
        fp = SCRIPTS_DIR / fname
        if fp.exists():
            h.update(fp.read_bytes())
    return h.hexdigest()[:8]


@app.route("/api/progress/<task_id>")
def api_progress(task_id):
    with TASK_LOCK:
        task = TASK_STORE.get(task_id)
    if not task:
        return jsonify({"status": "not_found"}), 404
    return jsonify({k: v for k, v in task.items() if k != "result"})


@app.route("/api/result/<task_id>")
def api_result(task_id):
    with TASK_LOCK:
        task = TASK_STORE.get(task_id)
    if not task:
        return jsonify({"status": "not_found"}), 404
    if task["status"] == "running":
        return jsonify({"status": "running", "pct": task["pct"]}), 202
    if task["status"] == "error":
        return jsonify({"status": "error", "message": task["message"]}), 500
    return jsonify({"status": "done", "result": task.get("result")})


@app.route("/api/run", methods=["POST"])
def api_run():
    try:
        guard = _require_ready()
        if guard:
            return guard
        params = request.json or {}
        is_async = request.args.get("async") == "1"

        if is_async:
            task_id = _cache_version_hash()[:8] + "-bt"
            print(f"  [ASYNC] Starting backtest task {task_id}")
            _task_start(task_id, 100, "准备回测...")
            def _run():
                try:
                    def _cb(current, total, msg):
                        pct = int(current / max(total, 1) * 100)
                        _task_update(task_id, pct, msg)
                    try:
                        result = run_tuner_backtest(params, progress_callback=_cb)
                    except Exception as _e:
                        import traceback as _tb
                        result = {"error": f"{type(_e).__name__}: {_e}", "traceback": _tb.format_exc()}
                        _task_update(task_id, 100, f"错误: {_e}")
                    _save_backtest_cache(params, result)
                    _task_done(task_id, result=result)
                    print(f"  [ASYNC] Task {task_id} done, elapsed={TASK_STORE.get(task_id,{}).get('elapsed',0)}s")
                except Exception as e:
                    import traceback; traceback.print_exc()
                    _task_done(task_id, error=f"{type(e).__name__}: {str(e)}")
            threading.Thread(target=_run, daemon=True).start()
            return jsonify({"task_id": task_id})

        try:
            result = run_tuner_backtest(params)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"{type(e).__name__}: {str(e)}"}), 500
        _save_backtest_cache(params, result)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"api_run: {type(e).__name__}: {str(e)}"}), 500


def _save_backtest_cache(params, result):
    try:
        cache = {"version": _cache_version_hash(), "params": params, "result": result}
        _BACKTEST_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _BACKTEST_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, default=str)
    except Exception:
        pass




@app.route("/api/debug_data_state")
def api_debug_data_state():
    """Debug: show data state — CSV row counts, intraday cache status."""
    ic = CACHE.get("intraday_cache", {})
    ic_date = CACHE.get("intraday_date", "")
    # Sample first 3 ETFs
    samples = {}
    for i, (code, df) in enumerate(CACHE["all_daily"].items()):
        if i >= 3: break
        dates = df["date"].astype(str).values
        samples[code] = {"rows": len(df), "first": dates[0][:10], "last": dates[-1][:10]}
    merged_samples = {}
    if ic:
        for i, code in enumerate(CACHE["all_daily"]):
            if i >= 3: break
            df = _get_daily_with_cache(code)
            dates = df["date"].astype(str).values
            merged_samples[code] = {"rows": len(df), "first": dates[0][:10], "last": dates[-1][:10]}
    return jsonify({
        "intraday_cache_etfs": len(ic),
        "intraday_date": ic_date,
        "csv_samples": samples,
        "merged_samples": merged_samples,
    })


# ── REQ-263a: snapshot intraday cache to disk ──
SNAPSHOT_DIR = DATA_DIR / "intraday_snapshots"

@app.route("/api/snapshot_intraday", methods=["POST"])
def api_snapshot_intraday():
    guard = _require_ready()
    if guard:
        return guard
    label = request.args.get("label", datetime.now().strftime("%H%M"))
    today_str = datetime.now().strftime("%Y-%m-%d")
    ic = CACHE.get("intraday_cache", {})
    if not ic:
        return jsonify({"ok": False, "error": "No intraday cache available"})
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{today_str}_{label}.json"
    path = SNAPSHOT_DIR / filename
    # Convert numpy/pandas types to native Python
    data = {"date": today_str, "time": label, "entries": {}}
    for code, row in ic.items():
        entry = {}
        for k, v in row.items():
            entry[k] = float(v) if hasattr(v, "item") else v
        data["entries"][code] = entry
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    return jsonify({"ok": True, "file": filename, "etfs": len(ic)})


# ── REQ-263b: backtest using saved intraday snapshot as today's close ──
@app.route("/api/backtest_with_snapshot", methods=["POST"])
def api_backtest_with_snapshot():
    guard = _require_ready()
    if guard:
        return guard
    params = request.json or {}
    snapshot_file = params.get("snapshot", "")
    if not snapshot_file:
        return jsonify({"error": "Missing 'snapshot' parameter"}), 400
    path = SNAPSHOT_DIR / snapshot_file
    if not path.exists():
        return jsonify({"error": f"Snapshot not found: {snapshot_file}"}), 404
    with path.open("r", encoding="utf-8") as f:
        snap = json.load(f)

    # Save original daily data, then patch with snapshot
    _orig_daily = dict(CACHE["all_daily"])
    import pandas as _pd
    snap_date = snap["date"]
    for code, entry in snap["entries"].items():
        if code not in _orig_daily:
            continue
        df = _orig_daily[code].copy()
        # Find or create row for snapshot date
        mask = df["date"].astype(str).str[:10] == snap_date
        if mask.any():
            idx = df[mask].index[0]
            df.at[idx, "close"] = entry.get("close", df.at[idx, "close"])
            if "volume" in entry:
                df.at[idx, "volume"] = entry["volume"]
            if "amount" in entry:
                df.at[idx, "amount"] = entry["amount"]
        CACHE["all_daily"][code] = df

    try:
        result = run_tuner_backtest(params)
    finally:
        CACHE["all_daily"] = _orig_daily
    return jsonify(result)


@app.route("/api/last_result")
def api_last_result():
    guard = _require_ready()
    if guard:
        return guard
    try:
        if _BACKTEST_CACHE_PATH.exists():
            with _BACKTEST_CACHE_PATH.open("r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("version") == _cache_version_hash():
                req_params = request.json or {}
                # REQ-375: compare params too — f7_down_power/f7_down_span don't change code hash
                cached_params = cache.get("params", {})
                if cached_params == req_params:
                    return jsonify({"cached": True, "params": cached_params,
                                    "result": cache.get("result", {})})
    except Exception:
        pass
    return jsonify({"cached": False})


@app.route("/api/metadata")
def api_metadata():
    guard = _require_ready()
    if guard:
        return guard
    return jsonify(CACHE.get("etf_metadata", {}))


@app.route("/api/refresh_metadata", methods=["POST"])
def api_refresh_metadata():
    guard = _require_ready()
    if guard:
        return guard
    try:
        from fetch_etf_metadata import load_universe, fetch_one, load_existing, save_metadata
        universe = load_universe()
        existing = load_existing()
        ok = fail = 0
        import time as _t
        for etf in universe:
            try:
                entry = fetch_one(etf, existing)
                existing[etf["code"]] = entry
                ok += 1
                _t.sleep(1.0)
            except Exception:
                fail += 1
        save_metadata(existing)
        CACHE["etf_metadata"] = existing
        # Also refresh stock business descriptions for all top10 holdings
        stock_ok, stock_fail = _refresh_stock_metadata()
        return jsonify({"ok": True, "message": f"meta: {ok} OK, {fail} fail | stock: {stock_ok} OK, {stock_fail} fail",
                        "ok_count": ok, "fail_count": fail,
                        "stock_ok": stock_ok, "stock_fail": stock_fail})
    except ImportError:
        return jsonify({"ok": False, "message": "fetch_etf_metadata not available"}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


def _refresh_stock_metadata():
    """Fetch stock business descriptions for all ETF top10 holdings.
    A-shares → cninfo API, HK stocks → HK company profile API, others → stock name fallback.
    Returns (ok_count, fail_count)."""
    import akshare as ak
    meta = CACHE.get("etf_metadata", {})
    if not meta:
        return 0, 0

    # Classify all stocks by market
    a_codes = set()   # 6-digit, starts with 0/3/6
    hk_codes = set()  # 4-5 digit, starts with 0
    other_codes = {}  # code -> stock_name
    for etf_code, m in meta.items():
        top10 = m.get("top10") if isinstance(m, dict) else []
        if not isinstance(top10, list):
            continue
        for h in top10:
            sc = h.get("code", "")
            name = h.get("name", "")
            if not sc:
                continue
            if len(sc) == 6 and sc[0] in "036":
                a_codes.add(sc)
            elif 4 <= len(sc) <= 5 and sc.isdigit() and sc[0] == "0":
                hk_codes.add(sc.zfill(5))
            else:
                other_codes[sc] = name

    # Load existing cache
    stock_path = DATA_DIR / "quant" / "stock_metadata.json"
    stock_cache = {}
    if stock_path.exists():
        try:
            with stock_path.open("r", encoding="utf-8") as f:
                stock_cache = json.load(f)
        except Exception:
            pass

    def _extract_short(biz, industry, name_cn):
        short = biz.split("。")[0].split("；")[0].strip()
        if len(short) > 18:
            short = short[:18]
        return short or industry or name_cn

    ok, fail = 0, 0

    # ── A-shares via cninfo ──
    for code in sorted(a_codes):
        if code in stock_cache and stock_cache[code].get("biz"):
            ok += 1; continue
        try:
            info = ak.stock_profile_cninfo(symbol=code)
            row = info.iloc[0]
            biz = str(row.get("主营业务", "") or "")
            industry = str(row.get("所属行业", "") or "")
            name_cn = str(row.get("公司简称", "") or "")
            stock_cache[code] = {
                "name": name_cn, "biz": biz[:200],
                "biz_short": _extract_short(biz, industry, name_cn),
                "industry": industry,
            }
            ok += 1
            if ok % 30 == 0:
                import time as _t; _t.sleep(1.0)
        except Exception:
            fail += 1
            if code not in stock_cache:
                stock_cache[code] = {"name": "", "biz": "", "biz_short": "", "industry": ""}

    # ── HK stocks via HK company profile ──
    for code in sorted(hk_codes):
        if code in stock_cache and stock_cache[code].get("biz"):
            ok += 1; continue
        try:
            info = ak.stock_hk_company_profile_em(symbol=code)
            row = info.iloc[0]
            biz = str(info.iloc[0, -1] or "")  # last column = 公司介绍
            industry = str(row.get("所属行业", "") or "")
            name_cn = str(row.get("公司名称", "") or "")
            stock_cache[code] = {
                "name": name_cn, "biz": biz[:200],
                "biz_short": _extract_short(biz, industry, name_cn),
                "industry": industry,
            }
            ok += 1
            if ok % 30 == 0:
                import time as _t; _t.sleep(1.0)
        except Exception:
            fail += 1
            if code not in stock_cache:
                stock_cache[code] = {"name": "", "biz": "", "biz_short": "", "industry": ""}

    # ── Other stocks (Korean etc) → fallback to stock name ──
    for code, name in other_codes.items():
        if code in stock_cache and stock_cache[code].get("biz_short"):
            ok += 1; continue
        stock_cache[code] = {"name": name, "biz": "", "biz_short": name or "-", "industry": ""}
        ok += 1

    # Save
    stock_path.parent.mkdir(parents=True, exist_ok=True)
    with stock_path.open("w", encoding="utf-8") as f:
        json.dump(stock_cache, f, ensure_ascii=False, indent=2)
    CACHE["stock_metadata"] = stock_cache
    return ok, fail


@app.route("/api/param_schema")
def api_param_schema():
    """Return shared Tuner parameter schema."""
    return jsonify(qc.get_param_schema())


@app.route("/api/presets")
def api_presets():
    guard = _require_ready()
    if guard:
        return guard
    """Return strategy presets from YAML config."""
    cfg = CACHE.get("cfg") or load_config()
    return jsonify(qc.build_presets_response(cfg))


@app.route("/api/frontier")
def api_frontier():
    """Return preset metrics grouped by school for frontier scatter charts.

    Reads preset_metrics.json (cached backtest results, preset-name → metrics).
    Groups gam-* → gambler, zen-* → zen, act-* → actuary.
    """
    import json as _json
    import pathlib as _pl
    import hashlib as _hashlib

    _proj = _pl.Path(__file__).resolve().parent.parent

    # ── Load preset metrics ──
    mp = _proj / "config" / "preset_metrics.json"
    data = {}
    if mp.exists():
        try:
            data = _json.loads(mp.read_text("utf-8"))
        except Exception:
            pass
    metrics = data.get("points", {})

    # ── Fingerprint check: detect stale metrics vs current presets ──
    WINDOW = "2020-06-01 ~ 2026-06-01"
    cfg = CACHE.get("cfg") or load_config()
    presets = cfg.get("presets", {})
    _h = _hashlib.sha256()
    _h.update(WINDOW.encode())
    for _name in sorted(presets):
        if not (_name.startswith('gam-') or _name.startswith('zen-') or _name.startswith('act-')):
            continue
        _p = presets[_name]
        _pos = _p.get('position', {})
        _conf = _p.get('confidence', {})
        _vals = f'{_pos.get("max_holdings")}|{_pos.get("top_boost")}|{_pos.get("signal_steps")}|{_pos.get("concentration")}|{_pos.get("c_sensitivity")}|{_pos.get("band")}|{_conf.get("ma_bull_pos")}|{_conf.get("ma_bear_pos")}|{_conf.get("ma_trend_period")}'
        _h.update(f'{_name}:{_vals}'.encode())
    _current_fp = _h.hexdigest()[:16]
    _stored_fp = data.get("fingerprint", "")
    _stale = (_stored_fp and _current_fp != _stored_fp)

    # ── Group into schools ──
    Y_FIELDS = {"gam": "ar_6y", "zen": "sortino", "act": "calmar"}
    SCHOOL_IDS = {"gam": "mh_ar", "zen": "mh_sortino", "act": "mh_calmar"}

    def _build_school(prefix):
        pts = []
        for name in sorted(metrics):
            if not name.startswith(prefix + "-"):
                continue
            m = metrics[name]
            pts.append({
                "preset": name, "mh": m.get("MH"),
                "ar_6y": m.get("AR"), "mdd": m.get("MDD"),
                "calmar": m.get("Calmar"), "sortino": m.get("Sortino"),
            })
        return {
            "type": "slider", "risk_axis": "MH", "risk_unit": "支",
            "risk_range": [2, 6], "risk_step": 1,
            "y_field": Y_FIELDS.get(prefix, "ar_6y"),
            "points": sorted(pts, key=lambda p: p.get("mh", 0)),
            "references": [],
            "updated": data.get("updated", "N/A"),
        }

    result = {}
    for prefix, sid in SCHOOL_IDS.items():
        school = _build_school(prefix)
        school["stale"] = _stale
        result[sid] = school
    result["custom"] = {"type": "discrete", "presets": []}
    result["_meta"] = {"fingerprint": _current_fp, "stale": _stale}

    return jsonify(result)


@app.route("/api/universe/save", methods=["POST"])
def api_universe_save():
    """Persist ETF active states to quant_universe.yaml."""
    if CACHE.get("readonly"):
        return jsonify({"error": "Tuner is in read-only mode"}), 403
    guard = _require_ready()
    if guard:
        return guard
    data = request.json or {}
    active_codes = set(data.get("active_codes", []))
    if not active_codes:
        return jsonify({"error": "active_codes is empty"}), 400

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    active_count = 0
    dormant_count = 0
    for e in cfg.get("universe", []):
        code = e["code"]
        if code in active_codes:
            e.pop("active", None)
            active_count += 1
        else:
            e["active"] = False
            dormant_count += 1

    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # Refresh cache so next /api/presets returns updated state
    CACHE["cfg"] = cfg

    return jsonify({"ok": True, "saved": active_count + dormant_count,
                    "active": active_count, "dormant": dormant_count})


def _snapshot_for_undo():
    """Snapshot config + overrides to .bak files before a save (single-level undo)."""
    import shutil
    for src in (CONFIG_PATH, OVERRIDES_PATH):
        if src.exists():
            shutil.copy2(src, str(src) + ".bak")


def _save_to_preset(preset_name, overrides):
    """Deep-merge overrides into a specific preset inside quant_universe.yaml.
    If the preset doesn't exist yet, auto-create it from the zen-1 template."""
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    presets = cfg.setdefault("presets", {})
    if preset_name not in presets:
        template = presets.get("zen-1", {})
        if template:
            presets[preset_name] = deepcopy(template)
            presets[preset_name]["label"] = "自定义策略"
            presets[preset_name]["description"] = "用户自定义策略。"
        else:
            raise ValueError(f"Preset '{preset_name}' not found and no template available")
    target = presets[preset_name]
    for section in ("scoring", "confidence", "position", "factors"):
        if section in overrides:
            target.setdefault(section, {}).update(overrides[section])
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


@app.route("/api/save", methods=["POST"])
def api_save():
    if CACHE.get("readonly"):
        return jsonify({"error": "Tuner is in read-only mode"}), 403
    guard = _require_ready()
    if guard:
        return guard
    params = dict(request.json or {})
    preset_name = params.pop("_preset", None)
    validation_error = _validate_tuner_params(params)
    if validation_error:
        return jsonify({"ok": False, "error": validation_error})
    try:
        # Build config fragment from shared parameter contract
        overrides = qc.tuner_params_to_preset_patch(params, load_config())

        _snapshot_for_undo()
        if preset_name and not preset_name.startswith("frontier"):
            _save_to_preset(preset_name, overrides)
        else:
            # Fallback: no preset selected → save to overrides file
            with OVERRIDES_PATH.open("w", encoding="utf-8") as f:
                f.write("# Quant Tuner user overrides — safe to delete to reset to defaults\n")
                yaml.dump(overrides, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        # Refresh CACHE so /api/presets returns updated config immediately
        CACHE["cfg"] = load_config()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/undo_save", methods=["POST"])
def api_undo_save():
    """Restore config + overrides from the .bak snapshot taken before the last save."""
    if CACHE.get("readonly"):
        return jsonify({"error": "Tuner is in read-only mode"}), 403
    import shutil
    restored = []
    for src in (CONFIG_PATH, OVERRIDES_PATH):
        bak = Path(str(src) + ".bak")
        if bak.exists():
            shutil.copy2(bak, src)
            restored.append(src.name)
    if not restored:
        return jsonify({"ok": False, "error": "没有可撤销的保存记录"})
    CACHE["cfg"] = load_config()
    return jsonify({"ok": True, "restored": restored})


@app.route("/api/kline")
def api_kline():
    guard = _require_ready()
    if guard:
        return guard
    """Return daily K-line (60d, OHLCV + RSI14 + Volume) and weekly K-line (30w, OHLCV + EMA20)
    for a given ETF, truncated to the rebalance date.

    Query params:
        code: ETF code (e.g. "512400")
        date: rebalance date (YYYY-MM-DD); data will be cut at this date
        rsi_period: RSI lookback (default 14)
        ema_period: EMA lookback in weeks (default 20)
    """
    code = request.args.get("code", "").strip()
    date_str = request.args.get("date", "").strip()
    rsi_period = int(request.args.get("rsi_period", 14))
    ema_period = int(request.args.get("ema_period", 20))

    if not code or code not in CACHE.get("all_daily", {}):
        return jsonify({"error": f"Unknown code: {code}"}), 404

    try:
        cutoff = pd.Timestamp(date_str) if date_str else None
    except Exception:
        return jsonify({"error": f"Bad date: {date_str}"}), 400

    daily = CACHE["all_daily"][code].copy()
    weekly = CACHE["all_weekly"][code].copy()

    # Truncate to cutoff
    if cutoff is not None:
        daily = daily[daily["date"] <= cutoff]
        weekly = weekly[weekly["date"] <= cutoff]

    # Take last 60 days / 30 weeks
    daily = daily.tail(60).reset_index(drop=True)
    weekly = weekly.tail(30).reset_index(drop=True)

    # Compute RSI on daily (need enough warmup; use full daily then tail again)
    full_daily = CACHE["all_daily"][code]
    if cutoff is not None:
        full_daily = full_daily[full_daily["date"] <= cutoff]
    rsi_full = calc_rsi(full_daily["close"].astype(float), period=rsi_period)
    daily_rsi = rsi_full.tail(len(daily)).tolist()

    # Compute EMA on weekly
    full_weekly = CACHE["all_weekly"][code]
    if cutoff is not None:
        full_weekly = full_weekly[full_weekly["date"] <= cutoff]
    ema_full = calc_ema(full_weekly["close"].astype(float), span=ema_period)
    weekly_ema = ema_full.tail(len(weekly)).tolist()

    def _ohlcv(df):
        # Returns lists for [date, open, close, low, high, volume] (echarts candlestick order: O,C,L,H)
        return {
            "dates": [d.strftime("%Y-%m-%d") for d in df["date"]],
            "ohlc": [[float(r["open"]), float(r["close"]), float(r["low"]), float(r["high"])] for _, r in df.iterrows()],
            "volume": [float(v) for v in df["volume"]],
        }

    daily_data = _ohlcv(daily)
    daily_data["rsi"] = [None if pd.isna(v) else round(float(v), 2) for v in daily_rsi]

    weekly_data = _ohlcv(weekly)
    weekly_data["ema"] = [None if pd.isna(v) else round(float(v), 4) for v in weekly_ema]

    name = next((e.get("name", code) for e in CACHE["cfg"].get("universe", []) if e["code"] == code), code)

    return jsonify({
        "code": code,
        "name": name,
        "date": date_str,
        "daily": daily_data,
        "weekly": weekly_data,
        "rsiPeriod": rsi_period,
        "emaPeriod": ema_period,
    })


@app.route("/api/refresh_data", methods=["POST"])
def api_refresh_data():
    guard = _require_ready()
    if guard:
        return guard
    """Fetch latest data: intraday cache (pre-market) or confirmed CSV update (post-market)."""
    try:
        result = refresh_data()
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/data_status")
def api_data_status():
    """Return current data freshness: latest CSV date, intraday cache status."""
    all_daily = CACHE.get("all_daily", {})
    ic = CACHE.get("intraday_cache", {})
    ic_date = CACHE.get("intraday_date")
    ic_times = [v.get("time") for v in ic.values() if v.get("time")]
    ic_time = ic_times[-1] if ic_times else None

    csv_latest = ""
    for df in all_daily.values():
        if len(df) > 0:
            d = df["date"].iloc[-1]
            ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            if ds > csv_latest:
                csv_latest = ds

    # BUG-060: include benchmark ETF data status
    bm_status = {}
    try:
        for bm_code in ("510050", "510300", "510500", "159915"):
            bm_path = CACHE.get("hs300_daily_df")
            if bm_code == "510300" and bm_path is not None:
                bm_df = bm_path
            else:
                from quant_data_fetcher import DATA_DIR as QDATA_DIR
                import pandas as pd
                bm_csv = QDATA_DIR / f"{bm_code}_daily.csv"
                bm_df = pd.read_csv(bm_csv, parse_dates=["date"]) if bm_csv.exists() else None
            if bm_df is not None and len(bm_df) > 0:
                bm_last = bm_df["date"].iloc[-1].strftime("%Y-%m-%d")
                bm_entry = {"date": bm_last, "rows": len(bm_df)}
                # BUG-061: reflect intraday cache availability for benchmark ETFs
                bm_ic = ic.get(bm_code)
                if bm_ic and bm_ic.get("date"):
                    bm_entry["intraday"] = bm_ic["date"]
                    bm_entry["intradayTime"] = bm_ic.get("time", "")
                bm_status[bm_code] = bm_entry
    except Exception:
        pass

    today = datetime.now().strftime("%Y-%m-%d")
    halted_codes = [code for code, v in ic.items() if v.get("halted")]
    return jsonify({
        "ready": CACHE.get("ready", False),
        "csvLatestDate": csv_latest,
        "todayDate": today,
        "intradayCacheDate": ic_date,
        "intradayCacheTime": ic_time,
        "intradayCacheCount": len(ic),
        "isPostMarket": _is_post_market(),
        "haltedEtfs": halted_codes,
        "benchmarkStatus": bm_status,
        "anomalyThreshold": DM_ANOMALY_THRESHOLD,
    })


@app.route("/api/etf_prices")
def api_etf_prices():
    guard = _require_ready()
    if guard:
        return guard
    """Return OHLCV + RSI series for an ETF, between optional start/end.
    Supports daily or weekly frequency.

    Query params:
        code: ETF code
        start: YYYY-MM-DD (inclusive, optional)
        end: YYYY-MM-DD (inclusive, optional)
        rsi_period: int (default 14)
        freq: "daily" (default) or "weekly"
    """
    code = request.args.get("code", "").strip()
    start_str = request.args.get("start", "").strip()
    end_str = request.args.get("end", "").strip()
    rsi_period = int(request.args.get("rsi_period", 14))
    freq = request.args.get("freq", "daily").strip().lower()

    if not code or code not in CACHE.get("all_daily", {}):
        return jsonify({"error": f"Unknown code: {code}"}), 404

    # Source dataframe: merge intraday cache so kline-replay shows EOD-estimated data
    if freq == "weekly":
        full = _get_weekly_with_cache(code)
    else:
        full = _get_daily_with_cache(code)

    # Compute RSI on full history (so values inside [start, end] have proper warmup)
    rsi_full = calc_rsi(full["close"].astype(float), period=rsi_period)

    df = full.copy()
    rsi_series = rsi_full.copy()
    rsi_series.index = df.index

    if start_str:
        try:
            mask = df["date"] >= pd.Timestamp(start_str)
            df = df[mask]
            rsi_series = rsi_series[mask]
        except Exception:
            pass
    if end_str:
        try:
            mask = df["date"] <= pd.Timestamp(end_str)
            df = df[mask]
            rsi_series = rsi_series[mask]
        except Exception:
            pass

    # Volume: try column "amount" (成交额) if present, else fallback to "volume"
    vol_col = "amount" if "amount" in df.columns else "volume"
    has_vol = vol_col in df.columns

    return jsonify({
        "code": code,
        "freq": freq,
        "dates": [d.strftime("%Y-%m-%d") for d in df["date"]],
        "open":  [round(float(v), 4) for v in df["open"]]  if "open"  in df.columns else [],
        "high":  [round(float(v), 4) for v in df["high"]]  if "high"  in df.columns else [],
        "low":   [round(float(v), 4) for v in df["low"]]   if "low"   in df.columns else [],
        "close": [round(float(v), 4) for v in df["close"]],
        "volume": [round(float(v), 2) for v in df[vol_col]] if has_vol else [],
        "volumeLabel": "成交额" if vol_col == "amount" else "成交量",
        "rsi": [None if pd.isna(v) else round(float(v), 2) for v in rsi_series],
        "rsiPeriod": rsi_period,
    })


@app.route("/api/heatmap_data")
def api_heatmap_data():
    guard = _require_ready()
    if guard:
        return guard
    """Return precomputed N-day rolling returns for all ETFs."""
    lookback = request.args.get("lookback", "20").strip()
    force = request.args.get("force", "0") == "1"

    if force or "heatmap" not in CACHE:
        _precompute_heatmap_returns()

    heatmap = CACHE.get("heatmap", {})
    if lookback not in heatmap:
        return jsonify({"error": f"Unknown lookback: {lookback}"}), 400

    data = heatmap[lookback]
    cfg = CACHE.get("cfg", {})

    # Collect all dates (union of all ETF date keys, sorted)
    all_dates = set()
    for code, ret_map in data.items():
        all_dates.update(ret_map.keys())
    dates = sorted(all_dates)

    etfs_out = []
    for entry in cfg.get("universe", []):
        code = entry["code"]
        if code not in data:
            continue
        ret_map = data[code]
        rets = [ret_map.get(d) for d in dates]  # None if date missing
        etfs_out.append({
            "code": code,
            "name": entry.get("name", code),
            "sector": entry.get("sector", ""),
            "returns": rets,
        })

    return jsonify({"lookback": int(lookback), "dates": dates, "etfs": etfs_out})


# ── Data Management Panel APIs ──

@app.route("/api/data_matrix")
def api_data_matrix():
    """Return ETF x date coverage matrix with anomaly detection."""
    guard = _require_ready()
    if guard:
        return guard

    start_str = request.args.get("start", "").strip()
    end_str = request.args.get("end", "").strip()
    freq = request.args.get("freq", "daily").strip()        # daily | weekly
    field = request.args.get("field", "close").strip()      # close | volume | amount
    codes_filter = request.args.get("codes", "").strip()    # comma-separated, optional

    # Determine date range: default last 60 trading days
    cal = load_trading_calendar()
    today = datetime.now().strftime("%Y-%m-%d")
    trading_days = sorted(d for d in cal if today >= d >= "2020-01-01")

    if end_str:
        end_idx = min(
            next((i for i, d in enumerate(trading_days) if d > end_str), len(trading_days)),
            len(trading_days)
        )
    else:
        end_idx = len(trading_days)

    n_days = 30
    start_idx = max(0, end_idx - n_days)
    dates = trading_days[start_idx:end_idx]

    # Weekly mode: use actual dates from weekly CSVs (holiday-shortened weeks
    # may end on Thursday, not Friday)
    if freq == "weekly":
        all_weekly_dates = set()
        for code in CACHE.get("all_weekly", {}):
            wdf = CACHE["all_weekly"][code]
            if wdf is not None and len(wdf) > 0:
                for _, row in wdf.iterrows():
                    d = row["date"]
                    if hasattr(d, "strftime"): d = d.strftime("%Y-%m-%d")
                    else: d = str(d)[:10]
                    if start_idx <= 0 or d >= dates[0]:
                        all_weekly_dates.add(d)
        # Also check benchmark weekly CSVs
        for bm_code in ("510300", "510050", "510500", "159915"):
            bm_wpath = DATA_DIR / "quant" / f"{bm_code}_weekly.csv"
            if bm_wpath.exists():
                try:
                    bm_wdf = pd.read_csv(bm_wpath, parse_dates=["date"])
                    for _, row in bm_wdf.iterrows():
                        d = row["date"]
                        if hasattr(d, "strftime"): d = d.strftime("%Y-%m-%d")
                        else: d = str(d)[:10]
                        if start_idx <= 0 or d >= dates[0]:
                            all_weekly_dates.add(d)
                except Exception:
                    pass
        dates = sorted(all_weekly_dates)

    date_set = set(dates)

    cfg = CACHE.get("cfg", {})
    ic = CACHE.get("intraday_cache", {})
    ic_date = CACHE.get("intraday_date", "")
    today_str = datetime.now().strftime("%Y-%m-%d")

    etfs_out = []
    cells = {}
    summary = {"totalCells": 0, "csvCount": 0, "intradayCount": 0,
               "missingCount": 0, "haltedCount": 0, "anomalyCount": 0}

    # Helper: build cell data for one code
    def _build_code_cells(code, is_qdii=False):
        # ── Factor cache mode (f1/f3/f7) ──
        if freq in ("f1", "f3", "f7"):
            import pickle as _pickle
            cache_dir = DATA_DIR / "quant" / ".factor_cache"
            daily_df = CACHE["all_daily"].get(code)
            if daily_df is None or not cache_dir.exists():
                return {d: {"status": "missing", "value": None} for d in dates}
            n_rows = len(daily_df)
            cache_data = None
            for cf in cache_dir.glob("fc_*.pickle"):
                try:
                    with open(cf, "rb") as fp:
                        data = _pickle.load(fp)
                    if "daily_dates" in data and len(data.get("daily_dates", [])) == n_rows:
                        cache_data = data
                        break
                except Exception:
                    pass
            if cache_data is None:
                return {d: {"status": "missing", "value": None} for d in dates}

            # F1/F3/F7 all use daily_dates (F1 is computed per trading day)
            src_dates = cache_data.get("daily_dates", [])
            src_values = cache_data.get(freq, [])
            if len(src_dates) != len(src_values):
                return {d: {"status": "missing", "value": None} for d in dates}

            # Build date→value map
            val_map = {}
            for i, dt in enumerate(src_dates):
                ds = str(dt)[:10] if hasattr(dt, "strftime") else str(dt)[:10]
                v = float(src_values[i]) if not (isinstance(src_values[i], float) and np.isnan(src_values[i])) else None
                val_map[ds] = v

            code_cells = {}
            for d in dates:
                if d in val_map and val_map[d] is not None:
                    v = round(val_map[d], 4)
                    code_cells[d] = {"status": "csv", "value": v, "close": v}
                    summary["csvCount"] += 1
                elif d in val_map:
                    # Cache exists but value is NaN (insufficient history for this date)
                    code_cells[d] = {"status": "nodata", "value": None}
                else:
                    code_cells[d] = {"status": "missing", "value": None}
                    summary["missingCount"] += 1
            return code_cells

        # Select source DataFrame based on freq
        if freq == "weekly":
            src_df = CACHE["all_weekly"].get(code)
            if src_df is None:
                # Rebuild weekly from daily on demand (benchmarks)
                daily_df = CACHE["all_daily"].get(code)
                if daily_df is not None:
                    from etf_report.core.quant_data_utils import rebuild_weekly_from_daily
                    src_df = rebuild_weekly_from_daily(daily_df)
                    if len(src_df) > 0:
                        CACHE["all_weekly"][code] = src_df
        else:
            src_df = CACHE["all_daily"].get(code)

        # Build date→value maps
        csv_dates = set()
        csv_values = {}  # field value per date
        csv_close_for_anomaly = {}  # close for anomaly calc (always needed)
        if src_df is not None:
            for _, row in src_df.iterrows():
                d = row["date"]
                if hasattr(d, "strftime"):
                    d = d.strftime("%Y-%m-%d")
                else:
                    d = str(d)[:10]
                csv_dates.add(d)
                # Value for the requested field
                if field == "volume":
                    csv_values[d] = float(row.get("volume", 0))
                elif field == "amount":
                    csv_values[d] = float(row.get("amount", 0))
                else:  # close
                    csv_values[d] = float(row["close"])
                csv_close_for_anomaly[d] = float(row["close"])

        # Anomaly detection on close (always, regardless of field)
        anomaly_threshold = DM_ANOMALY_THRESHOLD
        anomalies = {}
        csv_dates_sorted = sorted(csv_dates)
        for i in range(1, len(csv_dates_sorted)):
            d = csv_dates_sorted[i]
            prev_d = csv_dates_sorted[i - 1]
            if d not in date_set:
                continue
            prev_close = csv_close_for_anomaly.get(prev_d)
            cur_close = csv_close_for_anomaly.get(d)
            if prev_close and cur_close and prev_close > 0:
                ret = (cur_close - prev_close) / prev_close
                if abs(ret) > anomaly_threshold:
                    pct = round(ret * 100, 1)
                    anomalies[d] = {
                        "anomaly": "surge" if ret > 0 else "plunge",
                        "anomalyPct": pct,
                        "anomalyLabel": f"{'涨' if ret>0 else '跌'}{abs(pct):.1f}%"
                    }

        code_cells = {}
        for d in dates:
            cell_data = {"status": "missing", "value": None}
            if d in csv_dates:
                val = round(csv_values.get(d, 0), 4)
                cell_data = {"status": "csv", "value": val, "close": round(csv_close_for_anomaly.get(d, 0), 4)}
                if d in anomalies:
                    cell_data.update(anomalies[d])
                    summary["anomalyCount"] += 1
                summary["csvCount"] += 1
            elif d == ic_date and code in ic and freq == "daily":
                cached = ic[code]
                if cached.get("halted"):
                    cell_data = {"status": "halted", "value": None}
                    summary["haltedCount"] += 1
                else:
                    t = cached.get("time", "")
                    ival = float(cached.get(field, cached.get("close", 0)))
                    cell_data = {"status": "intraday", "time": t,
                                "value": round(ival, 4), "close": round(cached.get("close", 0), 4)}
                    summary["intradayCount"] += 1
            else:
                summary["missingCount"] += 1
            code_cells[d] = cell_data
        return code_cells

    # Parse optional code filter
    code_set = set(codes_filter.split(",")) if codes_filter else None

    # Universe ETFs
    for entry in cfg.get("universe", []):
        code = entry["code"]
        if code_set and code not in code_set: continue
        is_qdii = entry.get("qdii", False)
        cells[code] = _build_code_cells(code, is_qdii)
        etfs_out.append({
            "code": code, "name": entry.get("name", code),
            "sector": entry.get("sector", ""), "qdii": is_qdii,
        })

    # Benchmark indices (REQ-384: primary source is ETF CSV, loaded via
    # benchmark_data.INDEX_ETF_MAP — index CSV path is a mirror copy).
    BENCHMARKS = [
        ("510300", "沪深300", "基准"),
        ("510050", "上证50", "基准"),
        ("510500", "中证500", "基准"),
        ("159915", "创业板指", "基准"),
    ]
    for bm_code, bm_name, bm_sector in BENCHMARKS:
        if code_set and bm_code not in code_set: continue
        # Always reload benchmarks from disk to avoid stale cache after refetch
        bm_path = DATA_DIR / "quant" / f"{bm_code}_daily.csv"
        if bm_path.exists():
            try:
                bm_df = pd.read_csv(bm_path, parse_dates=["date"])
                if len(bm_df) > 0:
                    CACHE["all_daily"][bm_code] = bm_df
            except Exception:
                pass
        if bm_code in CACHE["all_daily"]:
            cells[bm_code] = _build_code_cells(bm_code, is_qdii=False)
            etfs_out.append({
                "code": bm_code, "name": bm_name,
                "sector": bm_sector, "qdii": False,
            })

    summary["totalCells"] = len(etfs_out) * len(dates)
    return jsonify({"dates": dates, "etfs": etfs_out, "cells": cells, "summary": summary})


@app.route("/api/data_delete", methods=["POST"])
def api_data_delete():
    """Delete CSV rows for given ETF x continuous-date-range operations.

    Accepts optional 'freq' param:
      - 'daily' (default): delete date range from daily CSV, rebuild weekly
      - 'weekly': expand each selected week to its trading-day boundaries,
                   delete those days from daily CSV, rebuild weekly
    """
    guard = _require_ready()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    ops = body.get("operations", [])
    freq = body.get("freq", "daily")
    if not ops:
        return jsonify({"ok": False, "error": "empty operations"}), 400

    today_str = datetime.now().strftime("%Y-%m-%d")
    deleted_total = 0
    errors = []

    # For weekly mode: expand week boundaries using trading calendar
    if freq == "weekly":
        cal = load_trading_calendar()
        cal_dates = [d for d in cal]  # sorted list of trading days
        expanded_ops = []
        for op in ops:
            code = op.get("code", "")
            start = op.get("start", "")
            end = op.get("end", "")
            if not code or not start or not end:
                errors.append(f"invalid op: {op}")
                continue
            if start == "1900-01-01" and end == "2099-12-31":
                # Full row delete → keep as-is
                expanded_ops.append(op)
                continue
            # Find the week containing each selected date, expand to full week
            week_dates = set()
            for sel_date in [start, end]:
                sel_dt = datetime.strptime(sel_date, "%Y-%m-%d")
                sel_isoweek = sel_dt.isocalendar()[1]
                sel_isoyear = sel_dt.isocalendar()[0]
                for d in cal_dates:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    if dt.isocalendar()[0] == sel_isoyear and dt.isocalendar()[1] == sel_isoweek:
                        week_dates.add(d)
            if not week_dates:
                expanded_ops.append(op)
                continue
            sorted_week = sorted(week_dates)
            expanded_ops.append({
                "code": code,
                "start": sorted_week[0],
                "end": sorted_week[-1],
            })
        ops = expanded_ops

    for op in ops:
        code = op.get("code", "")
        start = op.get("start", "")
        end = op.get("end", "")
        if not code or not start or not end:
            errors.append(f"invalid op: {op}")
            continue
        if start > end:
            errors.append(f"{code}: start > end ({start} > {end})")
            continue

        # Safety: don't delete today if intraday
        if start <= today_str <= end and CACHE.get("intraday_cache"):
            errors.append(f"{code}: refuses to delete today ({today_str}) while intraday cache active")
            continue

        csv_path = DATA_DIR / "quant" / f"{code}_daily.csv"
        if not csv_path.exists():
            continue

        try:
            df = pd.read_csv(csv_path, parse_dates=["date"])
            before = len(df)
            df = df[~df["date"].between(start, end)]
            after = len(df)

            # Safety: keep at least 80 rows
            if after < 80:
                errors.append(f"{code}: would leave only {after} rows (<80 minimum), skipped")
                continue

            deleted = before - after
            if deleted > 0:
                df.to_csv(csv_path, index=False)
                # Rebuild weekly
                daily_df, _ = load_etf_data(code)
                if daily_df is not None:
                    weekly_df = rebuild_weekly_from_daily(daily_df)
                    weekly_path = DATA_DIR / "quant" / f"{code}_weekly.csv"
                    weekly_df.to_csv(weekly_path, index=False)
                deleted_total += deleted
        except Exception as e:
            errors.append(f"{code}: {e}")

    # Reload affected data
    cfg = CACHE.get("cfg", {})
    _reload_csv_to_cache(cfg)
    # Invalidate heatmap cache
    if "heatmap" in CACHE:
        del CACHE["heatmap"]

    return jsonify({"ok": len(errors) == 0, "deleted": deleted_total, "errors": errors})


@app.route("/api/data_refetch", methods=["POST"])
def api_data_refetch():
    """Re-fetch ETF data for given continuous date ranges."""
    guard = _require_ready()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    ops = body.get("operations", [])
    freq = body.get("freq", "daily")
    if not ops:
        return jsonify({"ok": False, "error": "empty operations"}), 400

    from quant_data_fetcher import update_single
    cfg = CACHE.get("cfg", {})

    # Determine market phase
    today_str = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    is_trading = is_trading_day(now)
    is_intraday = is_trading and not _is_post_market()
    # During intraday, never write today's data to CSV — cap to yesterday
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d") if is_intraday else today_str

    # Check if any operation covers today (for intraday Sina fetch)
    codes_for_today = set()
    for op in ops:
        start = op.get("start", ""); end = op.get("end", "")
        if start <= today_str <= end:
            codes_for_today.add(op.get("code", ""))

    # Intraday: use shared path with refresh_data
    if codes_for_today and is_intraday:
        _populate_intraday_cache(cfg, now, today_str, now.strftime("%H:%M"), list(codes_for_today))

    results = {}
    for op in ops:
        code = op.get("code", "")
        start = op.get("start", "")
        end = op.get("end", "")
        if not code or not start or not end:
            continue
        # Cap end to yesterday if intraday
        effective_end = min(end, yesterday) if end > yesterday else end
        if effective_end < start:
            continue

        etf_entry = next((e for e in cfg.get("universe", []) if e["code"] == code), None)
        if not etf_entry:
            BM_NAMES = {"510300": "沪深300", "510050": "上证50", "510500": "中证500", "159915": "创业板指"}
            if code in BM_NAMES:
                etf_entry = {"code": code, "name": BM_NAMES[code],
                            "market": "sh" if code.startswith("000") else "sz"}
            else:
                continue

        try:
            if freq == "weekly":
                # 周线模式: 从日线重建周线，不拉取
                daily_df = CACHE["all_daily"].get(code)
                if daily_df is None:
                    daily_df, _ = load_etf_data(code)
                if daily_df is not None and len(daily_df) > 0:
                    from etf_report.core.quant_data_utils import rebuild_weekly_from_daily
                    weekly_df = rebuild_weekly_from_daily(daily_df)
                    weekly_path = DATA_DIR / "quant" / f"{code}_weekly.csv"
                    weekly_df.to_csv(weekly_path, index=False)
                    CACHE["all_weekly"][code] = weekly_df
                    results[code] = {"rows": len(weekly_df), "mode": "weekly_rebuild"}
                else:
                    results[code] = {"error": "no daily data to rebuild from"}
            else:
                # 日线模式: 覆盖式更新 — delete range from CSV, then re-fetch
                csv_path = DATA_DIR / "quant" / f"{code}_daily.csv"
                if csv_path.exists():
                    df = pd.read_csv(csv_path, parse_dates=["date"])
                    before = len(df)
                    df = df[~df["date"].between(start, effective_end)]
                    after = len(df)
                    if after >= 80:  # safety
                        df.to_csv(csv_path, index=False)
                rows, _, mode = update_single(etf_entry, full=False, end_date=effective_end)
                results[code] = {"rows": rows, "mode": mode}
        except Exception as e:
            results[code] = {"error": str(e)}

    # Reload all (including benchmarks loaded on-demand by data_matrix)
    _reload_csv_to_cache(cfg)
    if "heatmap" in CACHE:
        del CACHE["heatmap"]

    return jsonify({"ok": True, "results": results})


@app.route("/api/data_fill_gaps", methods=["POST"])
def api_data_fill_gaps():
    """Detect and fill missing trading-day data for given ETFs in a date range."""
    guard = _require_ready()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    codes = body.get("codes") or []
    start = body.get("start", "")
    end = body.get("end", "")
    freq = body.get("freq", "daily")

    if not start or not end:
        return jsonify({"ok": False, "error": "start/end required"}), 400

    cfg = CACHE.get("cfg", {})
    all_etfs = list(cfg.get("universe", []))

    # Add benchmark indices to target list
    BM_ENTRIES = [
        {"code": "510300", "name": "沪深300", "market": "sh"},
        {"code": "510050", "name": "上证50", "market": "sh"},
        {"code": "510500", "name": "中证500", "market": "sh"},
        {"code": "159915", "name": "创业板指", "market": "sz"},
    ]
    all_etfs.extend(BM_ENTRIES)

    # Filter to requested codes (or all)
    target_etfs = [e for e in all_etfs if not codes or e["code"] in codes]

    # Cap end for intraday
    today_str = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    is_trading = is_trading_day(now)
    is_intraday = is_trading and not _is_post_market()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d") if is_intraday else today_str
    effective_end = end if end <= yesterday else yesterday
    if start <= today_str <= end and is_intraday:
        codes = [etf["code"] for etf in target_etfs]
        _populate_intraday_cache(cfg, now, today_str, now.strftime("%H:%M"), codes)

    # Determine expected dates for gap detection
    cal = load_trading_calendar()
    if freq == "weekly":
        # Expected Friday dates in range
        expected_dates = [d for d in cal if start <= d <= effective_end
                         and datetime.strptime(d, "%Y-%m-%d").isoweekday() == 5]
    else:
        expected_dates = [d for d in cal if start <= d <= effective_end]

    from quant_data_fetcher import update_single
    from etf_report.core.quant_data_utils import rebuild_weekly_from_daily
    filled = {}
    total_filled = 0

    for etf in target_etfs:
        code = etf["code"]

        if freq == "weekly":
            # Check weekly CSV for gaps, rebuild from daily.
            # BUG-063c: first fill daily gaps, then rebuild weekly.
            weekly_df = CACHE["all_weekly"].get(code)
            csv_dates = set()
            if weekly_df is not None:
                for _, row in weekly_df.iterrows():
                    d = row["date"]
                    if hasattr(d, "strftime"): d = d.strftime("%Y-%m-%d")
                    else: d = str(d)[:10]
                    csv_dates.add(d)

            missing = [d for d in expected_dates if d not in csv_dates]
            if not missing:
                continue

            # Step 1: fill daily gaps first (so weekly rebuild has complete source)
            daily_df = CACHE["all_daily"].get(code)
            if daily_df is None:
                daily_df, _ = load_etf_data(code)
            if daily_df is not None and len(daily_df) > 0:
                daily_dates = set()
                for _, row in daily_df.iterrows():
                    d = row["date"]
                    if hasattr(d, "strftime"): d = d.strftime("%Y-%m-%d")
                    else: d = str(d)[:10]
                    daily_dates.add(d)
                # expected dates for daily: all trading days in range
                daily_expected = [d for d in cal if start <= d <= effective_end]
                daily_missing = [d for d in daily_expected if d not in daily_dates]
                if daily_missing:
                    gap_end = daily_missing[-1]
                    gap_start = daily_missing[0]
                    try:
                        rows, _, mode = update_single(etf, full=False, end_date=gap_end)
                        # Reload daily after fetch
                        daily_df, _ = load_etf_data(code)
                        if daily_df is not None:
                            CACHE["all_daily"][code] = daily_df
                    except Exception as e:
                        filled[code] = {"gaps": len(missing), "error": f"daily gap fill failed: {e}"}
                        continue

            # Step 2: rebuild weekly from (now complete) daily
            daily_df = CACHE["all_daily"].get(code)
            if daily_df is None:
                daily_df, _ = load_etf_data(code)
            if daily_df is not None and len(daily_df) > 0:
                new_weekly = rebuild_weekly_from_daily(daily_df)
                wpath = DATA_DIR / "quant" / f"{code}_weekly.csv"
                new_weekly.to_csv(wpath, index=False)
                CACHE["all_weekly"][code] = new_weekly
                filled[code] = {"gaps": len(missing), "rows_added": len(new_weekly), "mode": "weekly_rebuild"}
                total_filled += len(missing)
            else:
                filled[code] = {"gaps": len(missing), "error": "no daily data"}
        else:
            # Daily mode: detect gaps, fetch incrementally
            daily_df = CACHE["all_daily"].get(code)
            csv_dates = set()
            if daily_df is not None:
                for _, row in daily_df.iterrows():
                    d = row["date"]
                    if hasattr(d, "strftime"): d = d.strftime("%Y-%m-%d")
                    else: d = str(d)[:10]
                    csv_dates.add(d)

            missing = [d for d in expected_dates if d not in csv_dates]
            if not missing:
                continue

            gap_end = missing[-1]
            gap_start = missing[0]
            last_csv_date = max(csv_dates) if csv_dates else ""
            try:
                # Mid-file gap (CSV has later data past the gap): patch the specific range.
                # End gap (gap extends beyond CSV): use incremental append.
                if last_csv_date and gap_end < last_csv_date:
                    from quant_data_fetcher import patch_range
                    rows, _, mode = patch_range(etf, gap_start, gap_end)
                else:
                    rows, _, mode = update_single(etf, full=False, end_date=gap_end)
                filled[code] = {"gaps": len(missing), "rows_added": rows, "mode": mode}
                total_filled += len(missing)
            except Exception as e:
                filled[code] = {"gaps": len(missing), "error": str(e)}

    _reload_csv_to_cache(cfg)
    if "heatmap" in CACHE:
        del CACHE["heatmap"]

    return jsonify({"ok": True, "filled": total_filled, "perEtf": filled})


# ── Factor Cache Management APIs ──

@app.route("/api/factor_cache_status")
def api_factor_cache_status():
    """Return per-ETF factor cache status (fresh/stale/missing)."""
    guard = _require_ready()
    if guard: return guard

    cfg = CACHE.get("cfg", {})
    cache_dir = DATA_DIR / "quant" / ".factor_cache"
    etfs_out = []
    caches = {}

    for entry in cfg.get("universe", []):
        code = entry["code"]
        daily_df = CACHE["all_daily"].get(code)
        if daily_df is None: continue

        # Check cache files for this ETF (match by row count = simple and effective)
        cache_files = []
        n_rows = len(daily_df)
        if cache_dir.exists():
            for f in cache_dir.glob("fc_*.pickle"):
                try:
                    import pickle
                    with open(f, "rb") as fp:
                        data = pickle.load(fp)
                    if "daily_dates" in data and len(data.get("daily_dates", [])) == n_rows:
                        cache_files.append({
                            "name": f.name,
                            "size_kb": round(f.stat().st_size / 1024, 1),
                            "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                        })
                except Exception:
                    pass

        file_count = len(cache_files)
        status = "fresh" if file_count > 0 else "missing"

        caches[code] = {
            "status": status,
            "file_count": file_count,
            "files": cache_files,
        }
        etfs_out.append({
            "code": code,
            "name": entry.get("name", code),
            "sector": entry.get("sector", ""),
        })

    return jsonify({"etfs": etfs_out, "caches": caches})


@app.route("/api/factor_cache_delete", methods=["POST"])
def api_factor_cache_delete():
    """Delete factor cache files for given ETF codes."""
    guard = _require_ready()
    if guard: return guard

    body = request.get_json(silent=True) or {}
    codes = body.get("codes") or []
    cache_dir = DATA_DIR / "quant" / ".factor_cache"
    if not cache_dir.exists():
        return jsonify({"ok": True, "deleted": 0})

    deleted = 0
    for code in codes:
        daily_df = CACHE["all_daily"].get(code)
        if daily_df is None: continue
        n_rows = len(daily_df)
        for f in cache_dir.glob("fc_*.pickle"):
            try:
                import pickle
                with open(f, "rb") as fp:
                    data = pickle.load(fp)
                if "daily_dates" in data and len(data.get("daily_dates", [])) == n_rows:
                    f.unlink()
                    deleted += 1
            except Exception:
                pass

    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/factor_cache_rebuild", methods=["POST"])
def api_factor_cache_rebuild():
    """Delete factor cache for given ETFs; backtest will recreate on next run."""
    guard = _require_ready()
    if guard: return guard

    body = request.get_json(silent=True) or {}
    codes = body.get("codes")  # None/null = all ETFs
    cache_dir = DATA_DIR / "quant" / ".factor_cache"
    if not cache_dir.exists():
        return jsonify({"ok": True, "rebuilt": 0})

    # None/null means "all ETFs" — collect all codes with cached data
    if codes is None:
        codes = list(CACHE.get("all_daily", {}).keys())

    deleted = 0
    for code in (codes or []):
        # Delete ALL cached factor files for this ETF (by filename prefix).
        # Parameter change → hash changes → old caches become orphan files.
        # Row-count matching is fragile (misses orphans when data rows change).
        # Delete everything; the backtest engine will rebuild with current gam-0 params.
        for f in cache_dir.glob(f"fc_{code}_*.pickle"):
            try:
                f.unlink()
                deleted += 1
            except Exception:
                pass

    # Trigger recompute via backtest
    if codes:
        try:
            from quant_backtest import run_backtest
            nav, _, _ = run_backtest(
                start_date="2026-06-01", end_date="2026-06-30",
                preset="gam-0", return_details=False, verbose=False
            )
        except Exception:
            pass  # backtest will populate caches for the codes it processes

    return jsonify({"ok": True, "rebuilt": len(codes), "caches_deleted": deleted})


# ── Split / Corporate Action APIs ──

@app.route("/api/split_status")
def api_split_status():
    """Return split/corporate action status for all ETFs."""
    guard = _require_ready()
    if guard: return guard

    cfg = CACHE.get("cfg", {})
    _ensure_splits_detected(cfg)
    _detect_pending_splits_from_cache(cfg)  # REQ-360: check intraday vs CSV for splits

    result = {}
    for entry in cfg.get("universe", []):
        code = entry["code"]
        splits = sorted(
            [e for e in _SPLIT_EVENTS.get(code, [])
             if e.get("action") == "share_split"],
            key=lambda e: e.get("ex_date", ""), reverse=True
        )
        if not splits:
            result[code] = {"has_split": False, "status": "none"}
            continue

        latest = max(splits, key=lambda e: e.get("ex_date", ""))
        # Check if the split data has been repaired (no close jump in CSV)
        daily = CACHE["all_daily"].get(code)
        status = "repaired"
        symptom = ""

        # REQ-360: bridge detected a split that needs CSV repair — always show ⚠
        if code in _SPLIT_PENDING_REPAIR:
            status = "pending_repair"
            symptom = f"split bridge: ratio={latest.get('ratio',1.0)}, CSV still pre-split"
        elif daily is not None and len(daily) >= 2:
            close = daily["close"].astype(float).values
            # Scan last 10 rows for any close jump >30% (split symptom)
            for i in range(len(close)-1, max(len(close)-10, 1), -1):
                prev = close[i-1]; cur = close[i]
                if prev > 0 and abs(cur - prev) / prev > 0.30:
                    status = "pending_repair"
                    symptom = f"close jump {((cur-prev)/prev*100):+.0f}%: {prev:.3f}→{cur:.3f}"
                    break

        result[code] = {
            "has_split": True,
            "ex_date": latest.get("ex_date", ""),
            "ratio": latest.get("ratio", 1.0),
            "status": status,
            "symptom": symptom,
        }

    return jsonify(result)


@app.route("/api/data_full_refetch", methods=["POST"])
def api_data_full_refetch():
    """Full refetch for given ETFs (deletes CSV → Tencent full=True → rebuild weekly + cache)."""
    guard = _require_ready()
    if guard: return guard

    body = request.get_json(silent=True) or {}
    codes = body.get("codes") or []
    verify_split = body.get("verify_split", False)

    from quant_data_fetcher import update_single
    cfg = CACHE.get("cfg", {})
    _ensure_splits_detected(cfg)  # ensure _SPLIT_EVENTS is loaded

    results = {}
    for code in codes:
        etf_entry = next((e for e in cfg.get("universe", []) if e["code"] == code), None)
        if not etf_entry:
            BM_NAMES = {"510300": "沪深300", "510050": "上证50", "510500": "中证500", "159915": "创业板指"}
            if code in BM_NAMES:
                etf_entry = {"code": code, "name": BM_NAMES[code],
                            "market": "sh" if code.startswith("000") else "sz"}
            else:
                results[code] = {"error": "unknown code"}
                continue

        try:
            # Split repair: apply ratio to CSV (API may not have updated qfq yet)
            if verify_split:
                splits = sorted(
                    [e for e in _SPLIT_EVENTS.get(code, [])
                     if e.get("action") == "share_split"],
                    key=lambda e: e.get("ex_date", ""), reverse=True
                )
                if splits:
                    ratio = splits[0]["ratio"]  # most recent split
                    ex_date = splits[0]["ex_date"]
                    csv_path = DATA_DIR / "quant" / f"{code}_daily.csv"
                    if csv_path.exists():
                        df = pd.read_csv(csv_path, parse_dates=["date"])
                        mask = df["date"] <= ex_date
                        before_close = float(df.loc[df["date"] == "2026-06-30", "close"].iloc[0]) if "2026-06-30" in df["date"].values else 0
                        for col in ["open", "close", "high", "low"]:
                            df.loc[mask, col] = df.loc[mask, col] / ratio
                        after_close = float(df.loc[df["date"] == "2026-06-30", "close"].iloc[0]) if "2026-06-30" in df["date"].values else 0
                        with open(DATA_DIR / "quant" / ".split_debug.log", "a") as _dbg:
                            _dbg.write(f"{code}: ratio={ratio} type={type(ratio).__name__} ex={ex_date} mask={mask.sum()} before={before_close:.4f} after={after_close:.4f}\n")
                        df.to_csv(csv_path, index=False)
                        # Rebuild weekly
                        from etf_report.core.quant_data_utils import rebuild_weekly_from_daily
                        wdf = rebuild_weekly_from_daily(df)
                        wpath = DATA_DIR / "quant" / f"{code}_weekly.csv"
                        wdf.to_csv(wpath, index=False)
                        rows = int(mask.sum())
                        mode = "split_adjust"
                        # Verify
                        c = df["close"].astype(float).values
                        verified = True
                        for i in range(max(0, len(c)-10), len(c)-1):
                            if c[i] > 0 and abs(c[i+1]-c[i])/c[i] > 0.30:
                                verified = False; break
                        if verified:
                            _SPLIT_PENDING_REPAIR.discard(code)  # REQ-360: CSV repaired, clear ⚠
                    else:
                        rows, mode, verified = 0, "no_csv", False
                else:
                    rows, _, mode = update_single(etf_entry, full=True)
                    verified = True
                    _SPLIT_PENDING_REPAIR.discard(code)
            else:
                rows, _, mode = update_single(etf_entry, full=True)
                verified = True
                _SPLIT_PENDING_REPAIR.discard(code)
            results[code] = {"rows": rows, "mode": mode, "verified": bool(verified)}
        except Exception as e:
            results[code] = {"error": str(e)}

    # Reload + rebuild factor caches
    _reload_csv_to_cache(cfg)
    # Clear factor caches for refetched codes
    cache_dir = DATA_DIR / "quant" / ".factor_cache"
    if cache_dir.exists():
        for code in codes:
            n_rows = len(CACHE["all_daily"].get(code, pd.DataFrame()))
            if n_rows == 0: continue
            for f in cache_dir.glob("fc_*.pickle"):
                try:
                    import pickle
                    with open(f, "rb") as fp:
                        data = pickle.load(fp)
                    if "daily_dates" in data and len(data.get("daily_dates", [])) == n_rows:
                        f.unlink()
                except Exception:
                    pass
    # Rebuild factor caches
    try:
        from quant_backtest import _precompute_factors
        target_daily = {c: CACHE["all_daily"][c] for c in codes if c in CACHE["all_daily"]}
        target_weekly = {c: CACHE["all_weekly"].get(c) for c in codes}
        if target_daily:
            _precompute_factors(target_daily, target_weekly,
                ema_period=20, vol_window=20,
                f7_window=20, f7_lookback=250, f7_min_days=60, f7_sigma_floor=0.01,
                f1_daily_ema=False, f1_daily_ma=False, f1_active_days=127)
    except Exception:
        pass

    return jsonify({"ok": True, "results": results})


TUNER_PORT = DEV_TUNER_PORT


def is_port_in_use(port):
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_tuner_alive(port):
    """Check if the Tuner Flask server is actually running on the port."""
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/presets", timeout=2)
        data = json.loads(resp.read())
        return isinstance(data, dict) and len(data) > 0
    except Exception:
        return False


def try_open_browser(url):
    """Open browser on Windows if not already open."""
    try:
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        import os
        if os.path.isfile(edge):
            subprocess.Popen([edge, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import webbrowser
            webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    import argparse as _argparse
    _parser = _argparse.ArgumentParser(description="Quant Tuner - 量化策略调参服务器")
    _parser.add_argument("--auto", action="store_true",
                         help="自动检测并更新数据（冷启动或增量），然后启动 Tuner")
    _parser.add_argument("--full", action="store_true",
                         help="配合 --auto 使用，全量重新拉取数据")
    _parser.add_argument("--no-browser", action="store_true",
                         help="启动服务但不自动打开浏览器，供外部启动器统一控制")
    _parser.add_argument("--preload-then-wait", action="store_true",
                         help="Hot-swap: preload synchronously, signal via file, then wait for old process to be killed before binding port")
    _parser.add_argument("--readonly", action="store_true",
                         help="Disable write endpoints — for stable/production use")
    _parser.add_argument("--port", type=int, default=DEV_TUNER_PORT,
                         help="HTTP server port (default: %(default)s)")
    _args = _parser.parse_args()

    if _args.port != DEV_TUNER_PORT:
        TUNER_PORT = _args.port

    _hot_swap = _args.preload_then_wait
    CACHE["readonly"] = _args.readonly

    if not _hot_swap:
        # Normal mode: ensure port is free before starting
        for _retry in range(10):
            if not is_port_in_use(TUNER_PORT):
                break
            if _retry == 0:
                print(f"Port {TUNER_PORT} in use, waiting for release...")
            time.sleep(0.1)
        else:
            if is_tuner_alive(TUNER_PORT):
                print(f"Tuner already running at http://localhost:{TUNER_PORT}")
            else:
                print(f"Port {TUNER_PORT} is in use but NOT by Tuner.")
                print(f"Another process is occupying this port. Kill it or change TUNER_PORT.")
                print(f"Hint: netstat -ano | findstr :{TUNER_PORT}")
            sys.exit(0)

    # --auto: 自动更新数据 (in hot-swap mode this runs while old process still serves)
    if _args.auto:
        print("=" * 50)
        print("Auto mode: checking data freshness...")
        print("=" * 50)
        try:
            from quant_data_fetcher import load_universe, get_last_date, update_single
            universe = load_universe()
            csv_dir = DATA_DIR / "quant"

            # 统计缺失/过期数量
            missing = 0
            outdated = 0
            for etf in universe:
                daily_path = csv_dir / f"{etf['code']}_daily.csv"
                weekly_path = csv_dir / f"{etf['code']}_weekly.csv"
                if not daily_path.exists() or not weekly_path.exists():
                    missing += 1
                else:
                    last = get_last_date(daily_path)
                    if last:
                        import pandas as _pd
                        last_dt = _pd.Timestamp(last)
                        if (_pd.Timestamp.now() - last_dt).days > 3:
                            outdated += 1

            if missing > 0:
                print(f"  Cold start: {missing} ETFs missing data, will fetch full history")
            if outdated > 0:
                print(f"  Incremental: {outdated} ETFs have stale data, will update")
            if missing == 0 and outdated == 0:
                print("  All data up to date")

            # 执行更新
            ok, fail = 0, 0
            for i, etf in enumerate(universe, 1):
                try:
                    daily_rows, weekly_rows, mode = update_single(etf, full=_args.full)
                    tag = f"{mode}" + (f" daily+{daily_rows}" if daily_rows else "")
                    print(f"  [{i:2d}/{len(universe)}] {etf['name']}({etf['code']}) {tag}")
                    ok += 1
                    if i < len(universe):
                        time.sleep(1.0)
                except Exception as e:
                    print(f"  [{i:2d}/{len(universe)}] {etf['name']}({etf['code']}) FAIL: {e}")
                    fail += 1

            print(f"  Data update: OK={ok}, FAIL={fail}")
            if fail > 0:
                print("  WARNING: Some ETFs failed to update, Tuner may have incomplete data")
            print()

        except ImportError:
            print("  quant_data_fetcher not available, skipping data update")
        except Exception as e:
            print(f"  Data update error: {e}")

    # Preload
    if _hot_swap:
        # Hot-swap: preload synchronously while old process still serves the port.
        # Then signal PowerShell to kill the old process by writing a marker file.
        # Wait until PowerShell deletes the marker (means old process is gone),
        # then bind the port and start Flask.
        preload()

        signal_path = PROJECT_ROOT / ".tuner_ready_to_bind"
        signal_path.write_text("ready", encoding="utf-8")

        for _ in range(100):  # 10s timeout waiting for PowerShell to delete signal
            if not signal_path.exists():
                break
            time.sleep(0.1)
        else:
            # PowerShell may have crashed — clean up and proceed
            if signal_path.exists():
                signal_path.unlink(missing_ok=True)

        # OS may need ~50ms to release old port after taskkill /F
        for _ in range(20):
            if not is_port_in_use(TUNER_PORT):
                break
            time.sleep(0.05)
    else:
        # Normal mode: start preload in background, Flask serves frontend immediately.
        # The frontend polls /api/data_status until CACHE["ready"] is True.
        threading.Thread(target=preload, daemon=True).start()

    print("=" * 50)
    if _hot_swap:
        print(f"Quant Tuner ready: http://localhost:{TUNER_PORT}")
    else:
        print(f"Quant Tuner: http://localhost:{TUNER_PORT}")
        print("  (Loading data in background — page will refresh when ready)")
    print("Ctrl+C to stop")
    print("=" * 50)

    if not _args.no_browser:
        try_open_browser(f"http://localhost:{TUNER_PORT}")
    app.run(host="127.0.0.1", port=TUNER_PORT, debug=False, threaded=True)
