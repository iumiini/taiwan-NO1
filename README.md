# 台股寶可夢 — 圖鑑端

真實台股資料驅動的怪獸收集養成遊戲，本 repo 是**圖鑑端**（資料服務）。

圖鑑是整個專案的**單一真相來源**：每個交易日收盤後把股市資料換算成怪獸資料，
產出靜態 JSON 託管在 GitHub Pages。Unity 遊戲與圖鑑網頁都只是它的消費者。

> ⚠️ 「寶可夢」為任天堂商標，本名稱僅為開發期內部代號，公開發布前必須更名。

---

## 架構

```
GitHub Actions（每交易日 19:00 排程）
  ├ 拉 富果 snapshot + TWSE 財報／估值
  ├ StatMapper：怪獸化計算（在雲端執行）
  └ 產出 JSON → commit
        ↓
GitHub Pages（免費靜態託管）
  ├ /data/index.json    ← 遊戲每日先抓這個 1 KB 小檔
  ├ /data/species.json  ← 種族層，整季不變
  ├ /data/daily/*.json  ← 每日層
  ├ /data/balance.json  ← 平衡參數
  └ /index.html         ← 圖鑑網頁（B 階段）
```

**沒有伺服器程式在運作。** 運算在前一晚的排程就完成，寫成檔案。
遊戲端是下載檔案，不是呼叫 API —— 免費、不會當機、玩家數不放大成本。

---

## 目前進度

- [x] 富果 API 可行性驗證（2026/08/05）
- [x] TWSE 端點複驗
- [x] 資料契約 Schema v0.1.0 + 範例檔 + 驗證工具
- [x] 財報資料層（多業別合併、欄位容錯、季度快取沿用上一季）
- [x] 每日管線（選股 → 指標 → StatMapper → 輸出）
- [x] GitHub Actions 排程
- [ ] GitHub Pages 開啟
- [ ] 圖鑑網頁

---

## 目錄

| 路徑 | 內容 |
|---|---|
| `schema/` | JSON Schema（資料契約本體） |
| `examples/` | 範例資料檔，數值取自 2026-08-05 真實收盤 |
| `src/` | 管線程式 |
| `data/` | **輸入端**：平衡參數、屬性對照、招式模板、財報快取 |
| `site/data/` | **輸出端**：管線產出，GitHub Pages 託管給遊戲與網頁讀取 |
| `tools/` | 驗證與索引產生工具 |
| `docs/schema-guide.md` | **契約導覽 —— 先讀這份** |

---

## 快速開始

```bash
pip install jsonschema
python3 tools/validate.py             # 驗證範例檔
python3 tools/build_index.py examples # 重新產生索引
python3 -m src.fundamentals refresh   # 更新財報快取

# 跑完整管線（歷史 K 線需金鑰）
export FUGLE_API_KEY=<你的金鑰>
python3 -m src.pipeline --size 50
python3 tools/validate.py site/data   # 驗證產出
```

---

## 資料源

| 用途 | 來源 | 需金鑰 |
|---|---|---|
| 每日全市場行情 | 富果 `snapshot/quotes/TSE`、`/OTC` | **否** |
| 歷史 K 線回補（均線／KD／波動率） | 富果 `stock/historical/candles` | 是 |
| 大盤指數（世界天氣） | 富果 `stock/intraday/quote/IX0001` | 是 |
| **注意股／處置股**（超進化、禁閉室） | 富果 `stock/intraday/ticker/{代號}` | 是 |
| 本益比／殖利率／淨值比 | TWSE `BWIBBU_ALL` | 否 |
| 月營收＋YoY | TWSE `t187ap05_L` | 否 |
| 週轉率 | TWSE `FMSRFK_ALL` | 否 |
| 已發行股數（市值） | TWSE `t187ap03_L` | 否 |
| EPS | TWSE `t187ap06_L_*`（依業別分端點） | 否 |
| 負債比 | TWSE `t187ap07_L_*`（依業別分端點） | 否 |

⚠️ 財報端點務必用 `_L_`（上市公司）系列，不是 `_X_`（公發公司）。
詳見 `docs/schema-guide.md` 的「資料源陷阱」。

**每日排程不需要任何金鑰** —— 富果 snapshot 免金鑰即給全市場當日行情。
金鑰只在初次歷史回補（建 240 日年線基準）時使用一次。

### 金鑰處理

金鑰**絕不進版控**。本機開發放環境變數：

```bash
export FUGLE_API_KEY=<你的金鑰>
```

`.gitignore` 已擋掉 `.env`、`*.key`、`secrets/`。
若未來排程真的需要金鑰，走 GitHub Repository Secrets，不寫進程式碼。

---

## 設計文件

完整遊戲設計與規則書在 Notion《台股寶可夢 — 設計討論總結》。
本 repo 只負責圖鑑端；遊戲端（Unity）為另一專案。
