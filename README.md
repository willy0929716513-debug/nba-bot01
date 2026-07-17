# nba-bot01

NBA 賠率分析機器人：抓取即時賠率與球隊戰績，套用蒙地卡羅模擬與 Kelly 準則計算推薦注碼，每日透過 Discord Webhook 推播；並附一個讀取同一份資料的靜態網頁儀表板。

## 功能

- **例行賽推薦**：結合模型預測與市場共識線（`nba_bot.py`），只推送 Edge ≥ 6% 的盤口，並用蒙地卡羅模擬估計覆蓋機率、Kelly 準則建議注碼
- **傷兵調整**：即時爬取 RotoWire 傷兵報告，依球星/主力等級套用不同扣分
- **夏季聯賽觀察**（`analyze_summer_league`）：抓 ESPN 比分與 The Odds API 盤口，產出戰績排行與盤口觀察名單；因陣容多為菜鳥/雙向合約、樣本數小，僅供參考，不計入 Kelly 資金配置
- **歷史績效追蹤**：正式執行（GitHub Actions 排程）時將 💎頂級 等級的例行賽推薦、以及 Edge ≥ 6% 的夏季聯賽推薦（無 Kelly 資金配置）分開寫入 GitHub Gist，各自累積勝率/損益統計
- **網頁儀表板**（`docs/index.html`）：純靜態頁面，讀取每次執行輸出的 `docs/data/latest.json`，顯示今日推薦、歷史績效與夏季聯賽分析；透過 GitHub Pages 直接服務 `/docs` 資料夾

## 執行方式

由 `.github/workflows/nba_odds_bot.yml` 排程觸發（每日 UTC 22:00），也可用 workflow_dispatch 手動測試執行（測試執行不會寫入歷史紀錄，也會標示為「測試版本」）。

## 所需環境變數 / Secrets

| 變數 | 用途 |
|---|---|
| `ODDS_API_KEY` | [The Odds API](https://the-odds-api.com/) 金鑰，抓例行賽與夏季聯賽盤口 |
| `DISCORD_WEBHOOK` | 推播結果用的 Discord Webhook URL |
| `GH_TOKEN` | 具 gist 權限的 GitHub token，讀寫歷史績效 Gist |
| `BALLDONTLIE_KEY` | [balldontlie](https://www.balldontlie.io/) API 金鑰，抓即時戰績用於動態調整球隊評分（未設定時使用 `FALLBACK_RATINGS` 靜態評分） |

## 網頁版

啟用 GitHub Pages（Settings → Pages → Source: Deploy from a branch，選這個分支、資料夾選 `/docs`）後，網址為：

`https://<你的 GitHub 帳號>.github.io/nba-bot01/`

每次 GitHub Actions 執行完都會自動把最新的 `docs/data/latest.json` commit 回分支，網頁會同步更新。

## 免責聲明

本專案僅供研究與參考，所有推薦與分析不構成投注建議，請自行評估風險並遵守當地法規。
