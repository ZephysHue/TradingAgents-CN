/**
 * Binance USDT-M Futures 公共行情 API（只读）
 *
 * 数据源：Binance USDⓈ-M 合约公共行情（fapi.binance.com），无账户/下单接口。
 * 仅用于行情展示，禁止接入下单、API Key 管理、自动交易、策略回测等逻辑。
 */
import { ApiClient } from './request'

/** Binance 支持 K 线周期 */
export type CryptoInterval = '1m' | '3m' | '5m' | '15m' | '30m' | '1h' | '2h' | '4h' | '6h' | '8h' | '12h' | '1d'

/** 连接状态 */
export interface BinanceStatus {
  exchange: string
  market: string
  server_time: string | null
}

/** 交易对（USDT-M 合约） */
export interface BinanceSymbolInfo {
  symbol: string
  base_asset: string
  quote_asset: string
  status: string
  contract_type: string
  price_precision: number | null
  quantity_precision: number | null
}

/** 24 小时行情 + 合约快照字段 */
export interface BinanceQuote {
  symbol: string
  last_price: number
  price_change_pct_24h: number
  high_price_24h: number
  low_price_24h: number
  volume_24h: number
  quote_volume_24h: number
  mark_price: number
  index_price: number
  last_funding_rate: number
  next_funding_time: string | null
  open_interest: number
  source: string
  source_updated_at: string | null
}

/** 单根 K 线 */
export interface BinanceKlineBar {
  symbol: string
  interval: string
  open_time: string
  close_time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  quote_volume: number
  trade_count: number
  taker_buy_volume: number
}

export interface BinanceKlinesResponse {
  symbol: string
  interval: string
  count: number
  persisted: boolean
  items: BinanceKlineBar[]
}

/** 波动率指标 */
export interface BinanceVolatility {
  symbol: string
  interval: string
  bars: number
  realized_volatility_annualized: number
  atr_pct: number
  momentum_5_bars_pct: number
  momentum_15_bars_pct: number
  mean_bar_range_pct: number
  volume_ratio: number
  max_drawdown: number
  latest_close: number
  source_updated_at: string
}

/** 合约快照 = 最新报价 + 波动率 */
export interface BinanceSnapshot {
  quote: BinanceQuote
  volatility: BinanceVolatility
}

// 本页所有请求商业错误由页面内状态展示（避免轮询场景 ElMessage 轰炸）；
// 401 认证处理不受 skipErrorHandler 影响，登录过期仍会正常跳转。
const silentError = { skipErrorHandler: true }

export const cryptoApi = {
  /** Binance 连接状态 */
  async getStatus() {
    return ApiClient.get<BinanceStatus>('/api/crypto/binance/status', undefined, silentError)
  },

  /** USDT-M 合约交易对列表 */
  async getSymbols() {
    return ApiClient.get<BinanceSymbolInfo[]>('/api/crypto/binance/symbols', undefined, silentError)
  },

  /** 24 小时行情 + 标记价格/资金费率/持仓量 */
  async getQuote(symbol: string) {
    return ApiClient.get<BinanceQuote>(
      `/api/crypto/binance/${encodeURIComponent(symbol)}/quote`,
      undefined,
      silentError
    )
  },

  /**
   * K 线数据
   * @param interval 周期（1m/15m/1h/4h/1d 等）
   */
  async getKlines(symbol: string, interval: CryptoInterval, limit = 240, persist = false) {
    return ApiClient.get<BinanceKlinesResponse>(
      `/api/crypto/binance/${encodeURIComponent(symbol)}/klines`,
      { interval, limit, persist },
      silentError
    )
  },

  /**
   * 波动率指标。注意：后端年化系数按 1m bar 固定计算，因此波动率固定查 1m 周期。
   */
  async getVolatility(symbol: string, interval: CryptoInterval = '1m', limit = 240) {
    return ApiClient.get<BinanceVolatility>(
      `/api/crypto/binance/${encodeURIComponent(symbol)}/volatility`,
      { interval, limit },
      silentError
    )
  },

  /** 合约快照（报价 + 波动率），适合轮询刷新 */
  async getSnapshot(symbol: string, interval: CryptoInterval = '1m', limit = 240) {
    return ApiClient.get<BinanceSnapshot>(
      `/api/crypto/binance/${encodeURIComponent(symbol)}/snapshot`,
      { interval, limit },
      silentError
    )
  }
}
