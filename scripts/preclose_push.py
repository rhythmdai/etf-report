#!/usr/bin/env python3
"""Signal push: auto-start Tuner → refresh data → backtest → push execution table to WeChat via Server酱.
   Behavior (intraday vs post-market CSV write) is controlled by COOL_OFF_TIME in quant_tuner.py.
   --refresh-only: stop after data refresh (no backtest/push).
   --output-md: write markdown to Desktop instead of push.
   --dry-run: print signal table to stdout, skip Server酱 push (for CI testing)."""
import sys, os, time, subprocess, requests, yaml
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "config").is_dir() and (parent / "scripts").is_dir())
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from trading_calendar import is_trading_day
from tuner_config import PROD_TUNER_PORT

TUNER_PORT = PROD_TUNER_PORT  # prod Tuner uses its own port (config) to avoid conflict with dev Tuner
TUNER_URL = f"http://localhost:{TUNER_PORT}"
REFRESH_ONLY = "--refresh-only" in sys.argv
OUTPUT_MD = "--output-md" in sys.argv   # REQ-353: write markdown to Desktop instead of push
DRY_RUN = "--dry-run" in sys.argv
IS_CI = os.environ.get("GITHUB_ACTIONS", "") == "true"
from etf_report.core.quant_contract import DEFAULT_PRESET
# Which preset to push = the one the system currently uses (DEFAULT_PRESET).
# PRECLOSE_PRESET is only an escape hatch for one-off runs; the workflow does not set it,
# so a normal daily push always follows DEFAULT_PRESET.
PRESET = os.environ.get("PRECLOSE_PRESET", "").strip() or DEFAULT_PRESET
SCHOOL_LABELS = {"gam": "赌徒", "zen": "禅者", "act": "精算师", "cst": "自定义"}
PRESET_LABEL = next((v for k, v in SCHOOL_LABELS.items() if PRESET.startswith(k + "-")), PRESET)
TUNER_STARTUP_TIMEOUT = 60  # max seconds to wait for Tuner

def log(msg):
    """Timestamped console output."""
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

def _tuner_ready():
    """Check if Tuner is responding AND fully loaded (presets available)."""
    try:
        r = requests.get(f"{TUNER_URL}/api/presets", timeout=5)
        if r.status_code != 200:
            return False
        data = r.json()
        # Real presets (not _universe_options) must be present
        presets = [k for k in data.keys() if not k.startswith("_")]
        return len(presets) > 0
    except Exception:
        return False

def _ensure_tuner():
    """Kill any existing Tuner on TUNER_PORT, then start a fresh one from THIS repo.
    Uses a dedicated port (config prod_port) to avoid conflict with dev Tuner (config dev_port).
    Works on Windows (netstat + taskkill) and Linux (fuser / lsof + kill)."""
    port_str = f":{TUNER_PORT}"

    if sys.platform == "win32":
        # Windows: netstat -ano | findstr :<TUNER_PORT> → taskkill /f /pid
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                if port_str in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    log(f"Tuner: killing existing PID {pid} on port {TUNER_PORT}")
                    subprocess.run(["taskkill", "/f", "/pid", pid],
                                   capture_output=True, timeout=5)
                    time.sleep(2)
                    break
        except Exception as e:
            log(f"Tuner: failed to kill existing process: {e}")
    else:
        # Linux/macOS: try fuser first, fallback to lsof + kill
        killed = False
        try:
            result = subprocess.run(
                ["fuser", "-k", f"{TUNER_PORT}/tcp"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                killed = True
                time.sleep(1)
        except Exception:
            pass
        if not killed:
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{TUNER_PORT}"],
                    capture_output=True, text=True, timeout=5
                )
                for pid in result.stdout.strip().split("\n"):
                    if pid:
                        subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
                if result.stdout.strip():
                    time.sleep(1)
            except Exception:
                pass

    log(f"Tuner: starting fresh from this repo on port {TUNER_PORT}...")
    tuner_script = os.path.join(PROJECT_ROOT, "scripts", "quant_tuner.py")
    popen_kwargs = dict(
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        ["python", tuner_script, "--readonly", "--no-browser", "--port", str(TUNER_PORT)],
        **popen_kwargs,
    )

    # Wait for Tuner to become ready
    waited = 0
    while waited < TUNER_STARTUP_TIMEOUT:
        time.sleep(2)
        waited += 2
        if _tuner_ready():
            log(f"Tuner: ready after {waited}s")
            return True
        if waited % 10 == 0:
            log(f"Tuner: waiting... ({waited}s)")

    log("ERROR: Tuner failed to start within 60s")
    return False

# ═══════════════════════════════════════════════════════════════
# Stage 0: Pre-flight checks
# ═══════════════════════════════════════════════════════════════
log("=" * 50)
log("Stage 0: Pre-flight checks")
now = datetime.now()

if not is_trading_day():
    log("SKIP: Not a trading day")
    sys.exit(0)
log(f"Trading day: YES ({now:%Y-%m-%d})")

# sendkey: env var (CI) takes priority, then secrets.yaml (local)
sendkey = os.environ.get("SERVERCHAN_SENDKEY", "")
if not sendkey:
    secrets_path = os.path.join(PROJECT_ROOT, "config", "secrets.yaml")
    if os.path.exists(secrets_path):
        with open(secrets_path, "r", encoding="utf-8") as f:
            sec = yaml.safe_load(f) or {}
        sendkey = sec.get("publish", {}).get("serverchan", {}).get("sendkey", "")
if not sendkey and not DRY_RUN:
    log("ERROR: Server酱 sendkey not configured (set SERVERCHAN_SENDKEY env var or config/secrets.yaml)")
    sys.exit(1)
if DRY_RUN:
    log("Server酱 sendkey: skipped (dry-run)")
else:
    log("Server酱 sendkey: configured")

# ═══════════════════════════════════════════════════════════════
# Stage 1: Ensure Tuner is running
# ═══════════════════════════════════════════════════════════════
log("=" * 50)
log("Stage 1: Ensure Tuner")
if not _ensure_tuner():
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# Stage 2: Refresh market data (intraday or post-market, per COOL_OFF_TIME)
# ═══════════════════════════════════════════════════════════════
log("=" * 50)
log("Stage 2: Refresh market data")

# Long timeout: cold start (full fetch) ~6min; warm cache returns in ~30s.
# HTTP request ends when server responds — the timeout is a safety net, not a delay.
r = requests.post(f"{TUNER_URL}/api/refresh_data", timeout=600)
if r.status_code != 200:
    log(f"ERROR: Tuner returned HTTP {r.status_code}")
    log(f"  Response body (first 500 chars): {r.text[:500]}")
    sys.exit(1)
try:
    status = r.json()
except Exception:
    log(f"ERROR: Tuner response is not valid JSON (HTTP {r.status_code})")
    log(f"  Response body (first 500 chars): {r.text[:500]}")
    sys.exit(1)
log(f"  Status: {status.get('status', '?')} | {status.get('count', status.get('fetchOk', 0))} ETFs | {status.get('message', '')}")
halted = status.get("haltedCount", 0)
if halted:
    log(f"  Halted ETFs detected: {halted}")

if REFRESH_ONLY:
    log("--refresh-only: done after data refresh. Skipping backtest + push.")
    sys.exit(0)

# ═══════════════════════════════════════════════════════════════
# Stage 3: Load preset + build params
# ═══════════════════════════════════════════════════════════════
log("=" * 50)
log("Stage 3: Load preset params")

# ETF name map for display
with open(os.path.join(PROJECT_ROOT, "config", "quant_universe.yaml"), "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
etf_names = {e["code"]: e.get("name", e["code"]) for e in cfg.get("universe", [])}
qdii_codes = {e["code"] for e in cfg.get("universe", []) if e.get("qdii")}

def short(code):
    return etf_names.get(code, code).replace("ETF", "")

r = requests.get(f"{TUNER_URL}/api/presets", timeout=10)
presets = r.json()
available = [k for k in presets.keys() if not k.startswith("_")]
if PRESET not in presets:
    log(f"ERROR: preset '{PRESET}' not found in Tuner response")
    log(f"  Available presets: {available}")
    log(f"  Falling back to first available: {available[0] if available else 'NONE'}")
    if not available:
        log("FATAL: No presets available from Tuner")
        sys.exit(1)
    p = presets[available[0]]
else:
    p = presets[PRESET]
end = now.strftime("%Y-%m-%d")
start = f"{now.year}-05-01"
log(f"  Preset: {PRESET} | Window: {start} ~ {end}")

# Start from the full preset params (contract layer provides correct units + all keys).
# Only override fields that preclose specifically needs to change.
params = {k: v for k, v in p.items() if not k.startswith("_")}
params.update({
    "start_date": start,
    "end_date": end,
    "debug": False,
})

# ═══════════════════════════════════════════════════════════════
# Stage 4: Run backtest
# ═══════════════════════════════════════════════════════════════
log("=" * 50)
log("Stage 4: Run backtest")
log(f"  {start} ~ {end} ({PRESET})")

r = requests.post(f"{TUNER_URL}/api/run", json=params, timeout=180)
result = r.json()
if "error" in result:
    log(f"ERROR: {result['error']}")
    sys.exit(1)

s = result["summary"]
log(f"  Total: {s['totalReturn']:+.1f}% | Annual: {s['annualReturn']:+.1f}% | Sharpe: {s['sharpe']:.2f}")
log(f"  WinRate: {s['winRate']}% | MDD: {s['maxDrawdown']:.1f}% | Elapsed: {s['elapsed']}s")

# ═══════════════════════════════════════════════════════════════
# Stage 5: Build signal table
# ═══════════════════════════════════════════════════════════════
log("=" * 50)
log("Stage 5: Build execution reference table")

history = result["signalHistory"]
latest = history[-1]
detail = latest.get("detail", {})
positions = latest.get("actual_positions", latest.get("positions", {}))

# Filter: only buy positions (>0.5% = >0.005)
buy_list = []
for code, target in sorted(positions.items(), key=lambda x: -x[1]):
    if target > 0.005:
        d = detail.get(code, {})
        name = short(code)
        close_price = d.get("close", 0)
        if close_price <= 0:
            close_price = d.get("price", 0)
        px = float(close_price)
        # 建议挂单价 = 现价 +0.5%，向上取到 0.001 的最小价位。
        # 目的：限价单挂在收盘前一定成交，成交价只会更低或相等，不会买不到。
        limit_px = (int(px * 1.005 * 1000) + 1) / 1000.0
        buy_list.append({
            "code": code, "name": name,
            "target": target, "price": px, "limit": limit_px,
            "targetPct": f"{round(target * 100)}%"
        })

if not buy_list:
    log("ERROR: No buy positions found")
    sys.exit(1)

log(f"  Buy positions: {len(buy_list)}")

# Build execution reference table
AMOUNTS = [1000000]  # 只给 100w 一档，自己按实际持仓金额等比换算

now = datetime.now()
md_lines = [
    f"## 实盘调仓执行参照表",
    "",
    f"**策略**: {PRESET_LABEL} | **日期**: {now:%Y-%m-%d}",
    "",
]

# Halt check
halted_codes = []
try:
    r_status = requests.get(f"{TUNER_URL}/api/data_status", timeout=5)
    if r_status.status_code == 200:
        halted_codes = r_status.json().get("haltedEtfs", [])
except Exception:
    pass
if halted_codes:
    names = [short(c) for c in halted_codes]
    md_lines.append(f"> ⚠️ 停牌: {', '.join(names)}")
    md_lines.append("")

# Table: codes in header row, target/price as first two data rows
col_labels = [f"{b['name']} {b['code']}" for b in buy_list]
md_lines.append(f"|  | {' | '.join(col_labels)} |")
md_lines.append(f"|--------|{''.join('------|' for _ in buy_list)}")

# Target row
target_cols = [f"{b['targetPct']}" for b in buy_list]
md_lines.append(f"| 目标 | {' | '.join(target_cols)} |")

# Price row
price_cols = [f"{b['price']:.3f}" for b in buy_list]
md_lines.append(f"| 现价 | {' | '.join(price_cols)} |")

# Suggested limit-price row (现价 +0.5%，向上取整到 0.001)
limit_cols = [f"{b['limit']:.3f}" for b in buy_list]
md_lines.append(f"| 建议挂单 | {' | '.join(limit_cols)} |")

# Data rows: 金额/股数 in same cell（股数按建议挂单价算，保证 100w 不超支）
for amt in AMOUNTS:
    cols = [f"**{amt//10000}w**"]
    for b in buy_list:
        alloc = amt * b['target']
        shares = int(alloc / b['limit'] / 100) * 100
        cols.append(f"{alloc/10000:.1f}w / {shares:,}")
    md_lines.append(f"| {' | '.join(cols)} |")

md_lines.append("")
md_lines.append("> 买入：按「建议挂单」价挂限价单，14:45–14:55 之间下单，成交价只会更低。")
md_lines.append("> 卖出：表里没有的持仓全部清掉，直接挂市价单，不要等价。")

content_str = "\n".join(md_lines)

# ═══════════════════════════════════════════════════════════════
# Stage 6: Output
# ═══════════════════════════════════════════════════════════════
log("=" * 50)

if OUTPUT_MD:
    # REQ-353 test mode: write to Desktop
    import os as _os
    desktop = Path(_os.environ["USERPROFILE"]) / "Desktop" / f"调仓执行表_{now:%Y%m%d}.md"
    desktop.write_text(content_str, encoding="utf-8")
    log(f"Stage 6: Written to {desktop}")
elif DRY_RUN:
    log("Stage 6: DRY RUN — skipping push, printing to stdout")
    title = f"{now:%m/%d} {PRESET_LABEL} 调仓信号"
    if IS_CI:
        title += " (远端)"
    print()
    print(title)
    print(content_str)
else:
    log("Stage 6: Push to WeChat")
    title = f"{now:%m/%d} {PRESET_LABEL} 调仓信号"
    if IS_CI:
        title += " (远端)"

    print(title)
    print(content_str)

    r = requests.post(f"https://sctapi.ftqq.com/{sendkey}.send",
                      data={"title": title, "desp": content_str}, timeout=10)
    resp = r.json()
    if resp.get("code") == 0:
        pushid = resp.get("data", {}).get("pushid", "?")
        log(f"Push sent OK (id={pushid})")
    else:
        log(f"ERROR: Push failed — {resp}")
        sys.exit(1)

log("=" * 50)
log("Done.")
