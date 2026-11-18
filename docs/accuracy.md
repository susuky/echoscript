# 辨識品質紀錄

本次重點為純日文、日中混合課程，以及繁中／中英混合的專有名詞。改善分成「修復輸出損壞」與「改善模型輸入」。

## 已實作

- Qwen 時間對齊保留 ASR 原文的字形、標點、金額與空白；對齊器的清洗字串用來對應原文。
- 中文與英文混合字幕不任意插入空白；日文及含日文的混合稿不套用整篇繁體中文轉換。
- 無可靠對齊時保留原稿，字幕時間與語者歸屬依可靠對齊片段輸出。
- 提供專有名詞輸入，傳入實際辨識 backend；日中混合課程保持自動語言辨識，另外加入雙語課程情境。
- Qwen 補齊支援語言代碼；無時間戳的長音訊也使用套件既有切段能力，避免單次生成字數限制截掉後半段。
- 長音訊以可設定的低音量切點或固定長度切段；先完成並保存各段 ASR，再進入對齊，避免對齊模型占用記憶體影響後段辨識。可靠片段、未對齊缺口及各段錯誤均保留，對齊重試重用已保存的 ASR。
- faster-whisper 保留 beam search 與 VAD，情境、詞庫與前文分別管理 token 預算，不再把同一情境同時注入 `initial_prompt` 與 `hotwords`。切段與前文條件皆可設定。

目前另以 100 ms 視窗、−50 dBFS RMS 門檻記錄音訊活動；在可靠時間前後各保留 0.3 秒後，超過 1 秒的未涵蓋活動標為「可能漏辨」，供回聽核對。音樂、噪音或時間偏差也可能觸發。

## 實測證據

使用 [Google FLEURS](https://huggingface.co/datasets/google/fleurs) 的短日文與中文錄音，以及原資料提供的參考文字。音訊授權為 CC-BY-4.0。日中功能樣本由短日文與中文錄音加上短靜音串接而成。

在同一個已快取的 Qwen3-ASR-1.7B 模型上，觀察到：

| 情境 | 原本行為 | 加入修正／相關情境提示後 |
| --- | --- | --- |
| 日文術語 | 將「敵対的環境コース」錯聽為「適体適環境コース」 | 輸入正確術語提示後可辨識為「敵対的環境コース」 |
| 日文後接中文 | 未提供課程情境時漏掉中文段落 | 日中課程情境下保留兩種語言 |
| 中文後接日文 | 未提供課程情境時也漏掉中文內容 | 日中課程情境下保留兩種語言 |
| 日文字形 | 繁中後處理會改動日文漢字，例如「体」 | 日文原稿字形保留 |
| 金額字串的對齊回歸 | `$1.50`、`$15.00` 會變成 `150`、`1500` | 字幕與語者逐字稿保留原始金額 |

金額檢查使用實際對齊器的文字前處理搭配假模型回傳，是確定性的輸出回歸測試。其餘辨識例使用真實模型與短公開音訊。

這些結果證明特定問題已修復，並顯示相關情境及詞彙提示對這些樣本有幫助。時間戳已檢查範圍、順序與內容一致性。提示可能改變標點；過多或不相關的提示可能造成其他誤辨。

另以兩支約 10 分 51 秒、19 分 35 秒的日文影片驗證完整流程。在 GPU 可用記憶體約 8 GiB 的環境，原先較長切段會記憶體不足；改為約 60 秒低音量切段與單段推論後，同一個 Qwen3-ASR-1.7B 模型完成兩支轉錄，分別產生 119 與 253 個字幕段落。TXT／SRT／VTT／JSON 均可下載，逐字內容完整對應輸出文字，時間順序及範圍檢查通過。

比較實際課程正確率時，應使用有人工校正稿的日文、日中交替及問答片段，固定音檔、模型與設定，分別計算日文／中文的字元錯誤率、專有名詞命中率、漏段及語者歸屬錯誤。

## 可重複評測工具

`python -m echoscript.evaluation` 提供三個命令，不需另裝評測套件：

```bash
python -m echoscript.evaluation validate /path/to/manifest.json
python -m echoscript.evaluation run /path/to/manifest.json --output-dir /path/to/evaluation-run
python -m echoscript.evaluation score /path/to/manifest.json /path/to/hypotheses.json --output /path/to/report.json
```

`run` 依音檔與策略矩陣使用現有完整轉錄流程，逐次保存原始 ASR、最終顯示文字、選項、模型診斷、音檔 SHA-256、程式 SHA-256、套件版本及處理時間。多個策略各自使用新的子程序，避免原生 CUDA 資源影響下一個策略。重新執行會重用相同設定的已完成結果，未完成片段可重試，並在 `runtime.attempts` 累積每次耗時與總成本；設定或音檔改變時要求使用新的輸出目錄，避免混入舊結果。`--policy 策略代號` 可只執行一種策略。執行錯誤保留在結果中。

矩陣使用 `JobOptions` 的實際選項；未知選項直接報錯。每個策略執行時另存對應程式雜湊的 source ZIP，供固定程式版本重跑。正式基準應指定固定 revision 的本機模型路徑。提供 `cost_per_hour` 與 `currency` 時，成本依實測時間估算；未提供時以 `null` 表示成本資料缺漏。

以下是結構示例，文字與時間須換成經人工核對的資料：

```json
{
  "schema_version": 1,
  "cases": [{
    "id": "lesson-ja-zh-01",
    "audio": "audio/lesson-ja-zh-01.wav",
    "sha256": "替換成音檔的 SHA-256",
    "languages": ["ja", "zh"],
    "corpus_type": "natural_code_switching",
    "reference": {
      "human_verified": true,
      "provenance": "參考稿來源、校對者與校對日期",
      "language": "ja,zh",
      "raw_text": "人工核對的原始逐字稿",
      "display_text": "人工核對的預期顯示稿",
      "terms": ["專有名詞"],
      "numbers": ["二零二六"],
      "utterances": [{"id": "u1", "text": "人工核對的原始逐字稿", "start": 0.5, "end": 4.2}]
    }
  }],
  "policies": [
    {"id": "qwen-energy-60", "options": {"asr_backend": "qwen", "chunk_seconds": 60, "chunk_strategy": "energy", "language": null, "context": "", "glossary": ""}},
    {"id": "qwen-energy-120", "options": {"asr_backend": "qwen", "chunk_seconds": 120, "chunk_strategy": "energy", "language": null, "context": "日中雙語課程，日本講師與台灣翻譯輪流說話。", "glossary": ""}},
    {"id": "whisper-history", "options": {"asr_backend": "faster-whisper", "asr_model": "large-v3-turbo", "chunk_seconds": 300, "condition_on_previous_text": true}},
    {"id": "whisper-no-history", "options": {"asr_backend": "faster-whisper", "asr_model": "large-v3-turbo", "chunk_seconds": 300, "condition_on_previous_text": false}}
  ]
}
```

比較切段長度時保持情境、詞庫及其他選項一致；比較上下文時保持切段一致。上例展示切段長度（如 60 秒）、情境與前文條件等可設定維度。以同一批自然日文、自然日中交替、中英混合片段進行成對比較，再按語言與內容類型分組；串接錄音另列一組。

既有輸出可用以下 `hypotheses.json` 結構直接評分，路徑相對於此檔案；也可把 JSON 物件直接放入 `raw`、`display`：

```json
{"schema_version": 1, "runs": [{"case_id": "lesson-ja-zh-01", "policy_id": "qwen-energy-60", "raw": "job/asr.json", "display": "job/output/result.json", "options": {"zh_script": "tw"}, "provenance": "原始執行紀錄位置與版本"}]}
```

### 指標定義

- `asr`：人工原始稿與未轉換 ASR 的 CER／WER。CER 使用 NFC、忽略標點和空白並摺疊大小寫，不做繁簡或日文字形轉換；`cer_verbatim` 另計包含符號、標點及空白的錯誤，避免 `$1.50 → 150` 被正規化掩蓋。
- `postprocessing_on_reference`：把人工原始稿交給目前後處理，再與人工顯示稿比較，獨立驗證字形／詞彙轉換。須同時提供 `display_text` 及執行選項 `zh_script`，缺少時不計算。
- `final`：實際最終顯示稿對人工顯示稿的錯誤。ASR 與 final 各依對應字形規範計算，後處理的字形／詞彙轉換則由 `postprocessing_on_reference` 獨立比較。
- `wer_whitespace`：以空白分詞，日文／中文也沿用空白邊界；日中評測以 CER 為主。
- `numbers`／`terms`：保留小數點、貨幣符號及詞彙原貌的出現次數，另外支持人工列出的中文數字，採精確字串核對，不做數字語意等價判斷。
- `utterances`：依最小編輯對齊估計漏句；一個人工句子至少 80% 字元被刪除時標為疑似漏辨。整句超額重現另標疑似重複；末尾刪除字元數另列。診斷供回聽核對，改寫、同音詞或口吃情形需人工判讀。
- `timestamp`：以人工起訖標註及相符的模型字詞邊界計算誤差；缺字、未對齊或缺人工時間都明確標示。作者字幕的顯示時間列為來源資訊。
- `runtime`：包含模型載入的牆鐘秒數、音檔秒數與實時倍率；歷史資料須自行標明是否包含模型載入。成本取決於使用者提供的每小時費率，未量測 GPU 能耗或外部帳單。
- `complementarity`：依人工參考答案計算同一片段中，兩種策略各自獨有與共有的參考字元錯誤，以及總編輯錯誤，呈現錯誤互補的機會上限。工具保持自動跨模型校正關閉。

`inference_integrity` 另外列出辨識是否完整及對齊狀態。模型互補比較會拒絕音檔雜湊不一致的配對；舊假設未記錄雜湊時，明示其音檔身分證據不足。`policy_processes` 保留子程序退出碼、總牆鐘時間與日誌位置，包含程序啟動、模型清理及評分等每次辨識時間之外的開銷。

缺少人工參考稿的案例會回報 `missing_human_reference`，矩陣缺跑項目列在 `missing_runs`。精確編輯距離為二次複雜度，單次比較限制 2,500 萬格；長課程應以人工標註的多個可重複片段評測。數值工具的單元測試使用合成文字案例，驗證指標計算。

作者上傳的字幕可指定 `source_kind: "author_subtitle"`、`human_verified: false`，並記錄字幕來源、原檔雜湊及前處理。這類結果標為 `scored_against_author_subtitle`／`author_subtitle_not_verbatim_audited`，衡量與作者字幕的差異。字幕可能整理口語、使用另一套字形、包含非語音標籤或拼字錯誤；差異需回聽判讀。

現有評測資料尚缺自然日中交替的原語言人工稿、作者字幕逐句回聽複核、人工字詞時間與更大的獨立測試集。

### 2026-09-07：舊輸出的重新量化

找到 `/tmp/echoscript-accuracy/` 保存的三份 FLEURS 音檔、來源資料列及原始參考文字，並使用新工具重新計算此前保存的 Qwen 輸出。評測輸入與報告保存在 `/tmp/echoscript-evaluation-history/`，內容為舊版 Qwen 輸出的重新評分。暫存路徑可能被系統清理，正式重現時應另存音檔、manifest、hypotheses 及來源授權。

| 既有樣本與設定 | CER（忽略標點／空白） | 漏句診斷 |
| --- | --- | --- |
| 日文術語，未加提示 | 3 / 45 = 6.67% | 無整句漏辨 |
| 同一日文，加正確術語 | 0 / 45 = 0% | 無整句漏辨 |
| 日文後接中文，無情境 | 26 / 68 = 38.24% | 中文句疑似漏辨 |
| 同一串接樣本，加雙語情境 | 3 / 68 = 4.41% | 中文句保留 |
| 中文後接日文，無情境 | 33 / 68 = 48.53% | 中文句疑似漏辨 |
| 同一反向串接樣本，加雙語情境 | 3 / 68 = 4.41% | 中文句保留 |

這批少量已知案例的正確術語提示直接包含考題詞彙，歷史紀錄保存的模型輸出為 Qwen。

### 作者字幕素材的取得

使用者提供的三支公開影片均可取得作者字幕軌，另保留了對應的短原音片段：

| 影片 | 字幕軌 | 片段長度與用途 |
| --- | --- | --- |
| [荒野亂鬥 TOP12](https://www.youtube.com/watch?v=AE76f06ooE8) | `zh-TW` 作者字幕 | 60.916 秒；中文角色名、數字及少量英文名稱 |
| [AI Agent 之間可以有什麼樣的互動](https://www.youtube.com/watch?v=mmPmNezjCi0) | `zh-TW` 作者字幕 | 76.576 秒；自然中文搭配 AI agent、train、paper 等英文術語 |
| [The Best Low Calorie Burger Ever](https://www.youtube.com/watch?v=uNM5ZvNkN1U) | `en` 作者字幕 | 63.440 秒；英文數字、營養與料理術語 |

原始字幕 JSON3、音檔及來源 manifest 保存在 `/tmp/echoscript-evaluation-youtube/`。素材採用公開作者字幕軌，字幕時間列為來源資訊。英文原稿的 `[music]` 標籤依明確規則移除，其餘拼字保留。這三支影片的語言分別為中文、中英混合與英文。

## 2026-09-07：目前流程的隔離評測

已使用目前兩階段流程執行 6 份素材 × 8 種策略，共 48 次真實推論。Qwen3-ASR-1.7B 與 faster-whisper large-v3 各比較 30／60 秒切段，以及前文條件開／關；全部保持自動語言辨識，情境與詞庫皆空白。每個策略使用獨立程序，避免前一模型的原生 CUDA 資源污染下一個模型的可用記憶體。

完整保留包位於 [data/evaluations/2026-09-07](../data/evaluations/2026-09-07/README.md)，保留於本機並受 Git 忽略。包含唯一音檔、作者原始字幕、FLEURS 參考來源、相對音檔路徑 manifest、嵌入完整假設的 JSON、結果、環境與來源快照；歷史情境結果、修正前的記憶體失敗紀錄及最終 48 次結果分別保存。其他開發者需取得該資料包才能直接重跑。

FLEURS 人工參考稿結果如下，這些短錄音均未達 30 秒：

| 人工參考素材 | Qwen CER | Whisper CER |
| --- | --- | --- |
| 日文術語句，45 字元 | 6.67% | 6.67% |
| 第二段日文，46 字元 | 0% | 0% |
| 日文接中文的串接錄音，68 字元 | 38.24% | 4.41% |

下表是實際最終顯示稿對作者字幕的字元差異率。中文作者稿使用繁體；原始 ASR 的字形差異另列於 JSON 的 `asr`，raw 與 final 各依對應字形規範計算。

| 模型、切段與前文 | AI Agent 中英 | 荒野亂鬥中文 | Burger 英文 | 三份長片段的對齊狀態 |
| --- | --- | --- | --- | --- |
| Qwen，30 秒，開 | 1.54% | 11.49% | 2.54% | 完整 |
| Qwen，30 秒，關 | 1.54% | 11.49% | 2.54% | 完整 |
| Qwen，60 秒，開 | 3.34% | 12.53% | 2.86% | 部分可用 |
| Qwen，60 秒，關 | 3.34% | 12.01% | 2.86% | 部分可用 |
| Whisper，30 秒，開 | 3.34% | 10.18% | 1.48% | 完整 |
| Whisper，30 秒，關 | 3.34% | 9.14% | 1.59% | 完整 |
| Whisper，60 秒，開 | 3.60% | 8.36% | 0.74% | 完整 |
| Whisper，60 秒，關 | 3.60% | 7.57% | 1.48% | 完整 |

48 次的 ASR 片段保存皆完整；其中 Qwen 60 秒在三份長片段、兩種前文設定的共 6 次執行中，較長片段對齊遇到記憶體不足。原稿與後段可靠時間仍保留，對齊狀態明示部分可用。30 秒策略與所有 Whisper 策略的對齊均通過目前結構驗收。

這批資料呈現不同取捨：Qwen 在 AI Agent 作者稿較接近參考，Whisper 在串接雙語、角色名及英文作者稿較接近參考；Whisper 60 秒在英文優於 30 秒，關閉前文在該英文樣本反而增加差異。日文術語句中，兩者同為 3 個字元錯誤，但最小編輯對齊定位在互不重疊的參考位置，顯示這個樣本的錯誤有互補機會。自動跨模型校正保持關閉。

硬體為 RTX 5090 32 GiB；其他既有程序按原狀運行，測試開始時占用約 25 GiB。模型均使用本機快取，權重 revision 與套件版本保留在資料包。各策略處理 6 份素材的累積時間為 22.55–28.30 秒，包含模型載入；8 個隔離策略加上程序啟動、評分及清理的總牆鐘時間為 246.16 秒。未提供費率，所以金額成本保留 `null`。重做效能比較時，須固定外部 GPU 負載並重複量測。

執行期間其他 API 與評測程式仍有修改，故整個 Python 套件記錄了 6 個 source SHA-256。最後一組策略的完全相符快照已保留，其餘歷史雜湊缺少完整快照，記錄於 `environment.json`。資料包另存目前可重跑的程式基準；之後的 runner 已會在策略開始時自動保存對應 source ZIP。

48 次推論之後，另加入「有音訊能量但未被可靠時間涵蓋」的片段提示。使用既有日文接中文的錄音與 Qwen 快取、禁止載入模型進行 CPU 回歸，於中文所在的 15.1–16.5 與 17.0–18.3 秒提出疑似缺口，也在日文時間邊界提出其他候選。提示供原音核對，音樂、雜訊或時間邊界偏差也可能觸發。證據另存資料包 `diagnostics-followup/`，與原始 48 次假設及辨識指標分別保存。

## 研究來源

- [Qwen3-ASR 官方 GitHub](https://github.com/QwenLM/Qwen3-ASR)：context、支援語言、forced alignment、切段與生成長度設定。
- [faster-whisper 官方 GitHub](https://github.com/SYSTRAN/faster-whisper)：VAD、beam search、多語言與 hotwords。
- [WhisperX 官方 GitHub](https://github.com/m-bain/whisperX)：對齊限制、語者歸屬、以及 previous-text conditioning 的取捨。
- [Google FLEURS 資料集](https://huggingface.co/datasets/google/fleurs)：本次短音訊驗證來源與授權。
