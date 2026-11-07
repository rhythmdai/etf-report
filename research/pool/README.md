# pool/ — ETF 池研究证据

> 本目录记录 ETF 池候选、换池实验和历史证据，不维护当前 universe 副本。当前生效池子以 `../../config/quant_universe.yaml` 为准。

## 工作流

| 场景 | Owner 文档 |
|---|---|
| ETF 候选筛选 | `../../docs/runbook/v2-quant/screening.md` |
| ETF 池变更 | `../../docs/runbook/v2-quant/pool-change.md` |
| ETF 剔除备忘 | 用户说「记录剔除 XXX」/「为什么删了 XXX」时生成 → `removals/`（**默认不自动生成**）|
| 研究结论投产 | 更新 `../../research/params/README.md` 时间线 + 对应 REQ 状态 |

## 字段参考

新增 ETF 到 `quant_universe.yaml` 时需填写的字段：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `code` | 是 | string | 6 位交易代码 |
| `name` | 是 | string | 显示短名，去公司后缀，与池内风格一致 |
| `market` | 是 | string | `sh` / `sz` |
| `sector` | 是 | string | 扇区归属，用于展示和归因 |
| `qdii` | 条件必填 | bool | 跨境 QDII 品种必须标 true |
| `marginable` | 条件必填 | bool | 两融/杠杆研究需要显式标注 |
| `bias` | 否 | bool | 扇区内偏好加成 |

## 当前索引

| 记录 | 内容 | 状态 |
|---|---|---|
| `rounds/2026-06-16.md` | R15：+9/-4，油气→石油，池子 49→54 | 历史证据，当前池子仍以 config 为准 |
| `rounds/2026-10-31.md` | R16：-4/+2，替换 1，池子 54→52 | 历史证据，当前池子仍以 config 为准 |
| `rounds/2026-11-05.md` | R17：-2/+1，煤炭+石油→能源，池子 55→54 | 历史证据，当前池子仍以 config 为准 |
| `removals/512400_2026-10-31.md` | 512400 有色金属ETF 剔除备忘（宽基有色被窄基瓜分，用户确认接受）| 2026-10-31 |
| `removals/515220_2026-11-05.md` | 515220 煤炭ETF 剔除备忘（被能源宽基+红利瓜分，周期主力后期被弃）| 2026-11-05 |
| `removals/159697_2026-11-05.md` | 159697 石油ETF 剔除备忘（组内 PK 落败，边缘品种无贡献）| 2026-11-05 |

## Open Questions

| 问题 | 状态 |
|---|---|
| 34→40 的 6 支 ETF 来源 | 待追溯 |
| 巴西 520870 vs 159100 | 待判 |
| 粮食 159698 是否存在更好替代 | 待判 |

## 规则

1. 不在本文件复制完整当前池子表。
2. Applied 日志只写变更摘要和证据链接。
3. 当前 universe 永远以 `../../config/quant_universe.yaml` 为准。
