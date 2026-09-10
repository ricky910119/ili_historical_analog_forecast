# ili_historical_analog_forecast

以 **2025 年的歷史相似波形**，搭配 **2026 年最新的人次水位**，預測全國 ILI 未來 **H1–H8** 的週就診人次。

這是一個獨立研究專案。它 **不使用 LLM、不使用 TimesFM、不需要 GPU**，也 **不會**被加入
`disease_forecast_llm_model_router` 的正式候選池。

---

## 1. 演算法（v1，固定設定）

| 項目 | 值 |
| --- | --- |
| `algorithm_version` | `historical-analog-v1` |
| `reference_year` | 2025 |
| `target_year` | 2026 |
| `lookback_weeks` | 8 |
| `forecast_horizons` | H1–H8 |
| target | 全國 ILI＝`nhi_opd` + `rods` 的週就診人次 |

設今年截至有效 origin 的最近 8 個完整 DIM 週人次為 `x[1..8]`，
某個 2025 年合格候選片段為 `y[1..8]`，各自除以自身最後一週：

```
u[i] = x[i] / x[8]
v[i] = y[i] / y[8]

distance = mean( |u[i] - v[i]| ),  i = 1..8
```

取 **distance 最小的單一片段**；分數完全相同時，以 **候選結束週較早者** 優先，確保可重現。

設選中片段之後第 h 個 DIM 週的人次為 `y_future[h]`：

```
prediction[h] = x[8] * y_future[h] / y[8]
```

### 刻意不做的事（v1 不得加入）

- 不以 z-score 取代上述標準化。
- 不使用 DTW、時間拉伸或時間壓縮。
- 不做多片段平均、Top-k、近期趨勢混合或人工修形。
- 不加平滑、不加人工噪音、不強迫預測上升。
- 不用未來實際值挑選片段或調整預測。
- 原始浮點預測完整寫入 CSV／JSON，**只有圖表顯示時才四捨五入**。

---

## 2. 資料來源與目標定義

```
database : postgres
tables   : disease_forecast_data.model_nhi_opd_daily_county
           disease_forecast_data.model_rods_daily_county
columns  : date, county, ili, coverage_observed
calendar : DIM_DATA.public.dim_weekdate  (date, yearweek)
```

- 連線沿用既有環境的 `eic_utils`：`conn.deco.postgres(dbname="postgres")`。
  **本 repo 不寫入任何密碼或連線字串**，憑證與連線生命週期完全由已安裝的 `eic_utils` 擁有。
- 查詢值使用既有的 `?` 參數占位符（日期界限用 `?::date`），與既有專案一致；
  未自行改用其他 DB driver 語法。
- 縣市 allowlist 為臺灣 22 縣市，明確保存在 `ili_analog/data_pg.py` 的 `COUNTIES`。
  正規化規則為 `台` → `臺`（`tw22-tai-alias-v1`）；正規化後若仍出現重複鍵，直接拒絕。
- 未知縣市、負值、無限值、無法解析的數值一律拒絕（拋錯，不靜默略過）。
- 先分別計算各 source 的全國週人次，再相加：

```
ili_total = nhi_opd_total + rods_total
```

此數值是 **兩個監測來源的合計就診人次**，不是去重後的感染人口。

流感重症（SCI）**不加入目標，也不作為 v1 的輸入特徵**。

---

## 3. DIM 日曆契約

唯一週次權威是 `DIM_DATA.public.dim_weekdate`：

```sql
SELECT date, yearweek
FROM public.dim_weekdate
WHERE yearweek IS NOT NULL
ORDER BY date
```

`ili_analog/dim_calendar.py` 會驗證：每個日期唯一對應一週、週內日期連續、
週與週之間無缺口也無重疊、yearweek 隨週起日遞增。
**週內天數以實際日期集合為準**（可以不是 7 天）。
**週次編號也由 DIM 決定**：實際日曆存在 `200054` 這類第 54 週，程式不自行設上限，
只要求週次部分維持兩位數（`year_of()` 才有定義）；實際出現的週次集合寫入
`run.json` 的 `dim_calendar.week_numbers_in_calendar`。

不使用也不允許：ISO week、`pandas.resample("W")`、固定星期幾作為週界、
`origin + h * 7`、固定七天切片、固定 56 天取代 H1–H8、
以 yearweek 整數加一推導下一週。

- 「2025 年」「2026 年」一律取自 DIM `yearweek // 100`。
- 2025 候選的比對 8 週與其後續 8 週，**都必須整段落在 DIM yearweek 的 2025 年**。
- 今年比對窗口的 8 週必須 **全部屬於 2026 年**；
  年初不足 8 週時回報 `INELIGIBLE`，**不向前一年借週**。
- H1–H8 是 origin 之後 **連續八個 DIM 週**；DIM 未涵蓋時明確失敗（不推導、不外插）。

---

## 4. 完整性與可用時間

一個 source-week 「完整」的條件是：DIM 該週 **所有日期 × 22 縣市** 都存在
有效且 `coverage_observed` 為真的資料。

- 缺列、NULL、未觀測資料 **一律不補成 0**。
- 合法且已觀測的實際 0 會保留；但 `x[8]` 與 `y[8]` 必須 **大於 0**。
- 任一來源不完整時，該週 `ili_total` 標為缺值，**不用部分縣市或部分來源湊全國總數**。
- 今年窗口有缺漏 → `INELIGIBLE`；**不跳過缺週拼接，也不自動改選較早 origin**。
- 去年候選任一比對週或後續週不完整 → 排除該候選，理由寫入 `candidate_scores.csv`。
- 沒有任何合法候選 → `INELIGIBLE`，**不退回預設預測**。
- `--settled-cutoff` 為 **必填**，由使用者明確指定；本專案 **不從執行時鐘推導**。
  origin 必須存在於 DIM，且其週末日期不得晚於 `settled_cutoff`。
- 即時預測只讀取截至 origin 的今年資料（程式層面以 `as_of()` 硬性過濾）。

### 春節政策（需使用者確認的邊界規則）

設定檔：[`configs/spring_festival.json`](configs/spring_festival.json)

- 事件日期必須有來源（`source` 欄位為必填），並透過 DIM 的實際日期集合映射到週次。
- 若 **今年 origin 週** 或 **候選的最後一週**（即比例分母週）與設定的春節假期重疊：
  - 今年 → 回報 `INELIGIBLE`；
  - 去年候選 → 排除該候選（理由 `spring_festival_denominator_week`）。
- 春節判定只需要 DIM 日曆，因此 **只要候選有分母週就會記錄此理由**，
  即使該候選已被跨年規則排除；理由統計因而保持可稽核。
  以 2025 為參考年時，春節週落在年初前 8 週內，本來就已被跨年規則排除，
  所以此規則對候選池 **沒有實際影響**；它真正生效的地方是 2026 origin 的分母週。
- **不修改其他春節週的實際值、不插值、不移動全年波形**，
  也 **不新增任何資料推導的「異常週」門檻**。

> ⚠️ **待確認**：設定檔目前 `confirmed_by_user: false`，且兩筆假期的
> `verified_by_user` 皆為 `false`。2025 與 2026 的春節連假日期請對照
> 行政院人事行政總處（DGPA）公布的政府行政機關辦公日曆表核對後，
> 再將旗標改為 `true`。程式會把此狀態原樣寫入 `run.json`，不會偷偷通過。

---

## 5. CLI

```
python -m ili_analog.cli <preflight|forecast|backtest> [options]
```

共同參數：

| 參數 | 說明 |
| --- | --- |
| `--settled-cutoff` | **必填**，ISO 日期；最後一個已結算週的週末日期 |
| `--origin-yearweek` | DIM yearweek，例如 `202635`（`backtest` 可重複指定多次） |
| `--reference-year` | 預設 `2025` |
| `--target-year` | 預設 `2026` |
| `--output-dir` | 輸出根目錄，預設 `outputs` |
| `--spring-festival-config` | 預設 `configs/spring_festival.json` |
| `--history-weeks` | 預測圖顯示的今年週數，預設 26，最少 16 |

`backtest` 另有：

| 參數 | 說明 |
| --- | --- |
| `--timesfm-forecast-csv` | 既有 TimesFM 預測檔（欄位 `origin_yearweek, yearweek, prediction`）；**本 repo 絕不重新呼叫 TimesFM**，未提供即標記 `NOT AVAILABLE` |
| `--no-score` | 只做第一階段預測產生，完全不讀未來實際值 |

`--reference-year` / `--target-year` 只支援 `2025 → 2026`。
其他年份會 **明確拒絕**，不假裝已泛化。

三種模式：

1. **preflight** — 檢查設定、DIM 日曆、資料完整性與候選數；不產生預測檔與圖表。
2. **forecast** — 指定一個 origin，產生 H1–H8；**完全不讀 origin 之後的目標實際值**。
3. **backtest** — 使用者指定 origin 清單，逐一重建當時可用的輸入窗口並產生預測，
   之後才在獨立階段讀取未來實際值並評分。

---

## 6. 輸出 artifacts

每次執行建立獨立輸出目錄 `<output-dir>/<mode>_<UTC timestamp>/`：

| 檔案 | 內容 |
| --- | --- |
| `run.json` | 狀態與錯誤原因、演算法版本與完整設定、origin、`settled_cutoff`、資料快照語意、候選數與排除原因統計、選中片段與相似度、今年與去年的比例基準人次、輸入資料與設定 digest、所有 artifact 的 sha256 |
| `weekly_actuals.csv` | DIM yearweek、週起迄、DIM 天數、`nhi_opd`、`rods`、`ili_total`、每來源缺漏 cell 數與完整性狀態 |
| `candidate_scores.csv` | 候選起迄週、是否符合資格與排除原因、合法候選的 `distance`、排名、是否選中 |
| `matched_window.csv` | 相對位置 1–8、今年與去年 DIM 週次、原始人次、標準化值、絕對差 |
| `forecast.csv` | origin、horizon、目標 DIM week、週起迄、選中歷史片段、去年後續人次、相對倍率、預測人次 |
| `ILI_<origin>_analog_match.png` | 圖 A：相似片段比較（標準化overlay + 換算到今年水位 + 逐週對照表） |
| `ILI_<origin>_h1_h8_national.png` | 圖 B：H1–H8 預測（近期實際值、origin、預測與逐 horizon 表） |

`backtest` 另有：

| 檔案 | 內容 |
| --- | --- |
| `origins/<yearweek>/…` | 每個 origin 的完整 artifact 組（含自己的 `run.json`） |
| `evaluation.csv` | 逐列 `model, origin, horizon, target week, prediction, actual, actual_status, error` |
| `metrics.json` | MAE、WAPE、signed_bias_ratio；整體、逐 horizon、以及完整 H1–H8 origin cohort |
| `origins/<yw>/ILI_<yw>_h1_h8_national_scored.png` | 揭露實際值後另存的圖，**不覆蓋**第一階段的預測圖 |

CSV／JSON 一律保存原始浮點值；四捨五入只發生在圖表。

圖表沿用既有全國 ILI 研究圖的風格：`#626D71` / `#839D9A` / `#9E8F8A` /
`#AAA39B` / `#E5E0DA` 配色、15×9 版面、CJK 字型偵測（找不到時自動改用英文標籤）、
圖下方只有橫線的數值表、虛線 origin 標記、pending horizon 以空心點加淺色區塊表示、
300 dpi 輸出。

---

## 7. 回測與評估隔離

- `forecast` 路徑 **不讀取** origin 之後的目標實際值。
- `backtest` 分成兩個階段：
  1. **產生**：每個 origin 只看到週末日期 ≤ 自身 origin 的 DIM 週（`as_of()` 硬性過濾），
     預測全部寫入磁碟；
  2. **評分**：之後才讀取未來實際值並計分。
     評分 **無法**回頭影響片段選擇，也 **不會回寫或改動** 已產生的 forecast 檔。
- 未成熟或不完整的 target actual 保留 `pending`
  （`pending_unsettled` / `pending_incomplete`），**不會被當成 0**。
- 各 horizon 報告可評分筆數；另外報告完整 H1–H8 origin cohort 的比較結果。

指標：

```
MAE               = mean(|pred - actual|)
WAPE              = sum(|pred - actual|) / sum(actual)
signed_bias_ratio = sum(pred - actual)   / sum(actual)
```

分母為零時回報 **不可計算**（`null` + 說明），**不加 epsilon 偽造結果**。

基準模型：`persistence_origin_week` — 維持 origin 最後一週人次 `x[8]` 於 H1–H8。
若使用者提供既有 TimesFM 預測檔則額外比較；否則標記 `NOT AVAILABLE`。
所有模型對齊 **相同 origin、相同目標 DIM week、相同目標口徑與共同可評分資料**。

---

## 8. 研究限制（必讀）

- 這個方法是在 **看過 2026 圖之後** 提出的。因此 2026 回測只能標示為
  **exploratory retrospective evaluation**，
  **不得**宣稱 untouched holdout、out-of-sample，或作為正式升級證據。
- 資料來自目前資料庫快照，`run.json` 一律標記
  **`current database snapshot; not historical as-of replay`**。
  以 origin 做時間截斷只界定了「時間上的可用範圍」，
  **不等於**已解決歷史資料修訂（revision）造成的洩漏。
- v1 **沒有機率模型**，因此不畫任何信賴／預測區間；任何區間都會是未經校準的裝飾。
- 本模型 **不進入** `disease_forecast_llm_model_router` 的正式候選池。

---

## 9. 在 server 上執行

```bash
cd /path/to/ili_historical_analog_forecast

# 0) 純邏輯單元測試（無 DB、無網路、無模型）
python -m pytest tests -q

# 1) preflight：檢查設定、DIM、完整性與候選數
python -m ili_analog.cli preflight \
    --origin-yearweek 202635 \
    --settled-cutoff 2026-09-05 \
    --output-dir outputs

# 2) 單次預測：origin 202635 的 H1–H8
python -m ili_analog.cli forecast \
    --origin-yearweek 202635 \
    --settled-cutoff 2026-09-05 \
    --output-dir outputs

# 3) 探索性回測：多個 2026 origin
python -m ili_analog.cli backtest \
    --origin-yearweek 202612 \
    --origin-yearweek 202616 \
    --origin-yearweek 202620 \
    --origin-yearweek 202624 \
    --origin-yearweek 202628 \
    --settled-cutoff 2026-09-05 \
    --output-dir outputs

# 4) 只產生回測預測、完全不讀未來實際值
python -m ili_analog.cli backtest \
    --origin-yearweek 202612 --origin-yearweek 202620 \
    --settled-cutoff 2026-09-05 --no-score \
    --output-dir outputs

# 5) 附帶既有 TimesFM 預測檔的比較（不會重新呼叫 TimesFM）
python -m ili_analog.cli backtest \
    --origin-yearweek 202612 --origin-yearweek 202620 \
    --settled-cutoff 2026-09-05 \
    --timesfm-forecast-csv /path/to/timesfm_national_h1_h8.csv \
    --output-dir outputs
```

### 如何挑 `--origin-yearweek` 與 `--settled-cutoff`

兩者必須自洽：**origin 的週末日期不得晚於 `settled_cutoff`**，否則程式直接拒絕
（不會自動改用較早的 origin）。上面用的 `202635 / 2026-09-05` 是自洽的一組
（202635 的週末即 2026-09-05）；`202634` 對應的則是 `2026-08-29`。

依既有 ETL 節奏（週三 15:00 載入、NHI 回補約三天），已結算的通常是
「含最近一次 ETL 執行的那個 DIM 週」的 **前一週**。實際週界請直接向 DIM 查證：

```sql
SELECT yearweek, MIN(date) AS week_start, MAX(date) AS week_end
FROM public.dim_weekdate
WHERE yearweek BETWEEN 202630 AND 202640
GROUP BY yearweek ORDER BY yearweek;
```

把該週的 `week_end` 填進 `--settled-cutoff`、`yearweek` 填進 `--origin-yearweek`。

依賴：Python 3.9+、`matplotlib`（僅畫圖用）、`pytest`（僅測試用），
以及環境中既有的 `eic_utils`。
