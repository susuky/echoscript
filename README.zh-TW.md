# echoscript

[English](README.md) | **繁體中文**

本機音訊／影片轉錄工作區，由 FastAPI 服務、React 前端及獨立 GPU 工作程序組成。支援日文、日中交替課程、中文及其他語言，提供術語提示、選用的講者分離，以及 TXT／SRT／VTT／JSON 匯出。

## 操作介面

介面預設為英文，可使用右上角的語言選單切換英文與繁體中文。瀏覽器會記住選擇；切換語言會保留目前表單及逐字稿，不會翻譯錄音或逐字稿內容。

![EchoScript 英文介面的檔案上傳與轉錄設定](docs/images/workspace-en.png)

<details>
<summary>繁體中文介面</summary>

![EchoScript 繁體中文介面](docs/images/workspace-zh.png)

</details>

## 快速開始

需求：Linux、Python 3.11、[uv](https://docs.astral.sh/uv/)、ffmpeg／ffprobe；預設設定需要 NVIDIA GPU。

```bash
uv sync && \
(test -e .env || cp .env.example .env) && \
chmod 600 .env && \
uv run --env-file .env --no-sync python serve.py
```

安裝失敗時，以上指令會停止後續步驟，且不會覆寫既有 `.env`。之後啟動可直接執行 `uv run --env-file .env --no-sync python serve.py`；依賴變更後須重新執行 `uv sync`。首次安裝需要連線 PyPI 及套件下載服務；第一次轉錄也會下載尚未快取的模型權重。

開啟 `http://127.0.0.1:7860`。範例監聽 `0.0.0.0`；如需僅供本機存取，設定 `ECHOSCRIPT_WEB_HOST=127.0.0.1`。這是沒有內建身分驗證的共用工作區，連線使用者能看見相同的工作與結果。分享時請使用可信任網路，或具身分驗證的反向代理。

預設安裝 Qwen3-ASR，其他後端為選用：

```bash
uv sync --extra faster-whisper --extra diarization
```

講者分離需要 `pyannote/speaker-diarization-community-1` 的存取權。先在 Hugging Face 接受模型使用條件，再執行 `hf auth login`，或在 `.env` 設定 `HF_TOKEN`。

### 安裝問題排查

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

長音檔逐片段保存辨識與對齊結果，支援進度、取消、續跑與局部重試。前端可播放原音、點選時間回聽、修正文字並同步更新匯出。切段與前文策略可設定；目前不宣稱單一策略適合所有語言。詳見[片段處理與核對](docs/segment-review.md)。

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

狀態包括 `uploading`、`queued`、`running`、`done`、`failed` 與 `cancelled`。已發布的部分結果也可核對；請依回傳的 `files` 判斷實際匯出格式，並查看片段進度及字幕缺口。回聽、取消、續跑與修正端點見[操作文件](docs/segment-review.md)。應用程式本身不要求 API key；反向代理的身分驗證須另行提供。

## CLI

```bash
uv run echoscript --help
uv run echoscript web
uv run echoscript list --languages
```

同步轉錄 CLI 使用 faster-whisper，會自行載入模型。使用前先安裝選用依賴：

```bash
uv sync --extra faster-whisper
uv run echoscript -a /path/to/audio.mp3 -m large-v3 -f txt -l ja -o transcript.txt
```

如需透過執行中的服務及共用模型快取進行自動化，請使用前述 HTTP API。

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

已驗證案例與限制見[辨識品質紀錄](docs/accuracy.md)。沒有人工參考稿時，成功轉錄及有效時間戳不足以建立實測字錯率或詞錯率。

## 文件維護

`README.md` 為預設英文文件，`README.zh-TW.md` 為對應繁體中文版。安裝流程、指令、選項或文件描述的行為有變更時，須在同一次修改中更新兩版，並保持範例及設定值一致。目前僅有繁體中文的詳細文件，會在英文連結中標示語言。

## 參考資料

- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)：多語辨識、情境提示與對齊。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)：VAD、術語提示與解碼設定。
- [WhisperX](https://github.com/m-bain/whisperX)：對齊與講者歸屬。
- [whisper-asr-webservice](https://github.com/ahmetoner/whisper-asr-webservice)：本機辨識的 HTTP 服務。
- [AmicoScript](https://github.com/sim186/AmicoScript)：本機轉錄工作區。
- [yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS)：YouTube JavaScript 執行環境需求。

## 授權

MIT
