<template>
  <div class="crypto-page">
    <!-- 数据源与连接状态 -->
    <el-card shadow="never" class="source-card">
      <div class="source-bar">
        <div class="source-left">
          <span class="source-badge">数据源：Bybit USDT 线性永续公共行情</span>
          <span class="source-note">只读行情展示，不含下单 / API Key / 自动交易 / 回测功能</span>
        </div>
        <div class="source-right">
          <template v-if="statusLoading">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>检测连接…</span>
          </template>
          <template v-else-if="statusError">
            <el-tag type="danger" effect="plain">Bybit 连接不可用</el-tag>
            <el-button size="small" text type="primary" @click="loadStatus">重试</el-button>
          </template>
          <template v-else-if="status">
            <span class="conn-dot" :class="{ ok: true }"></span>
            <span class="server-time">Bybit 服务器时间：{{ formatFullTime(status.server_time) }}</span>
          </template>
        </div>
      </div>
    </el-card>

    <!-- 合约选择 + 手动刷新 -->
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <div class="toolbar-left">
          <span class="field-label">合约</span>
          <el-select
            v-model="selectedSymbol"
            filterable
            :loading="symbolsLoading"
            placeholder="选择 USDT 线性永续合约"
            class="symbol-select"
            :disabled="symbolsError"
          >
            <el-option
              v-for="s in tradableSymbols"
              :key="s.symbol"
              :label="s.symbol"
              :value="s.symbol"
            >
              <span>{{ s.symbol }}</span>
              <span class="option-meta">{{ s.base_asset }} / {{ s.quote_asset }} · {{ s.contract_type }}</span>
            </el-option>
          </el-select>
          <span v-if="symbolsError" class="symbols-error">
            合约列表加载失败
            <el-button size="small" text type="primary" @click="loadSymbols">重试</el-button>
          </span>
          <span v-else-if="symbolsLoading" class="symbols-loading">合约列表加载中…</span>
          <span v-else-if="tradableSymbols.length" class="symbols-count">
            共 {{ tradableSymbols.length }} 个 TRADING 状态合约
          </span>
        </div>
        <div class="toolbar-right">
          <el-switch
            v-model="autoRefresh"
            active-text="自动刷新"
            inline-prompt
            style="margin-right: 12px"
          />
          <el-button type="primary" :icon="Refresh" :loading="quoteLoading && firstQuoteLoaded" @click="refreshAll">
            手动刷新
          </el-button>
        </div>
      </div>
    </el-card>

    <!-- 24h 行情概览 -->
    <el-card shadow="never" class="quote-card" v-loading="quoteLoading && !firstQuoteLoaded">
      <template #header>
        <div class="card-header">
          <span>24h 行情概览</span>
          <span v-if="quote?.source_updated_at" class="updated-at">
            更新于 {{ formatFullTime(quote.source_updated_at) }}
          </span>
        </div>
      </template>

      <template v-if="quote">
        <div class="quote-main">
          <div class="price-block">
            <span class="price" :class="changeClass">{{ formatPrice(quote.last_price) }}</span>
            <span class="change" :class="changeClass">
              {{ formatSigned(quote.price_change_pct_24h) }}%
            </span>
            <span class="unit">USDT · {{ selectedSymbol }}</span>
          </div>
          <div class="quote-grid">
            <div class="grid-item">
              <span class="grid-label">24h 最高</span>
              <span class="grid-value">{{ formatPrice(quote.high_price_24h) }}</span>
            </div>
            <div class="grid-item">
              <span class="grid-label">24h 最低</span>
              <span class="grid-value">{{ formatPrice(quote.low_price_24h) }}</span>
            </div>
            <div class="grid-item">
              <span class="grid-label">24h 成交量</span>
              <span class="grid-value">{{ formatCompact(quote.volume_24h) }}</span>
            </div>
            <div class="grid-item">
              <span class="grid-label">24h 成交额 (USDT)</span>
              <span class="grid-value">{{ formatCompact(quote.quote_volume_24h) }}</span>
            </div>
            <div class="grid-item">
              <span class="grid-label">标记价格</span>
              <span class="grid-value">{{ formatPrice(quote.mark_price) }}</span>
            </div>
            <div class="grid-item">
              <span class="grid-label">指数价格</span>
              <span class="grid-value">{{ formatPrice(quote.index_price) }}</span>
            </div>
            <div class="grid-item">
              <span class="grid-label">当前资金费率</span>
              <span class="grid-value">{{ formatRate(quote.last_funding_rate) }}</span>
              <span class="grid-sub">下期 {{ formatTime(quote.next_funding_time) }}</span>
            </div>
            <div class="grid-item">
              <span class="grid-label">持仓量</span>
              <span class="grid-value">{{ formatCompact(quote.open_interest) }}</span>
            </div>
          </div>
        </div>
      </template>
      <el-empty v-else-if="!quoteLoading && quoteError" description="行情获取失败">
        <span class="empty-hint">{{ quoteError }}</span>
        <el-button type="primary" size="small" @click="refreshSnapshot">重试</el-button>
      </el-empty>
      <el-empty v-else-if="!quoteLoading" description="暂无行情数据" />
    </el-card>

    <!-- 波动率 -->
    <el-card shadow="never" class="vol-card">
      <template #header>
        <div class="card-header">
          <div class="header-title">
            <span>波动率指标</span>
            <el-tag size="small" type="info" effect="plain">基于 1m K 线最近 20 根收益计算</el-tag>
          </div>
          <span class="updated-at">
            <el-icon v-if="volLoading" class="is-loading"><Loading /></el-icon>
            <template v-else-if="volatility?.source_updated_at">
              更新于 {{ formatFullTime(volatility.source_updated_at) }}
            </template>
          </span>
        </div>
      </template>
      <template v-if="volatility">
        <div class="vol-grid">
          <div class="vol-item">
            <span class="vol-label">年化实现波动率</span>
            <span class="vol-value highlight">{{ formatPct(volatility.realized_volatility_annualized * 100) }}</span>
          </div>
          <div class="vol-item">
            <span class="vol-label">ATR%（14 根均值）</span>
            <span class="vol-value">{{ formatPct(volatility.atr_pct * 100) }}</span>
          </div>
          <div class="vol-item">
            <span class="vol-label">平均单根振幅</span>
            <span class="vol-value">{{ formatPct(volatility.mean_bar_range_pct * 100) }}</span>
          </div>
          <div class="vol-item">
            <span class="vol-label">5 根动量</span>
            <span class="vol-value" :class="volatility.momentum_5_bars_pct >= 0 ? 'up' : 'down'">
              {{ formatSigned(volatility.momentum_5_bars_pct * 100) }}%
            </span>
          </div>
          <div class="vol-item">
            <span class="vol-label">15 根动量</span>
            <span class="vol-value" :class="volatility.momentum_15_bars_pct >= 0 ? 'up' : 'down'">
              {{ formatSigned(volatility.momentum_15_bars_pct * 100) }}%
            </span>
          </div>
          <div class="vol-item">
            <span class="vol-label">最新量能与前期均值比</span>
            <span class="vol-value">{{ formatTimes(volatility.volume_ratio) }}</span>
          </div>
          <div class="vol-item">
            <span class="vol-label">区间最大回撤</span>
            <span class="vol-value down">{{ formatSigned(volatility.max_drawdown * 100) }}%</span>
          </div>
          <div class="vol-item">
            <span class="vol-label">样本 bar 数</span>
            <span class="vol-value">{{ volatility.bars }}</span>
          </div>
        </div>
      </template>
      <el-empty v-else-if="!volLoading && volError" description="波动率获取失败" :image-size="60">
        <span class="empty-hint">{{ volError }}</span>
      </el-empty>
      <el-empty v-else-if="!volLoading" description="暂无波动率数据" :image-size="60" />
    </el-card>

    <!-- K 线 -->
    <el-card shadow="never" class="kline-card">
      <template #header>
        <div class="card-header">
          <span>K 线（{{ selectedSymbol }}）</span>
          <div class="kline-actions">
            <el-radio-group v-model="klineInterval" size="small">
              <el-radio-button
                v-for="p in displayIntervals"
                :key="p.value"
                :value="p.value"
              >
                {{ p.label }}
              </el-radio-button>
            </el-radio-group>
            <el-button size="small" :icon="Refresh" :loading="klineLoading" @click="fetchKlines(true)">
              刷新K线
            </el-button>
          </div>
        </div>
      </template>

      <template v-if="klineBars.length">
        <v-chart :option="klineOption" autoresize class="kline-chart" />
        <div class="ohlc-section">
          <div class="ohlc-title">最近行情（最新在前，最多 30 根）</div>
          <el-table :data="recentBars" size="small" stripe max-height="360">
            <el-table-column label="时间" width="150">
              <template #default="{ row }">{{ formatBarTime(row.open_time) }}</template>
            </el-table-column>
            <el-table-column label="开盘" align="right" width="110">
              <template #default="{ row }">{{ formatPrice(row.open) }}</template>
            </el-table-column>
            <el-table-column label="最高" align="right" width="110">
              <template #default="{ row }">{{ formatPrice(row.high) }}</template>
            </el-table-column>
            <el-table-column label="最低" align="right" width="110">
              <template #default="{ row }">{{ formatPrice(row.low) }}</template>
            </el-table-column>
            <el-table-column label="收盘" align="right" width="110">
              <template #default="{ row }">
                <span :class="row.close >= row.open ? 'up' : 'down'">{{ formatPrice(row.close) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="涨跌" align="right" width="90">
              <template #default="{ row }">
                <span :class="row.close >= row.open ? 'up' : 'down'">
                  {{ formatSigned((row.close / row.open - 1) * 100) }}%
                </span>
              </template>
            </el-table-column>
            <el-table-column label="成交量" align="right" width="110">
              <template #default="{ row }">{{ formatCompact(row.volume) }}</template>
            </el-table-column>
            <el-table-column label="成交额 (USDT)" align="right" min-width="120">
              <template #default="{ row }">{{ formatCompact(row.quote_volume) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </template>
      <el-empty v-else-if="klineLoading" description="K 线加载中…" :image-size="60" />
      <el-empty v-else-if="klineError" :description="klineError" :image-size="60">
        <el-button type="primary" size="small" @click="fetchKlines(true)">重试</el-button>
      </el-empty>
      <el-empty v-else description="暂无 K 线数据" :image-size="60" />
    </el-card>

    <!-- 页脚声明 -->
    <div class="page-footer">
      以上数据均来自 Bybit V5 USDT 线性永续公共行情接口（api.bybit.com），仅用于行情查看与波动观察，
      与既有 Binance COIN-M 回测数据不能直接混用，不构成任何交易建议。
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { Refresh, Loading } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { use as echartsUse } from 'echarts/core'
import { CandlestickChart, BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
  TitleComponent
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { EChartsOption } from 'echarts'
import {
  cryptoApi,
  type BinanceStatus,
  type BinanceSymbolInfo,
  type BinanceQuote,
  type BinanceVolatility,
  type BinanceKlineBar,
  type CryptoInterval
} from '@/api/crypto'

echartsUse([
  CandlestickChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  LegendComponent,
  TitleComponent,
  CanvasRenderer
])

const REFRESH_INTERVAL_MS = 15000
const KLINE_LIMIT = 240

const displayIntervals: { label: string; value: CryptoInterval }[] = [
  { label: '15m', value: '15m' },
  { label: '1h', value: '1h' },
  { label: '4h', value: '4h' },
  { label: '1d', value: '1d' }
]

// ---------- 连接状态 ----------
const status = ref<BinanceStatus | null>(null)
const statusLoading = ref(false)
const statusError = ref(false)

async function loadStatus() {
  statusLoading.value = true
  statusError.value = false
  try {
    const res: any = await cryptoApi.getStatus()
    status.value = (res?.data as BinanceStatus) ?? null
    if (!status.value) throw new Error('status 响应为空')
  } catch (e: any) {
    statusError.value = true
    console.error('[Crypto] 获取 Bybit 状态失败', e)
  } finally {
    statusLoading.value = false
  }
}

// ---------- 合约列表 ----------
const symbols = ref<BinanceSymbolInfo[]>([])
const symbolsLoading = ref(false)
const symbolsError = ref(false)
const selectedSymbol = ref('BTCUSDT')

const tradableSymbols = computed(() => symbols.value.filter((s) => s.status.toUpperCase() === 'TRADING'))

async function loadSymbols() {
  symbolsLoading.value = true
  symbolsError.value = false
  try {
    const res: any = await cryptoApi.getSymbols()
    const list = Array.isArray(res?.data) ? (res.data as BinanceSymbolInfo[]) : []
    if (!list.length) throw new Error('symbols 响应为空')
    symbols.value = list
    // 若当前所选合约不在 TRADING 列表中，回退到 BTCUSDT 或首个可用合约
    if (!list.some((s) => s.symbol === selectedSymbol.value && s.status.toUpperCase() === 'TRADING')) {
      selectedSymbol.value = list.some((s) => s.symbol === 'BTCUSDT' && s.status.toUpperCase() === 'TRADING')
        ? 'BTCUSDT'
        : (list.find((s) => s.status.toUpperCase() === 'TRADING')?.symbol ?? 'BTCUSDT')
    }
  } catch (e: any) {
    symbolsError.value = true
    console.error('[Crypto] 获取合约列表失败', e)
  } finally {
    symbolsLoading.value = false
  }
}

// ---------- 快照（quote + volatility） ----------
const quote = ref<BinanceQuote | null>(null)
const volatility = ref<BinanceVolatility | null>(null)
const quoteLoading = ref(false)
const volLoading = ref(false)
const quoteError = ref('')
const volError = ref('')
const firstQuoteLoaded = ref(false)

const changeClass = computed(() => {
  if (!quote.value) return ''
  const v = quote.value.price_change_pct_24h
  return v > 0 ? 'up' : v < 0 ? 'down' : ''
})

async function refreshSnapshot() {
  if (!selectedSymbol.value) return
  // quote：允许失败静默重试（轮询场景由页内状态提示，避免 ElMessage 轰炸）
  if (!firstQuoteLoaded.value) quoteLoading.value = true
  try {
    const res: any = await cryptoApi.getQuote(selectedSymbol.value)
    quote.value = (res?.data as BinanceQuote) ?? null
    quoteError.value = quote.value ? '' : '接口返回为空'
    firstQuoteLoaded.value = true
  } catch (e: any) {
    quoteError.value = e?.message || '行情获取失败'
    console.error('[Crypto] 获取行情失败', e)
  } finally {
    quoteLoading.value = false
  }

  // volatility：固定 1m（后端年化系数仅按 1m 正确计算）
  volLoading.value = true
  try {
    const res: any = await cryptoApi.getVolatility(selectedSymbol.value, '1m', 240)
    volatility.value = (res?.data as BinanceVolatility) ?? null
    volError.value = volatility.value ? '' : '接口返回为空'
  } catch (e: any) {
    volError.value = e?.message || '波动率获取失败'
    console.error('[Crypto] 获取波动率失败', e)
  } finally {
    volLoading.value = false
  }
}

// ---------- K 线 ----------
const klineInterval = ref<CryptoInterval>('1h')
const klineBars = ref<BinanceKlineBar[]>([])
const klineLoading = ref(false)
const klineError = ref('')

const recentBars = computed(() => klineBars.value.slice(-30).reverse())

const klineOption = computed<EChartsOption>(() => {
  const category = klineBars.value.map((b) => dayjs(b.open_time).format('MM-DD HH:mm'))
  const candles = klineBars.value.map((b) => [b.open, b.close, b.low, b.high])
  const volumes = klineBars.value.map((b) => ({
    value: b.volume,
    itemStyle: { color: b.close >= b.open ? '#ef4444' : '#16a34a', opacity: 0.6 }
  }))

  return {
    title: {
      text: `${selectedSymbol.value} · ${klineInterval.value} · Bybit USDT Linear Perpetual`,
      left: 8,
      top: 4,
      textStyle: { fontSize: 12, fontWeight: 'normal', color: '#909399' }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(255,255,255,0.96)',
      borderColor: '#dcdfe6'
    },
    legend: { data: ['K线', '成交量'], top: 24, textStyle: { fontSize: 12 } },
    grid: [
      { left: 64, right: 24, top: 56, height: '52%' },
      { left: 64, right: 24, top: '76%', height: '14%' }
    ],
    xAxis: [
      {
        type: 'category',
        data: category,
        boundaryGap: true,
        axisLine: { onZero: false },
        gridIndex: 0,
        axisLabel: { show: false },
        axisTick: { show: false }
      },
      {
        type: 'category',
        data: category,
        gridIndex: 1,
        boundaryGap: true,
        axisLine: { onZero: false },
        axisLabel: { fontSize: 11 }
      }
    ],
    yAxis: [
      { scale: true, type: 'value', gridIndex: 0, axisLabel: { fontSize: 11 } },
      { type: 'value', gridIndex: 1, axisLabel: { fontSize: 11, formatter: (v: number) => formatCompact(v) }, splitLine: { show: false } }
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], start: 60, end: 100, bottom: 6, height: 18 }
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        data: candles,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: '#ef4444',
          color0: '#16a34a',
          borderColor: '#ef4444',
          borderColor0: '#16a34a'
        }
      },
      {
        name: '成交量',
        type: 'bar',
        data: volumes,
        xAxisIndex: 1,
        yAxisIndex: 1
      }
    ]
  }
})

async function fetchKlines(manual = false) {
  if (!selectedSymbol.value) return
  if (manual) klineLoading.value = true
  try {
    const res: any = await cryptoApi.getKlines(selectedSymbol.value, klineInterval.value, KLINE_LIMIT)
    const items = Array.isArray(res?.data?.items) ? (res.data.items as BinanceKlineBar[]) : []
    klineBars.value = items
    klineError.value = items.length ? '' : (manual ? '接口未返回 K 线数据' : '暂无 K 线数据')
  } catch (e: any) {
    klineError.value = e?.message || 'K 线获取失败'
    console.error('[Crypto] 获取K线失败', e)
  } finally {
    klineLoading.value = false
  }
}

// ---------- 刷新策略 ----------
// 页面周期切换只影响 K 线；波动率固定 1m，不随周期切换（保证年化数值正确）。
watch(klineInterval, () => {
  fetchKlines(true)
})

watch(selectedSymbol, (nv, ov) => {
  if (nv && nv !== ov) {
    quote.value = null
    volatility.value = null
    firstQuoteLoaded.value = false
    klineBars.value = []
    refreshSnapshot()
    fetchKlines(true)
  }
})

const autoRefresh = ref(true)
let refreshTimer: ReturnType<typeof setInterval> | null = null

// 开关切换时立即生效
watch(autoRefresh, (on) => {
  if (on) {
    startAutoRefresh()
    refreshSnapshot()
  } else {
    stopAutoRefresh()
  }
})

function startAutoRefresh() {
  stopAutoRefresh()
  refreshTimer = setInterval(() => {
    if (document.hidden || !autoRefresh.value || !selectedSymbol.value) return
    refreshSnapshot()
  }, REFRESH_INTERVAL_MS)
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

function refreshAll() {
  loadStatus()
  refreshSnapshot()
  fetchKlines(true)
}

function onVisibility() {
  // 标签页从后台切回时立即刷新一次，避免展示过期数据
  if (!document.hidden && firstQuoteLoaded.value) {
    refreshSnapshot()
  }
}

// ---------- 格式化 ----------
const pricePrecision = computed(() => {
  const info = symbols.value.find((s) => s.symbol === selectedSymbol.value)
  if (info && typeof info.price_precision === 'number') return info.price_precision
  return null
})

function formatPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  const n = Number(v)
  const digits = pricePrecision.value ?? (n < 1 ? 6 : n < 100 ? 4 : 2)
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function formatCompact(v: number | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'K'
  return n.toFixed(2)
}

function formatPct(v: number | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toFixed(2) + '%'
}

function formatSigned(v: number | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return (n > 0 ? '+' : '') + n.toFixed(2)
}

function formatRate(v: number | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return (n * 100).toFixed(4) + '%'
}

function formatTimes(v: number | null | undefined): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toFixed(2) + 'x'
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = dayjs(iso)
  return d.isValid() ? d.format('MM-DD HH:mm') : '—'
}

function formatFullTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = dayjs(iso)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : '—'
}

function formatBarTime(iso: string): string {
  const d = dayjs(iso)
  if (!d.isValid()) return iso
  return klineInterval.value === '1d' ? d.format('YYYY-MM-DD') : d.format('MM-DD HH:mm')
}

// ---------- 生命周期 ----------
onMounted(() => {
  loadStatus()
  loadSymbols()
  refreshSnapshot()
  fetchKlines(true)
  startAutoRefresh()
  document.addEventListener('visibilitychange', onVisibility)
})

onUnmounted(() => {
  stopAutoRefresh()
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<style lang="scss" scoped>
.crypto-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.source-card {
  :deep(.el-card__body) {
    padding: 12px 20px;
  }
}

.source-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;

  .source-left {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;

    .source-badge {
      font-weight: 600;
      color: var(--el-color-primary);
    }

    .source-note {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }
  }

  .source-right {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--el-text-color-secondary);

    .conn-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--el-text-color-disabled);

      &.ok {
        background: var(--el-color-success);
        box-shadow: 0 0 4px var(--el-color-success);
      }
    }
  }
}

.toolbar-card {
  :deep(.el-card__body) {
    padding: 12px 20px;
  }
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;

    .field-label {
      font-size: 14px;
      color: var(--el-text-color-regular);
    }

    .symbol-select {
      width: 220px;
    }

    .option-meta {
      float: right;
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }

    .symbols-count,
    .symbols-loading {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }

    .symbols-error {
      font-size: 12px;
      color: var(--el-color-danger);
    }
  }

  .toolbar-right {
    display: flex;
    align-items: center;
  }
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;

  .header-title {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .updated-at {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
}

.quote-main {
  .price-block {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;

    .price {
      font-size: 32px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;

      &.up { color: #ef4444; }
      &.down { color: #16a34a; }
    }

    .change {
      font-size: 16px;
      font-weight: 600;

      &.up { color: #ef4444; }
      &.down { color: #16a34a; }
    }

    .unit {
      font-size: 13px;
      color: var(--el-text-color-secondary);
    }
  }

  .quote-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 12px;

    .grid-item {
      padding: 10px 14px;
      background: var(--el-fill-color-light);
      border-radius: 6px;

      .grid-label {
        display: block;
        font-size: 12px;
        color: var(--el-text-color-secondary);
        margin-bottom: 4px;
      }

      .grid-value {
        font-size: 15px;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
      }

      .grid-sub {
        display: block;
        font-size: 11px;
        color: var(--el-text-color-secondary);
        margin-top: 2px;
      }
    }
  }
}

.vol-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px;

  .vol-item {
    padding: 10px 14px;
    background: var(--el-fill-color-light);
    border-radius: 6px;

    .vol-label {
      display: block;
      font-size: 12px;
      color: var(--el-text-color-secondary);
      margin-bottom: 4px;
    }

    .vol-value {
      font-size: 16px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;

      &.highlight { color: var(--el-color-primary); }
      &.up { color: #ef4444; }
      &.down { color: #16a34a; }
    }
  }
}

.kline-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.kline-chart {
  width: 100%;
  height: 420px;
}

.ohlc-section {
  margin-top: 16px;

  .ohlc-title {
    font-size: 13px;
    color: var(--el-text-color-secondary);
    margin-bottom: 8px;
  }
}

.page-footer {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-align: center;
  padding: 4px 0 12px;
}

.up { color: #ef4444; }
.down { color: #16a34a; }

.empty-hint {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}

@media (max-width: 768px) {
  .kline-chart {
    height: 320px;
  }
}
</style>
