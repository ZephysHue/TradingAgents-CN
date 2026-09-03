---
title: TradingAgents-CN 当前项目背景、模块与功能基线
version: v1.0.1
updated: 2026-08-28
source: 当前 Git 工作区代码、配置、接口定义、前端页面与 Docker 运行状态
status: active
---

# TradingAgents-CN 当前项目背景、模块与功能基线

> 本文是当前代码仓库的事实基线，不替代接口契约、部署手册和具体功能设计文档。状态以当前代码和运行结果为准；历史文档中的旧版本描述不自动视为当前事实。

## 1. 项目定位

### 1.1 项目背景

[KNOWN] TradingAgents-CN 是一个面向中文用户的多智能体金融研究平台。项目基于 TradingAgents 思路，将市场分析拆分为多个专业角色，由大语言模型协同完成资料收集、技术分析、基本面分析、新闻分析、风险讨论和最终决策汇总。

[KNOWN] 当前仓库已经从早期 Streamlit 形态演进为前后端分离系统：

- 前端：Vue 3、Vite、Pinia、Vue Router、Element Plus
- 后端：FastAPI、Uvicorn、Pydantic
- 数据层：MongoDB、Redis、多级缓存
- 智能体编排：LangGraph 及 `tradingagents` 数据流和分析模块
- 部署：Docker Compose，Nginx 统一入口

[KNOWN] 项目当前定位仍是股票研究、策略实验和模拟交易，不是实盘交易执行系统。当前市场接口明确支持 A 股、港股和美股。

### 1.2 当前版本事实

| 来源 | 当前值 | 说明 |
|---|---|---|
| `VERSION` | `v1.0.1` | 当前项目版本文件 |
| `README.md` | v1.0.1 | 当前主说明版本 |
| `frontend/package.json` | `1.0.0-preview` | 前端包版本尚未同步到 v1.0.1 |
| `docker-compose.yml` | `v1.0.0-preview` | 镜像标签和注释仍使用 preview 命名 |
| `app/main.py` | 从 `VERSION` 读取 | API 健康接口使用版本文件 |

[INFERRED] 版本管理存在未完全收口的历史残留：项目发布版本已是 v1.0.1，但前端包、Docker 镜像标签和部分注释仍是 v1.0.0-preview。本文按 `VERSION` 和运行时 API 版本优先记录当前版本。

## 2. 总体架构

```text
浏览器
  │
  ▼
Nginx Gateway :3000
  ├── /        ──> frontend:80     Vue 3 SPA
  └── /api/*   ──> backend:8000    FastAPI
                    ├── MongoDB   用户、配置、行情、任务、报告
                    ├── Redis     缓存、队列、进度、通知
                    └── LLM/数据源  多智能体分析与市场数据
```

### 2.1 部署组件

| 组件 | 技术 | 主要职责 | 当前状态 |
|---|---|---|---|
| `nginx-gateway` | Nginx Alpine | 对外暴露 3000，代理前端和 API | 运行正常 |
| `frontend` | Nginx Alpine | 提供 Vue 静态页面和 SPA 路由 | 运行正常、健康检查通过 |
| `backend` | FastAPI + Uvicorn | API、认证、分析、同步、报告和配置 | 运行正常、健康检查通过 |
| `mongodb` | MongoDB 4.4 | 持久化业务数据 | 运行正常、健康检查通过 |
| `redis` | Redis 7 Alpine | 缓存、队列、实时进度和通知 | 运行正常、健康检查通过 |
| `redis-commander` | 可选 | Redis 管理界面 | Compose profile `management` |
| `mongo-express` | 可选 | MongoDB 管理界面 | Compose profile `management` |

## 3. 后端模块

### 3.1 应用入口与核心基础设施

| 路径 | 模块 | 功能 |
|---|---|---|
| `app/main.py` | 应用入口 | 创建 FastAPI 应用、注册路由、中间件、调度任务和启动检查 |
| `app/core/config.py` | 配置 | 环境变量、MongoDB、Redis、LLM、数据源和系统开关 |
| `app/core/database.py` | 数据库 | Motor/PyMongo 连接、数据库初始化和同步访问 |
| `app/core/response.py` | 响应封装 | 统一 API 响应结构 |
| `app/core/startup_validator.py` | 启动校验 | 校验数据库、数据源和关键配置状态 |
| `app/core/config_bridge.py` | 配置桥接 | 兼容旧配置体系与当前 FastAPI 配置体系 |
| `app/middleware/` | 中间件 | 请求日志、请求 ID、限流、操作日志和异常处理 |

### 3.2 认证与用户

| 路径 | 功能 |
|---|---|
| `app/routers/auth_db.py` | 登录、刷新 Token、登出、当前用户、改密、用户管理 |
| `app/services/auth_service.py` | JWT Token 创建和校验 |
| `app/services/user_service.py` | 用户创建、认证、权限字段和用户资料 |
| `app/models/user.py` | 用户、注册和响应数据模型 |
| `app/routers/operation_logs.py` | 用户操作日志查询、统计、导出和清理 |

### 3.3 多智能体分析

| 路径 | 功能 |
|---|---|
| `app/routers/analysis.py` | 单股分析、批量分析、任务查询、状态、结果、取消和历史 |
| `app/services/analysis_service.py` | 分析任务创建、调度、状态和结果处理 |
| `app/services/simple_analysis_service.py` | 分析流程入口、市场类型选择、分析深度和结果组装 |
| `tradingagents/agents/` | 专业分析智能体 |
| `tradingagents/graph/` | LangGraph 工作流、节点和状态流转 |
| `tradingagents/llm_clients/` | LLM 厂家和模型客户端抽象 |
| `tradingagents/dataflows/` | 市场数据、新闻、缓存、技术指标和数据源编排 |

[KNOWN] 当前分析流程包含市场、基本面、新闻/情绪、看涨研究、看跌研究、交易决策和风险控制等角色或阶段。实际输出模块以保存报告结构和前端展示映射为准，不能简单按角色数量等同于报告数量。

### 3.4 任务队列、进度和通知

| 模块 | 路径 | 功能 |
|---|---|---|
| 队列 | `app/routers/queue.py`、`app/services/queue/` | 任务队列、并发控制、排队状态 |
| 任务 | `app/routers/analysis.py`、`app/services/queue/` | 单任务和批量任务生命周期 |
| SSE | `app/routers/sse.py` | 分析过程和进度流式推送 |
| WebSocket | `app/routers/websocket_notifications.py` | 通知和实时状态推送 |
| 通知 | `app/routers/notifications.py` | 通知列表、未读数和已读处理 |
| 内部消息 | `app/routers/internal_messages.py` | 分析过程消息、研究报告和分析师笔记 |

### 3.5 股票数据与同步

| 模块 | 主要路径 | 功能 |
|---|---|---|
| 数据源适配器 | `app/services/data_sources/` | Tushare、AKShare、BaoStock 的统一适配接口 |
| 数据源管理 | `app/services/data_sources/manager.py` | 可用性检测、优先级和 fallback |
| Tushare 同步 | `app/worker/tushare_sync_service.py`、`app/routers/tushare_init.py` | 基础信息、行情、历史、财务和状态同步 |
| AKShare 同步 | `app/worker/akshare_sync_service.py`、`app/routers/akshare_init.py` | 基础信息、行情、历史、财务和状态同步 |
| BaoStock 同步 | `app/worker/baostock_sync_service.py`、`app/routers/baostock_init.py` | 基础信息、日线、历史数据和状态同步 |
| 行情入库 | `app/services/quotes_ingestion_service.py` | 实时快照和历史行情导入 `market_quotes` |
| 股票数据 | `app/routers/stock_data.py`、`app/services/stock_data_service.py` | 个股信息、行情和数据状态 |
| 多市场股票 | `app/routers/multi_market_stocks.py`、`app/services/unified_stock_service.py` | A 股、港股、美股统一查询 |
| 多周期同步 | `app/routers/multi_period_sync.py` | 日、周、月及全历史同步 |
| 财务数据 | `app/routers/financial_data.py` | 财务数据查询、同步和统计 |
| 新闻数据 | `app/routers/news_data.py` | 新闻查询、同步、检索和清理 |
| 社交数据 | `app/routers/social_media.py` | 社交媒体相关数据入口 |

[KNOWN] 当前统一适配器基类 `app/services/data_sources/base.py` 的接口仍是股票导向：股票列表、每日基本面、实时行情、K 线和新闻。当前管理器实际加载的是 Tushare、AKShare、BaoStock。

### 3.6 筛选、自选股和报告

| 功能 | 路径 | 当前能力 |
|---|---|---|
| 智能选股 | `app/routers/screening.py`、`app/services/screening_service.py` | 字段查询、条件筛选、增强筛选、结果排序 |
| 自选股 | `app/routers/favorites.py`、`app/services/favorites_service.py` | 添加、删除、分组、标签、批量同步 |
| 报告 | `app/routers/reports.py`、`app/services/report_service.py` | 报告列表、详情、模块内容、删除和下载 |
| 报告导出 | `app/services/report_export_service.py` 等 | Markdown、Word、PDF 导出链路 |
| 模拟交易 | `app/routers/paper.py` | 模拟账户、订单、持仓、订单历史和重置 |

### 3.7 配置、模型和运维

| 功能 | 路径 | 当前能力 |
|---|---|---|
| LLM 配置 | `app/routers/config.py` | 厂家、模型、默认模型、模型目录和连通性测试 |
| 数据源配置 | `app/routers/config.py` | 数据源增删改、启用开关和优先级 |
| 市场类别 | `app/routers/config.py` | 市场类别和数据源分组管理 |
| 模型能力 | `app/routers/model_capabilities.py` | 模型能力、推荐、校验和批量初始化 |
| 调度器 | `app/routers/scheduler.py` | 任务查看、暂停、恢复、触发和历史 |
| 数据库运维 | `app/routers/database.py` | 状态、统计、备份、导入、导出和清理 |
| 缓存运维 | `app/routers/cache.py` | 缓存统计、详情、清理、清空和后端信息 |
| 系统日志 | `app/routers/logs.py` | 日志文件、读取、统计、导出和删除 |

## 4. 前端模块

### 4.1 页面模块

`frontend/src/views/` 当前包含：

| 页面目录 | 功能 |
|---|---|
| `Auth` | 登录和认证入口 |
| `Dashboard` | 首页概览和系统状态 |
| `Analysis` | 单股分析入口和分析过程 |
| `Tasks` | 分析任务中心 |
| `Queue` | 任务队列和进度 |
| `Stocks` | 股票搜索、详情和行情 |
| `Screening` | 条件选股和结果处理 |
| `Favorites` | 自选股、分组和数据同步 |
| `Reports` | 分析报告列表、详情和导出 |
| `PaperTrading` | 模拟账户、下单、持仓和订单 |
| `Settings` | LLM、数据源、市场类别、系统和数据库配置 |
| `Learning` | 学习中心和文档内容 |
| `System` | 系统信息和运维入口 |
| `History`/相关历史视图 | 分析历史和结果回看 |

### 4.2 前端支撑模块

| 路径 | 功能 |
|---|---|
| `frontend/src/router/` | 登录守卫和页面路由 |
| `frontend/src/stores/` | 认证、分析、任务、配置等 Pinia 状态 |
| `frontend/src/api/` | 后端 API 客户端和领域接口 |
| `frontend/src/types/` | TypeScript 类型定义 |
| `frontend/src/components/` | 布局、选择器、进度、配置和报告组件 |
| `frontend/src/utils/` | Token、请求、格式化和通用辅助逻辑 |

## 5. 数据与存储

### 5.1 MongoDB 数据域

[KNOWN] 当前初始化脚本和业务代码涉及以下主要集合：

| 数据域 | 主要集合 |
|---|---|
| 用户与权限 | `users`、`user_sessions`、`user_activities` |
| 分析任务 | `analysis_tasks`、`analysis_progress`、`analysis_reports` |
| 股票基础信息 | `stock_basic_info`、港股/美股对应集合 |
| 股票行情 | `market_quotes`、历史日线集合及多周期集合 |
| 财务与新闻 | `stock_financial_data`、`stock_news` |
| 选股与自选 | `screening_results`、`favorites`、`tags` |
| 配置 | `system_config`、`model_config`、`system_configs`、数据源分组集合 |
| 运维与统计 | `system_logs`、`operation_logs`、`token_usage`、同步状态集合 |

[INFERRED] 当前代码同时存在历史配置集合名和新配置集合名，实际使用时应以 `app/core/config.py`、配置服务和运行数据库中的集合为准，不能仅凭初始化脚本判断所有集合都在主链路使用。

### 5.2 Redis 数据域

- 分析任务队列和任务状态
- 分析进度与短期结果
- SSE/WebSocket 通知相关状态
- 多级缓存和缓存统计
- 限流和短期会话数据

### 5.3 文件与导出

- `data/`：本地数据和辅助文件
- `logs/`：运行日志
- `reports/`：报告、部署证据和分析产物
- `config/`：运行时配置目录
- `frontend/dist/`：前端构建产物，不作为源码事实

## 6. 当前功能矩阵

| 功能 | 代码状态 | 当前判断 |
|---|---|---|
| 用户登录和 JWT | 已实现 | Docker 环境已验证登录成功 |
| 单股分析 | 已实现 | 有 API、任务和智能体链路 |
| 批量分析 | 已实现 | 有批量 API、队列和前端入口 |
| A 股数据 | 已实现 | Tushare、AKShare、BaoStock |
| 港股/美股查询 | 已实现部分能力 | 有统一查询和独立集合设计，数据覆盖依赖实际同步源 |
| 技术指标 | 已实现 | `tradingagents/dataflows/technical/` 和分析流程使用 |
| 基本面分析 | 已实现 | 依赖财务数据是否完成同步 |
| 新闻和情绪 | 已实现 | 新闻、社交数据和相关智能体存在 |
| 选股 | 已实现 | 普通筛选和增强筛选均有路由 |
| 自选股 | 已实现 | 支持收藏、分组、标签和同步 |
| 报告查看/导出 | 已实现 | 支持详情、模块内容和多格式导出 |
| 模拟交易 | 已实现 | 当前是虚拟交易 API，不连接真实交易所 |
| LLM 多厂家 | 已实现 | 支持动态配置和模型能力管理 |
| 实时进度 | 已实现 | SSE + WebSocket |
| Binance 行情 | 未实现 | 当前无 Binance 适配器、Crypto 市场和专用集合 |
| 其他虚拟币平台 | 未实现 | 需要新增资产类别和数据源适配层 |

## 7. 已验证运行状态

[KNOWN] 当前 Docker Compose 环境已验证：

- `backend`：healthy
- `frontend`：healthy
- `mongodb`：healthy
- `redis`：healthy
- `nginx-gateway`：正常运行，端口 `3000` 映射正常
- `GET http://127.0.0.1:3000/`：HTTP 200
- `GET http://127.0.0.1:3000/api/health`：成功
- `POST /api/auth/login`：默认管理员登录成功并返回 Token

详细部署变更、哈希、命令、结果和回滚证据见：

- `reports/deployment/VERIFICATION.txt`
- `reports/deployment/DIFF_FILE`
- `reports/deployment/ROLLBACK.sh`

## 8. 当前边界与缺口

### 8.1 业务边界

- 当前核心对象是股票，不是通用金融资产。
- 分析链路默认依赖股票代码、交易日、财务字段、公司新闻和股票市场类型。
- 模拟交易目前是内部虚拟账户，不等于券商或交易所下单。
- 数据源可用不代表数据已经同步；分析前仍需要确认目标市场的数据完整性。

### 8.2 工程缺口

- 版本号未在 `VERSION`、前端包、Compose 镜像标签和注释中完全统一。
- 数据源适配器和部分同步任务仍以 A 股为中心。
- 多市场接口支持 CN/HK/US，但部分市场的数据同步完整性依赖配置和外部数据源。
- 运行时配置、历史兼容层和多个集合命名并存，需要继续以运行事实梳理权威集合。
- 虚拟币市场尚未接入，不能直接把 Binance 配置为现有股票数据源。

## 9. 后续扩展方向

### 9.1 虚拟币数据接入

建议新增独立资产域，而不是把币种伪装成股票：

```text
CRYPTO market
  ├── crypto_symbols
  ├── crypto_klines
  ├── crypto_tickers
  └── crypto_orderbook
```

推荐先实现：

1. Binance Spot 公共行情和历史 K 线
2. WebSocket 实时 K 线和盘口
3. `CRYPTO` 市场类型和 `venue` 字段
4. 独立同步任务、缓存和数据质量检查
5. 前端市场选择、交易对搜索和 K 线展示
6. 分析引擎对 24/7 市场和无财务报表资产的适配

### 9.2 版本和文档治理

1. 统一 `VERSION`、`frontend/package.json`、Compose 镜像标签和 Docker 注释。
2. 将历史版本文档明确标记为历史资料，当前事实集中到本文和对应接口文档。
3. 为每个市场记录数据源、覆盖范围、最近同步时间和失败原因。
4. 将“代码存在”和“运行验证通过”分开记录，避免 README 功能列表替代验收结果。

## 10. 事实来源与阅读规则

本文结论主要来自：

- `README.md`
- `VERSION`
- `app/main.py`
- `app/routers/`
- `app/services/`
- `tradingagents/dataflows/`
- `frontend/src/`
- `docker-compose.yml`
- 当前 Docker Compose 容器状态和健康接口验证

[KNOWN] 当前项目代码、接口、测试和运行事实优先于旧版 `docs/overview/project-overview.md`。旧版文档中的 v0.1.7、Streamlit 架构和早期功能描述不作为当前实现依据。
