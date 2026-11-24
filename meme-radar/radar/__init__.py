"""meme-radar — Robinhood Chain 早期 MEME / 小市值代币的 AI 辅助研究与验证系统。

分层：
  1. regime      市场环境 → 风险预算
  2. screen      新币初筛（硬过滤 + 软评分）
  3. forensics   钱包关联取证（女巫聚类）+ smartmoney 聪明钱共振
  4. crossval    价格 / 真实资金 / 参与者 交叉验证
  5. ledger      样本记录 + 模拟仓 + 结果回填 + 与随机基线对比

它不自动下单。它的产出是：候选名单、证据、决策记录，以及最终能回答
"这套筛选是否真的优于随机"的统计。
"""
__version__ = "0.1.0"
