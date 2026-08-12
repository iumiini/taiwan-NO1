# 台股寶可夢 — 圖鑑端

真實台股資料驅動的怪獸收集養成遊戲。本 repo 是圖鑑端（資料服務 + 圖鑑網頁），
遊戲端（Unity）為另一專案。詳見 `README.md` 與 `docs/schema-guide.md`。

---

## 協作規範

### Notion 是設計的唯一真相來源

| 用途 | 頁面 |
|---|---|
| 設計規格總表 | [設計討論總結](https://app.notion.com/p/396fd52e67648143845ed7a667dd85ba) |
| Q&A 協作紀錄 | [Claude 協作對話內容](https://app.notion.com/p/3bafd52e6764800c9fdcfd526923f090) |
| 專案頁 | [台股寶可夢](https://app.notion.com/p/395fd52e67648010a05cdcfe9bf9b8c9) |

專案頁的摺疊區塊**已凍結**，一切以《設計討論總結》為準。

### Q&A 流程（每次都要照做，不必等使用者提醒）

使用者在《Claude 協作對話內容》以 `Q :` 提問、留空的 `A :` 等待回填。
被要求讀取該頁時：

1. **先列出讀到的所有 Q**，向使用者確認清單，再開始回答
2. 在對話中完整回答
3. **主動把答案回填該頁的對應 `A :`** —— 這是流程的一部分，不是額外要求
4. 若答案牽涉設計規格的**新增或修正**，同步寫進《設計討論總結》新增章節
   （只新增，不動既有章節；標題註明日期）

回填用 `notion-update-page` 的 `update_content`，以 `Q : ...\n\tA :` 當
`old_str` 精準定位。寫入前務必先 `notion-fetch` 讀取現況。

### 誠實標示未定案內容

真實財報行情數字與遊戲規則的提案值必須可區分。遊戲規則尚未定案者一律標「暫定」，
由 `data/balance.json` 的 `status` 欄位驅動（`draft` / `playtest` / `locked`）。
原則：**數字是真的，換算成怪獸的規則還在調。**

不要把自己臨時擬的佔位值講得像既有設計。`data/moves.json` 的招式即為佔位樣板。

---

## 長時間任務的規範

管線跑 50 檔約需 5～7 分鐘。**曾因錯誤的等待方式浪費 20 分鐘並產生資料損毀**，
務必遵守：

- **背景執行後不要輪詢**。harness 會在任務完成時自動通知，寫 `sleep` 迴圈等待
  不但無效，迴圈結束時還會把管線的程序群一起收掉
- **不要用 `setsid` 迴避**。它會讓工具立刻回報「完成」，導致讀到舊產出而誤判
- **先小樣本驗證再跑全量**：`--size 5` 確認流程無誤，再跑 `--size 50`
- 管線已內建鎖檔（`.pipeline.lock`），同時啟動第二份會直接拒絕

---

## 專案關鍵事實

- **金鑰絕不進版控**。富果金鑰只在初次歷史回補需要；每日排程用免金鑰的
  snapshot 端點即可
- **財報快取必須 commit**（`data/fundamentals/`）。GitHub Actions 每次是全新容器
- **賽季名單一旦凍結就不再變動**（`data/universe/`）。實測六天內市值排名就會
  換掉成員，名單浮動會讓玩家的怪獸憑空消失
- **TWSE 財報用 `_L_` 系列**（上市公司），不是 `_X_`（公發公司）；欄位名會隨
  出表日變動，一律走別名表正規化
- `data/` 是輸入端，`site/data/` 是發布產物，兩者不要混用

## 常用指令

```bash
python3 tools/validate.py site/data          # 驗證產出
python3 -m src.fundamentals refresh          # 更新財報快取
python3 -m src.pipeline --size 50            # 跑管線
python3 -m src.pipeline --size 50 --refreeze # 重新凍結賽季名單
python3 -m http.server 8899 --directory site # 本機預覽圖鑑頁
```
