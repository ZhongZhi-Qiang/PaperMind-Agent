---
name: quick-lookup
description: 快速联网查询工具，基于 Tavily Search API。支持天气查询、事实检索、新闻动态、官方文档查找、实时行情等场景。用户要求搜索、联网、查官网、核实事实、获取最新信息时使用。
---

## 目标

用 Tavily Search API 返回可追溯的联网结果，整理成简洁中文结论。

## 必备前提

- 环境变量 `TAVILY_API_KEY` 必须已配置。
- 使用本技能目录下的脚本发起搜索：

```bash
python skills/web-search/scripts/tavily_search.py --query "查询词"
```

- 如果 key 缺失、鉴权失败或限流，明确说明原因，不要假装联网成功。

## 查询策略

1. 把用户问题压缩成 1 个主查询，控制在 400 字符以内。
2. 复杂问题拆成多个子查询，不要把多个主题塞进一个 prompt。
3. 按场景选择 `topic`：
   - `general`：天气、官网、文档、常规事实、产品信息
   - `news`：最新消息、今日动态、近期事件
   - `finance`：黄金价格、股票、汇率、市场数据
4. 默认 `search_depth=basic`；需要更高相关性时改 `advanced`。
5. 对时间敏感查询，加 `--time-range day|week|month`。
6. 对金融查询，优先把中文改写成英文 ticker（如 `XAU USD price today`）。
7. 对官方信息，加 `--include-domain` 过滤。

## 常见场景示例

### 天气查询

```bash
python skills/web-search/scripts/tavily_search.py --query "北京今天天气" --topic general --max-results 3
```

备用：Open-Meteo API（无需 key，更稳定）：

```python
import requests
lat, lon = 39.9042, 116.4074
url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
resp = requests.get(url, timeout=10)
data = resp.json()
# 解析 current_weather 中的 temperature, weathercode, windspeed
```

天气代码对照（WMO）：0=晴, 1-3=多云, 45-48=雾, 51-67=雨, 71-77=雪, 80-82=阵雨, 95-99=雷暴。

如果需要具体城市坐标，用 python_repl + geocoding API 查询。

### 事实检索

```bash
python skills/web-search/scripts/tavily_search.py --query "LangChain create_agent 用法" --topic general --include-domain docs.langchain.com --max-results 3
```

### 新闻动态

```bash
python skills/web-search/scripts/tavily_search.py --query "OpenAI 最新发布" --topic news --time-range week --max-results 5
```

### 实时行情

```bash
python skills/web-search/scripts/tavily_search.py --query "XAU USD price today" --topic finance --time-range day --max-results 3
```

## 执行步骤

1. 判断用户问题属于哪个场景（天气/事实/新闻/行情/其他）。
2. 构造查询词，运行 Tavily 脚本。
3. 读取返回 JSON，关注 `title`、`url`、`score`、`published_date`、`content`。
4. 如果结果足够，直接整理答案并给出来源。
5. 如果结果不足或冲突：
   - 换更具体的查询词重试一次
   - 必要时用 `fetch_url` 抓取候选 URL 正文核验
6. 对"今天/最新/当前"类查询，回答里必须写明查询日期或来源发布日期。

## 结果筛选

- 不要只用第一条结果下结论。
- 优先使用官方文档、官方公告、政府/标准组织、主流一手媒体。
- 多个来源冲突时，优先最新且更权威的来源，并说明冲突。

## 失败处理

| 错误 | 说明 |
|------|------|
| `TAVILY_API_KEY is not set` | 本地配置缺失，检查 .env |
| `401/403` | Tavily 鉴权失败，检查 key |
| `429` | Tavily 限流或额度用尽 |
| `5xx` / 网络异常 | Tavily 服务问题或链路不通 |

失败时明确说明原因，不编造答案。

## 输出格式

```md
结论：...

依据：
1. ...
2. ...

来源：
- 标题 1: URL
- 标题 2: URL

时间说明：查询时间 YYYY-MM-DD
```

## 经验教训

<!-- 当出现失败→成功的切换时，在此记录可复用经验 -->
