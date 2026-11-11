# -*- coding: utf-8 -*-
"""修复 CSV 里 volume 单位写错的行（把「股」写进了契约是「手」的列，数值大 100 倍）。

背景
----
CSV 的 volume 列契约是「手」（1 手 = 100 股），见 quant_data_fetcher._parse_tx_rows：

    "amount": round(close * vol * 100, 2)

但历史上两条新浪路径直接把「股」写进了这一列：
  - scripts/quant_tuner.py::_sina_batch_append._append_one   （实时行情落盘）
  - scripts/quant_data_fetcher.py::_fetch_sina_batch         （批量增量落盘）

两处已在 2026-11-11 修正，并在写入前加了单位校验。本脚本只负责清理
**已经写脏的历史行**——代码修好了，旧数据不会自己变对。

判定
----
健康行： amount / (volume * close) ≈ 100
脏  行： amount / (volume * close) ≈ 1   → volume 除以 100

用法
----
    python scripts/repair_volume_units.py                 # 只体检，不动文件
    python scripts/repair_volume_units.py --apply         # 修复并重建周线
    python scripts/repair_volume_units.py --data-dir DIR  # 指定数据目录（测试用）

退出码：0 = 正常结束（无论是否发现脏行）；1 = 执行出错。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quant_data_fetcher import DATA_DIR, rebuild_weekly_from_daily, save_csv  # noqa: E402

RATIO_MIN = 20.0  # 健康行 ≈100；脏行 ≈1；取 20 作分界，远离两端


def scan(path: Path) -> pd.DataFrame:
    """返回该 CSV 里单位可疑的行（保留原索引）。"""
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        print(f"  [skip] {path.name}: {exc}")
        return pd.DataFrame()
    need = {"date", "close", "volume", "amount"}
    if not need.issubset(df.columns):
        return pd.DataFrame()
    df = df.copy()
    for col in ("close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    mask = (df["volume"] > 0) & (df["amount"] > 0) & (df["close"] > 0)
    ratio = pd.Series(float("nan"), index=df.index)
    ratio[mask] = df.loc[mask, "amount"] / (df.loc[mask, "volume"] * df.loc[mask, "close"])
    return df.assign(_ratio=ratio)[mask & (ratio < RATIO_MIN)]


def main() -> int:
    ap = argparse.ArgumentParser(description="修复 volume 单位写错的 CSV 行")
    ap.add_argument("--apply", action="store_true", help="真的写回文件（默认只体检）")
    ap.add_argument("--data-dir", default=None, help="数据目录，默认 data/quant")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(DATA_DIR)
    if not data_dir.is_dir():
        print(f"[ERROR] 数据目录不存在: {data_dir}")
        return 1

    files = sorted(data_dir.glob("*_daily.csv"))
    print(f"扫描 {len(files)} 个日线文件: {data_dir}")

    total_bad_rows = 0
    bad_files = 0
    for path in files:
        bad = scan(path)
        if bad.empty:
            continue
        bad_files += 1
        total_bad_rows += len(bad)
        code = path.stem.replace("_daily", "")
        days = bad["date"].astype(str).tolist()
        print(f"\n{code}: {len(bad)} 行单位可疑（ratio≈1 应为 100）")
        for _, row in bad.iterrows():
            print(f"   {row['date']}  volume={int(row['volume']):,}  "
                  f"ratio={row['_ratio']:.2f}  → 修正为 {int(round(row['volume'] / 100.0)):,}")
        if args.apply:
            df = pd.read_csv(path)
            df.loc[bad.index, "volume"] = (
                pd.to_numeric(df.loc[bad.index, "volume"], errors="coerce") / 100.0
            ).round().astype("int64")
            save_csv(df, path)
            weekly_path = path.with_name(path.name.replace("_daily.csv", "_weekly.csv"))
            save_csv(rebuild_weekly_from_daily(df), weekly_path)
            print(f"   [已修复] 日线 + 周线已重写（{len(days)} 行）")

    print("\n" + "=" * 60)
    if total_bad_rows == 0:
        print("体检通过：没有发现单位错误的行。")
    elif args.apply:
        print(f"修复完成：{bad_files} 个文件、{total_bad_rows} 行，周线已重建。")
    else:
        print(f"发现 {bad_files} 个文件、{total_bad_rows} 行单位错误。"
              f"加 --apply 才会写回。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
