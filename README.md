# NSFNET IoT Dashboard — ISA-95 Level 1/2 雲端整合作業

## 專案說明

本專案將研究所論文「**PPO-Based Dynamic Traffic Splitting for Multi-State Flow Network Reliability Optimization**」與 IoT + ISA-95 架構及 Google Sheets 雲端整合結合，完成製造控制與執行系統課程作業。

---

## 目前檔案清單

```
NSFNET_IoT_Dashboard/
├── README.md                   ← 本文件
├── apps_script.js              ← Google Apps Script 後端（直接複製貼上）
├── simulator.py                ← Python 模擬器（Level 1，載入 PPO model）
├── index.html                  ← GitHub Pages 前端（NSFNET 拓樸圖 + 圖表）
├── nsfnet_multistate_arcs.csv  ← ✅ arc 多狀態分布資料
├── nsfnet_paths.csv            ← ✅ 5 條候選路徑定義
├── ppo_gnn_mss_best.zip        ← ✅ 訓練好的 PPO model
└── vecnormalize_stats.pkl      ← ✅ 觀測值正規化參數
```

> **尚未完成的步驟：**
> 1. `apps_script.js` 裡的 `SHEET_ID` 需要換成你的 Google Sheets 試算表 ID
> 2. 將 `index.html` 上傳到 GitHub 開啟 Pages

---

## ISA-95 對應關係

```
ISA-95 層級           對應本系統
─────────────────────────────────────────────────────────────
Level 0（物理設備）   NSFNET 的 20 條網路鏈路 (arc a1~a20)
Level 1（感測/控制）  Python 模擬器：
                        - 依 binomial 分布對每條 arc 取樣容量狀態
                        - PPO agent 即時決策流量分流比例
                        - ECMP baseline 對比
Level 2（監控 SCADA） Google Apps Script + Google Sheets：
                        - 接收每輪狀態 POST webhook
                        - 紀錄 arc 容量、flow、utilization 歷史
                        - doGet() 回傳 JSON 給前端
雲端 / MES            GitHub Pages 前端：
                        - NSFNET 拓樸圖（SVG，arc 顏色代表狀態）
                        - 即時 throughput、reliability 指標
                        - PPO vs ECMP 比較圖表
```

老師範例對照：

| 老師的例子 | 本專案 |
|---|---|
| 工廠機台 | NSFNET 的 arc（網路鏈路） |
| 機台感應器偵測是否在使用 | binomial 分布取樣 arc 容量狀態 |
| 雲端表單改變狀態 | Google Sheets 紀錄 arc 狀態歷史 |
| 網頁即時呈現哪些機台在工作 | 拓樸圖 arc 顏色：綠(正常)/黃(降速)/紅(故障) |
| 是否有車輛經過 | 是否有流量在該 arc 上傳輸（utilization > 0） |

---

## 系統架構

```
┌─────────────────────────────────────────────────┐
│  Level 1：Python 模擬器 (simulator.py)          │
│  ┌──────────────────────────────────────────┐   │
│  │  每 10 秒執行一輪：                       │   │
│  │  1. 對 20 條 arc 依 binomial 取樣容量     │   │
│  │  2. PPO model.predict() → 分流比例        │   │
│  │  3. ECMP baseline 計算                    │   │
│  │  4. 計算 throughput、utilization          │   │
│  └──────────────────────────┬───────────────┘   │
└─────────────────────────────┼───────────────────┘
                              │ POST (JSON)
                              ▼
┌─────────────────────────────────────────────────┐
│  Level 2 雲端：Google Apps Script               │
│  URL: https://script.google.com/macros/s/       │
│       AKfycbydtf5HG75RUEUF7gkrQLZs4S9tmsi-     │
│       uic_mqgEJjja5xYMnRfp8cYSOXkiVz1sZB8s/exec│
│  ┌──────────────────────────────────────────┐   │
│  │  doPost() → 寫入 Google Sheets           │   │
│  │  doGet()  → 回傳最新狀態 JSON 給前端     │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────┼───────────────────┘
                              │ fetch JSON
                              ▼
┌─────────────────────────────────────────────────┐
│  GitHub Pages：index.html                       │
│  • NSFNET 拓樸圖（SVG 動態著色）                │
│  • 即時指標：throughput、efficiency             │
│  • PPO vs ECMP 比較圖表（Chart.js）             │
│  • arc 狀態歷史表格                             │
│  • 自動每 10 秒 refresh                         │
└─────────────────────────────────────────────────┘
```

---

## 部署步驟

### Step 1：Google Apps Script 設定 ✅ 完成

- Apps Script URL：`https://script.google.com/macros/s/AKfycbydtf5HG75RUEUF7gkrQLZs4S9tmsi-uic_mqgEJjja5xYMnRfp8cYSOXkiVz1sZB8s/exec`
- Google Sheets ID：`1sv_yBP9L7b78qduXbA1ns47J0h_uUipQtvb6eji6W98`
- 已確認資料正常寫入 Sheets

### Step 2：Python 模擬器執行 ✅ 完成

所有必要檔案已在資料夾中：
- `nsfnet_multistate_arcs.csv` ✅
- `nsfnet_paths.csv` ✅
- `ppo_gnn_mss_best.zip` ✅
- `vecnormalize_stats.pkl` ✅

```bash
# 安裝依賴
pip install stable-baselines3 gymnasium pandas numpy requests torch

# 執行模擬器
python simulator.py
```

`simulator.py` 頂端設定：
```python
APPS_SCRIPT_URL  = "https://script.google.com/macros/s/AKfycby.../exec"  # ✅ 已填好
INTERVAL_SECONDS = 10   # 每幾秒推一次
MAX_ROUNDS       = 0    # 0 = 無限執行
```

### Step 3：GitHub Pages 部署 ✅ 完成

- Repository：`https://github.com/Grouper44/nsfnet-iot-dashboard`
- 頁面網址：`https://grouper44.github.io/nsfnet-iot-dashboard/`
- Settings → Pages → Branch: `main` 已設定

---

## Google Sheets 結構（自動建立）

### 工作表1（狀態快照，每輪一筆）

| 欄位 | 說明 |
|---|---|
| Timestamp | 時間戳記 |
| Round | 第幾輪 |
| PPO_Throughput | PPO agent 吞吐量 (Mbps) |
| ECMP_Throughput | ECMP 吞吐量 (Mbps) |
| PPO_Efficiency | PPO 效率 (%) |
| ECMP_Efficiency | ECMP 效率 (%) |
| Total_Available | 總可用容量 (Mbps) |
| Active_Arcs | 正常 arc 數 |
| Degraded_Arcs | 降速 arc 數 |
| Failed_Arcs | 故障 arc 數 |

### arc_log（arc 詳細歷史，每輪寫 20 筆）

| 欄位 | 說明 |
|---|---|
| Timestamp | 時間戳記 |
| Round | 第幾輪 |
| arc_id | arc 名稱（a1~a20） |
| capacity | 當前容量 (Mbps) |
| max_capacity | 最大容量 (Mbps) |
| utilization | PPO 使用率（0~1） |
| status | normal / degraded / failed |

---

## 目前進度

| 項目 | 狀態 |
|---|---|
| 資料夾建立 | ✅ 完成 |
| `simulator.py` | ✅ 完成，URL 已填入，測試通過（float32 序列化 bug 已修正，隨機 seed） |
| `apps_script.js` | ✅ 完成，SHEET_ID 已填入，資料已確認寫入 Sheets |
| `index.html` | ✅ 完成，重新設計（左右 10% 留白、美國地圖背景、arc 標籤不重疊） |
| `nsfnet_multistate_arcs.csv` | ✅ 已放入資料夾 |
| `nsfnet_paths.csv` | ✅ 已放入資料夾 |
| `ppo_gnn_mss_best.zip` | ✅ 已放入資料夾（git 忽略，不上傳） |
| `vecnormalize_stats.pkl` | ✅ 已放入資料夾（git 忽略，不上傳） |
| Google Sheets 資料寫入 | ✅ 已確認資料正常進來 |
| GitHub Pages 部署 | ✅ 已上線 `https://grouper44.github.io/nsfnet-iot-dashboard/` |

---

## 前端改版紀錄（index.html）

### v2（最新）
- 左右各留 10% 空白，所有內容在中間 80% 欄寬顯示
- 拓樸圖 viewBox 擴大至 1000×560
- 節點座標依照美國**實際地理位置**重新排布
  - Seattle（左上）→ Cambridge（右上）呈現由西向東的真實路徑走向
- 背景加入美國本土 SVG 輪廓、主要州界線、五大湖
- 每個節點顯示縮寫 + 完整城市名
- arc 標籤手動偏移到線旁，加半透明背景遮罩，不再重疊

### v1（初始版本）
- 滿版佈局，節點以邏輯位置排列
- arc 標籤位置未優化，部分重疊

---

## 注意事項

- `SHEET_ID` ≠ Apps Script URL。`SHEET_ID` 是 Google Sheets 試算表網址中 `/d/` 後面那段
- Apps Script 免費版每日執行次數上限 20,000 次，`INTERVAL_SECONDS = 10` 完全夠用
- 前端有 **Demo 模式**，按「Demo 模式」按鈕可在不連線 Apps Script 的情況下展示完整功能
- PPO model 載入使用 `device='cpu'`，不需要 GPU
