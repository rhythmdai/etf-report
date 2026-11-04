"""Shared parameter contract for Quant Tuner and backtest engine.

This module is the single place for converting between:
- quant_universe.yaml preset blocks
- Tuner frontend parameter dicts
- quant_backtest.run_backtest(config_override=...) fragments
"""
import json
from copy import deepcopy
from datetime import datetime


_WEIGHT_KEYS = ("w1", "w3", "w7")

PARAM_SCHEMA = {
    "version": 1,
    "groups": [
        {
            "key": "scoring",
            "label": "因子权重",
            "params": [
                {"key": "w1", "label": "F1 EMA 偏离度权重", "unit": "ui_percent", "engine_path": "scoring.weights.ema_deviation"},
                {"key": "w3", "label": "F3 自归一化量比权重", "unit": "ui_percent", "engine_path": "scoring.weights.volume_ratio"},
                {"key": "w7", "label": "F7 对数收益偏离权重", "unit": "ui_percent", "engine_path": "scoring.weights.log_return_deviation"},
                ],
        },
        {
            "key": "sensitivity",
            "label": "映射灵敏度",
            "params": [
                {"key": "f1_sensitivity", "label": "F1 sigmoid 尺度", "unit": "raw", "engine_path": "scoring.sensitivity.f1"},
                {"key": "f3_sensitivity", "label": "F3 log-sigmoid 尺度", "unit": "raw", "engine_path": "scoring.sensitivity.f3"},
                {"key": "f7_up_power", "label": "F7 幂次（超涨）", "unit": "raw", "engine_path": "scoring.sensitivity.f7_up_power"},
                {"key": "f7_up_span", "label": "F7 饱和半径（超涨）", "unit": "raw", "engine_path": "scoring.sensitivity.f7_up_span"},
                {"key": "f7_down_power", "label": "F7 幂次（超跌）", "unit": "raw", "engine_path": "scoring.sensitivity.f7_down_power"},
                {"key": "f7_down_span", "label": "F7 饱和半径（超跌）", "unit": "raw", "engine_path": "scoring.sensitivity.f7_down_span"},
            ],
        },
        {
            "key": "confidence",
            "label": "仓位控制",
            "params": [
                {"key": "conf_type", "label": "信心函数类型", "unit": "enum", "engine_path": "confidence.type", "locked": True, "lock_reason": "仅 MA 趋势可用。旧信心函数已退役。"},
                {"key": "benchmarks", "label": "投票委员会", "unit": "enum", "engine_path": "confidence.benchmarks", "locked": True, "lock_reason": "固定沪深 300 单票。多指数投票未见稳健超额。"},
                {"key": "ma_trend_period", "label": "MA Trend 周期", "unit": "weeks", "engine_path": "confidence.ma_trend_period"},
                {"key": "ma_bull_pos", "label": "MA 上方仓位", "unit": "ratio", "engine_path": "confidence.ma_bull_pos"},
                {"key": "ma_bear_pos", "label": "MA 下方仓位", "unit": "ratio", "engine_path": "confidence.ma_bear_pos"},
                {"key": "ma_direction_confirm", "label": "MA 方向确认", "unit": "bool", "engine_path": "confidence.ma_direction_confirm", "locked": True, "lock_reason": "未测试过 False。关闭可能减少误判但也可能引入反向信号，不优化。"},
                {"key": "dead_zone", "label": "旧信心函数死区", "unit": "ui_percent", "engine_path": "confidence.dead_zone"},
                {"key": "full_zone", "label": "旧信心函数满配阈值", "unit": "ui_percent", "engine_path": "confidence.full_zone"},
            ],
        },
        {
            "key": "position",
            "label": "仓位分配与调仓",
            "params": [
                {"key": "max_holdings", "label": "最大持仓数", "unit": "count", "engine_path": "position.max_holdings"},
                {"key": "signal_steps", "label": "仓位步数", "unit": "count", "engine_path": "position.signal_steps", "locked": True, "lock_reason": "纯摩擦参数。减小会隐式提升集中度，与 TB 越俎代庖。"},
                {"key": "top_boost", "label": "头名加成", "unit": "steps", "engine_path": "position.top_boost"},
                {"key": "concentration", "label": "仓位集中度 C", "unit": "raw", "engine_path": "position.concentration"},
                {"key": "c_sensitivity", "label": "C 动态灵敏度", "unit": "raw", "engine_path": "position.c_sensitivity"},
                {"key": "rebalance_freq", "label": "调仓频率", "unit": "enum", "engine_path": "position.rebalance_freq", "locked": True, "lock_reason": "固定日调仓。周调仓因信号延迟已被淘汰。"},
                {"key": "band", "label": "B 分数带", "unit": "raw", "engine_path": "position.band"},
                {"key": "band_sensitivity", "label": "BS 分数带灵敏度", "unit": "raw", "engine_path": "position.band_sensitivity", "locked": True, "lock_reason": "2026-07-28 扫参：所有非零值严格劣于静态（-3~-25pp AR）。动态 band 引入过度交易。"},
            ],
        },
        {
            "key": "factors",
            "label": "因子周期与阈值",
            "params": [
                {"key": "f1_ema_period", "label": "F1 EMA 周期", "unit": "weeks", "engine_path": "factors.ema.period_weeks"},
                {"key": "f1_active_days", "label": "F1 抢跑日", "unit": "enum", "engine_path": "factors.f1_active_days", "locked": True, "lock_reason": "固定周五抢跑。多日抢跑未见稳健超额。"},
                {"key": "f3_vol_window", "label": "F3 量比窗口", "unit": "days", "engine_path": "factors.volume_ratio.window_days"},
                {"key": "f7_window", "label": "F7 累计收益窗口", "unit": "days", "engine_path": "factors.log_return_deviation.window_days"},
                {"key": "f7_lookback", "label": "F7 归一化窗口", "unit": "days", "engine_path": "factors.log_return_deviation.lookback_days", "locked": True, "lock_reason": "REQ-382 联合扫参确认 250 日最优，缩短无益。"},
            ],
        },
        {
            "key": "universe",
            "label": "标的池",
            "params": [
                {"key": "universe", "label": "参与回测 ETF 代码", "unit": "csv_codes", "engine_path": "runtime.universe_filter"},
            ],
        },
    ],
}

def get_param_schema():
    """Return PARAM_SCHEMA with locked_value injected for locked params."""
    schema = deepcopy(PARAM_SCHEMA)
    for group in schema["groups"]:
        for p in group["params"]:
            if p.get("locked"):
                lp = LOCKED_PARAMS.get(p["key"], {})
                p["locked_value"] = lp.get("value")
    return schema


def iter_schema_param_keys():
    for group in PARAM_SCHEMA["groups"]:
        for param in group["params"]:
            yield param["key"]


def _as_float(value, default=0.0):
    if value is None or value == "":
        return default
    return float(value)


def _as_int(value, default=0):
    if value is None or value == "":
        return default
    return int(float(value))


def _weight_to_ui_percent(value, default=0.0):
    """Convert engine-scale weight (0-1) to UI percentage (0-100)."""
    return int(round(_as_float(value, default) * 100))


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def weight_total_pct(params):
    return sum(_as_float(params.get(k), 0.0) for k in _WEIGHT_KEYS)


def parse_universe_filter(params):
    universe_str = (params.get("universe", "") or "").strip()
    if universe_str == "__NONE__":
        return [], "empty"
    if not universe_str:
        return None, "all"
    codes = [c.strip() for c in universe_str.split(",") if c.strip()]
    return sorted(set(codes)), "filtered"


def validate_tuner_params(params):
    total = weight_total_pct(params)
    if abs(total - 100.0) > 1e-6:
        return f"Factor weights must sum to 100%, got {total:g}%"

    universe_filter, mode = parse_universe_filter(params)
    if mode == "empty":
        return "Universe is empty; select at least 6 ETFs"
    if universe_filter is not None and len(universe_filter) < 6:
        return f"Only {len(universe_filter)} ETFs selected, need at least 6"

    if _as_float(params.get("ma_bull_pos"), 1.0) <= _as_float(params.get("ma_bear_pos"), 0.3):
        return "Bull position must be greater than bear position"

    execution_timing = params.get("execution_timing", "same_close")
    if execution_timing != "same_close":
        return "execution_timing must be same_close"

    return None


# Params that must be present — missing = silent bug, refuse to proceed.
# MODIFYING THIS SET: keep in sync with —
#   1. tests/test_quant_contract.py  sample_params()        (rigid: missing → ValueError → pytest fail)
#   2. config/defaults.yaml           non-searchable defaults (soft: DEFAULT_LOCK is single source of truth)
#   3. assets/tuner-left.html          slider / input control  (soft: missing → param not adjustable in UI)
_REQUIRED_PARAMS = frozenset([
    'w1', 'w3', 'w7', 'ma_bull_pos', 'ma_bear_pos', 'max_holdings', 'ma_trend_period',
    'concentration', 'c_sensitivity', 'band', 'band_sensitivity', 'signal_steps', 'top_boost',
    'f7_up_power', 'f7_up_span', 'f7_window', 'f7_lookback', 'f3_vol_window', 'f1_sensitivity', 'f3_sensitivity',
    'f1_ema_period',
])


# ── Locked params — single source of truth ──
# These params appear in the UI but are not adjustable.  All consumers
# (WF scripts, research utils, Tuner frontend) read this dict to know
# which params are read-only and what value to use.
LOCKED_PARAMS = {
    "signal_steps":    {"value": 40,    "reason": "纯摩擦参数。减小会隐式提升集中度，与 TB 越俎代庖。REQ-323 WF 诊断确认。"},
    "f7_lookback":     {"value": 250,   "reason": "REQ-382 联合扫参确认 250 日最优，缩短无益。"},
    "conf_type":       {"value": "ma_trend", "reason": "仅 MA 趋势可用。旧信心函数已退役。"},
    "rebalance_freq":  {"value": "daily",    "reason": "固定日调仓。周调仓因信号延迟已被淘汰。"},
    "benchmarks":      {"value": ["510300"], "reason": "固定沪深 300 单票。多指数投票未见稳健超额。"},
    "f1_active_days":  {"value": 1,     "reason": "固定周五抢跑。多日抢跑未见稳健超额。"},
    "ma_direction_confirm": {"value": True, "reason": "未测试过 False。关闭可能减少误判但也可能引入反向信号，不优化。"},
    "band_sensitivity": {"value": 0.0, "reason": "2026-07-28 扫参：所有非零值严格劣于静态 band=0.03（-3~-25pp AR）。动态 band 引入过度交易。"},
}


def load_defaults():
    """Load non-searchable parameter defaults from config/defaults.yaml."""
    import yaml as _yaml, pathlib as _pl
    _path = _pl.Path(__file__).resolve().parent.parent.parent.parent / "config" / "defaults.yaml"
    with open(_path, "r", encoding="utf-8") as f:
        return _yaml.safe_load(f)


def tuner_params_to_config_override(params):
    missing = [k for k in _REQUIRED_PARAMS if k not in params]
    if missing:
        raise ValueError(
            f"Missing required params: {missing}. "
            f"Old pool trials may lack params added in newer versions. "
            f"Check YAML preset configuration and re-save from Tuner."
        )
    df = load_defaults()
    sc_df = df.get("scoring", {})
    sens_df = sc_df.get("sensitivity", {})
    cf_df = df.get("confidence", {})
    pos_df = df.get("position", {})
    fac_df = df.get("factors", {})


    result = {
        "scoring": {
            "weights": {
                "ema_deviation": _as_float(params.get("w1"), sc_df.get("weights", {}).get("ema_deviation", 0.71) * 100) / 100.0,
                "volume_ratio": _as_float(params.get("w3"), sc_df.get("weights", {}).get("volume_ratio", 0.13) * 100) / 100.0,
                "log_return_deviation": _as_float(params.get("w7"), sc_df.get("weights", {}).get("log_return_deviation", 0.0)) / 100.0,
            },
            "sensitivity": {
                "f1": _as_float(params.get("f1_sensitivity"), sens_df.get("f1", 8.0)),
                "f3": _as_float(params.get("f3_sensitivity"), sens_df.get("f3", 1.0)),
                "f7_up_power": _as_float(params.get("f7_up_power"), sens_df.get("f7_up_power", 7.0)),
                "f7_up_span": _as_float(params.get("f7_up_span"), sens_df.get("f7_up_span", 3.0)),
                "f7_down_power": _as_float(params.get("f7_down_power"), sens_df.get("f7_down_power", 7.0)),
                "f7_down_span": _as_float(params.get("f7_down_span"), sens_df.get("f7_down_span", 3.0)),
            },
        },
        "confidence": {
            "type": params.get("conf_type", cf_df.get("type", "ma_trend")),
            "dead_zone": _as_int(params.get("dead_zone"), cf_df.get("dead_zone", 25)),
            "full_zone": _as_int(params.get("full_zone"), cf_df.get("full_zone", 65)),
            "ma_bull_pos": _as_float(params.get("ma_bull_pos"), cf_df.get("ma_bull_pos", 1.0)),
            "ma_bear_pos": _as_float(params.get("ma_bear_pos"), cf_df.get("ma_bear_pos", 0.3)),
            "ma_trend_period": _as_int(params.get("ma_trend_period"), cf_df.get("ma_trend_period", 26)),
            "ma_direction_confirm": _as_bool(params.get("ma_direction_confirm"), cf_df.get("ma_direction_confirm", True)),
            "benchmarks": params.get("benchmarks", cf_df.get("benchmarks", ["510300"])),
        },
        "position": {
            "max_holdings": _as_int(params.get("max_holdings"), pos_df.get("max_holdings", 6)),
            "signal_steps": _as_int(params.get("signal_steps"), pos_df.get("signal_steps", 17)),
            "top_boost": _as_int(params.get("top_boost"), pos_df.get("top_boost", 0)),
            "concentration": _as_float(params.get("concentration"), pos_df.get("concentration", 2.0)),
            "c_sensitivity": _as_float(params.get("c_sensitivity"), pos_df.get("c_sensitivity", 0.0)),
            "rebalance_freq": params.get("rebalance_freq", pos_df.get("rebalance_freq", "daily")),
            "execution_timing": params.get("execution_timing", pos_df.get("execution_timing", "same_close")),
            "band": _as_float(params.get("band"), pos_df.get("band", 0.0)),
            "band_sensitivity": _as_float(params.get("band_sensitivity"), pos_df.get("band_sensitivity", 0.0)),
        },
        "factors": {
            "f1_active_days": _as_int(params.get("f1_active_days"), fac_df.get("f1_active_days", 1)),
            "ema": {"period_weeks": _as_int(params.get("f1_ema_period"), fac_df.get("ema", {}).get("period_weeks", 20))},
            "volume_ratio": {"window_days": _as_int(params.get("f3_vol_window"), fac_df.get("volume_ratio", {}).get("window_days", 20))},
            "log_return_deviation": {
                "window_days": _as_int(params.get("f7_window"), fac_df.get("log_return_deviation", {}).get("window_days", 20)),
                "lookback_days": _as_int(params.get("f7_lookback"), fac_df.get("log_return_deviation", {}).get("lookback_days", 250)),
                "min_days": fac_df.get("log_return_deviation", {}).get("min_days", 60),
                "sigma_floor": fac_df.get("log_return_deviation", {}).get("sigma_floor", 0.01),
            },
        },
    }
    return result


def tuner_params_to_preset_patch(params, base_cfg=None):
    if base_cfg is None:
        return tuner_params_to_config_override(params)

    cfg = deepcopy(base_cfg)
    cfg.setdefault("scoring", {})
    cfg.setdefault("confidence", {})
    cfg.setdefault("position", {})
    cfg.setdefault("factors", {})
    cfg["factors"].setdefault("ema", {})
    cfg["factors"].setdefault("volume_ratio", {})
    cfg["scoring"].setdefault("sensitivity", {})

    override = tuner_params_to_config_override(params)

    cfg["scoring"].update(override["scoring"])
    cfg["confidence"].update(override["confidence"])
    cfg["position"].update(override["position"])
    cfg["factors"].update(override["factors"])

    return {
        "scoring": cfg["scoring"],
        "confidence": cfg["confidence"],
        "position": cfg["position"],
        "factors": cfg["factors"],
    }


def preset_to_tuner_params(preset_key, preset_cfg, global_conf=None):
    global_conf = global_conf or {}
    global_regime_base = global_conf.get("regime_base", {})
    pc = preset_cfg.get("confidence", {})
    prb = pc.get("regime_base", {})
    w = preset_cfg.get("scoring", {}).get("weights", {})
    scoring = preset_cfg.get("scoring", {})
    sensitivity = scoring.get("sensitivity", {})
    position = preset_cfg.get("position", {})
    factors = preset_cfg.get("factors", {})

    _df = load_defaults()
    _pos_df = _df["position"]
    _fac_df = _df["factors"]
    _sens_df = _df["scoring"]["sensitivity"]
    _cf_df = _df["confidence"]
    return {
        "label": preset_cfg.get("label", preset_key),
        "description": preset_cfg.get("description", ""),
        "w1": _weight_to_ui_percent(w.get("ema_deviation"), 0.30),
        "w3": _weight_to_ui_percent(w.get("volume_ratio"), 0.30),
        "conf_type": pc.get("type", _cf_df["type"]),
        "dead_zone": pc.get("dead_zone", global_conf.get("dead_zone", _cf_df["dead_zone"])),
        "full_zone": pc.get("full_zone", global_conf.get("full_zone", _cf_df["full_zone"])),
        "regime_base_bull": prb.get("bull_trend", global_regime_base.get("bull_trend", 0.95)),
        "regime_base_choppy": prb.get("choppy_range", global_regime_base.get("choppy_range", 0.75)),
        "regime_base_bear": prb.get("bear_trend", global_regime_base.get("bear_trend", 0.35)),
        "regime_window": pc.get("regime_window", global_conf.get("regime_window", 8)),
        "regime_threshold": pc.get("regime_threshold", global_conf.get("regime_threshold", 0.03)),
        "breadth_weight": pc.get("breadth_weight", global_conf.get("breadth_weight", 0.2)),
        "clarity_threshold": pc.get("clarity_threshold", global_conf.get("clarity_threshold", 0.03)),
        "dd_sensitivity": pc.get("dd_sensitivity", global_conf.get("dd_sensitivity", 0.2)),
        "crash_window": pc.get("crash_window", global_conf.get("crash_window", 2)),
        "crash_threshold": pc.get("crash_threshold", global_conf.get("crash_threshold", -0.03)),
        "recovery_threshold": pc.get("recovery_threshold", global_conf.get("recovery_threshold", -0.01)),
        "crash_pos": pc.get("crash_pos", global_conf.get("crash_pos", 0.20)),
        "recovery_pos": pc.get("recovery_pos", global_conf.get("recovery_pos", 0.70)),
        "recovery_dd_level": pc.get("recovery_dd_level", global_conf.get("recovery_dd_level", -0.05)),
        "ma_bull_pos": pc.get("ma_bull_pos", global_conf.get("ma_bull_pos", _cf_df["ma_bull_pos"])),
        "ma_bear_pos": pc.get("ma_bear_pos", global_conf.get("ma_bear_pos", _cf_df["ma_bear_pos"])),
        "ma_trend_period": pc.get("ma_trend_period", global_conf.get("ma_trend_period", _cf_df["ma_trend_period"])),
        "ma_direction_confirm": pc.get("ma_direction_confirm", global_conf.get("ma_direction_confirm", _cf_df["ma_direction_confirm"])),
        "benchmarks": pc.get("benchmarks", global_conf.get("benchmarks", _cf_df["benchmarks"])),
        "max_holdings": position.get("max_holdings", _pos_df["max_holdings"]),
        "signal_steps": int(position.get("signal_steps", _pos_df.get("signal_steps", 17))),
        "top_boost": int(position.get("top_boost", _pos_df.get("top_boost", 0))),
        "concentration": position.get("concentration", _pos_df["concentration"]),
        "c_sensitivity": position.get("c_sensitivity", _pos_df["c_sensitivity"]),
        "f1_ema_period": factors.get("ema", {}).get("period_weeks", _fac_df["ema"]["period_weeks"]),
        "f3_vol_window": factors.get("volume_ratio", {}).get("window_days", _fac_df["volume_ratio"]["window_days"]),
        "f1_sensitivity": sensitivity.get("f1", _sens_df["f1"]),
        "f3_sensitivity": sensitivity.get("f3", _sens_df["f3"]),
        "rebalance_freq": position.get("rebalance_freq", _pos_df["rebalance_freq"]),
        "execution_timing": position.get("execution_timing", _pos_df["execution_timing"]),
        "band": position.get("band", _pos_df["band"]),
        "band_sensitivity": position.get("band_sensitivity", _pos_df.get("band_sensitivity", 0)),
        "w7": _weight_to_ui_percent(w.get("log_return_deviation"), 0),
        "f7_up_power": sensitivity.get("f7_up_power", _sens_df["f7_up_power"]),
        "f7_up_span": sensitivity.get("f7_up_span", _sens_df["f7_up_span"]),
        "f7_down_power": sensitivity.get("f7_down_power", _sens_df.get("f7_down_power", 7.0)),
        "f7_down_span": sensitivity.get("f7_down_span", _sens_df.get("f7_down_span", 3.0)),
        "f7_window": factors.get("log_return_deviation", {}).get("window_days", _fac_df["log_return_deviation"]["window_days"]),
        "f7_lookback": factors.get("log_return_deviation", {}).get("lookback_days", _fac_df["log_return_deviation"]["lookback_days"]),
        "f1_active_days": factors.get("f1_active_days", _fac_df["f1_active_days"]),
    }


def build_presets_response(cfg):
    presets = cfg.get("presets", {})
    global_conf = cfg.get("confidence", {})
    result = {
        key: preset_to_tuner_params(key, preset_cfg, global_conf)
        for key, preset_cfg in presets.items()
    }

    if "cst-1" not in result:
        template = result.get("act-1", {}) or result.get("zen-1", {})
        if template:
            cst = dict(template)
            cst["label"] = "自定义策略"
            cst["description"] = "用户自定义策略，初始参数继承自趋势锚定。"
            result["cst-1"] = cst

    result["_universe_options"] = [
        {
            "code": e["code"],
            "name": e.get("name", e["code"]),
            "sector": e.get("sector", ""),
            "active": e.get("active", True),
            "marginable": e.get("marginable", True),
            "group1": e.get("group1", ""),
        }
        for e in cfg.get("universe", [])
    ]
    return result
DEFAULT_PRESET = "zen-nl"
