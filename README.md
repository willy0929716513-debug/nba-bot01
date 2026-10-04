# nba-bot01

NBA 賠率分析機器人：抓取即時賠率與球隊戰績，套用蒙地卡羅模擬與 Kelly 準則計算推薦注碼，每日透過 Discord Webhook 推播；並附一個讀取同一份資料的靜態網頁儀表板。

## 功能

- **例行賽推薦**：結合模型預測與市場共識線（`nba_bot.py`），只推送 Edge ≥ 6% 的盤口，並用蒙地卡羅模擬估計覆蓋機率、Kelly 準則建議注碼
- **串關推薦**：從當天 Edge ≥ 6% 推薦中的 💎頂級 等級裡挑出最多 3 場組成串關（取勝率最高者優先），求穩不求大，並明確提示串關全部腿都要命中才算贏
- **今日賽程**：列出當天全部例行賽賽事（不限於有推薦的場次），比賽結束後自動帶入比分
- **傷兵調整**：即時爬取 RotoWire 傷兵報告，依球星/主力等級套用不同扣分
- **夏季聯賽觀察**（`analyze_summer_league`）：僅在 6-8 月（`SUMMER_LEAGUE_MONTHS`）執行，抓 ESPN 比分與 The Odds API 盤口，產出戰績排行與盤口觀察名單；因陣容多為菜鳥/雙向合約、樣本數小，僅供參考，不計入 Kelly 資金配置。其餘月份完全不會呼叫對應 API，網頁與 Discord 訊息也不會顯示這個區塊
- **自動核對戰績**：每次執行會用 [balldontlie](https://www.balldontlie.io/) 的完整賽季比分，自動把之前「待開獎」的例行賽推薦結算成獲勝/落敗/走盤，並記錄實際比分，不需要再手動去 Gist 修改
- **歷史績效追蹤**：正式執行（GitHub Actions 排程）時將 💎頂級 等級的例行賽推薦、以及 Edge ≥ 6% 的夏季聯賽推薦（無 Kelly 資金配置）分開寫入 GitHub Gist，各自累積勝率/損益統計
- **網頁儀表板**（`docs/index.html`）：純靜態頁面，讀取每次執行輸出的 `docs/data/latest.json`，顯示今日推薦、串關建議、今日賽程、歷史績效與夏季聯賽分析（夏聯分頁僅在開打期間顯示）；透過 GitHub Pages 直接服務 `/docs` 資料夾

## 執行方式

由 `.github/workflows/nba_odds_bot.yml` 排程觸發（每日 UTC 22:00），也可用 workflow_dispatch 手動測試執行（測試執行不會寫入歷史紀錄，也會標示為「測試版本」）。

## 所需環境變數 / Secrets

| 變數 | 用途 |
|---|---|
| `ODDS_API_KEY` | [The Odds API](https://the-odds-api.com/) 金鑰，抓例行賽與夏季聯賽盤口 |
| `DISCORD_WEBHOOK` | 推播結果用的 Discord Webhook URL |
| `GH_TOKEN` | 具 gist 權限的 GitHub token，讀寫歷史績效 Gist |
| `BALLDONTLIE_KEY` | [balldontlie](https://www.balldontlie.io/) API 金鑰，抓整季賽程與比分，用於動態調整球隊評分、自動核對戰績、產生今日賽程（未設定時戰績評分改用 `FALLBACK_RATINGS` 靜態評分，賽程與自動核對戰績功能也無法運作） |

## 網頁版

啟用 GitHub Pages（Settings → Pages → Source: Deploy from a branch，選這個分支、資料夾選 `/docs`）後，網址為：

`https://<你的 GitHub 帳號>.github.io/nba-bot01/`

每次 GitHub Actions 執行完都會自動把最新的 `docs/data/latest.json` commit 回分支，網頁會同步更新。

## 免責聲明

本專案僅供研究與參考，所有推薦與分析不構成投注建議，請自行評估風險並遵守當地法規。
