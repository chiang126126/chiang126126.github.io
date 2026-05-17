# L6 执行层 · 实现规范

> 代码实现位置：私仓 `cresus-bot/sprite/execution/`
> 这一层涉及 Binance API key 和实盘下单，**绝不能进公仓**。

## 核心职责

消费公仓 L5 输出的 `TradeIntent`，转成实际的交易所订单。包含：

1. **预飞检**（5 道，全部通过才下单）
2. **OTOCO 挂单**（一触发即挂止盈+止损）
3. **三级风控看门狗**（单笔 / 账户 / 系统）
4. **平仓监控**（监视 OTOCO 状态 + 持仓时间）

## 预飞检（每次下单前必跑）

```python
def preflight_checks(intent: TradeIntent) -> tuple[bool, str | None]:
    """5 道检查全过才放行。返回 (ok, reason)。"""

    # 1. 时钟同步
    offset_ms = clock_offset_to_binance_ms()
    if offset_ms > 500:
        return False, f"clock_offset_too_high:{offset_ms}ms"

    # 2. API 错误率
    if api_error_rate_last_5min() > 0.05:
        return False, f"api_error_rate_high:{api_error_rate_last_5min():.3f}"

    # 3. 当日回撤
    if daily_drawdown_pct() > MAX_DAILY_DRAWDOWN:
        return False, f"daily_drawdown_exceeded:{daily_drawdown_pct():.3f}"

    # 4. 持仓数
    if count_open_positions() >= MAX_OPEN_POSITIONS:
        return False, f"too_many_open_positions:{count_open_positions()}"

    # 5. 价格异常（vs 多源中位数）
    if price_deviation_from_median(intent.symbol) > 0.02:
        return False, "price_deviation_too_high"

    return True, None
```

## 下单：OTOCO（不是 OCO）

⚠️ **历史 bug**：之前误用 OCO 来"买入 + 挂 TP/SL"，这是错的。OCO 是两个对向卖单二选一。要"主单触发后挂 OCO"必须用 **OTOCO** (One-Triggers-OCO)。

### Binance Spot OTOCO 调用模板

```python
# 多头入场示例
response = binance_client.new_order_otoco(
    symbol="BTCUSDT",
    side="BUY",
    quantity=intent.sizing.qty_quote_usd / intent.entry_price,

    # 主单 (working order): 限价买入
    working_type="LIMIT",
    working_price=intent.entry_price,
    working_timeInForce="GTC",

    # 主单成交后，自动挂 OCO 卖单（TP + SL）
    pending_above_type="LIMIT_MAKER",
    pending_above_price=intent.take_profit_price,  # 多头 TP 在上方

    pending_below_type="STOP_LOSS_LIMIT",
    pending_below_stopPrice=intent.stop_loss_price,  # 多头 SL 在下方
    pending_below_price=intent.stop_loss_price * 0.995,  # 触发后挂的限价
    pending_below_timeInForce="GTC",
)
```

空头方向：把 side 反过来、TP 在下方、SL 在上方。**对 Trailing Stop**：先用固定 SL 挂 OTOCO，后续由 monitor loop 动态上调（见下）。

### Trailing Stop（趋势跟随专用）

`intent.take_profit_price is None` 表示用 trailing：

```python
def update_trailing_stop(position):
    """每分钟跑一次。"""
    current_price = get_current_price(position.symbol)
    new_stop = current_price - 3 * atr_14(position.symbol)

    # 只上调（多头），不下调
    if position.side == Side.BUY:
        if new_stop > position.stop_loss_price:
            cancel_and_replace_stop(position, new_stop)
    else:
        if new_stop < position.stop_loss_price:
            cancel_and_replace_stop(position, new_stop)
```

## 三级风控看门狗

### 单笔级

通过 OTOCO 实现：SL 在挂单时已经锁死，不靠程序"到时候去挂"。
**永远不要**用 "市价单买入 → 等几秒 → 挂 SL" 的模式。

### 账户级

每分钟跑：

```python
def account_level_check():
    if daily_drawdown_pct() > 0.05:
        switch_to_only_close_mode("daily_drawdown_5pct")

    if consecutive_losses() >= 5:
        pause_new_orders_for_hours(24)

    if module_b_daily_drawdown_pct() > 0.05:
        pause_module_b_for_hours(24)

    if module_b_monthly_drawdown_pct() > 0.15:
        pause_module_b_until_manual_review()
```

### 系统级

每 30 秒跑：

```python
def system_level_check():
    if api_error_rate_last_5min() > 0.10:
        switch_to_only_close_mode("api_unstable")

    if clock_offset_ms() > 1000:
        switch_to_only_close_mode("clock_drift")

    if telegram_halt_signal_received():
        emergency_close_all()
        sys.exit("halt")
```

## Telegram /halt 一键熔断

```
/halt        立即平仓所有 + 杀进程
/pause       暂停开新仓（已有持仓继续监控）
/resume      恢复开新仓
/status      返回当前持仓 + 当日 PnL + 风控状态
/positions   列出所有持仓
```

实现时**第一周就要做**，不要等到出问题才补。

## API key 配置（私仓 .env）

```
BINANCE_API_KEY=...
BINANCE_API_SECRET=...

# 权限设置（在 Binance 后台配置，代码无法控制）
# ✅ Enable Spot Trading
# ✅ Enable Futures (仅 Module B)
# ❌ Enable Withdrawals  ← 永远关闭
# ❌ Enable Internal Transfer
# ❌ Enable Margin (除非明确需要)
# IP 白名单：只列 VPS 出口 IP
```

## 冷库分层

- **硬件钱包**（Ledger/Trezor）：主仓资金，永远离线
- **Binance 账户**：只放当周交易额度
- **Telegram bot**：每周自动提醒"利润超出额度部分应手动提到硬件钱包"

⚠️ **Simple Earn 不是冷库**——它仍然托管在你的 Binance 账户名下，账号被攻破时同时丢失。

## 失败模式 + 应对

| 失败 | 应对 |
|---|---|
| OTOCO 部分成交（仅主单成交，TP/SL 挂单失败）| 立即用独立 stop-loss 订单兜底 |
| 网络抖动导致下单超时 | 不重试，先查实际订单状态再决定 |
| 网络断 | switch_to_only_close_mode，持仓由 OTOCO 自管 |
| Binance 限频 | 退避到最低频率 + Telegram 告警 |
| 强平价格逼近 (Module B) | 主动减仓 50%，把杠杆降下来 |
| 价格喂送数据偏离 | 拒绝下单（preflight 第 5 项已覆盖） |

## 测试要求

私仓实现时必须有：
- mock Binance API 的单元测试（验证 OTOCO 参数、错误处理）
- "破坏性"集成测试：模拟网络抖动 / 部分成交 / 限频，验证看门狗反应
- Paper Broker 走完整流程（包括 OTOCO 模拟）
- Telegram /halt 手动测试 → 验证 < 5s 内全平
