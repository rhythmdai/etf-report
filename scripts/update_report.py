#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF投资报告更新主控脚本

功能：一键执行完整的报告更新流程
触发词："更新今天的投资报告"

执行流程：
1. 获取K线数据（日线+周线+基准指数）
2. 计算MA均线（含预热）
3. 获取实时行情数据（ETF涨跌幅+成分股涨跌幅）
4. 更新HTML中的klineData和realtimeData数据
5. 更新HTML报告中的日期信息

使用方法：
    python update_report.py           # 执行完整更新

数据来源：
    全部使用新浪财经API（K线数据、实时行情数据同源）
"""

import subprocess
import sys
import os
import webbrowser
import time
import json
import argparse
from datetime import datetime
from pathlib import Path

PROJECT_ROOT_PATH = next(parent for parent in Path(__file__).resolve().parents if (parent / "config").is_dir() and (parent / "scripts").is_dir())
SRC_DIR = PROJECT_ROOT_PATH / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from etf_report.core.paths import ASSETS_DIR, CONFIG_DIR, DATA_DIR as PROJECT_DATA_DIR, INDEX_HTML, OUTPUTS_DIR as PROJECT_OUTPUTS_DIR, PROJECT_ROOT as PROJECT_ROOT_DIR

from logger import Logger

from config_manager import get_config

from tuner_config import DEV_TUNER_PORT

# 工作目录
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = str(PROJECT_ROOT_DIR)
os.chdir(WORK_DIR)

TUNER_URL = f"http://localhost:{DEV_TUNER_PORT}"
TUNER_STARTUP_TIMEOUT = 60

def _tuner_ready():
    try:
        import requests as _r
        resp = _r.get(f"{TUNER_URL}/api/presets", timeout=5)
        if resp.status_code != 200:
            return False
        data = resp.json()
        presets = [k for k in data.keys() if not k.startswith("_")]
        return len(presets) > 0
    except Exception:
        return False

def _ensure_tuner():
    """Kill any existing Tuner, then start a fresh one from THIS repo."""
    import requests as _r
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=5
            )
            for line in result.stdout.split("\n"):
                if f":{DEV_TUNER_PORT}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    logger.info(f"Tuner: killing existing PID {pid}")
                    subprocess.run(["taskkill", "/f", "/pid", pid],
                                   capture_output=True, timeout=5)
                    time.sleep(2)
                    break
        except Exception as e:
            logger.warn(f"Tuner kill failed: {e}")

    logger.info("Tuner: starting from this repo...")
    tuner_script = os.path.join(PROJECT_ROOT, "scripts", "quant_tuner.py")
    subprocess.Popen(
        ["python", tuner_script, "--readonly", "--no-browser"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    waited = 0
    while waited < TUNER_STARTUP_TIMEOUT:
        time.sleep(2)
        waited += 2
        if _tuner_ready():
            logger.info(f"Tuner ready after {waited}s")
            return True
    logger.error("Tuner failed to start")
    return False

# 配置管理器初始化
config = get_config()

# 从配置加载文件路径
files_config = config.get_files_config()
DATA_DIR = str(PROJECT_DATA_DIR)
OUTPUTS_DIR = str(PROJECT_OUTPUTS_DIR)
ASSETS_JS_DIR = str(ASSETS_DIR / "js")
HTML_FILE = str(INDEX_HTML)

# 日志初始化
logger = Logger(name="update_report", level="INFO", file_output=True)

# 记录配置加载信息
logger.info("配置已加载", {
    "data_dir": DATA_DIR,
    "outputs_dir": OUTPUTS_DIR
})





def run_kline_update():

    """执行K线数据更新"""
    logger.info("=" * 60)
    logger.info("Step 1: 获取K线数据并更新JS")
    logger.info("=" * 60)
    
    try:
        # 导入并执行fix_ma_and_benchmark模块
        import fix_ma_and_benchmark
        fix_ma_and_benchmark.main()
        return True
    except Exception as e:
        logger.error("K线数据更新失败", {"error": str(e)})
        import traceback
        traceback.print_exc()
        return False


def run_realtime_update():

    """执行实时行情数据更新"""
    logger.info("=" * 60)
    logger.info("Step 2: 获取实时行情数据（ETF涨跌幅+成分股涨跌幅）")
    logger.info("=" * 60)

    try:
        # 导入并执行realtime_data_updater模块
        import realtime_data_updater
        realtime_data_updater.main()
        return True
    except Exception as e:
        logger.error("实时数据更新失败", {"error": str(e)})
        import traceback
        traceback.print_exc()
        return False


def run_editorial_update():
    """REQ-158：抓取 editorial（研究卡 + 宏观卡）并写入 data/editorial_content.yaml。

    失败策略：
      - 任何源抓取失败：不抛出，logger.warn 记录，保留上一版 data/editorial_content.yaml
      - 部分 ETF 成功部分失败：按"保留 final>0 的新条目，final=0 的回退到上一版"合并
      - 全部成功：完整覆盖

    返回 True 表示流程完成（即便有部分回退），False 表示严重故障（导入失败等）。
    """
    logger.info("=" * 60)
    logger.info("Step 2.5: 抓取 editorial 内容（研究卡 + 宏观卡）")
    logger.info("=" * 60)

    try:
        import editorial_fetcher
        import yaml as _yaml
    except Exception as e:
        logger.warn("editorial_fetcher 模块未找到或导入失败，跳过 editorial 更新", {"error": str(e)})
        return True  # 不阻断主流程，继续用上一版 yaml

    try:
        result = editorial_fetcher.fetch_all_editorial()
    except Exception as e:
        logger.warn("editorial 抓取异常，保留上一版 editorial_content.yaml", {"error": str(e)})
        import traceback
        traceback.print_exc()
        return True

    # 读取上一版，作为 fallback 基线
    editorial_path = os.path.join(PROJECT_ROOT, "data", "editorial_content.yaml")
    previous = {}
    if os.path.exists(editorial_path):
        try:
            with open(editorial_path, "r", encoding="utf-8") as f:
                previous = _yaml.safe_load(f) or {}
        except Exception as e:
            logger.warn("读取上一版 editorial_content.yaml 失败，将使用全新生成", {"error": str(e)})
            previous = {}

    # 合并：新抓成功的覆盖旧，新抓失败（final=0）的保留旧
    merged = result.to_yaml_dict()
    fallback_notes = []

    prev_etfs = (previous.get("etf_cards") or {})
    for code, new_card in list(merged.get("etf_cards", {}).items()):
        new_cards = new_card.get("research_cards") or []
        if not new_cards and prev_etfs.get(code):
            merged["etf_cards"][code] = prev_etfs[code]
            fallback_notes.append(f"etf:{code}")

    prev_macro = (previous.get("macro_cards") or {})
    for card_id, new_card in list(merged.get("macro_cards", {}).items()):
        new_items = new_card.get("items") or []
        if not new_items and prev_macro.get(card_id):
            merged["macro_cards"][card_id] = prev_macro[card_id]
            fallback_notes.append(f"macro:{card_id}")

    if fallback_notes:
        logger.warn("部分 editorial 片段回退到上一版", {"fallback": fallback_notes})

    # 写回
    try:
        with open(editorial_path, "w", encoding="utf-8") as f:
            _yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False, width=1000)
        logger.info("editorial_content.yaml 已更新", {
            "file": editorial_path,
            "etf_count": len(merged.get("etf_cards") or {}),
            "macro_count": len(merged.get("macro_cards") or {}),
            "fallback_count": len(fallback_notes),
        })
    except Exception as e:
        logger.warn("写入 editorial_content.yaml 失败，保留上一版", {"error": str(e)})

    # 统计信息（仅记录，不阻断）
    logger.info("editorial 抓取统计", {"stats": result.stats})
    return True


def run_quant_data_update():
    """增量更新量化回测所需的 27 支 ETF 日线/周线 CSV 数据。
    不因拉取失败阻断主流程，失败时量化板块使用上一版数据。
    """
    logger.info("Step 2.6: 增量更新量化回测数据（ETF 日线/周线 CSV）")
    try:
        import quant_data_fetcher
        # 传空 argv 避免 argparse 读到 update_report.py 的参数
        import sys
        old_argv = sys.argv
        sys.argv = ["quant_data_fetcher"]
        try:
            quant_data_fetcher.main()
        finally:
            sys.argv = old_argv
        logger.info("量化数据增量更新完成")
        return True
    except ImportError:
        logger.warn("quant_data_fetcher 模块未找到，跳过量化数据更新")
        return False
    except Exception as e:
        logger.warn("量化数据增量更新失败，保留上一版 CSV", {"error": str(e)})
        return False



def _replace_text_in_html(html_content, marker, old_pattern, replacement):
    """在 HTML 原始文本中定位并替换文本内容
    
    Args:
        html_content: HTML 原始文本
        marker: 定位锚点（如 "报告日期:"）
        old_pattern: 需要被替换的旧内容正则
        replacement: 替换后的内容
    
    Returns:
        (html_content, found): 更新后的内容和是否找到
    """
    import re as _re
    pos = html_content.find(marker)
    if pos == -1:
        return html_content, False
    
    # 在 marker 附近搜索旧模式
    search_start = max(0, pos - 200)
    search_end = min(len(html_content), pos + 200)
    region = html_content[search_start:search_end]
    
    match = _re.search(old_pattern, region)
    if match:
        old_text = match.group()
        abs_start = search_start + match.start()
        abs_end = search_start + match.end()
        html_content = html_content[:abs_start] + replacement + html_content[abs_end:]
        return html_content, True
    
    return html_content, False


def update_html_dates(html_file=None):
    """更新HTML报告中的日期信息（报告日期、数据截止、页脚生成时间）
    
    使用字符串定位替换，不经过 BS4 序列化，避免破坏 script 内容。
    """
    logger.info("=" * 60)
    logger.info("Step 3: 更新报告日期")
    logger.info("=" * 60)
    
    # 从配置加载文件路径和定位标记
    files_config = config.get_files_config()
    html_update_config = config.get_html_update_config()
    
    html_file = html_file or HTML_FILE
    kline_file = os.path.join(DATA_DIR, files_config.get('data_files', {}).get('kline', 'etf_full_kline_data.json'))

    
    # 加载定位标记和日期格式
    locators = html_update_config.get('locators', {})
    date_formats = html_update_config.get('date_formats', {})
    date_patterns = html_update_config.get('date_patterns', {})
    
    report_date_label = locators.get('report_date_label', '报告日期:')
    data_cutoff_label = locators.get('data_cutoff_label', '数据截止:')
    generation_time_label = locators.get('generation_time_label', '生成时间:')
    
    report_date_cn_format = date_formats.get('report_date_cn', '%Y年%m月%d日')
    iso_date_format = date_formats.get('iso_date', '%Y-%m-%d')
    
    report_date_pattern = date_patterns.get('report_date', r'\d{4}年\d{2}月\d{2}日')
    iso_date_pattern = date_patterns.get('iso_date', r'\d{4}-\d{2}-\d{2}')
    
    # 从K线数据中提取最新的日线日期作为"数据截止"日期
    data_date = None
    try:
        with open(kline_file, 'r', encoding='utf-8') as f:
            kline_data = json.load(f)
        for code, etf_data in kline_data.items():
            daily = etf_data.get('daily', {})
            dates = daily.get('dates', [])
            if dates:
                data_date = dates[-1]
                break
    except Exception as e:
        logger.warn("无法读取K线数据文件", {"error": str(e)})
    
    if not data_date:
        data_date = datetime.now().strftime(iso_date_format)
        logger.warn("使用当前日期作为数据截止日期", {"date": data_date})
    
    today = datetime.now()
    report_date_cn = today.strftime(report_date_cn_format)
    report_date_iso = today.strftime(iso_date_format)
    
    # 读取原始 HTML 文本
    with open(html_file, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    updated = False
    
    # 1. 更新"报告日期": 优先保留新的细粒度 ID，再回退到旧结构兼容替换
    html_content, found = _replace_element_by_id(
        html_content,
        "report-date-value",
        report_date_cn,
        class_name="text-blue",
    )
    if not found:
        html_content, found = _replace_text_in_html(
            html_content, report_date_label,
            r'<strong[^>]*>' + report_date_pattern + r'</strong>',
            f'<strong class="text-blue">{report_date_cn}</strong>'
        )
    if found:
        logger.info("报告日期更新成功", {"date": report_date_cn})
        updated = True
    
    # 2. 更新"数据截止": 优先保留新的细粒度 ID，再兼容旧纯文本结构
    html_content, found = _replace_element_by_id(
        html_content,
        "report-cutoff-value",
        data_date,
    )
    if not found:
        html_content, found = _replace_text_in_html(
            html_content, data_cutoff_label,
            data_cutoff_label + r'\s*' + iso_date_pattern,
            f'{data_cutoff_label} {data_date}'
        )
    if found:
        logger.info("数据截止更新成功", {"date": data_date})
        updated = True
    
    # 3. 更新页脚"生成时间": 生成时间: 2026-03-17
    html_content, found = _replace_text_in_html(
        html_content, generation_time_label,
        generation_time_label + r'\s*' + iso_date_pattern,
        f'{generation_time_label} {report_date_iso}'
    )
    if found:
        logger.info("页脚生成时间更新成功", {"time": report_date_iso})
        updated = True
    
    if updated:
        with logger.audit_operation("file_io", f"write {html_file}"):
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(html_content)
        logger.info("日期更新完成")
    else:
        logger.warn("未找到需要更新的日期字段")
    
    return updated


def _replace_js_const_in_html(html_content, const_name, new_value_str):
    """在 HTML 原始文本中替换 const xxxData = {...}; 块，不经过 BS4 序列化
    
    使用位置定位：找到 const xxxData = 的位置，找到匹配的 }; 结尾，整体替换。
    这避免了 BS4 str(soup) 丢失 script 中其他 JS 代码的问题。
    """
    marker = f'const {const_name} = '
    start = html_content.find(marker)
    if start == -1:
        return html_content, False
    
    # 从 = 后面找到 { 的位置
    brace_start = html_content.find('{', start)
    if brace_start == -1:
        return html_content, False
    
    # 用括号深度匹配找到 }; 结尾
    depth = 0
    i = brace_start
    while i < len(html_content):
        ch = html_content[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                # 找到匹配的 }，检查后面是否有 ;
                end = i + 1
                if end < len(html_content) and html_content[end] == ';':
                    end += 1
                new_section = f'{new_value_str};'
                html_content = html_content[:start] + new_section + html_content[end:]
                return html_content, True
        i += 1
    
    return html_content, False




def write_runtime_payload_file(kline_data, realtime_data):
    """生成外置运行时载荷，写入 assets/js/ 以便部署到 GitHub Pages。"""

    payload_file = os.path.join(ASSETS_JS_DIR, "runtime_payload.js")
    payload = {
        "generatedAt": datetime.now().isoformat(),
        "klineData": kline_data,
        "realtimeData": realtime_data,
    }
    content = 'window.__ETF_REPORT_RUNTIME__ = ' + json.dumps(payload, ensure_ascii=False, indent=2) + ';\n'
    with logger.audit_operation("file_io", f"write {payload_file}"):
        with open(payload_file, 'w', encoding='utf-8') as f:
            f.write(content)
    logger.info("运行时载荷已写入", {"file": payload_file})
    return payload_file


def _build_kline_replay_data(etf_name_map, start_date, end_date):
    """Build per-ETF price data for K-line replay chart, reading from quant CSVs."""
    import pandas as pd
    quant_dir = os.path.join(DATA_DIR, "quant")
    if not os.path.isdir(quant_dir):
        return {}

    result = {}
    for code in etf_name_map:
        csv_path = os.path.join(quant_dir, f"{code}_daily.csv")
        if not os.path.exists(csv_path):
            continue
        try:
            df = pd.read_csv(csv_path, parse_dates=["date"])
            if start_date:
                df = df[df["date"] >= start_date]
            if end_date:
                df = df[df["date"] <= end_date]
            if df.empty:
                continue

            dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
            close = df["close"].round(4).tolist()
            volume = df["volume"].tolist() if "volume" in df.columns else []

            # Compute RSI(14) if enough data
            rsi = []
            if "close" in df.columns and len(df) >= 15:
                delta = df["close"].diff()
                gain = delta.clip(lower=0)
                loss = (-delta).clip(lower=0)
                avg_gain = gain.rolling(14, min_periods=14).mean()
                avg_loss = loss.rolling(14, min_periods=14).mean()
                rs = avg_gain / avg_loss.replace(0, float("inf"))
                rsi_vals = 100 - (100 / (1 + rs))
                rsi_vals = rsi_vals.round(2).tolist()
                # First 14 values are NaN, fill with null
                rsi = [None] * min(14, len(rsi_vals)) + rsi_vals[14:]

            result[code] = {
                "dates": dates,
                "close": close,
                "volume": volume,
                "rsi": rsi,
            }
        except Exception as e:
            logger.warn(f"读取 {code} K线数据失败", {"error": str(e)})
            continue

    return result


def load_quant_preset_params(quant_config_path, preset=None):
    if preset is None:
        from etf_report.core.quant_contract import DEFAULT_PRESET
        preset = DEFAULT_PRESET
    """Load a quant preset and convert it to Tuner parameter shape via quant_contract."""
    import yaml
    from etf_report.core import quant_contract as qc

    with open(quant_config_path, "r", encoding="utf-8") as f:
        qcfg = yaml.safe_load(f)
    p = qcfg.get("presets", {}).get(preset, {})
    params = qc.preset_to_tuner_params(preset, p, qcfg.get("confidence", {}))
    validation_error = qc.validate_tuner_params(params)
    if validation_error:
        raise ValueError(validation_error)
    return params


def default_quant_preset_params():
    """Fallback params matching current preset2 defaults."""
    return {
        "w1": 45, "w3": 45, "w7": 10,
        "conf_type": "ma_trend", "ma_trend_period": 26,
        "ma_bull_pos": 1.0, "ma_bear_pos": 0.3, "ma_direction_confirm": True,
        "benchmarks": ["510300"],
        "max_holdings": 6, "signal_steps": 17, "top_boost": 0,
        "concentration": 0.0, "c_sensitivity": 0,
        "rebalance_freq": "daily", "execution_timing": "same_close",
        "band": 0.03, "band_sensitivity": 0,
        "f1_ema_period": 5, "f3_vol_window": 20,
        "f1_sensitivity": 8.0, "f3_sensitivity": 1.5,
        "f7_up_power": 15.0, "f7_up_span": 3.5, "f7_window": 20, "f7_lookback": 250,
        "f6_rsi_thresh": 80.0, "f6_drop_thresh": 2.5, "f6_base_penalty": 0.15,
    }


def build_quant_payload_config_section(preset_params):
    """Build yr1/yr3 payload config from the same contract used by Tuner /api/run."""
    from etf_report.core import quant_contract as qc

    config_override = qc.tuner_params_to_config_override(preset_params)
    return {
        "yr1": config_override,
        "yr3": json.loads(json.dumps(config_override)),
    }


def generate_quant_baseline_payload():
    """生成量化回测 payload（1yr + 3yr），从 YAML 读取策略参数，调用 tuner 真实回测接口。"""
    import urllib.request
    import urllib.error

    os.makedirs(ASSETS_JS_DIR, exist_ok=True)
    payload_file = os.path.join(ASSETS_JS_DIR, "quant_payload.js")

    # ── 从 YAML 读取当前默认预设（DEFAULT_PRESET）参数 ─────────────────
    QUANT_CONFIG = os.path.join(PROJECT_ROOT, "config", "quant_universe.yaml")
    from etf_report.core.quant_contract import DEFAULT_PRESET as _DEFAULT_PRESET
    preset_params = {}
    try:
        preset_params = load_quant_preset_params(QUANT_CONFIG)
        logger.info("从 YAML 读取默认预设参数", {"preset": _DEFAULT_PRESET, "params": {k: v for k, v in preset_params.items() if k != "start_date"}})
    except Exception as e:
        logger.warn("读取 quant_universe.yaml 失败，使用默认参数", {"error": str(e)})
        preset_params = default_quant_preset_params()

    # ── 确保 Tuner 从本仓库启动 ─────────────────────────
    if not _ensure_tuner():
        raise RuntimeError("Tuner failed to start; abort quant_payload generation")

    # ── 调用 tuner 运行 1yr + 3yr 回测 ─────────────────────
    TUNER_RUN_URL = f"http://localhost:{DEV_TUNER_PORT}/api/run"

    def run_backtest(label, start_date, end_date):
        """Run a single backtest via tuner API. Returns raw response dict or None."""
        params = dict(preset_params)
        params["start_date"] = start_date
        params["end_date"] = end_date
        try:
            req = urllib.request.Request(
                TUNER_RUN_URL,
                data=json.dumps(params).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            if "error" in data:
                logger.warn(f"tuner {label} 回测返回错误", {"error": data.get("error")})
                return None
            logger.info(f"成功从 tuner 获取 {label} 回测数据")
            return data
        except Exception as e:
            logger.warn(f"tuner {label} 回测请求失败", {"error": str(e)})
            return None

    # Determine date ranges from quant CSV data
    data_max_date = None
    sample_csv = os.path.join(DATA_DIR, "quant", "510300_daily.csv")
    if os.path.exists(sample_csv):
        try:
            import pandas as pd
            df = pd.read_csv(sample_csv, usecols=["date"], parse_dates=["date"])
            if not df.empty:
                data_max_date = df["date"].max().strftime("%Y-%m-%d")
        except Exception:
            pass

    if not data_max_date:
        data_max_date = datetime.now().strftime("%Y-%m-%d")
        logger.warn("无法确定数据截止日，使用当前日期", {"date": data_max_date})

    from datetime import timedelta as _td
    max_dt = datetime.strptime(data_max_date, "%Y-%m-%d")
    yr1_start = (max_dt - _td(days=365)).strftime("%Y-%m-%d")
    yr3_start = (max_dt - _td(days=1095)).strftime("%Y-%m-%d")

    bt_1yr = run_backtest("1yr", yr1_start, data_max_date)
    bt_3yr = run_backtest("3yr", yr3_start, data_max_date)
    if not bt_1yr and not bt_3yr:
        raise RuntimeError("Both quant backtests failed; keep previous quant_payload.js")

    # ── 构建 payload ─────────────────────────────────────────
    def build_template(bt_data):
        """Build a single template dict from backtest raw data."""
        if not bt_data:
            return None
        nav_dates = bt_data.get("nav", {}).get("dates", [])
        nav_pct = bt_data.get("nav", {}).get("pct", [])
        hs300_pct = bt_data.get("hs300", [])
        eq_weight_pct = bt_data.get("eqWeight", [])
        drawdown = bt_data.get("drawdown", [])
        summary = bt_data.get("summary", {})
        signal_history = bt_data.get("signalHistory", [])

        drawdown_series = {"dates": nav_dates, "drawdown": drawdown}

        weekly_snapshots = []
        for i, sig in enumerate(signal_history):
            weekly_snapshots.append({
                "date": sig.get("date"),
                "index": i,
                "scores": sig.get("scores", {}),
                "top6": sig.get("topN", []),
                "positions": sig.get("positions", {}),
                "detail": sig.get("detail", {}),
                "avgConfidence": sig.get("avgConfidence", 0) / 100.0,
                "totalTarget": sig.get("totalPosition", 0) / 100.0,
                "regime": sig.get("regime", ""),
            })

        monthly_returns = _compute_monthly_returns(nav_dates, nav_pct)
        rebalance_freq = _compute_rebalance_freq(signal_history, bt_data.get("etfNameMap", {}))
        latest_signal = _build_latest_signal(signal_history, bt_data.get("etfNameMap", {}), bt_data.get("etfSectorMap", {})) if signal_history else {}
        sector_dist = _compute_sector_distribution(latest_signal, bt_data.get("etfSectorMap", {}))

        # Compute monthly win rate
        mr = monthly_returns
        monthly_win_rate = round(sum(1 for m in mr if m.get("ret", 0) > 0) / len(mr) * 100, 1) if mr else 0

        # Compute win/loss streaks from nav
        max_win = max_loss = 0
        cur_win = cur_loss = 0
        for i in range(1, len(nav_pct)):
            if nav_pct[i] > nav_pct[i-1]:
                cur_win += 1; cur_loss = 0
                max_win = max(max_win, cur_win)
            elif nav_pct[i] < nav_pct[i-1]:
                cur_loss += 1; cur_win = 0
                max_loss = max(max_loss, cur_loss)
            else:
                cur_win = 0; cur_loss = 0

        return {
            "summary": {
                "totalReturn": summary.get("totalReturn", 0),
                "annualReturn": summary.get("annualReturn", 0),
                "maxDrawdown": summary.get("maxDrawdown", 0),
                "sharpe": summary.get("sharpe", 0),
                "sortino": summary.get("sortino", 0),
                "calmar": summary.get("calmar", 0),
                "winRate": summary.get("winRate", 0),
                "payoffRatio": summary.get("payoffRatio", 0),
                "excessReturn": summary.get("excessReturn", 0),
                "avgWin": summary.get("avgWin", 0),
                "avgLoss": summary.get("avgLoss", 0),
                "tradeCount": summary.get("tradeCount", 0),
                "monthlyWinRate": monthly_win_rate,
                "bestMonth": max([m.get("ret", 0) for m in mr]) if mr else 0,
                "worstMonth": min([m.get("ret", 0) for m in mr]) if mr else 0,
                "maxWinStreak": max_win,
                "maxLossStreak": max_loss,
                "startDate": summary.get("startDate", ""),
                "endDate": summary.get("endDate", ""),
                "tradingDays": len(nav_dates),
                "rebalanceCount": summary.get("rebalanceCount", len(signal_history)),
                "rebalanceDays": summary.get("rebalanceDays", len(signal_history)),
                "commissionPct": summary.get("commissionPct", 0),
                "initialCapital": 1000000.0,
                "finalNav": 1000000.0 * (1 + summary.get("totalReturn", 0) / 100),
                "exposureSummary": summary.get("exposureSummary", {})
            },
            "navSeries": {
                "dates": nav_dates,
                "nav": nav_pct,
                "hs300": hs300_pct,
                "eqWeight": eq_weight_pct
            },
            "drawdownSeries": drawdown_series,
            "monthlyReturns": monthly_returns,
            "weeklySnapshots": weekly_snapshots,
            "rebalanceFreq": rebalance_freq,
            "sectorDistribution": sector_dist,
            "latestSignal": latest_signal
        }

    templates = {}
    template_meta = {}

    tpl_1yr = build_template(bt_1yr)
    if tpl_1yr:
        s = tpl_1yr["summary"]
        templates["yr1"] = tpl_1yr
        template_meta["yr1"] = {
            "label": "1 年回测",
            "description": f"{s['startDate']} ~ {s['endDate']} | 年化 {s['annualReturn']:.1f}% | Calmar {s['calmar']:.2f}"
        }

    tpl_3yr = build_template(bt_3yr)
    if tpl_3yr:
        s = tpl_3yr["summary"]
        templates["yr3"] = tpl_3yr
        template_meta["yr3"] = {
            "label": "3 年回测",
            "description": f"{s['startDate']} ~ {s['endDate']} | 年化 {s['annualReturn']:.1f}% | Calmar {s['calmar']:.2f}"
        }

    # Build config section from the same contract used by Tuner /api/run.
    config_section = build_quant_payload_config_section(preset_params) if (tpl_1yr or tpl_3yr) else {}

    etf_name_map = (bt_1yr or bt_3yr or {}).get("etfNameMap", {})

    # ── Build kline replay data from CSV ─────────────────────
    kline_replay = _build_kline_replay_data(etf_name_map, yr1_start, data_max_date)

    payload = {
        "generatedAt": datetime.now().isoformat(),
        "templateMeta": template_meta,
        "templates": templates,
        "config": config_section,
        "etfNameMap": etf_name_map,
        "klineReplay": kline_replay,
    }

    content = '// Auto-generated by update_report.py\n// Strategy: act-1 (from quant_universe.yaml)\n// Generated: ' + datetime.now().isoformat() + '\nwindow.__QUANT_RUNTIME__ = ' + json.dumps(payload, ensure_ascii=False, indent=2) + ';\n'
    with open(payload_file, 'w', encoding='utf-8') as f:
        f.write(content)
    logger.info("量化回测载荷已写入", {
        "file": payload_file,
        "source": "real" if templates else "fallback",
        "templates": list(templates.keys()),
    })
    return payload_file


def _compute_monthly_returns(dates, nav_pct):
    """从日度 NAV 计算月度收益。"""
    if not dates or not nav_pct or len(dates) != len(nav_pct):
        return []
    monthly = {}
    for i, d in enumerate(dates):
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            key = (dt.year, dt.month)
            if key not in monthly:
                monthly[key] = {"start": nav_pct[i], "end": nav_pct[i]}
            else:
                monthly[key]["end"] = nav_pct[i]
        except (IndexError, KeyError, ValueError):
            continue
    result = []
    for (year, month), data in sorted(monthly.items()):
        ret = (data["end"] / data["start"] - 1) * 100 if data["start"] > 0 else 0
        result.append({"year": year, "month": month, "ret": round(ret, 2)})
    return result


def _compute_rebalance_freq(signal_history, etf_name_map):
    """计算每只 ETF 被选中次数统计。"""
    counts = {}
    for sig in signal_history:
        for code in sig.get("topN", []):
            counts[code] = counts.get(code, 0) + 1
    total = len(signal_history) if signal_history else 1
    result = []
    for code, count in sorted(counts.items(), key=lambda x: -x[1])[:10]:
        result.append({
            "code": code,
            "name": etf_name_map.get(code, code),
            "count": count,
            "pct": round(count / total * 100, 1)
        })
    return result


def _build_latest_signal(signal_history, etf_name_map, etf_sector_map=None):
    """从最后一次调仓构建 latestSignal 格式。"""
    if not signal_history:
        return {}
    if etf_sector_map is None:
        etf_sector_map = {}
    last = signal_history[-1]
    holdings = []
    all_scores = []

    # 从 detail 中获取更多信息（如果有的话）
    detail = last.get("detail", {})

    for code in last.get("topN", []):
        score = last.get("scores", {}).get(code, 0)
        pos = last.get("positions", {}).get(code, 0)
        # 从 detail 获取更多信息
        code_detail = detail.get(code, {}) if detail else {}
        holdings.append({
            "code": code,
            "name": etf_name_map.get(code, code),
            "sector": etf_sector_map.get(code, ""),
            "score": score,
            "confidence": last.get("avgConfidence", 0) / 100,  # 使用平均信心度
            "position": pos,
            "price": code_detail.get("price", 0),
        })

    # 构建 allScores（包含全部）
    for code, score in last.get("scores", {}).items():
        all_scores.append({
            "code": code,
            "name": etf_name_map.get(code, code),
            "score": score,
            "inTop": code in last.get("topN", [])
        })
    all_scores.sort(key=lambda x: -x["score"])

    total_target = last.get("totalPosition", 0)
    return {
        "date": last.get("date"),
        "avgConfidence": last.get("avgConfidence", 0) / 100,
        "totalTarget": total_target,
        "cashTarget": 100 - total_target,
        "maxHoldings": 6,
        "holdings": holdings,
        "allScores": all_scores[:12]  # 只取前12
    }


def _compute_sector_distribution(latest_signal, etf_sector_map):
    """计算持仓行业分布。"""
    if not latest_signal or not latest_signal.get("holdings"):
        return []
    sector_weights = {}
    for h in latest_signal["holdings"]:
        sector = etf_sector_map.get(h["code"], "其他")
        sector_weights[sector] = sector_weights.get(sector, 0) + h.get("position", 0)
    total = sum(sector_weights.values()) if sector_weights else 1
    result = []
    for sector, weight in sector_weights.items():
        result.append({"sector": sector, "weight": round(weight / total * 100, 1)})
    return sorted(result, key=lambda x: -x["weight"])


def _to_number(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric



def _get_close_series(kline_rows):
    closes = []
    for row in kline_rows or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            close_value = _to_number(row[1])
            if close_value is not None:
                closes.append(close_value)
    return closes



def _get_daily_close_series(kline_entry):
    daily_entry = (kline_entry or {}).get("daily") or {}
    return _get_close_series(daily_entry.get("kline") or [])



def _get_latest_daily_close(kline_entry):
    daily_entry = (kline_entry or {}).get("daily") or {}
    latest_close = _to_number(daily_entry.get("latest_close"))
    if latest_close is not None:
        return latest_close
    closes = _get_daily_close_series(kline_entry)
    return closes[-1] if closes else None



def _get_latest_daily_change(kline_entry):
    daily_entry = (kline_entry or {}).get("daily") or {}
    latest_change = _to_number(daily_entry.get("latest_change"))
    if latest_change is not None:
        return latest_change
    return _get_return_from_series(_get_daily_close_series(kline_entry), 1)



def _get_weekly_close_series(kline_entry):

    weekly_entry = (kline_entry or {}).get("weekly") or {}
    return _get_close_series(weekly_entry.get("kline") or [])



def _get_return_from_series(series, lookback_points):
    if not isinstance(series, list) or len(series) < 2:
        return None
    end_value = _to_number(series[-1])
    if end_value is None:
        return None
    safe_lookback = max(1, min(lookback_points, len(series) - 1))
    start_value = _to_number(series[-1 - safe_lookback])
    if start_value in (None, 0):
        return None
    return ((end_value - start_value) / start_value) * 100



def _format_percent(value):
    numeric = _to_number(value)
    if numeric is None:
        return "--"
    if numeric > 0:
        return f"+{numeric:.2f}%"
    if numeric < 0:
        return f"{numeric:.2f}%"
    return "0.00%"



def _format_price(value):
    numeric = _to_number(value)
    if numeric is None:
        return "--"
    digits = 2 if numeric >= 100 else 4
    return f"{numeric:.{digits}f}元"



def _get_trend_class(value):

    numeric = _to_number(value)
    if numeric is None:
        return "text-amber"
    if numeric > 0:
        return "text-green"
    if numeric < 0:
        return "text-red"
    return "text-amber"



def _get_overview_change_class(value):
    numeric = _to_number(value)
    if numeric is None:
        return "etf-change"
    if numeric > 0:
        return "etf-change positive"
    if numeric < 0:
        return "etf-change negative"
    return "etf-change"



def _format_weight_percent(value):
    numeric = _to_number(value)
    if numeric is None:
        return "--"
    return f"{numeric:.2f}%"



def _get_text_value_class(value):
    numeric = _to_number(value)
    if numeric is None:
        return "text-amber text-bold"
    if numeric > 0:
        return "text-green text-bold"
    if numeric < 0:
        return "text-red text-bold"
    return "text-amber text-bold"



def _get_stat_value_class(value):
    numeric = _to_number(value)
    if numeric is None:
        return "stat-value text-amber"
    if numeric > 0:
        return "stat-value text-green"
    if numeric < 0:
        return "stat-value text-red"
    return "stat-value text-amber"



def _format_runtime_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return "--"

    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value.strip().replace("T", " ")[:19]



def _build_fund_flow_snapshot(kline_data, realtime_data):
    etf_entries = []
    holding_map = {}
    latest_timestamp_text = "--"
    latest_timestamp_value = None

    for code, realtime_entry in (realtime_data or {}).items():
        realtime_entry = realtime_entry or {}
        kline_entry = (kline_data or {}).get(code) or {}
        etf_name = str(realtime_entry.get("name") or kline_entry.get("name") or code).strip()
        # REQ-160：ETF 简称用于 leaders/laggards 的"ETF 归属"列
        etf_short_name = etf_name[:-3].strip() if etf_name.endswith("ETF") else etf_name
        etf_change = _to_number(realtime_entry.get("etf_change"))
        if etf_change is not None:
            etf_entries.append({"code": code, "name": etf_name, "change": etf_change})

        raw_timestamp = realtime_entry.get("timestamp")
        if isinstance(raw_timestamp, str) and raw_timestamp.strip():
            normalized = raw_timestamp.strip().replace("Z", "+00:00")
            try:
                parsed_timestamp = datetime.fromisoformat(normalized)
            except ValueError:
                parsed_timestamp = None
            if parsed_timestamp and (latest_timestamp_value is None or parsed_timestamp > latest_timestamp_value):
                latest_timestamp_value = parsed_timestamp
                latest_timestamp_text = parsed_timestamp.strftime("%Y-%m-%d %H:%M:%S")
            elif latest_timestamp_text == "--":
                latest_timestamp_text = _format_runtime_timestamp(raw_timestamp)

        for holding in realtime_entry.get("holdings") or []:
            holding_name = str((holding or {}).get("name") or "").strip()
            holding_change = _to_number((holding or {}).get("change"))
            if not holding_name or holding_change is None:
                continue

            ratio = _to_number((holding or {}).get("ratio")) or 0.0
            aggregated = holding_map.setdefault(
                holding_name,
                {
                    "name": holding_name,
                    "ratio": 0.0,
                    "change": holding_change,
                    # REQ-160：主归属 ETF = 该股权重最大的那只 ETF 简称
                    "primary_etf_short": etf_short_name,
                    "primary_etf_ratio": ratio,
                },
            )
            aggregated["ratio"] += ratio
            if aggregated.get("change") is None:
                aggregated["change"] = holding_change
            # 如果该股在当前 ETF 的权重比此前记录的"主归属 ETF"权重更大，更新归属
            if ratio > (aggregated.get("primary_etf_ratio") or 0.0):
                aggregated["primary_etf_short"] = etf_short_name
                aggregated["primary_etf_ratio"] = ratio

    etf_entries.sort(key=lambda item: (item["change"], item["name"]), reverse=True)
    leader = etf_entries[0] if etf_entries else {"name": "--", "change": None}
    laggard = min(etf_entries, key=lambda item: (item["change"], item["name"])) if etf_entries else {"name": "--", "change": None}

    average_change = None
    if etf_entries:
        average_change = sum(item["change"] for item in etf_entries) / len(etf_entries)

    positive_count = sum(1 for item in etf_entries if item["change"] > 0)
    negative_count = sum(1 for item in etf_entries if item["change"] < 0)
    flat_count = sum(1 for item in etf_entries if item["change"] == 0)

    holding_entries = list(holding_map.values())
    leaders = sorted(
        holding_entries,
        key=lambda item: (item["change"], item["ratio"], item["name"]),
        reverse=True,
    )[:5]
    laggards = sorted(
        holding_entries,
        key=lambda item: (item["change"], -item["ratio"], item["name"]),
    )[:5]

    return {
        "source": "新浪财经实时行情 + 主流程K线快照",
        "updated_at": latest_timestamp_text,
        "leader": leader,
        "laggard": laggard,
        "average_change": average_change,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "flat_count": flat_count,
        "etf_count": len(etf_entries),
        "leaders": leaders,
        "laggards": laggards,
    }



def _sync_ranked_holding_table(html_content, table_prefix, rows):
    visible_rows = list(rows or [])[:5]
    while len(visible_rows) < 5:
        visible_rows.append({
            "name": "--", "ratio": None, "change": None,
            "primary_etf_short": None, "primary_etf_ratio": None,
        })

    for index, row in enumerate(visible_rows, start=1):
        html_content, _ = _replace_element_by_id(html_content, f"{table_prefix}-name-{index}", row.get("name") or "--")
        # REQ-160：新增"ETF 归属"列 — 显示主归属 ETF 简称
        etf_short = row.get("primary_etf_short") or "--"
        html_content, _ = _replace_element_by_id(html_content, f"{table_prefix}-industry-{index}", etf_short)
        # REQ-160：权重列显示主归属 ETF 内的真实权重（而非跨 ETF 累加值，避免产生不对应任何 ETF 的虚拟数字）
        display_ratio = row.get("primary_etf_ratio")
        if display_ratio is None:
            display_ratio = row.get("ratio")
        html_content, _ = _replace_element_by_id(html_content, f"{table_prefix}-weight-{index}", _format_weight_percent(display_ratio))
        html_content, _ = _replace_element_by_id(
            html_content,
            f"{table_prefix}-change-{index}",
            _format_percent(row.get("change")),
            class_name=_get_text_value_class(row.get("change")),
        )

    return html_content



def sync_fund_flow_section_html(html_content, kline_data, realtime_data, data_cutoff_date=None):
    snapshot = _build_fund_flow_snapshot(kline_data, realtime_data)
    updated_at = snapshot.get("updated_at") or "--"
    breadth_value = f'{snapshot.get("positive_count", 0)} / {snapshot.get("negative_count", 0)} / {snapshot.get("flat_count", 0)}'
    average_name = f'{snapshot.get("etf_count", 0)}支ETF均值' if snapshot.get("etf_count") else '暂无可用行情'


    html_content, _ = _replace_element_by_id(html_content, "fund-flow-title", "💰 市场热度与轮动")
    html_content, _ = _replace_element_by_id(html_content, "fund-flow-source-value", snapshot.get("source") or "--")
    html_content, _ = _replace_element_by_id(html_content, "fund-flow-updated-value", updated_at)
    html_content, _ = _replace_element_by_id(html_content, "market-rotation-card-title", "ETF日内轮动")
    html_content, _ = _replace_element_by_id(html_content, "market-rotation-stat-leader-name", snapshot.get("leader", {}).get("name") or "--")
    html_content, _ = _replace_element_by_id(
        html_content,
        "market-rotation-stat-leader-value",
        _format_percent(snapshot.get("leader", {}).get("change")),
        class_name=_get_stat_value_class(snapshot.get("leader", {}).get("change")),
    )
    html_content, _ = _replace_element_by_id(html_content, "market-rotation-stat-laggard-name", snapshot.get("laggard", {}).get("name") or "--")
    html_content, _ = _replace_element_by_id(
        html_content,
        "market-rotation-stat-laggard-value",
        _format_percent(snapshot.get("laggard", {}).get("change")),
        class_name=_get_stat_value_class(snapshot.get("laggard", {}).get("change")),
    )
    html_content, _ = _replace_element_by_id(html_content, "market-rotation-stat-average-name", average_name)
    html_content, _ = _replace_element_by_id(
        html_content,
        "market-rotation-stat-average-value",
        _format_percent(snapshot.get("average_change")),
        class_name=_get_stat_value_class(snapshot.get("average_change")),
    )
    html_content, _ = _replace_element_by_id(html_content, "market-rotation-stat-breadth-name", "ETF日内分布")
    html_content, _ = _replace_element_by_id(
        html_content,
        "market-rotation-stat-breadth-value",
        breadth_value,
        class_name="stat-value text-blue",
    )
    html_content = _sync_ranked_holding_table(html_content, "leaders-top5-table", snapshot.get("leaders"))

    html_content = _sync_ranked_holding_table(html_content, "laggards-top5-table", snapshot.get("laggards"))
    return html_content



def _replace_element_by_id(html_content, element_id, new_inner_html, class_name=None):
    import re as _re

    pattern = rf'(<(?P<tag>\w+)(?=[^>]*\bid="{_re.escape(element_id)}")[^>]*>)(?P<inner>[\s\S]*?)(</(?P=tag)>)'
    match = _re.search(pattern, html_content)
    if not match:
        return html_content, False

    opening_tag = match.group(1)
    if class_name:
        if 'class="' in opening_tag:
            opening_tag = _re.sub(r'class="[^"]*"', f'class="{class_name}"', opening_tag, count=1)
        else:
            opening_tag = opening_tag[:-1] + f' class="{class_name}">'

    replacement = f'{opening_tag}{new_inner_html}{match.group(4)}'
    updated = html_content[:match.start()] + replacement + html_content[match.end():]
    return updated, True



def _get_editorial_content_path():
    files_config = config.get_files_config()
    editorial_filename = files_config.get('editorial_content_file', 'editorial_content.yaml')
    return os.path.join(PROJECT_ROOT, 'data', editorial_filename)



def _extract_latest_kline_date(kline_data):
    for _code, etf_data in (kline_data or {}).items():
        daily = (etf_data or {}).get('daily', {})
        dates = daily.get('dates', [])
        if dates:
            return dates[-1]
    return None



def _parse_iso_date(date_str):
    if not date_str or not isinstance(date_str, str):
        return None

    normalized = date_str.strip()[:10]
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError:
        return None



def _resolve_editorial_content_date(editorial_content, card_content):
    if isinstance(card_content, dict):
        content_date = card_content.get("content_date")
        if isinstance(content_date, str) and content_date.strip():
            return content_date.strip()

    global_date = (editorial_content or {}).get("content_date")
    if isinstance(global_date, str) and global_date.strip():
        return global_date.strip()

    return None



def _resolve_editorial_entry_html(entry):
    if isinstance(entry, dict):
        for key in ("html", "content", "text"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    if isinstance(entry, str):
        return entry.strip()

    return ""



def _truncate_inline_editorial_text(item_html, max_chars=44):
    if not isinstance(item_html, str):
        return ""

    normalized = item_html.strip()
    if len(normalized) <= max_chars:
        return normalized

    if "<" in normalized and ">" in normalized:
        return normalized

    truncated = normalized[: max_chars - 1].rstrip("，,、；;：: ")
    return f"{truncated}…"



def _build_editorial_entry_context(card_content, entry):
    context = {}

    if isinstance(card_content, dict):
        policy = card_content.get("freshness_policy")
        if isinstance(policy, str) and policy.strip():
            context["freshness_policy"] = policy.strip()

        content_date = card_content.get("content_date")
        if isinstance(content_date, str) and content_date.strip():
            context["content_date"] = content_date.strip()

    if isinstance(entry, dict):
        entry_policy = entry.get("freshness_policy")
        if isinstance(entry_policy, str) and entry_policy.strip():
            context["freshness_policy"] = entry_policy.strip()

        entry_date = entry.get("content_date")
        if isinstance(entry_date, str) and entry_date.strip():
            context["content_date"] = entry_date.strip()

    return context



def _evaluate_editorial_freshness(editorial_content, card_content, data_cutoff_date):
    policy = ((card_content or {}).get("freshness_policy") or (editorial_content or {}).get("freshness_policy") or "sticky").strip()
    content_date = _resolve_editorial_content_date(editorial_content, card_content)
    content_dt = _parse_iso_date(content_date)
    cutoff_dt = _parse_iso_date(data_cutoff_date)
    delta_days = None if not content_dt or not cutoff_dt else abs((cutoff_dt - content_dt).days)

    severity = "PASS"
    stale = False

    if not content_date:
        severity = "FAIL" if policy == "daily" else "WARN"
        stale = True
    elif policy in {"daily", "manual_daily"}:
        stale = delta_days is None or delta_days != 0
        severity = "FAIL" if policy == "daily" and stale else ("WARN" if stale else "PASS")
    elif policy == "weekly":
        stale = delta_days is None or delta_days > 7
        severity = "WARN" if stale else "PASS"
    else:
        stale = False
        severity = "PASS"

    if not content_date:
        message = f"观点日期：未标注 · {policy}"
    elif policy == "daily":
        message = f"观点日期：{content_date} · {'严格日更待修正' if stale else '严格日更'}"
    elif policy == "manual_daily":
        message = f"观点日期：{content_date} · {'待按数据截止日复核' if stale else '已按数据截止日复核'}"
    elif policy == "weekly":
        message = f"观点日期：{content_date} · {'周更观点待复核' if stale else '周更观点'}"
    else:
        reuse_hint = '沿用上一版编辑内容' if data_cutoff_date and content_date != data_cutoff_date else '编辑态内容'
        message = f"观点日期：{content_date} · {reuse_hint}"

    css_class = "editorial-meta"
    if severity != "PASS":
        css_class += " editorial-meta--warn"

    return {
        "policy": policy,
        "content_date": content_date,
        "data_cutoff_date": data_cutoff_date,
        "delta_days": delta_days,
        "severity": severity,
        "is_stale": stale,
        "message": message,
        "css_class": css_class,
    }



def _render_editorial_date_html(date_element_id, freshness):
    date_text = freshness.get("content_date") or freshness.get("data_cutoff_date") or "未标注"
    css_class = "editorial-date"
    if freshness.get("severity") != "PASS":
        css_class += " editorial-date--warn"
    return f'<span class="{css_class}" id="{date_element_id}">{date_text}</span>'



def load_editorial_content():
    editorial_path = _get_editorial_content_path()
    if not os.path.exists(editorial_path):
        logger.warn("未找到解释层内容配置，沿用当前 HTML 内联内容", {"file": editorial_path})
        return None

    editorial_content = config.get_editorial_content()
    if not editorial_content:
        logger.warn("解释层内容配置为空，沿用当前 HTML 内联内容", {"file": editorial_path})
        return None

    logger.info("解释层内容配置已加载", {
        "file": editorial_path,
        "content_date": editorial_content.get("content_date"),
        "etf_card_groups": len(editorial_content.get("etf_cards") or {}),
        "macro_cards": len(editorial_content.get("macro_cards") or {}),
    })
    return editorial_content



def _remove_element_by_id(html_content, element_id):
    import re as _re

    pattern = rf'<(?P<tag>\w+)(?=[^>]*\bid="{_re.escape(element_id)}")[^>]*>[\s\S]*?</(?P=tag)>'
    updated, count = _re.subn(pattern, '', html_content, count=1)
    return updated, count > 0



def _render_macro_card_inner_html(card_id, card_content, editorial_content, data_cutoff_date):
    title = card_content.get("title") or ""
    items = card_content.get("items") or []
    freshness = _evaluate_editorial_freshness(editorial_content, card_content, data_cutoff_date)
    rendered_items = []

    for index, item in enumerate(items, start=1):
        item_html = _truncate_inline_editorial_text(_resolve_editorial_entry_html(item))
        if not item_html:
            continue
        item_context = _build_editorial_entry_context(card_content, item)
        item_freshness = _evaluate_editorial_freshness(editorial_content, item_context, data_cutoff_date)
        date_html = _render_editorial_date_html(f"editorial-date-{card_id}-{index}", item_freshness)
        rendered_items.append(
            f'<li id="{card_id}-item-{index}"><span class="macro-item-text" id="{card_id}-text-{index}"><span class="macro-item-content" id="{card_id}-content-{index}">{item_html}</span>{date_html}</span></li>'
        )

    list_html = ''.join(rendered_items)
    return f'<h3 id="{card_id}-title">{title}</h3><ul id="{card_id}-list">{list_html}</ul>', freshness




def sync_editorial_content_html(html_content, editorial_content, data_cutoff_date=None):
    if not editorial_content:
        return html_content

    updated_targets = []
    missing_targets = []
    freshness_summary = {"PASS": 0, "WARN": 0, "FAIL": 0}
    freshness_alerts = []

    for code, card_group in (editorial_content.get("etf_cards") or {}).items():
        group = card_group or {}
        research_cards = group.get("research_cards") or []
        group_freshness = _evaluate_editorial_freshness(editorial_content, group, data_cutoff_date)
        freshness_summary[group_freshness["severity"]] += 1
        if group_freshness["severity"] != "PASS":
            freshness_alerts.append({"target": f"research-{code}", "message": group_freshness["message"]})

        html_content, removed = _remove_element_by_id(html_content, f"research-meta-{code}")
        if removed:
            updated_targets.append(f"research-meta-{code}:removed")

        for index, card_entry in enumerate(research_cards, start=1):
            card_html = _resolve_editorial_entry_html(card_entry)
            target_id = f"report-card-content-{code}-{index}"
            entry_context = _build_editorial_entry_context(group, card_entry)
            entry_freshness = _evaluate_editorial_freshness(editorial_content, entry_context, data_cutoff_date)
            text_target_id = f"report-card-text-{code}-{index}"
            rendered_html = f'<span class="report-card-text" id="{text_target_id}">{card_html}</span>{_render_editorial_date_html(f"research-date-{code}-{index}", entry_freshness)}'
            html_content, found = _replace_element_by_id(html_content, target_id, rendered_html)

            if found:
                updated_targets.append(target_id)
            else:
                missing_targets.append(target_id)

    for card_id, card_content in (editorial_content.get("macro_cards") or {}).items():
        rendered_html, freshness = _render_macro_card_inner_html(card_id, card_content or {}, editorial_content, data_cutoff_date)
        freshness_summary[freshness["severity"]] += 1
        if freshness["severity"] != "PASS":
            freshness_alerts.append({"target": card_id, "message": freshness["message"]})

        html_content, removed = _remove_element_by_id(html_content, f"editorial-meta-{card_id}")
        if removed:
            updated_targets.append(f"editorial-meta-{card_id}:removed")

        html_content, found = _replace_element_by_id(html_content, card_id, rendered_html)
        if found:
            updated_targets.append(card_id)
        else:
            missing_targets.append(card_id)

    logger.info("解释层内容已同步", {
        "updated_count": len(updated_targets),
        "missing_count": len(missing_targets),
        "content_date": editorial_content.get("content_date"),
        "data_cutoff_date": data_cutoff_date,
        "freshness_pass": freshness_summary["PASS"],
        "freshness_warn": freshness_summary["WARN"],
        "freshness_fail": freshness_summary["FAIL"],
    })
    if missing_targets:
        logger.warn("部分解释层内容目标未命中", {"targets": missing_targets[:10]})
    if freshness_alerts:
        logger.warn("解释层内容鲜度提醒", {"targets": freshness_alerts[:10]})

    return html_content




def sync_detail_panel_snapshot_html(html_content, kline_data, realtime_data):

    """把详情页首屏静态值同步到数据截止日口径，避免盘中实时价污染报告。"""
    updated_count = 0

    for code, kline_entry in (kline_data or {}).items():
        daily_change = _get_latest_daily_change(kline_entry)
        latest_close = _get_latest_daily_close(kline_entry)
        daily_close_series = _get_daily_close_series(kline_entry)
        performance_values = [
            _get_return_from_series(daily_close_series, 20),
            _get_return_from_series(daily_close_series, 59),
            _get_return_from_series(_get_weekly_close_series(kline_entry), 26),
            _get_return_from_series(_get_weekly_close_series(kline_entry), 51),
        ]

        html_content, found_label = _replace_element_by_id(
            html_content,
            f"latest-nav-label-{code}",
            "最新收盘价",
        )
        html_content, found_price = _replace_element_by_id(
            html_content,
            f"latest-nav-value-{code}",
            _format_price(latest_close),
        )

        html_content, found_daily = _replace_element_by_id(
            html_content,
            f"daily-change-value-{code}",
            _format_percent(daily_change),
            class_name=f"info-value {_get_trend_class(daily_change)}",
        )

        performance_columns = [
            ('1m', '近1月', performance_values[0]),
            ('3m', '近3月', performance_values[1]),
            ('6m', '近6月', performance_values[2]),
            ('1y', '近1年', performance_values[3]),
        ]
        cells = []
        for period_key, _label, value in performance_columns:
            class_name = '' if value is None else ('positive' if value >= 0 else 'negative')
            class_attr = f' class="{class_name}"' if class_name else ''
            cells.append(f'<td id="performance-return-{period_key}-{code}"{class_attr}>{_format_percent(value)}</td>')
        performance_html = '<tr>' + ''.join(f'<th>{label}</th>' for _, label, _ in performance_columns) + '</tr><tr>' + ''.join(cells) + '</tr>'
        html_content, found_perf = _replace_element_by_id(
            html_content,
            f"performance-table-{code}",
            performance_html,
        )

        overview_three_month_return = performance_values[1]
        html_content, found_overview_change = _replace_element_by_id(
            html_content,
            f"overview-card-{code}-change",
            _format_percent(overview_three_month_return),
            class_name=_get_overview_change_class(overview_three_month_return),
        )

        if found_label or found_price or found_daily or found_perf or found_overview_change:
            updated_count += 1

    logger.info("详情页静态快照已同步", {"etf_count": updated_count, "basis": "latest_close"})
    return html_content




def update_html_data(html_file=None):



    """更新HTML中的klineData和realtimeData数据
    
    使用字符串直接替换（不经过 BS4 序列化），避免 BS4 破坏 script 内容。
    """
    logger.info("=" * 60)
    logger.info("Step 3.5: 更新HTML中的数据（K线+实时行情）")
    logger.info("=" * 60)
    
    # 从配置加载文件路径
    files_config = config.get_files_config()
    html_update_config = config.get_html_update_config()
    
    html_file = html_file or HTML_FILE
    kline_file = os.path.join(DATA_DIR, files_config.get('data_files', {}).get('kline', 'etf_full_kline_data.json'))
    realtime_file = os.path.join(DATA_DIR, files_config.get('data_files', {}).get('realtime', 'etf_realtime_data.json'))

    
    # 加载HTML定位标记
    locators = html_update_config.get('locators', {})
    kline_const = locators.get('kline_const', 'const klineData = ')
    realtime_const = locators.get('realtime_const', 'const realtimeData = ')
    
    kline_data = None
    realtime_data = None
    
    try:
        with open(kline_file, 'r', encoding='utf-8') as f:
            kline_data = json.load(f)
        data_cutoff_date = _extract_latest_kline_date(kline_data)
        logger.info("读取K线数据成功", {"file": kline_file, "data_cutoff_date": data_cutoff_date})
    except Exception as e:
        logger.error("无法读取K线数据", {"error": str(e), "file": kline_file})
        return False

    
    try:
        with open(realtime_file, 'r', encoding='utf-8') as f:
            realtime_data = json.load(f)
        logger.info("读取实时行情数据成功", {"file": realtime_file})
    except Exception as e:
        logger.error("无法读取实时行情数据", {"error": str(e), "file": realtime_file})
        return False
    
    try:
        write_runtime_payload_file(kline_data, realtime_data)
    except Exception as e:
        logger.error("生成运行时载荷失败", {"error": str(e)})
        return False

    # 生成量化回测 baseline payload
    try:
        generate_quant_baseline_payload()
    except Exception as e:
        logger.error("生成量化回测载荷失败", {"error": str(e)})
        # 量化载荷失败不阻塞主流程

    # 读取 HTML 原始文本
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        logger.error("无法读取HTML文件", {"error": str(e), "file": html_file})
        return False

    
    # 1. 更新 klineData — 用字符串替换
    #    新架构下 index.html 不再内联 const klineData（已抽离到 assets/js/runtime_payload.js），
    #    此处对"找不到"场景降级为 INFO 跳过，而不是 ERROR 退出（BUG-011）。
    try:
        kline_json_str = json.dumps(kline_data, ensure_ascii=False, indent=8)
        new_kline_section = f'{kline_const}{kline_json_str}'
        # 提取const名称用于替换
        const_name = kline_const.replace('const ', '').replace(' = ', '').strip()
        html_content, found = _replace_js_const_in_html(html_content, const_name, new_kline_section)
        if found:
            logger.info("更新klineData成功", {"etf_count": 6, "data_type": "K线数据"})
        else:
            logger.info("HTML 中未发现 klineData 内联常量，跳过内联注入（已走 runtime_payload.js）", {
                "file": html_file, "const": const_name
            })
    except Exception as e:
        logger.error("更新klineData失败", {"error": str(e)})
        import traceback
        traceback.print_exc()
        return False

    # 2. 更新 realtimeData — 用字符串替换并强校验
    #    同上：新架构下不再内联，找不到时降级为 INFO（BUG-011）。
    try:
        realtime_json_str = json.dumps(realtime_data, ensure_ascii=False, indent=8)
        new_realtime_section = f'{realtime_const}{realtime_json_str}'
        # 提取const名称用于替换
        const_name = realtime_const.replace('const ', '').replace(' = ', '').strip()
        html_content, found = _replace_js_const_in_html(html_content, const_name, new_realtime_section)
        if found:
            logger.info("更新realtimeData成功", {"data_type": "实时行情数据"})
        else:
            logger.info("HTML 中未发现 realtimeData 内联常量，跳过内联注入（已走 runtime_payload.js）", {
                "file": html_file, "const": const_name
            })
    except Exception as e:
        logger.error("更新realtimeData失败", {"error": str(e)})
        import traceback
        traceback.print_exc()
        return False
    
    try:
        html_content = sync_detail_panel_snapshot_html(html_content, kline_data, realtime_data)
    except Exception as e:
        logger.error("同步详情页静态快照失败", {"error": str(e)})
        return False

    try:
        html_content = sync_fund_flow_section_html(html_content, kline_data, realtime_data, data_cutoff_date=data_cutoff_date)
    except Exception as e:
        logger.error("同步市场热度模块失败", {"error": str(e)})
        return False

    try:
        editorial_content = load_editorial_content()
        html_content = sync_editorial_content_html(html_content, editorial_content, data_cutoff_date=data_cutoff_date)
    except Exception as e:
        logger.error("同步解释层内容失败", {"error": str(e), "file": _get_editorial_content_path()})
        return False


    # 写回 HTML（直接写字符串，不经过 BS4）

    try:
        with logger.audit_operation("file_io", f"write {html_file}"):
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(html_content)

        file_size = os.path.getsize(html_file)
        logger.audit("file_io", f"HTML data updated: {html_file}", extra={"file_size_bytes": file_size})
        logger.info("HTML数据更新完成")
        return True
    except Exception as e:
        logger.error("写入HTML失败", {"error": str(e), "file": html_file})
        return False


def verify_output_files(html_file=None):
    """验证输出文件"""

    logger.info("=" * 60)
    logger.info("Step 4: 验证输出文件")
    logger.info("=" * 60)
    
    # 从配置加载文件配置
    files_config = config.get_files_config()
    data_files = files_config.get('data_files', {})
    html_file = html_file or HTML_FILE
    html_file_name = os.path.basename(html_file)
    required_files = [
        (os.path.join(DATA_DIR, data_files.get('kline', 'etf_full_kline_data.json')), data_files.get('kline', 'etf_full_kline_data.json')),
        (os.path.join(DATA_DIR, data_files.get('realtime', 'etf_realtime_data.json')), data_files.get('realtime', 'etf_realtime_data.json')),
        (html_file, html_file_name),
    ]

    
    all_exist = True
    for path, name in required_files:
        if os.path.exists(path):
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            logger.info("文件验证成功", {
                "name": name,
                "path": path,
                "modified_time": mtime.strftime('%Y-%m-%d %H:%M:%S')
            })
        else:
            logger.warn("文件缺失", {"name": name, "path": path})
            all_exist = False
    
    return all_exist

def print_summary(html_file=None):
    """打印总结信息"""
    logger.info("=" * 60)
    logger.info("更新完成")
    logger.info("=" * 60)
    
    # 从配置加载消息提示
    messages = config._config.get('messages', {})
    active_html_file = html_file or HTML_FILE
    local_preview = f"file:///{active_html_file.replace(os.sep, '/')}"
    html_output_line = f"  - {os.path.basename(active_html_file)}  (综合报告，根目录主文件)"
    
    summary = f"""
报告已更新完成！

输出文件:
  - data/etf_full_kline_data.json  (K线数据)
  - data/etf_realtime_data.json    (实时行情数据)
{html_output_line}

本地预览:
  {local_preview}

数据来源:
  - {messages.get('data_source', 'K线/实时行情：新浪财经API')}

注意事项:
  - {messages.get('update_timing', '建议在交易日收盘后(15:00之后)执行更新')}
  - {messages.get('ma_warmup_note', 'MA均线从第一天即有完整数据(已预热)')}
  - {messages.get('realtime_data_note', 'ETF主展示按数据截止日收盘价口径，成分股涨跌幅仍来自实时数据')}
"""
    logger.info("完成总结", {"summary": summary})





def main(publish: bool = False):
    """主函数
    
    执行以下步骤：
    1. 获取K线数据并更新
    2. 获取实时行情数据并更新
    3. 更新HTML中的数据（klineData + realtimeData）
    4. 更新报告日期
    5. 验证输出文件
    6. HTML 完整性验证（REQ-102）
    7. [发布模式] 企微通知推送
    8. [发布模式] GitHub Pages 部署
    """
    mode_label = "发布模式" if publish else "开发模式"
    logger.info("=" * 60)
    logger.info(f"ETF投资报告更新 - {datetime.now().strftime('%Y-%m-%d')} [{mode_label}]")
    logger.info("=" * 60)

    working_html_file = HTML_FILE
    
    logger.info("工作环境信息", {

        "work_dir": WORK_DIR,
        "start_time": datetime.now().strftime('%H:%M:%S'),
        "publish": publish,
        "html_target": working_html_file,
    })
    
    # 备份: 优先使用 transaction 模块；不存在时回退为简单文件备份。
    import shutil
    backup_path = os.path.join(PROJECT_ROOT, "index.html.bak")
    index_path = os.path.join(PROJECT_ROOT, "index.html")
    tx = None
    try:
        from transaction import TransactionManager
        tx = TransactionManager(PROJECT_ROOT)
        backup_path = tx.backup()
    except Exception:
        if os.path.exists(index_path):
            shutil.copy2(index_path, backup_path)

    def restore_backup():
        if tx is not None:
            return tx.restore(backup_path)
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, os.path.join(PROJECT_ROOT, "index.html"))
            return True
        return False

    def cleanup_backup():
        if tx is not None:
            return tx.cleanup()
        if os.path.exists(backup_path):
            os.remove(backup_path)
        return 0

    try:
        # Step 1: 更新K线数据
        if not run_kline_update():
            logger.error("K线数据更新失败，流程终止")
            return False
        
        # Step 2: 更新实时行情数据
        if not run_realtime_update():
            logger.warn("实时数据更新失败，继续执行")

        # Step 2.5: 抓取 editorial（研究卡 + 宏观卡）— REQ-158
        # 不因抓取失败阻断主流程，失败时保留上一版 yaml
        run_editorial_update()

        # Step 2.6: 增量更新量化回测数据（27 支 ETF 日线/周线 CSV）
        # 不因拉取失败阻断主流程，失败时量化板块使用上一版数据
        run_quant_data_update()

        # Step 3: 更新HTML中的数据
        if not update_html_data(html_file=working_html_file):
            logger.error("HTML数据注入失败，流程终止")
            return False
        
        # Step 4: 更新报告日期
        update_html_dates(html_file=working_html_file)

        # 估值模块已随 F4 退役移除（REQ-281）


        # Step 5: 验证输出文件
        if not verify_output_files(html_file=working_html_file):
            logger.warn("部分文件缺失，请检查")
        
        # Step 6: HTML 完整性验证（REQ-102）
        logger.info("=" * 60)
        logger.info("Step 5: HTML 完整性验证")
        logger.info("=" * 60)
        try:
            from verify_html_integrity import verify_html_integrity, print_report
            html_path = working_html_file
            result = verify_html_integrity(html_path)
            print_report(result, html_path)
            
            if not result["passed"]:
                logger.warn("HTML完整性验证失败，正在回滚")
                restore_backup()
                return False
        except ImportError:
            logger.info("verify_html_integrity 模块未找到，跳过验证")
        
        # 清理旧备份
        cleanup_backup()
        
        # Step 7: 执行系统健康检查（REQ-106）
        logger.info("=" * 60)
        logger.info("Step 6: 执行系统健康检查")
        logger.info("=" * 60)
        try:
            import health_check
            if os.path.abspath(working_html_file) != os.path.abspath(HTML_FILE):
                health_check.HTML_FILE = working_html_file
            health_check_results = health_check.run_all_checks()
            
            # 统计检查结果
            total = len(health_check_results)
            passed = sum(1 for r in health_check_results if r.status == "PASS")
            warnings = sum(1 for r in health_check_results if r.status == "WARN")
            failed = sum(1 for r in health_check_results if r.status == "FAIL")
            
            logger.info("健康检查完成", {
                "total": total,
                "passed": passed,
                "warnings": warnings,
                "failed": failed
            })
            
            if failed > 0:
                logger.warn("健康检查发现问题，请查看报告")
            elif warnings > 0:
                logger.warn("健康检查有若干警告，但不影响功能")
            else:
                logger.info("系统健康状态: 正常")
        except ImportError:
            logger.info("health_check 模块未找到，跳过健康检查")
        except Exception as e:
            logger.warn("执行健康检查时出错", {"error": str(e)})
        
        # ---- 发布模式专属步骤 ----
        if publish:
            # Step 7: 企微通知推送
            try:
                import notifier
                notifier.main(DATA_DIR)
            except ImportError:
                logger.warn("notifier 模块未找到，跳过企微通知")
            except Exception as e:
                logger.error("企微通知失败，不阻塞后续步骤", {"error": str(e)})
            
            # Step 8: GitHub Pages 部署
            try:
                import deployer
                deployer.main(PROJECT_ROOT, html_source_path=working_html_file)
            except ImportError:
                logger.info("deployer 模块未找到，跳过 GitHub 部署")
            except Exception as e:
                logger.error("GitHub 部署失败", {"error": str(e)})
        
        # 打印总结
        print_summary(html_file=working_html_file)
        
        logger.info("工作完成", {
            "end_time": datetime.now().strftime('%H:%M:%S')
        })
        
        return True
        
    except Exception as e:
        # REQ-103: 事务回滚
        logger.error("更新过程中发生异常", {"error": str(e)})
        import traceback
        traceback.print_exc()
        logger.warn("正在从备份恢复")
        restore_backup()
        return False



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETF投资报告更新")
    parser.add_argument("--publish", action="store_true", help="发布模式：企微通知 + GitHub Pages 部署")
    args = parser.parse_args()
    success = main(publish=args.publish)
    sys.exit(0 if success else 1)
