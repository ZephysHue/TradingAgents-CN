# Binance 超短线波动数据源 v1

## 定位

第一版接入 Binance USDⓈ-M 合约公开行情，服务于超短线波动研究、回测输入和后续模拟盘。

本版本只读，不包含真实下单、账户余额、仓位管理或杠杆倍数控制。

## 数据范围

- 交易对：USDT 永续/交割合约中状态为 `TRADING` 的交易对
- K 线：`1m`、`3m`、`5m`、`15m`、`30m`、`1h`、`2h`、`4h`、`6h`、`8h`、`12h`、`1d`
- 合约快照：最新价、24 小时涨跌幅、高低价、成交量、标记价格、指数价格、资金费率、持仓量
- 波动指标：年化实现波动率、ATR 百分比、5/15 根动量、平均振幅、成交量放大倍数、最大回撤
- 落库：请求 K 线时传 `persist=true`，写入 MongoDB 的 `crypto_klines` 集合并按交易对/周期/开盘时间去重

## API

所有接口都需要现有 Bearer 登录令牌：

```text
GET /api/crypto/binance/status
GET /api/crypto/binance/symbols
GET /api/crypto/binance/{symbol}/quote
GET /api/crypto/binance/{symbol}/klines?interval=1m&limit=240&persist=true
GET /api/crypto/binance/{symbol}/volatility?interval=1m&limit=240
GET /api/crypto/binance/{symbol}/snapshot?interval=1m&limit=240
```

推荐第一版先用 `snapshot` 做轮询，随后再增加 WebSocket 增量订阅和模拟撮合；不要直接把行情指标连接到真实订单执行。

## 模拟杠杆 API

模拟账户初始余额为 `10,000 USDT`，只支持逐仓、单交易对单仓位，最大杠杆为 `50x`。价格不传时读取 Binance 标记价格，传入价格则适合回测和确定性验收。

```text
GET  /api/crypto/paper/account
POST /api/crypto/paper/reset
POST /api/crypto/paper/open
POST /api/crypto/paper/mark
POST /api/crypto/paper/close
```

开仓示例：

```json
{
  "symbol": "BTCUSDT",
  "side": "long",
  "quantity": 0.01,
  "leverage": 10,
  "price": 100000
}
```

模拟规则：开仓扣除保证金和开仓手续费；标记价格更新未实现盈亏；触及估算强平价自动平仓；平仓结算盈亏、手续费和释放保证金。当前手续费率为双边 `0.04%`，维持保证金率为 `0.5%`。

## 当前验证边界

- 本地客户端归一化、K 线映射、指标计算：已验证通过。
- Docker 后端构建、启动、健康检查、路由认证和参数校验：已验证通过。
- 当前 Docker 网络访问 `https://fapi.binance.com` 超时，因而 live Binance 数据请求暂未在本机拿到成功响应；接口会返回 `502`，而不是伪造行情。
