# 環境與聊天工具

每家公司的東西放在哪裡、用哪種聊天工具都不一樣。第一天就弄清楚你在哪一種，寫進 `workplace.toml`。不要從上一家公司推。

## 四種環境

可以同時存在：一個系統是直接的，另一個在 VPN 後面。

| 型態 | 特徵 | 代理怎麼做事 | 要記的 |
| --- | --- | --- | --- |
| 直接 | 程式碼 clone 在自己的機器，系統用公司帳號登入就到 | 直接在倉庫裡工作 | 哪些資料不能留在本機 |
| 網路閘 | 系統在一層或多層 VPN、跳板機後面 | 每個系統記走哪條路；動手前先探測連不連得到 | 哪個 VPN 對哪個系統、能不能同時開、登入多久過期、出口地區有沒有限制 |
| 封閉桌面 | 程式碼與工具都在雲桌面或遠端桌面裡，東西不能帶出來 | 代理在桌面裡跑，或在外面透過一條公司允許的指令通道操作 | 什麼可以跨出邊界（沒寫明就當作什麼都不行）、通道是誰允許的、斷線後怎麼接回 |
| 經手他人 | 自己碰不到正式環境，改動要開單由別人執行 | 產出工單與腳本；驗證靠對方的回條，加上自己看得到的結果 | 工單的格式、誰執行、回條長什麼樣、多久沒回算逾時 |

每個系統一筆 `[[system]]`：

```toml
[[system]]
id = "tracker"
what = "工單系統"
reach = "vpn:office"           # direct | vpn:<名稱> | desktop:<名稱> | via:<主機> | person:<誰>
hosts = []                     # 你的哪幾台機器到得了；留空＝任何一台
login = "單一登入加一次性驗證碼"    # 怎麼登入，不寫帳密
probe = "curl -fsS -m 5 -o /dev/null https://tracker.example/health"
may_leave = "notes"            # nothing | notes | code | anything
src = "誰說的、什麼時候"
```

- `wm access check` 跑探測。連不到的系統是 `unreachable`，結果是 `UNVERIFIED`：連不到不等於沒資料。
- 你有好幾台機器時，把握著登入狀態、跑長時間服務的那一台設成 `resident_host`。其他機器透過它做事，不各自登入一份。
- 被登入頁、鎖定畫面、過期的工作階段擋住：自動處理一次。還是不行就交給使用者，不重試。
- 封閉桌面的專案，留在外面的東西只記結構與結論，不貼程式碼與內部資料。照每個系統的 `may_leave` 做。
- 經手他人時，寫入就是一個 `other` 類的動作（`wm action send --kind other --target "<哪個佇列或誰>" < 工單內容`）。這類動作一定先給使用者核准。有指令能開單，就接在 `--` 後面，使用者會連同內容一起看到；由人開單的話，事後用 `wm action resolve <編號> --state sent --proof <對方的回條>` 結案。對會顯示結果的地方掛一個 `wm watch`。

## 聊天工具

可能是 Slack、Teams、Telegram、釘釘、飛書、企業微信，也可能是公司自己做的。方法不綁任何一家。每個群一筆 `[[channel]]`：

```toml
[[channel]]
id = "release"
kind = "slack"                 # 只是標示：slack | teams | telegram | dingtalk | lark | wecom | custom
what = "發版通知"
mention = "@\\S+"              # 草稿裡什麼算提及
threads = true
max_chars = 4000
send = "tools/chat send --channel release"      # 訊息內容從 stdin 進來
readback = "tools/chat find --channel release"  # 要印出剛送出那則的編號，這次送出才算驗證過
src = "誰說的、什麼時候"
```

- 草稿一律寫成純文字加 `@名字`。送出的指令負責轉成平台自己的標記，所以公司換工具時，規則與草稿不用改。
- `wm action send --kind chat --target <群的代號>` 會照規則與這筆設定檢查草稿，再走核准流程。`wm` 自己沒有連接器，只執行 `send`。
- 沒有公開介面的自研工具：先做唯讀的擷取，因為時間大多花在讀。送出仍然是預覽、核准、一次、讀回；做不到讀回，送出的指令可以只把核准過的文字放進輸入框就停，由使用者按送出。
- 用個人帳號做自動化之前，先確認公司與那個平台都允許，把答案記在 team skill 的硬規則裡。

各平台的細節（官方介面提供什麼、提及的標記、長度上限）在 workplace-mapping 倉庫的 `docs/adapters.md`。
