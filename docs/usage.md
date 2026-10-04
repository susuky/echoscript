# 使用指南

首次使用請先完成 [README 的安裝與啟動步驟](../README.zh-TW.md)。以下指令除另有註明，均在專案目錄執行。

## 選用模型與講者分離

預設安裝 Qwen3-ASR，其他後端為選用：

```bash
uv sync --extra faster-whisper --extra diarization
```

更新依賴時，請沿用所選的 `--extra` 參數；單獨執行 `uv sync` 會移除未選用的額外套件。

講者分離需要 `pyannote/speaker-diarization-community-1` 的存取權。先在 Hugging Face 接受模型使用條件，再執行 `hf auth login`，或在 `.env` 設定 `HF_TOKEN`。

## 安裝問題排查

若 `uv sync` 在下載 `https://pypi.org/simple/setuptools/` 時出現連線逾時，代表建置依賴下載失敗。接著出現的 `ModuleNotFoundError: No module named 'echoscript'` 是安裝未完成的連帶結果：`--no-sync` 會略過依賴同步。請先讓 `uv sync` 成功，再啟動服務；這個錯誤不需要刪除 `.venv` 或修改程式的匯入路徑。

在同一個 Linux／WSL 終端機確認實際目錄及網路：

```bash
pwd -P
curl --fail --show-error --location --connect-timeout 15 --max-time 60 \
  --output /dev/null https://pypi.org/simple/setuptools/
```

若此請求也逾時，請檢查該環境的 DNS、網路、防火牆及代理設定。Windows 瀏覽器能連線，不代表 WSL 也能連線。實際目錄位於 `/mnt/c/` 可能解釋提示字元與錯誤紀錄的路徑差異，但單憑此路徑無法解釋 PyPI 連線逾時。

若連線可達但速度較慢，可延長連線及讀取逾時後重試：

```bash
UV_HTTP_CONNECT_TIMEOUT=30 UV_HTTP_TIMEOUT=120 uv sync && \
(test -e .env || cp .env.example .env) && \
chmod 600 .env && \
uv run --env-file .env --no-sync python serve.py
```

若網路需要代理，請在該終端機設定 `HTTPS_PROXY`／`HTTP_PROXY`，並使用 Linux／WSL 能連到的位址。延長逾時無法修復無法連線的代理或遭封鎖的路由。安裝相關變數須設定在執行 `uv sync` 的 shell；應用程式的 `.env` 是稍後由 `uv run --env-file` 載入。逾時及代理設定詳見 [uv 環境變數文件](https://docs.astral.sh/uv/configuration/environment/)。索引連線成功只是第一步，安裝也需要連到 `files.pythonhosted.org` 等套件下載主機。

## 轉錄選項

- **日文：** 錄音僅包含日文時，選擇日文。
- **日中混合課程：** 保持自動語言辨識，並加入日文講師與中文口譯交替發言的課程背景。
- **混合語言：** 中英交替或其他組合使用自動辨識。
- **背景與詞庫：** 在背景欄描述錄音主題，將人名、術語、品牌及正確拼寫放入詞庫。各後端會限制提示預算；提示不保證辨識正確。
- **參考前文：** 預設開啟；若輸出重複，可關閉後比較。短切段或關閉前文不一定較好。
- **時間戳與講者：** 需要字幕時開啟時間戳；講者分離也需要時間戳及選用的講者分離依賴。

日文及包含日文的混合逐字稿保留原始字形，略過繁中轉換，因此其中的中文段落可能維持簡體。對齊失敗會保留辨識文字及可靠片段；字幕僅匯出通過最終驗收的段落，並在介面標示缺口。

長音檔逐片段保存辨識與對齊結果，支援進度、取消、續跑與局部重試。前端可播放原音、點選時間回聽、修正文字並同步更新匯出。切段與前文策略可設定；目前不宣稱單一策略適合所有語言。詳見[片段處理與核對](segment-review.md)。

## 作為服務執行

網頁／API 程序啟動時不載入辨識模型。有工作進入時，由工作程序載入模型並供後續工作重用；**閒置 300 秒**後退出並釋放 GPU 記憶體。新的請求會自動啟動新的工作程序。

在 `.env` 設定 `ECHOSCRIPT_MODEL_IDLE_TIMEOUT_SECONDS` 可調整閒置時間；設為 `0` 會在每份工作結束後退出。切換模型可能需要重新載入；切換辨識後端會啟動新的工作程序。`ECHOSCRIPT_RELEASE_BETWEEN_STAGES=true` 會在辨識與講者分離階段之間釋放模型，以降低 GPU 記憶體佔用。工作失敗時會先清除模型快取，再處理下一份工作。

### 使用者服務

服務以你的帳號執行，沿用既有模型快取。啟動前，將範本中的 `/path/to/echoscript` 換成專案的絕對路徑。

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/echoscript-user.service.example ~/.config/systemd/user/echoscript.service
# 修改複製檔案中的 WorkingDirectory、EnvironmentFile 與 ExecStart。
systemctl --user daemon-reload
systemctl --user enable --now echoscript.service
systemctl --user status echoscript.service
journalctl --user -u echoscript.service -f
```

如需開機啟動，並在登出後持續執行，請為帳號啟用 lingering；此步驟可能需要管理員權限：

```bash
loginctl enable-linger "$USER"
```

### 系統服務

系統層級安裝使用另一份範本。將 `YOUR_USER`、`YOUR_GROUP` 及 `/path/to/echoscript` 換成你的帳號、群組及專案路徑。

```bash
cp deploy/systemd/echoscript.service.example /tmp/echoscript.service
# 編輯複製的 unit 檔案，再安裝。
sudo cp /tmp/echoscript.service /etc/systemd/system/echoscript.service
sudo systemctl daemon-reload
sudo systemctl enable --now echoscript.service
```

兩份範本都直接使用專案的虛擬環境。啟動服務前須先執行 `uv sync`；修改 `.env` 或更新程式後，重新啟動已安裝的服務。`KillMode=control-group` 確保停止服務時也會停止工作程序。

## HTTP API

自動化可使用 HTTP API，與網頁工作區共用佇列、模型、閒置時間及結果，不需要操作瀏覽器。互動式 API 文件位於 `/docs`，OpenAPI 文件位於 `/openapi.json`。

| 方法 | 端點 | 用途 |
| --- | --- | --- |
| GET | `/api/config` | 可用選項及上傳限制 |
| POST | `/api/jobs` | 排入公開網址工作或準備檔案上傳 |
| PUT | `/api/jobs/{id}/media` | 串流傳送已建立上傳工作的原始位元組 |
| GET | `/api/jobs?limit=50` | 列出近期工作 |
| GET | `/api/jobs/{id}` | 讀取狀態、階段及錯誤 |
| GET | `/api/jobs/{id}/result` | 讀取完成的逐字稿及可用匯出 |
| GET | `/api/jobs/{id}/files/{format}` | 下載 `txt`、`srt`、`vtt` 或 `json` |

### 提交網址

```bash
ECHOSCRIPT_URL=http://127.0.0.1:7860
curl --fail-with-body "$ECHOSCRIPT_URL/api/jobs" \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"url","url":"https://www.youtube.com/watch?v=VIDEO_ID","options":{"language":"ja","timestamps":true,"diarize":false,"context":"A language lesson","glossary":"EchoScript"}}'
```

回應包含 `id`。日中混合課程使用 `"language":"ja-zh"`，中文使用 `"zh"`，自動語言辨識使用 `null`。`GET /api/config` 列出可用的語言及模型。

### 上傳本機檔案

先建立上傳工作，再傳送檔案位元組；只有成功完成上傳後，工作才會進入佇列。

```bash
ECHOSCRIPT_URL=http://127.0.0.1:7860
JOB_ID=$(curl --fail-with-body "$ECHOSCRIPT_URL/api/jobs" \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"upload","filename":"recording.wav","options":{"language":"ja","timestamps":true}}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl --fail-with-body -X PUT "$ECHOSCRIPT_URL/api/jobs/$JOB_ID/media" \
  -H 'Content-Type: application/octet-stream' \
  --data-binary @/path/to/recording.wav
```

### 查詢進度與下載

```bash
curl --fail-with-body "$ECHOSCRIPT_URL/api/jobs/$JOB_ID"
# 重複查詢，直到狀態為 "done"、"failed" 或 "cancelled"。
curl --fail-with-body "$ECHOSCRIPT_URL/api/jobs/$JOB_ID/result"
curl --fail-with-body "$ECHOSCRIPT_URL/api/jobs/$JOB_ID/files/txt" -o transcript.txt
curl --fail-with-body "$ECHOSCRIPT_URL/api/jobs/$JOB_ID/files/srt" -o transcript.srt
```

狀態包括 `uploading`、`queued`、`running`、`done`、`failed` 與 `cancelled`。已發布的部分結果也可核對；請依回傳的 `files` 判斷實際匯出格式，並查看片段進度及字幕缺口。回聽、取消、續跑與修正端點見[操作文件](segment-review.md)。應用程式本身不要求 API key；反向代理的身分驗證須另行提供。

## CLI

完成安裝後，在專案目錄執行以下指令。`--env-file .env` 會載入設定；EchoScript 不會自動讀取 `.env`。使用 CLI 轉錄不需要先啟動網頁服務。

### 轉錄錄音或公開網址

預設使用 **Qwen3-ASR-1.7B**，與網頁工作區相同。首次執行會下載尚未快取的模型。不指定輸出選項時，指令會印出純文字：

```bash
uv run --env-file .env --no-sync echoscript transcribe recording.mp3
```

可先使用專案附帶的英文範例，保存所有可用格式：

```bash
uv run --env-file .env --no-sync echoscript transcribe This_is_an_example.mp3 \
  --language en --format all --output-dir transcripts/example
```

這會在 `transcripts/example` 寫入 `result.txt`、`result.srt`、`result.vtt` 與 `result.json`。字幕只包含通過時間檢查的段落。每份錄音請使用不同目錄；若要取代既有匯出檔，加入 `--overwrite`。

也可以用公開 YouTube 網址或直接提供媒體檔的 HTTP(S) 網址取代檔名：

```bash
uv run --env-file .env --no-sync echoscript transcribe \
  'https://www.youtube.com/watch?v=VIDEO_ID' \
  --format all --output-dir transcripts/video
```

網址下載沿用網頁工作區的驗證與下載限制。其他串流平台須先下載檔案。需要登入的媒體，請在已登入瀏覽器的電腦執行 `echoscript download URL --browser chrome --output-dir downloads`，再轉錄下載好的檔案。詳見[下載指南](#私人與會員限定影片)。

### 選擇輸出與轉錄選項

`--output` 保存單一格式。未指定 `--format` 時，輸出檔的 `.txt`、`.srt`、`.vtt` 或 `.json` 副檔名會決定格式。搭配 `--output-dir` 並重複指定 `--format`，可保存所選格式：

```bash
uv run --env-file .env --no-sync echoscript transcribe recording.mp4 \
  --output transcript.srt --no-verbose

uv run --env-file .env --no-sync echoscript transcribe recording.wav \
  --format txt --format json --output-dir transcripts/recording

uv run --env-file .env --no-sync echoscript transcribe recording.wav \
  --no-timestamps --format json > transcript.json
```

單一格式的結果會送到 stdout，工作 ID 與狀態訊息送到 stderr。`--no-verbose` 停用逐字稿列印，`--quiet` 隱藏工作狀態訊息。選擇多種格式時，只寫入指定目錄，不列印逐字稿。既有匯出檔須指定 `--overwrite` 才能覆寫，且不能將輸入媒體檔當成輸出檔。

日文使用 `--language ja`，中文使用 `--language zh`。混合語言可省略此選項，或指定 `auto`。`ja-zh` 會使用自動辨識，並加入日中雙語課程提示：

```bash
uv run --env-file .env --no-sync echoscript transcribe lesson.m4a \
  --language ja-zh --context 'A Japanese instructor and a Chinese interpreter alternate.' \
  --glossary 'EchoScript, Yamada, motor learning' \
  --format all --output-dir transcripts/lesson
```

| 選項 | 用途／預設值 |
| --- | --- |
| `--backend qwen\|faster-whisper` | 預設 Qwen；指定已知的 Whisper `--model` 名稱，也會選用 faster-whisper |
| `--model NAME` | `Qwen/Qwen3-ASR-1.7B`，或 faster-whisper 的 `large-v3-turbo`；可使用模型 ID 或本機模型路徑 |
| `--language CODE` | 預設自動辨識；也接受語言名稱及區域代碼 |
| `--context TEXT` / `--glossary TEXT` | 錄音背景／人名與專有名詞 |
| `--timestamps` / `--no-timestamps` | 預設開啟時間戳；SRT/VTT 與講者分離需要此功能 |
| `--diarize` | 講者分離，預設停用 |
| `--min-speakers N` / `--max-speakers N` | 選用的講者人數範圍，須搭配 `--diarize` |
| `--zh-script tw\|twp\|none` | 預設繁體中文（`tw`）；`none` 保留原始字形 |
| `--no-condition-on-previous-text` | 關閉預設的參考前文功能 |
| `--chunk-seconds N` / `--chunk-strategy energy\|fixed` | 預設 Qwen 最長 60 秒／Whisper 最長 300 秒，優先在低音量處切段 |
| `--context-token-budget N` / `--glossary-token-budget N` | 調整後端提示預算；接受 0 至 8192 的整數 |
| `--device DEVICE` / `--compute-type TYPE` | 預設 `cuda`／`float16`；計算型別只適用 faster-whisper |

使用 `--no-timestamps` 時，`--format all` 只匯出 TXT 與 JSON。Qwen 片段上限為需要時間戳時 180 秒、不需要時 1200 秒。日文及包含日文的混合逐字稿保留原始字形，不套用繁中轉換。提示不保證辨識正確。

### 選用模型與講者分離

安裝需要的額外依賴，之後執行 `uv sync` 時也須保留所選參數：

```bash
uv sync --extra faster-whisper --extra diarization

uv run --env-file .env --no-sync echoscript transcribe recording.wav \
  --backend faster-whisper --model large-v3-turbo \
  --format all --output-dir transcripts/whisper

uv run --env-file .env --no-sync echoscript transcribe meeting.wav \
  --diarize --min-speakers 2 --max-speakers 4 \
  --format all --output-dir transcripts/meeting
```

講者分離另需 `pyannote/speaker-diarization-community-1` 的存取權：先在 Hugging Face 接受模型使用條件，再執行 `hf auth login`，或在 `.env` 設定 `HF_TOKEN`。

### 查詢、續跑與匯出已保存的工作

CLI 與網頁工作共用資料目錄及工作程序鎖定。其他工作程序忙碌時，CLI 會等待。CLI 自己載入的模型會在該份工作結束後釋放。媒體、辨識片段與可用匯出檔保存在 `$ECHOSCRIPT_DATA_DIR/jobs/<job-id>/`（預設資料目錄為 `~/.local/share/echoscript`）。

指令會將工作 ID 印到 stderr。按 `Ctrl+C` 可要求停止並保留已完成的片段。沿用相同 `.env` 與資料目錄，以該 ID 查詢或續跑：

```bash
uv run --env-file .env --no-sync echoscript jobs --limit 10
uv run --env-file .env --no-sync echoscript jobs JOB_ID
uv run --env-file .env --no-sync echoscript resume JOB_ID \
  --format all --output-dir transcripts/resumed
uv run --env-file .env --no-sync echoscript export JOB_ID \
  --output transcript.json --no-verbose
```

`jobs` 回傳 JSON。`resume` 沿用已完成的辨識，並以原設定重試尚未完成的辨識或不可靠的字幕時間。`export` 讀取既有結果，不載入模型，也可匯出已保存的部分結果。請將匯出檔寫到工作保存目錄之外，以保留片段進度與不可變的結果版本。

工作失敗、辨識不完整，或所要求的字幕不可用／不完整時，指令會回傳非零結束碼。可用匯出檔與片段進度仍會保留；確認工作狀態或續跑後，再將輸出視為完整結果。辨識完整但字幕時間只有部分可用時，TXT/JSON 仍可成功匯出。

### 說明與相容用法

```bash
uv run --no-sync echoscript --help
uv run --no-sync echoscript transcribe --help
uv run --no-sync echoscript list --models
uv run --no-sync echoscript list --languages --backend qwen
uv run --no-sync echoscript --version
```

舊的 `echoscript -a FILE` 語法與 `--model-name`、`--lang`、`--fmt`、`--filename` 別名仍可使用，現在沿用目前處理流程，預設改為 Qwen。舊語法若明確指定 `-m`，會選用 faster-whisper；模型名稱以 `Qwen/` 開頭或已指定 `--backend` 時，則依該設定。Whisper 仍須安裝其額外依賴。新腳本建議使用 `transcribe`。

### 私人與會員限定影片

帳號必須已具有影片觀看權限。請在瀏覽器已登入該帳號的電腦下載，再透過網頁介面或 HTTP API 上傳媒體檔：

```bash
uv run echoscript download 'https://www.youtube.com/watch?v=VIDEO_ID' \
  --browser chrome --profile 'Default' --output-dir ./downloads
```

使用能播放該影片的瀏覽器設定檔。登入 Cookie 僅在本機下載時使用，轉錄 API 只接收媒體檔。下載檔名具有唯一識別，避免不同影片誤用舊檔。

私人且可信任的部署可由管理員啟用伺服器已登入的瀏覽器，供 YouTube 影片連結使用：

```dotenv
ECHOSCRIPT_YOUTUBE_BROWSER=chrome
ECHOSCRIPT_YOUTUBE_BROWSER_PROFILE=Default
ECHOSCRIPT_YOUTUBE_BROWSER_KEYRING=
```

變更後須重新啟動服務。Chrome 必須安裝在**伺服器**上，並由與服務相同的作業系統使用者登入具有權限的帳號；服務也需要存取該使用者的瀏覽器 Cookie 金鑰環。在另一台用戶端電腦登入 YouTube，不代表伺服器已登入。

Linux 上的 `uv sync` 也會安裝 `secretstorage`，供讀取 Chrome 的 GNOME 金鑰環。若自動選擇金鑰環失敗，設定 `ECHOSCRIPT_YOUTUBE_BROWSER_KEYRING=gnomekeyring`，並使用 `chrome://version` 顯示的完整 Profile Path。金鑰環設定留空時維持自動選擇。請從與 Chrome 相同的已登入桌面工作階段啟動服務，讓它能存取已解鎖的金鑰環及該工作階段的 D-Bus。系統服務不會自動繼承桌面工作階段；僅設定瀏覽器路徑仍不足以取得登入狀態。

可在該桌面的終端機進入專案目錄，替換設定檔路徑後執行下列診斷。此指令只讀取影片資訊，不下載媒體；成功列出格式還不代表完整媒體下載已驗證。

```bash
uv run --env-file .env --no-sync python -m yt_dlp \
  --ignore-config \
  --cookies-from-browser "chrome+gnomekeyring:/home/YOUR_USER/.config/google-chrome/Default" \
  --simulate --no-playlist "https://www.youtube.com/watch?v=VIDEO_ID"
```

使用 `python -m yt_dlp` 可讓下載器與 Python 依賴維持在同一環境。單次 `uv run --with secretstorage` 測試成功，不會把該依賴安裝到專案的持久環境；更新專案後請執行 `uv sync`，並保留你使用的選用依賴參數。修改依賴或登入設定後，須停止並重新啟動原服務。

此功能預設停用。啟用後，任何能向此共用工作區提交工作的使用者，都能要求下載設定帳號可觀看的影片，因此僅應供可信任使用者使用。工作 API 不能指定瀏覽器或設定檔。只有辨識為單一影片的 YouTube 網址會使用瀏覽器登入；其他網站維持公開下載流程，且下載器記憶體中的非 YouTube Cookie 會被移除。

伺服器端公開下載支援 YouTube 及直接 HTTP(S) 媒體檔。每次連線（含重新導向）都會檢查 DNS 位址。不支援的 HLS／DASH 串流或其他平台須先在本機下載，再上傳檔案。YouTube 網址中的 `t=11s` 等參數不會裁切錄音，系統會轉錄整部影片。

固定版本的 yt-dlp 依賴包含官方 EJS 腳本及 Deno 執行環境。拉取依賴變更後請執行 `uv sync`；調整 yt-dlp 固定版本前，須重新執行下載安全測試。

## 設定

將 `.env.example` 複製為 `.env`。手動執行時以 `uv run --env-file .env` 載入；systemd 透過 `EnvironmentFile` 載入。應用程式不會自動讀取 `.env`。

| 變數 | 預設值／用途 |
| --- | --- |
| `ECHOSCRIPT_DATA_DIR` | `~/.local/share/echoscript`；永久保存工作資料 |
| `ECHOSCRIPT_WEB_HOST` / `ECHOSCRIPT_WEB_PORT` | `0.0.0.0` / `7860` |
| `ECHOSCRIPT_MODEL_IDLE_TIMEOUT_SECONDS` | `300`；模型閒置保留時間，`0` 表示工作程序立即退出 |
| `ECHOSCRIPT_RELEASE_BETWEEN_STAGES` | `true`；降低辨識與講者分離之間的 GPU 佔用 |
| `ECHOSCRIPT_MAX_UPLOAD_BYTES` | `0`；不限檔案大小，設為正整數位元組可限制 |
| `ECHOSCRIPT_MAX_REMOTE_DOWNLOAD_BYTES` | `0`；不限檔案大小，設為正整數位元組可限制 |
| `ECHOSCRIPT_MAX_MEDIA_DURATION_SECONDS` | `43200`（12 小時） |
| `ECHOSCRIPT_JOB_RETENTION_DAYS` | `30`；非正數停用已結束工作的清理 |
| `HF_TOKEN` | 選用的 Hugging Face 憑證；也支援 CLI 登入快取 |

工作保存在 `$ECHOSCRIPT_DATA_DIR/jobs/<job-id>/`。上傳先寫入暫存檔，完成後才發布；失敗上傳會移除，停滯的上傳及過期的完成／失敗／取消工作會定期清理。新結果以工作目錄中的不可變版本保存，下載連結綁定版本；舊結果仍可讀取。同一資料目錄一次只允許一個派送程序及一個工作程序持有。

## 前端開發

建置產物已包含在 Python 套件中，由 FastAPI 提供；執行已建置的介面不需要 Node.js。

```bash
cd frontend
npm ci
npm run dev
npm run build
```

Vite 在 `127.0.0.1:5173` 執行，將 `/api` 代理至 `http://127.0.0.1:7860`；前端開發時請在該位址啟動後端。正式建置產物寫入 `src/echoscript/web_assets/`。

## 測試與辨識品質

```bash
uv sync --extra faster-whisper --extra diarization
uv run --no-sync python -m pytest
```

一般測試使用替代模型與小型媒體測試資料，不下載模型。真實模型測試須明確啟用：

```bash
ECHOSCRIPT_RUN_INTEGRATION=1 \
ECHOSCRIPT_E2E_MEDIA=/path/to/sample.wav \
ECHOSCRIPT_E2E_BACKEND=qwen \
ECHOSCRIPT_E2E_MODEL=Qwen/Qwen3-ASR-1.7B \
ECHOSCRIPT_E2E_LANGUAGE=ja \
uv run --no-sync python -m pytest -m integration test/integration/test_real_pipeline.py
```

已驗證案例與限制見[辨識品質紀錄](accuracy.md)。沒有人工參考稿時，成功轉錄及有效時間戳不足以建立實測字錯率或詞錯率。
