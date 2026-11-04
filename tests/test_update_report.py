import json
import sys
import textwrap
from datetime import datetime
from types import SimpleNamespace

import pytest



def test_load_quant_preset_params_uses_shared_contract(tmp_path, load_module):
    module = load_module("update_report")
    config_path = tmp_path / "quant_universe.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            confidence:
              dead_zone: 25
              full_zone: 65
            presets:
              gam-0:
                label: 波动探测
                scoring:
                  weights:
                    ema_deviation: 0.50
                    volume_ratio: 0.40
                    residual_momentum: 0
                    log_return_deviation: 0.10
                  sensitivity:
                    f1: 8.0
                    f3: 1.5
                    f7_up_power: 15.0
                    f7_up_span: 3.5
                confidence:
                  type: ma_trend
                  ma_trend_period: 26
                  ma_bull_pos: 1.0
                  ma_bear_pos: 0.3
                  ma_direction_confirm: true
                position:
                  max_holdings: 6
                  discretize_step: 0.05
                  concentration: 0.0
                  rebalance_freq: daily
                  execution_timing: same_close
                  band: 0.03
                factors:
                  ema:
                    period_weeks: 5
                  volume_ratio:
                    window_days: 20
                  log_return_deviation:
                    window_days: 20
                  f6_rsi_thresh: 80.0
                  f6_drop_thresh: 0.025
                  f6_base_penalty: 0.15
            """
        ).strip(),
        encoding="utf-8",
    )

    # 显式传入 gam-0：本用例的 fixture 只定义了这一个预设，
    # 而不传参时函数会跟随 DEFAULT_PRESET（当前是 zen-nl）。
    params = module.load_quant_preset_params(str(config_path), "gam-0")

    assert params["w1"] == 50
    assert params["w3"] == 40
    assert params["w7"] == 10
    assert params["signal_steps"] > 0
    assert params["execution_timing"] == "same_close"
    assert params["concentration"] == 0.0
    assert params["band"] == pytest.approx(0.03)


def test_build_quant_payload_config_section_uses_shared_contract(load_module):
    module = load_module("update_report")
    params = module.default_quant_preset_params()

    section = module.build_quant_payload_config_section(params)
    yr1 = section["yr1"]
    yr3 = section["yr3"]

    assert yr1["scoring"]["weights"]["volume_ratio"] == pytest.approx(0.45)
    assert yr1["scoring"]["weights"]["log_return_deviation"] == 0.1
    assert yr1["position"]["signal_steps"] > 0
    assert yr1["position"]["execution_timing"] == "same_close"
    assert yr1["position"]["concentration"] == 0.0
    assert yr1["position"]["band"] == 0.03
    assert yr1["factors"]["log_return_deviation"]["window_days"] == 20
    assert yr3 == yr1
    assert yr3 is not yr1


def test_replace_text_in_html_replaces_target_near_marker(load_module):
    module = load_module("update_report")
    html = 'ReportDate: <strong style="color: #3b82f6;">2026-01-01</strong>'

    updated, found = module._replace_text_in_html(
        html,
        "ReportDate:",
        r"<strong[^>]*>\d{4}-\d{2}-\d{2}</strong>",
        '<strong style="color: #3b82f6;">2026-04-11</strong>',
    )

    assert found is True
    assert "2026-04-11" in updated
    assert "2026-01-01" not in updated



def test_replace_js_const_in_html_replaces_nested_object(load_module):
    module = load_module("update_report")
    html = '<script>const klineData = {"old": {"nested": 1}}; const other = 1;</script>'

    updated, found = module._replace_js_const_in_html(
        html,
        "klineData",
        'const klineData = {"new": {"nested": 2}}',
    )

    assert found is True
    assert 'const klineData = {"new": {"nested": 2}};' in updated
    assert '"old"' not in updated


def test_update_html_dates_uses_latest_kline_date(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    html_file = tmp_path / "index.html"

    (data_dir / "etf_full_kline_data.json").write_text(
        json.dumps({"510000": {"daily": {"dates": ["2026-04-10"]}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    html_file.write_text(
        '报告日期: <strong style="color: #3b82f6;">2026年01月01日</strong>\n'
        '数据截止: 2026-01-01\n'
        '生成时间: 2026-01-01\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))

    assert module.update_html_dates() is True

    updated = html_file.read_text(encoding="utf-8")
    assert "数据截止: 2026-04-10" in updated
    assert datetime.now().strftime("%Y-%m-%d") in updated
    assert datetime.now().strftime("%Y年%m月%d日") in updated


def test_update_html_dates_preserves_header_ids(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    html_file = tmp_path / "index.html"

    (data_dir / "etf_full_kline_data.json").write_text(
        json.dumps({"510000": {"daily": {"dates": ["2026-04-10"]}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    html_file.write_text(
        '<div id="report-date-text"><span id="report-date-label">报告日期:</span> <strong class="text-blue" id="report-date-value">2026年01月01日</strong></div>\n'
        '<div id="report-cutoff-text"><span id="report-cutoff-label">数据截止:</span> <span id="report-cutoff-value">2026-01-01</span></div>\n'
        '生成时间: 2026-01-01\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))

    assert module.update_html_dates() is True

    updated = html_file.read_text(encoding="utf-8")
    assert f'id="report-date-value">{datetime.now().strftime("%Y年%m月%d日")}</strong>' in updated
    assert 'id="report-cutoff-value">2026-04-10</span>' in updated



def test_sync_fund_flow_section_html_uses_daily_realtime_snapshot(load_module):
    module = load_module("update_report")
    html = (
        '<h2 id="fund-flow-title">旧标题</h2>'
        '<span id="fund-flow-source-value">旧来源</span>'
        '<span id="fund-flow-updated-value">旧时间</span>'
        '<span id="market-rotation-card-title">旧卡标题</span>'
        '<div id="market-rotation-stat-leader-name">旧强势</div>'
        '<div class="stat-value text-amber" id="market-rotation-stat-leader-value">旧值</div>'
        '<div id="market-rotation-stat-laggard-name">旧弱势</div>'
        '<div class="stat-value text-amber" id="market-rotation-stat-laggard-value">旧值</div>'
        '<div id="market-rotation-stat-average-name">旧均值名</div>'
        '<div class="stat-value text-amber" id="market-rotation-stat-average-value">旧均值</div>'
        '<div id="market-rotation-stat-breadth-name">旧宽度名</div>'
        '<div class="stat-value text-blue" id="market-rotation-stat-breadth-value">旧宽度</div>'
        '<td id="leaders-top5-table-name-1">旧股票</td>'
        '<td id="leaders-top5-table-industry-1">旧行业</td>'
        '<td id="leaders-top5-table-weight-1">旧权重</td>'
        '<td class="text-amber text-bold" id="leaders-top5-table-change-1">旧涨幅</td>'
        '<td id="leaders-top5-table-name-2">旧股票</td>'
        '<td id="leaders-top5-table-industry-2">旧行业</td>'
        '<td id="leaders-top5-table-weight-2">旧权重</td>'
        '<td class="text-amber text-bold" id="leaders-top5-table-change-2">旧涨幅</td>'
        '<td id="laggards-top5-table-name-1">旧股票</td>'
        '<td id="laggards-top5-table-industry-1">旧行业</td>'
        '<td id="laggards-top5-table-weight-1">旧权重</td>'
        '<td class="text-amber text-bold" id="laggards-top5-table-change-1">旧跌幅</td>'
        '<td id="laggards-top5-table-name-2">旧股票</td>'
        '<td id="laggards-top5-table-industry-2">旧行业</td>'
        '<td id="laggards-top5-table-weight-2">旧权重</td>'
        '<td class="text-amber text-bold" id="laggards-top5-table-change-2">旧跌幅</td>'
    )
    kline_data = {
        "510000": {"name": "示例ETF甲"},
        "510001": {"name": "示例ETF乙"},
    }
    realtime_data = {
        "510000": {
            "name": "示例ETF甲",
            "etf_change": 1.2,
            "timestamp": "2026-04-16T15:00:00",
            "holdings": [
                {"name": "宁德时代", "ratio": 10.0, "change": 3.2},
                {"name": "隆基绿能", "ratio": 8.0, "change": -2.1},
            ],
        },
        "510001": {
            "name": "示例ETF乙",
            "etf_change": -0.8,
            "timestamp": "2026-04-16T15:01:00",
            "holdings": [
                {"name": "东方财富", "ratio": 7.5, "change": 4.5},
                {"name": "迈瑞医疗", "ratio": 6.0, "change": -1.2},
            ],
        },
    }

    updated = module.sync_fund_flow_section_html(html, kline_data, realtime_data, data_cutoff_date="2026-04-16")

    assert 'id="fund-flow-title">💰 市场热度与轮动</h2>' in updated
    assert 'id="fund-flow-source-value">新浪财经实时行情 + 主流程K线快照</span>' in updated
    assert 'id="fund-flow-updated-value">2026-04-16 15:01:00</span>' in updated
    assert 'id="market-rotation-stat-leader-name">示例ETF甲</div>' in updated
    assert 'class="stat-value text-green" id="market-rotation-stat-leader-value">+1.20%</div>' in updated
    assert 'id="market-rotation-stat-laggard-name">示例ETF乙</div>' in updated
    assert 'class="stat-value text-red" id="market-rotation-stat-laggard-value">-0.80%</div>' in updated
    assert 'id="market-rotation-stat-average-name">2支ETF均值</div>' in updated
    assert 'class="stat-value text-green" id="market-rotation-stat-average-value">+0.20%</div>' in updated
    assert 'class="stat-value text-blue" id="market-rotation-stat-breadth-value">1 / 1 / 0</div>' in updated
    assert 'id="leaders-top5-table-name-1">东方财富</td>' in updated

    assert 'id="leaders-top5-table-industry-1">示例ETF乙</td>' in updated
    assert 'id="leaders-top5-table-weight-1">7.50%</td>' in updated
    assert 'class="text-green text-bold" id="leaders-top5-table-change-1">+4.50%</td>' in updated
    assert 'id="laggards-top5-table-name-1">隆基绿能</td>' in updated
    assert 'id="laggards-top5-table-industry-1">示例ETF甲</td>' in updated
    assert 'id="laggards-top5-table-weight-1">8.00%</td>' in updated
    assert 'class="text-red text-bold" id="laggards-top5-table-change-1">-2.10%</td>' in updated



def test_update_html_data_returns_false_when_realtime_const_missing(tmp_path, monkeypatch, load_module):
    """BUG-011 契约翻转：新架构下 update_html_data 允许 HTML 完全不含内联 const（走 runtime_payload.js）。
    这里保留测试函数名以记录历史契约变更，并验证新契约：即使只命中 klineData 不命中 realtimeData，
    也不再 ERROR 退出，而是返回 True（runtime_payload.js 是唯一可靠数据源，HTML 替换变为 best-effort）。
    """
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    html_file = tmp_path / "index.html"

    (data_dir / "etf_full_kline_data.json").write_text(
        json.dumps({"510000": {"name": "示例ETF", "daily": {"dates": ["2026-04-10"]}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "etf_realtime_data.json").write_text(
        json.dumps({"510000": {"etf_change": 1.23}}, ensure_ascii=False),
        encoding="utf-8",
    )
    original_html = '<script>const klineData = {"old": 1};\nconst somethingElse = {};</script>'
    html_file.write_text(original_html, encoding="utf-8")

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    assets_js_dir = tmp_path / "assets" / "js"
    assets_js_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "ASSETS_JS_DIR", str(assets_js_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))
    monkeypatch.setattr(module, "generate_quant_baseline_payload", lambda: None)

    # 新契约：即使 realtimeData 常量缺失，也应返回 True（不阻断主流程）
    assert module.update_html_data() is True
    # runtime_payload.js 必定已写入
    runtime_payload = (assets_js_dir / "runtime_payload.js").read_text(encoding="utf-8")
    assert 'window.__ETF_REPORT_RUNTIME__ =' in runtime_payload



def test_update_html_data_replaces_existing_realtime_const(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    html_file = tmp_path / "index.html"

    daily_dates = [f"2026-02-{i:02d}" for i in range(1, 61)]
    daily_kline = [[float(price), float(price), float(price - 1), float(price + 1)] for price in range(100, 160)]
    weekly_dates = [f"2025-W{i:02d}" for i in range(1, 53)]
    weekly_kline = [[float(price), float(price), float(price - 1), float(price + 1)] for price in range(200, 252)]

    (data_dir / "etf_full_kline_data.json").write_text(
        json.dumps({
            "510000": {
                "name": "示例ETF",
                "daily": {"dates": daily_dates, "kline": daily_kline},
                "weekly": {"dates": weekly_dates, "kline": weekly_kline},
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "etf_realtime_data.json").write_text(
        json.dumps({"510000": {"etf_change": 1.23, "etf_price": 200.0}}, ensure_ascii=False),
        encoding="utf-8",
    )
    html_file.write_text(
        '<div class="info-label" id="latest-nav-label-510000">最新净值</div>'
        '<div class="info-value" id="latest-nav-value-510000">旧价格</div>'
        '<div class="info-value text-red" id="daily-change-value-510000">旧值</div>'
        '<table class="performance-table" id="performance-table-510000"><tr><td>旧业绩</td></tr></table>'
        '<div class="etf-change negative" id="overview-card-510000-change">旧概览涨幅</div>'
        '<script>const klineData = {"old": 1};\nconst realtimeData = {"old": true};</script>',
        encoding="utf-8",
    )


    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    assets_js_dir = tmp_path / "assets" / "js"
    assets_js_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "ASSETS_JS_DIR", str(assets_js_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))
    monkeypatch.setattr(module, "generate_quant_baseline_payload", lambda: None)

    assert module.update_html_data() is True

    updated = html_file.read_text(encoding="utf-8")
    runtime_payload = (assets_js_dir / "runtime_payload.js").read_text(encoding="utf-8")
    assert 'const realtimeData = {' in updated
    assert '"etf_price": 200.0' in updated
    assert '"old": true' not in updated
    assert 'window.__ETF_REPORT_RUNTIME__ =' in runtime_payload
    assert '"etf_price": 200.0' in runtime_payload
    assert 'id="latest-nav-label-510000">最新收盘价</div>' in updated
    assert 'id="latest-nav-value-510000">159.00元</div>' in updated
    assert 'id="daily-change-value-510000">+0.63%</div>' in updated
    assert 'class="info-value text-green" id="daily-change-value-510000"' in updated
    assert '<td id="performance-return-1m-510000" class="positive">+14.39%</td>' in updated
    assert '<td id="performance-return-3m-510000" class="positive">+59.00%</td>' in updated
    assert '<td id="performance-return-6m-510000" class="positive">+11.56%</td>' in updated
    assert '<td id="performance-return-1y-510000" class="positive">+25.50%</td>' in updated
    assert 'class="etf-change positive" id="overview-card-510000-change">+59.00%</div>' in updated


def test_update_html_data_honors_explicit_html_target(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source_html = tmp_path / "source.html"
    target_html = tmp_path / "publish.html"

    daily_dates = [f"2026-02-{i:02d}" for i in range(1, 61)]
    daily_kline = [[float(price), float(price), float(price - 1), float(price + 1)] for price in range(100, 160)]
    weekly_dates = [f"2025-W{i:02d}" for i in range(1, 53)]
    weekly_kline = [[float(price), float(price), float(price - 1), float(price + 1)] for price in range(200, 252)]

    (data_dir / "etf_full_kline_data.json").write_text(
        json.dumps({
            "510000": {
                "name": "示例ETF",
                "daily": {"dates": daily_dates, "kline": daily_kline},
                "weekly": {"dates": weekly_dates, "kline": weekly_kline},
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "etf_realtime_data.json").write_text(
        json.dumps({"510000": {"etf_change": 1.23, "etf_price": 200.0}}, ensure_ascii=False),
        encoding="utf-8",
    )
    source_html.write_text("SOURCE-UNCHANGED", encoding="utf-8")
    target_html.write_text(
        '<div class="info-label" id="latest-nav-label-510000">最新净值</div>'
        '<div class="info-value" id="latest-nav-value-510000">旧价格</div>'
        '<div class="info-value text-red" id="daily-change-value-510000">旧值</div>'
        '<table class="performance-table" id="performance-table-510000"><tr><td>旧业绩</td></tr></table>'
        '<div class="etf-change negative" id="overview-card-510000-change">旧概览涨幅</div>'
        '<script>const klineData = {"old": 1};\nconst realtimeData = {"old": true};</script>',
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    assets_js_dir = tmp_path / "assets" / "js"
    assets_js_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "ASSETS_JS_DIR", str(assets_js_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(source_html))
    monkeypatch.setattr(module, "generate_quant_baseline_payload", lambda: None)

    assert module.update_html_data(html_file=str(target_html)) is True

    assert source_html.read_text(encoding="utf-8") == "SOURCE-UNCHANGED"
    updated = target_html.read_text(encoding="utf-8")
    assert 'const realtimeData = {' in updated
    assert '"etf_price": 200.0' in updated







def test_update_html_data_returns_false_when_kline_file_missing(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    html_file = tmp_path / "index.html"
    html_file.write_text('<script>const klineData = {};</script>', encoding="utf-8")

    (data_dir / "etf_realtime_data.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))

    assert module.update_html_data() is False



def test_update_html_data_succeeds_without_inline_const(tmp_path, monkeypatch, load_module):
    """BUG-011 回归保护：新架构下 index.html 不再内联 const klineData/realtimeData
    （已抽离到 assets/js/runtime_payload.js）。此时 update_html_data 不应 ERROR 退出，
    应该在 runtime_payload.js 成功写入后，对找不到内联 const 的情况降级为 INFO 跳过。
    """
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    html_file = tmp_path / "index.html"

    daily_dates = [f"2026-02-{i:02d}" for i in range(1, 61)]
    daily_kline = [[float(price), float(price), float(price - 1), float(price + 1)] for price in range(100, 160)]
    weekly_dates = [f"2025-W{i:02d}" for i in range(1, 53)]
    weekly_kline = [[float(price), float(price), float(price - 1), float(price + 1)] for price in range(200, 252)]

    (data_dir / "etf_full_kline_data.json").write_text(
        json.dumps({
            "510000": {
                "name": "示例ETF",
                "daily": {"dates": daily_dates, "kline": daily_kline},
                "weekly": {"dates": weekly_dates, "kline": weekly_kline},
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "etf_realtime_data.json").write_text(
        json.dumps({"510000": {"etf_change": 1.23, "etf_price": 200.0}}, ensure_ascii=False),
        encoding="utf-8",
    )
    # 关键：HTML 里完全不含 const klineData / const realtimeData，模拟 REQ-146 新架构
    html_file.write_text(
        '<div class="info-label" id="latest-nav-label-510000">最新净值</div>'
        '<div class="info-value" id="latest-nav-value-510000">旧价格</div>'
        '<div class="info-value text-red" id="daily-change-value-510000">旧值</div>'
        '<table class="performance-table" id="performance-table-510000"><tr><td>旧业绩</td></tr></table>'
        '<div class="etf-change negative" id="overview-card-510000-change">旧概览涨幅</div>'
        '<script src="./assets/js/runtime_payload.js"></script>'
        '<script src="./assets/js/report-main.js"></script>',
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    assets_js_dir = tmp_path / "assets" / "js"
    assets_js_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "ASSETS_JS_DIR", str(assets_js_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))
    monkeypatch.setattr(module, "generate_quant_baseline_payload", lambda: None)

    # 主要断言：即使 HTML 里没有 const，也应该返回 True（不再 ERROR 退出）
    assert module.update_html_data() is True

    # runtime_payload.js 必须已写入，这是新架构的唯一数据源
    runtime_payload = (assets_js_dir / "runtime_payload.js").read_text(encoding="utf-8")
    assert 'window.__ETF_REPORT_RUNTIME__ =' in runtime_payload
    assert '"etf_price": 200.0' in runtime_payload

    # 静态快照同步仍应生效（latest-nav-value / daily-change-value / performance-table / overview-card）
    updated = html_file.read_text(encoding="utf-8")
    assert 'id="latest-nav-value-510000">159.00元</div>' in updated
    assert 'id="daily-change-value-510000">+0.63%</div>' in updated
    assert 'class="etf-change positive" id="overview-card-510000-change">+59.00%</div>' in updated



def test_update_html_data_syncs_editorial_content_blocks(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    html_file = tmp_path / "index.html"

    (data_dir / "etf_full_kline_data.json").write_text(
        json.dumps({
            "510000": {
                "name": "示例ETF",
                "daily": {"dates": ["2026-04-10"], "kline": [[10.0, 10.5, 9.8, 10.6]]},
                "weekly": {"dates": ["2026-W15"], "kline": [[10.0, 10.5, 9.8, 10.6]]},
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "etf_realtime_data.json").write_text(
        json.dumps({"510000": {"etf_change": 1.23, "etf_price": 10.5}}, ensure_ascii=False),
        encoding="utf-8",
    )
    html_file.write_text(
        '<h2 id="research-title-510000">研究标题</h2>'
        '<div class="editorial-meta" id="research-meta-510000">旧研究日期</div>'
        '<div class="report-card"><p id="report-card-content-510000-1">旧研究卡</p></div>'
        '<div class="macro-card" id="domestic-policy-card"><h3>旧标题</h3><div class="editorial-meta" id="editorial-meta-domestic-policy-card">旧宏观日期</div><ul><li>旧内容</li></ul></div>'
        '<script>const klineData = {"old": 1};\nconst realtimeData = {"old": true};</script>',
        encoding="utf-8",
    )


    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    assets_js_dir = tmp_path / "assets" / "js"
    assets_js_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "ASSETS_JS_DIR", str(assets_js_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))
    monkeypatch.setattr(module, "generate_quant_baseline_payload", lambda: None)
    monkeypatch.setattr(
        module,
        "load_editorial_content",
        lambda: {
            "content_date": "2026-04-16",
            "etf_cards": {
                "510000": {
                    "freshness_policy": "manual_daily",
                    "research_cards": [
                        '💡 <span class="highlight-blue">新研究卡</span>'
                    ]
                }
            },

            "macro_cards": {
                "domestic-policy-card": {
                    "title": "🇨🇳 新宏观标题",
                    "items": ["条目一", "条目二"],
                }
            },
        },
    )

    assert module.update_html_data() is True

    updated = html_file.read_text(encoding="utf-8")
    assert 'id="report-card-content-510000-1"><span class="report-card-text" id="report-card-text-510000-1">💡 <span class="highlight-blue">新研究卡</span></span><span class="editorial-date editorial-date--warn" id="research-date-510000-1">2026-04-16</span></p>' in updated
    assert 'id="research-meta-510000"' not in updated
    assert 'id="editorial-meta-domestic-policy-card"' not in updated
    assert 'id="domestic-policy-card"><h3 id="domestic-policy-card-title">🇨🇳 新宏观标题</h3><ul id="domestic-policy-card-list"><li id="domestic-policy-card-item-1"><span class="macro-item-text" id="domestic-policy-card-text-1"><span class="macro-item-content" id="domestic-policy-card-content-1">条目一</span><span class="editorial-date" id="editorial-date-domestic-policy-card-1">2026-04-16</span></span></li><li id="domestic-policy-card-item-2"><span class="macro-item-text" id="domestic-policy-card-text-2"><span class="macro-item-content" id="domestic-policy-card-content-2">条目二</span><span class="editorial-date" id="editorial-date-domestic-policy-card-2">2026-04-16</span></span></li></ul></div>' in updated





def test_run_kline_update_returns_true_when_submodule_succeeds(monkeypatch, load_module):

    module = load_module("update_report")
    monkeypatch.setitem(sys.modules, "fix_ma_and_benchmark", SimpleNamespace(main=lambda: None))

    assert module.run_kline_update() is True



def test_run_realtime_update_returns_false_when_submodule_raises(monkeypatch, load_module):
    module = load_module("update_report")

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "realtime_data_updater", SimpleNamespace(main=_boom))

    assert module.run_realtime_update() is False



def test_verify_output_files_returns_true_when_required_files_exist(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    html_file = tmp_path / "index.html"
    data_dir.mkdir()

    (data_dir / "etf_full_kline_data.json").write_text("{}", encoding="utf-8")
    (data_dir / "etf_realtime_data.json").write_text("{}", encoding="utf-8")
    html_file.write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))

    assert module.verify_output_files() is True



def test_verify_output_files_returns_false_when_html_missing(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    data_dir = tmp_path / "data"
    html_file = tmp_path / "index.html"
    data_dir.mkdir()

    (data_dir / "etf_full_kline_data.json").write_text("{}", encoding="utf-8")
    (data_dir / "etf_realtime_data.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(module, "HTML_FILE", str(html_file))

    assert module.verify_output_files() is False




def test_main_publish_success_runs_cleanup_and_publishers(tmp_path, monkeypatch, load_module):
    module = load_module("update_report")
    tx_instances = []
    publish_calls = []
    html_targets = []
    source_html = tmp_path / "index.html"
    source_html.write_text("SOURCE-HTML", encoding="utf-8")

    class FakeTx:
        def __init__(self, _project_root):
            self.actions = []
            tx_instances.append(self)

        def backup(self):
            self.actions.append("backup")
            return "backup-path"

        def restore(self, backup_path):
            self.actions.append(("restore", backup_path))
            return True

        def cleanup(self):
            self.actions.append("cleanup")
            return 0

    def fake_update_html_data(html_file=None):
        html_targets.append(("data", html_file))
        with open(html_file, "w", encoding="utf-8") as f:
            f.write("PUBLISHED-HTML")
        return True

    def fake_update_html_dates(html_file=None):
        html_targets.append(("dates", html_file))
        return True

    def fake_verify_output_files(html_file=None):
        html_targets.append(("verify", html_file))
        return True

    def fake_print_summary(html_file=None):
        publish_calls.append(("summary", html_file))


    fake_health_check = SimpleNamespace(HTML_FILE=None, run_all_checks=lambda: [SimpleNamespace(status="PASS")])

    monkeypatch.setattr(module, "HTML_FILE", str(source_html))
    monkeypatch.setattr(module, "run_kline_update", lambda: True)
    monkeypatch.setattr(module, "run_realtime_update", lambda: True)
    monkeypatch.setattr(module, "run_editorial_update", lambda: True)
    monkeypatch.setattr(module, "update_html_data", fake_update_html_data)
    monkeypatch.setattr(module, "update_html_dates", fake_update_html_dates)
    monkeypatch.setattr(module, "verify_output_files", fake_verify_output_files)
    monkeypatch.setattr(module, "print_summary", fake_print_summary)
    monkeypatch.setitem(sys.modules, "transaction", SimpleNamespace(TransactionManager=FakeTx))
    monkeypatch.setitem(
        sys.modules,
        "verify_html_integrity",
        SimpleNamespace(
            verify_html_integrity=lambda _path: {"passed": True},
            print_report=lambda _result, _path: None,
        ),
    )
    monkeypatch.setitem(sys.modules, "health_check", fake_health_check)
    monkeypatch.setitem(
        sys.modules,
        "valuation_fetcher",
        SimpleNamespace(run_valuation_update=lambda html_file=None: True),
    )
    monkeypatch.setitem(
        sys.modules,
        "notifier",
        SimpleNamespace(main=lambda data_dir: publish_calls.append(("notifier", data_dir))),
    )
    monkeypatch.setitem(
        sys.modules,
        "deployer",
        SimpleNamespace(main=lambda project_root, html_source_path=None: publish_calls.append(("deployer", project_root, html_source_path))),
    )

    assert module.main(publish=True) is True
    assert tx_instances[0].actions[0] == "backup"
    assert "cleanup" in tx_instances[0].actions
    assert publish_calls[0][0] == "notifier"
    assert publish_calls[1][0] == "deployer"
    published_html = publish_calls[1][2]
    assert published_html == str(source_html)
    assert source_html.read_text(encoding="utf-8") == "PUBLISHED-HTML"
    assert fake_health_check.HTML_FILE is None
    assert any(kind == "data" and target == str(source_html) for kind, target in html_targets)
    assert any(call[0] == "summary" and call[1] == str(source_html) for call in publish_calls)





def test_main_restores_backup_when_html_integrity_fails(monkeypatch, load_module):
    module = load_module("update_report")
    tx_instances = []

    class FakeTx:
        def __init__(self, _project_root):
            self.actions = []
            tx_instances.append(self)

        def backup(self):
            self.actions.append("backup")
            return "backup-path"

        def restore(self, backup_path):
            self.actions.append(("restore", backup_path))
            return True

        def cleanup(self):
            self.actions.append("cleanup")
            return 0

    monkeypatch.setattr(module, "run_kline_update", lambda: True)
    monkeypatch.setattr(module, "run_realtime_update", lambda: True)
    monkeypatch.setattr(module, "run_editorial_update", lambda: True)
    monkeypatch.setattr(module, "update_html_data", lambda html_file=None: True)
    monkeypatch.setattr(module, "update_html_dates", lambda html_file=None: True)
    monkeypatch.setattr(module, "verify_output_files", lambda html_file=None: True)
    monkeypatch.setattr(module, "print_summary", lambda html_file=None: None)

    monkeypatch.setitem(sys.modules, "transaction", SimpleNamespace(TransactionManager=FakeTx))
    monkeypatch.setitem(
        sys.modules,
        "verify_html_integrity",

        SimpleNamespace(
            verify_html_integrity=lambda _path: {"passed": False},
            print_report=lambda _result, _path: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "valuation_fetcher",
        SimpleNamespace(run_valuation_update=lambda html_file=None: True),
    )

    assert module.main(publish=False) is False
    assert ("restore", "backup-path") in tx_instances[0].actions



def test_run_realtime_update_returns_true_when_submodule_succeeds(monkeypatch, load_module):
    module = load_module("update_report")
    monkeypatch.setitem(sys.modules, "realtime_data_updater", SimpleNamespace(main=lambda: None))

    assert module.run_realtime_update() is True



def test_print_summary_logs_summary_block(monkeypatch, load_module):
    module = load_module("update_report")
    captured = []

    monkeypatch.setattr(module.logger, "info", lambda message, context=None: captured.append((message, context)))
    module.print_summary()

    assert any(item[0] == "完成总结" for item in captured)
    assert any("根目录主文件" in (item[1] or {}).get("summary", "") for item in captured)




def test_main_returns_false_when_kline_update_fails(monkeypatch, load_module):
    module = load_module("update_report")
    tx_instances = []

    class FakeTx:
        def __init__(self, _project_root):
            self.actions = []
            tx_instances.append(self)

        def backup(self):
            self.actions.append("backup")
            return "backup-path"

        def restore(self, backup_path):
            self.actions.append(("restore", backup_path))
            return True

        def cleanup(self):
            self.actions.append("cleanup")
            return 0

    monkeypatch.setitem(sys.modules, "transaction", SimpleNamespace(TransactionManager=FakeTx))
    monkeypatch.setattr(module, "run_kline_update", lambda: False)

    assert module.main(publish=False) is False
    assert tx_instances[0].actions == ["backup"]



def test_main_returns_false_when_update_html_data_fails(monkeypatch, load_module):
    module = load_module("update_report")
    tx_instances = []

    class FakeTx:
        def __init__(self, _project_root):
            self.actions = []
            tx_instances.append(self)

        def backup(self):
            self.actions.append("backup")
            return "backup-path"

        def restore(self, backup_path):
            self.actions.append(("restore", backup_path))
            return True

        def cleanup(self):
            self.actions.append("cleanup")
            return 0

    monkeypatch.setattr(module, "run_kline_update", lambda: True)
    monkeypatch.setattr(module, "run_realtime_update", lambda: True)
    monkeypatch.setattr(module, "run_editorial_update", lambda: True)
    monkeypatch.setattr(module, "update_html_data", lambda html_file=None: False)
    monkeypatch.setitem(sys.modules, "transaction", SimpleNamespace(TransactionManager=FakeTx))


    assert module.main(publish=False) is False
    assert tx_instances[0].actions == ["backup"]



def test_main_continues_when_realtime_update_fails(monkeypatch, load_module):
    module = load_module("update_report")
    tx_instances = []

    class FakeTx:
        def __init__(self, _project_root):
            self.actions = []
            tx_instances.append(self)

        def backup(self):
            self.actions.append("backup")
            return "backup-path"

        def restore(self, backup_path):
            self.actions.append(("restore", backup_path))
            return True

        def cleanup(self):
            self.actions.append("cleanup")
            return 0

    monkeypatch.setattr(module, "run_kline_update", lambda: True)
    monkeypatch.setattr(module, "run_realtime_update", lambda: False)
    monkeypatch.setattr(module, "run_editorial_update", lambda: True)
    monkeypatch.setattr(module, "update_html_data", lambda html_file=None: True)
    monkeypatch.setattr(module, "update_html_dates", lambda html_file=None: True)
    monkeypatch.setattr(module, "verify_output_files", lambda html_file=None: True)
    monkeypatch.setattr(module, "print_summary", lambda html_file=None: None)

    monkeypatch.setitem(sys.modules, "transaction", SimpleNamespace(TransactionManager=FakeTx))

    monkeypatch.setitem(
        sys.modules,
        "verify_html_integrity",
        SimpleNamespace(
            verify_html_integrity=lambda _path: {"passed": True},
            print_report=lambda _result, _path: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "health_check",
        SimpleNamespace(run_all_checks=lambda: [SimpleNamespace(status="PASS")]),
    )
    monkeypatch.setitem(
        sys.modules,
        "valuation_fetcher",
        SimpleNamespace(run_valuation_update=lambda html_file=None: True),
    )

    assert module.main(publish=False) is True
    assert "cleanup" in tx_instances[0].actions



# ---------- REQ-158 Step D：editorial 集成 ----------

def test_run_editorial_update_writes_new_yaml(tmp_path, monkeypatch, load_module):
    """editorial_fetcher 抓到新内容时应完整覆盖 editorial_content.yaml。"""
    import yaml as _yaml
    from types import SimpleNamespace

    module = load_module("update_report")

    # 构造临时 PROJECT_ROOT 与 config 目录
    project_root = tmp_path
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    editorial_path = data_dir / "editorial_content.yaml"
    editorial_path.write_text(
        "content_date: '2026-04-16'\netf_cards: {}\nmacro_cards: {}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "PROJECT_ROOT", str(project_root))

    # mock editorial_fetcher 返回一个完整的新结果
    class FakeResult:
        stats = {"etf:159755": {"final": 4}}

        def to_yaml_dict(self):
            return {
                "content_date": "2026-04-21",
                "etf_cards": {
                    "159755": {
                        "freshness_policy": "manual_daily",
                        "research_cards": ["💡 宁德时代一季报靓丽", "💡 阳光电源业绩预增"],
                    }
                },
                "macro_cards": {
                    "global-news-card": {
                        "title": "🌐 国际",
                        "freshness_policy": "manual_daily",
                        "items": ["美联储维持利率"],
                    }
                },
            }

    monkeypatch.setitem(
        sys.modules,
        "editorial_fetcher",
        SimpleNamespace(fetch_all_editorial=lambda: FakeResult()),
    )

    assert module.run_editorial_update() is True

    written = _yaml.safe_load(editorial_path.read_text(encoding="utf-8"))
    assert written["content_date"] == "2026-04-21"
    assert "159755" in written["etf_cards"]
    assert len(written["etf_cards"]["159755"]["research_cards"]) == 2
    assert "global-news-card" in written["macro_cards"]


def test_run_editorial_update_falls_back_on_empty_etf(tmp_path, monkeypatch, load_module):
    """某只 ETF 抓取返回空时，应回退该 ETF 到上一版 yaml，不丢失数据。"""
    import yaml as _yaml
    from types import SimpleNamespace

    module = load_module("update_report")

    project_root = tmp_path
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    editorial_path = data_dir / "editorial_content.yaml"
    # 上一版含 513120 的历史卡片
    editorial_path.write_text(
        _yaml.safe_dump({
            "content_date": "2026-04-16",
            "etf_cards": {
                "513120": {"freshness_policy": "manual_daily",
                           "research_cards": ["💡 旧卡1", "💡 旧卡2"]}
            },
            "macro_cards": {},
        }, allow_unicode=True),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "PROJECT_ROOT", str(project_root))

    # fetcher 返回 513120 的新结果为空（模拟港股成分股今天没新闻）
    class FakeResult:
        stats = {"etf:513120": {"final": 0}}

        def to_yaml_dict(self):
            return {
                "content_date": "2026-04-21",
                "etf_cards": {
                    "513120": {"freshness_policy": "manual_daily", "research_cards": []},
                },
                "macro_cards": {},
            }

    monkeypatch.setitem(
        sys.modules,
        "editorial_fetcher",
        SimpleNamespace(fetch_all_editorial=lambda: FakeResult()),
    )

    assert module.run_editorial_update() is True

    written = _yaml.safe_load(editorial_path.read_text(encoding="utf-8"))
    # 应保留上一版的 513120（旧卡1/旧卡2），而不是写成空
    assert written["etf_cards"]["513120"]["research_cards"] == ["💡 旧卡1", "💡 旧卡2"]


def test_run_editorial_update_survives_fetcher_exception(tmp_path, monkeypatch, load_module):
    """editorial_fetcher 抛异常时不应阻断主流程，yaml 保留不动。"""
    from types import SimpleNamespace

    module = load_module("update_report")

    project_root = tmp_path
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    editorial_path = data_dir / "editorial_content.yaml"
    original = "content_date: '2026-04-16'\netf_cards: {}\nmacro_cards: {}\n"
    editorial_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(module, "PROJECT_ROOT", str(project_root))

    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setitem(
        sys.modules,
        "editorial_fetcher",
        SimpleNamespace(fetch_all_editorial=_boom),
    )

    # 异常不应传播
    assert module.run_editorial_update() is True
    # yaml 保持原样
    assert editorial_path.read_text(encoding="utf-8") == original


def test_run_editorial_update_survives_missing_module(tmp_path, monkeypatch, load_module):
    """editorial_fetcher 模块不存在时跳过但返回 True。"""
    module = load_module("update_report")

    project_root = tmp_path
    config_dir = project_root / "config"
    config_dir.mkdir()

    monkeypatch.setattr(module, "PROJECT_ROOT", str(project_root))

    # 让 import editorial_fetcher 失败
    import builtins
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "editorial_fetcher":
            raise ImportError("simulated missing module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert module.run_editorial_update() is True


