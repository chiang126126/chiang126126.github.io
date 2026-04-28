# P13：信号结构分布看板（Structure Breakdown Panel）

> 回填文档（retroactive）。原 commit：[`c6ff925`](https://github.com/chiang126126/chiang126126.github.io/commit/c6ff925) · 2026-04-27

把所有信号按 Master Framework v0.7 的 8 种结构类型（A–H）+ "无结构" 分桶聚合，看板上一眼看出**哪种结构最常触发、哪种偏多 vs 偏空、哪种平均信心度更高**。是 P18 "按结构反哺框架" 的数据基础。

---

## 数据流

```
signals.jsonl（每条带 structure 字段，A-H 或 null）
    ↓
dashboard/index.html · renderStructures()
    ├─ 按 (structure || "none") 分桶
    ├─ 每桶统计：count / 平均 confidence / 方向分布 (LONG/SHORT/WATCH/SKIP)
    └─ 渲染：色字母徽章 + 中文名 + 横向 bar + 计数 + 百分比 + 方向分解 + 平均信心
```

---

## 看板视觉

```
A  OI 积累多        ████████████░░░░░  342  32.0%   多 311 · 观 31     avg 73
B  资金费反转空     ████████░░░░░░░░░  198  18.5%   空 185 · 观 13     avg 71
C  V4A-Flash 空     ███░░░░░░░░░░░░░░   78   7.3%   空 78              avg 76
D  分布出货空       █░░░░░░░░░░░░░░░░   24   2.2%   空 24              avg 68
E  强控盘回避       ░░░░░░░░░░░░░░░░░    8   0.7%   跳 8               avg 0
F  财库 BTC 多      █░░░░░░░░░░░░░░░░   15   1.4%   多 15              avg 80
G  盘前套利         ░░░░░░░░░░░░░░░░░    3   0.3%   多 2 · 空 1        avg 65
H  新闻事件         ██░░░░░░░░░░░░░░░░  41   3.8%   多 22 · 空 19      avg 70
?  无结构           ████████████████░  362  33.8%   多 124 · 空 142 · 观 96  avg 65
```

每行的字段：

| 列 | 内容 |
|---|---|
| 字母徽章 | 圆形色块 + 字母（A 绿 / B 红 / C 橙 / D 黄 / E 灰 / F 金 / G 灰 / H 蓝） |
| 中文名 | OI 积累多 / 资金费反转空 / V4A-Flash 空 / 分布出货空 / 强控盘回避 / 财库 BTC 多 / 盘前套利 / 新闻事件 / 无结构 |
| 横条 | 长度 = `count / max(count) × 100%`，颜色随结构 |
| count | 该结构下信号数 |
| pct | 占总信号数的百分比 |
| 方向分解 | `多 N · 空 M · 观 K · 跳 J`（颜色对应方向）|
| avg conf | 该结构下平均信心度 |

---

## 实现

### 结构 → 色 + 名映射（`dashboard/index.html`）

```javascript
const STRUCT_META = {
  A: { name: "OI 积累多",     color: "#00c853" },
  B: { name: "资金费反转空",  color: "#ff1744" },
  C: { name: "V4A-Flash 空",  color: "#ff5722" },
  D: { name: "分布出货空",    color: "#ff9800" },
  E: { name: "强控盘回避",    color: "#9e9e9e" },
  F: { name: "财库 BTC 多",   color: "#ffd600" },
  G: { name: "盘前套利",      color: "#9e9e9e" },
  H: { name: "新闻事件",      color: "#2196f3" },
  none: { name: "无结构",     color: "#5a6478" },
};
```

### 聚合 + 渲染逻辑

```javascript
function renderStructures() {
  const buckets = {};
  for (const s of allSignals) {
    const k = (s.structure || "none").toUpperCase();
    const key = STRUCT_META[k] ? k : "none";
    if (!buckets[key]) buckets[key] = {
      count: 0, conf_sum: 0, LONG: 0, SHORT: 0, WATCH: 0, SKIP: 0
    };
    const b = buckets[key];
    b.count++;
    b.conf_sum += (s.confidence || 0);
    if (s.direction in b) b[s.direction]++;
  }

  const total = allSignals.length || 1;
  const order = ["A", "B", "C", "D", "E", "F", "G", "H", "none"];
  const max = Math.max(...order.map(k => buckets[k]?.count || 0), 1);
  // ...渲染每行（横条宽 = count/max × 100%）
}
```

### 网格布局

```css
.struct-row {
  display: grid;
  grid-template-columns: 28px 110px 1fr 60px 50px 140px 90px;
  align-items: center;
  gap: 10px;
}
```

7 列：徽章 / 中文名 / 横条 / count / pct / 方向分解 / avg conf。

---

## 为什么有用（业务视角）

1. **快速洞察结构分布**：一眼看出当前市场环境下哪种结构主导。极端单边市场 A/F 多，震荡市 B/D 多，黑天鹅事件 H 多。
2. **方向分解暴露异常**：理论上 A 必须全多、B 必须全空。如果出现 `A: 多 311 · 空 5`，那 5 条空是 DeepSeek 失误，可以直接 grep 出来当反例改 prompt。
3. **平均信心度做诚实校验**：如果某结构 avg conf 高但实际胜率低（P18 加进去后），就是 prompt 给该结构加权过头。
4. **"无结构" 桶是诊断指标**："无结构" 占比 30%+ 说明 DeepSeek prompt 里的结构判断没收敛——P12 已修了一次（structure 字段 not null），但仍有 33.8% null，说明还有提升空间。

---

## ✅ P13 完成检查清单

- [x] `dashboard/index.html` 加 STRUCT_META 映射 + renderStructures() + .struct-row CSS
- [x] 看板"PnL 追踪"和"每日信号数量"之间出现新 panel
- [x] 9 种结构（含 none）色 + 中文名渲染正确
- [x] 方向分解（多/空/观/跳）颜色与全局一致
- [x] 横条宽度 = `count / max_count × 100%`
- [x] 文档（本文）回填

---

## 下一步

| 阶段 | 内容 |
|------|------|
| **P13b**（可选） | 点击某行展开该结构下的所有信号 |
| **P18**（关键） | 按结构 cohort 跑实际胜率（需 P&L 闭环），对比 avg conf，反哺 v0.7 → v0.8 框架权重 |
| **P13c**（可选） | "无结构" 占比超 25% 时看板顶部出黄色警示，提示 DeepSeek prompt 该调了 |
