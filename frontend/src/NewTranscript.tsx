import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { formatBytes, request, uploadMedia } from './api';
import type { Config, Job, Options } from './api';
import { Icon } from './Icons';

type Props = {
  config: Config;
  onJobUpdate: (job: Job) => void;
  onSubmitted: (job: Job) => void;
  onBusyChange: (busy: boolean) => void;
};

export default function NewTranscript({ config, onJobUpdate, onSubmitted, onBusyChange }: Props) {
  const [source, setSource] = useState<'upload' | 'url'>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [options, setOptions] = useState<Options>(() => {
    const defaults = config.default_options;
    const initial =
      config.backends.find((item) => item.id === defaults.asr_backend && item.available) ||
      config.backends.find((item) => item.available);
    return {
      ...defaults,
      language: defaults.language || null,
      asr_backend: initial?.id || defaults.asr_backend,
      asr_model: initial?.models.some((item) => item.id === defaults.asr_model)
        ? defaults.asr_model
        : initial?.models[0]?.id || defaults.asr_model,
      diarize: defaults.diarize && config.diarization_available,
    };
  });
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const backend = config.backends.find((item) => item.id === options.asr_backend);

  useEffect(() => {
    if (!busy) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [busy]);

  function chooseFile(next: File | undefined) {
    setError('');
    if (!next) return;
    if (next.size === 0) {
      setFile(null);
      setError('這個檔案是空的，請重新選取音訊或影片。');
      return;
    }
    if (next.size > config.max_upload_bytes) {
      setFile(null);
      setError(
        `檔案大小超過 ${formatBytes(config.max_upload_bytes)}，請縮短錄音或壓縮檔案後再試。`,
      );
      return;
    }
    setFile(next);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (source === 'upload' && !file) {
      setError('請先選取要轉錄的音訊或影片。');
      fileInput.current?.focus();
      return;
    }
    if (!backend?.available) {
      setError('目前沒有可用的轉錄方式，請稍後再試。');
      return;
    }
    setError('');
    setBusy(true);
    onBusyChange(true);
    setProgress(source === 'upload' ? 0 : null);
    try {
      const body =
        source === 'upload'
          ? { source_type: 'upload', filename: file!.name, options }
          : { source_type: 'url', url: url.trim(), options };
      let job = await request<Job>('/api/jobs', { method: 'POST', body: JSON.stringify(body) });
      onJobUpdate(job);
      if (source === 'upload') {
        job = await uploadMedia(job.id, file!, setProgress);
        onJobUpdate(job);
      }
      onSubmitted(job);
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : '無法送出轉錄，請稍後再試。');
    } finally {
      setBusy(false);
      onBusyChange(false);
      setProgress(null);
    }
  }

  return (
    <div className="composer page-enter">
      <div className="page-heading">
        <h1>新增轉錄</h1>
        <p>把錄音與影片，整理成清楚的逐字稿。</p>
      </div>
      {!config.backends.some((item) => item.available) ? (
        <div className="notice" role="status">
          <Icon name="info" />
          <span>轉錄服務目前尚未開放，請稍後再試。</span>
        </div>
      ) : null}
      <form onSubmit={submit}>
        <fieldset disabled={busy} className="form-fields">
          <section className="source-section" aria-labelledby="source-title">
            <div className="section-heading">
              <h2 id="source-title">選擇音訊來源</h2>
              <span className="subtle">音訊與影片皆可</span>
            </div>
            <div className="source-tabs" aria-label="音訊來源">
              <button
                type="button"
                aria-pressed={source === 'upload'}
                className={source === 'upload' ? 'active' : ''}
                onClick={() => {
                  setSource('upload');
                  setError('');
                }}
              >
                <Icon name="upload" size={18} />
                上傳檔案
              </button>
              <button
                type="button"
                aria-pressed={source === 'url'}
                className={source === 'url' ? 'active' : ''}
                onClick={() => {
                  setSource('url');
                  setError('');
                }}
              >
                <Icon name="link" size={18} />
                貼上連結
              </button>
            </div>
            {source === 'upload' ? (
              <div
                className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (!busy) setDragging(true);
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node))
                    setDragging(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  if (!busy) chooseFile(event.dataTransfer.files[0]);
                }}
              >
                <input
                  ref={fileInput}
                  className="file-picker"
                  type="file"
                  aria-label="選擇音訊或影片檔案"
                  accept="audio/*,video/*,.mkv,.m4a,.flac,.opus,.ogg,.webm"
                  onChange={(event) => {
                    chooseFile(event.target.files?.[0]);
                    event.target.value = '';
                  }}
                />
                <span className="upload-symbol">
                  <Icon name={file ? 'file' : 'upload'} size={27} />
                </span>
                <strong className="filename">{file ? file.name : '將檔案拖放到這裡'}</strong>
                <span>
                  {file ? (
                    `${formatBytes(file.size)} · ${busy ? '正在上傳' : '點選可更換檔案'}`
                  ) : (
                    <>
                      或 <span className="text-link">選擇檔案</span>
                    </>
                  )}
                </span>
                <small>
                  {busy
                    ? '請保留此頁面，直到上傳完成。'
                    : file
                      ? '檔案已選取，開始轉錄時會上傳。'
                      : `MP3、WAV、M4A、MP4 等格式 · 最大 ${formatBytes(config.max_upload_bytes)}`}
                </small>
              </div>
            ) : (
              <div className="url-panel">
                <label htmlFor="media-url">影片或音訊連結</label>
                <input
                  id="media-url"
                  type="url"
                  placeholder="https://www.youtube.com/watch?v=…"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  required
                />
                <p>貼上 YouTube 或公開的音影片連結。</p>
              </div>
            )}
          </section>

          <section className="transcription-settings" aria-labelledby="settings-title">
            <div className="section-heading">
              <h2 id="settings-title">轉錄設定</h2>
            </div>
            <div className="field-grid">
              <label className="field" htmlFor="language">
                <span>音訊語言</span>
                <select
                  id="language"
                  aria-label="音訊語言"
                  aria-describedby="language-help"
                  value={options.language || 'auto'}
                  onChange={(event) =>
                    setOptions({
                      ...options,
                      language: event.target.value === 'auto' ? null : event.target.value,
                    })
                  }
                >
                  {config.languages.map((item) => (
                    <option key={item.code} value={item.code}>
                      {item.label}
                    </option>
                  ))}
                </select>
                <small id="language-help">
                  {options.language === 'ja-zh'
                    ? '加入雙語課程情境；人名與術語可在下方補充。'
                    : '講師說日文、口譯說中文時，可選日中混合課程。'}
                </small>
              </label>
              <label className="field" htmlFor="chinese-script">
                <span>中文文字</span>
                <select
                  id="chinese-script"
                  aria-label="中文文字"
                  aria-describedby="chinese-script-help"
                  value={options.zh_script || 'none'}
                  onChange={(event) =>
                    setOptions({
                      ...options,
                      zh_script: event.target.value === 'none' ? null : event.target.value,
                    })
                  }
                >
                  <option value="tw">繁體中文</option>
                  <option value="twp">繁體中文（台灣慣用詞）</option>
                  <option value="none">保留原始文字</option>
                </select>
                <small id="chinese-script-help">
                  {options.language === 'ja' || options.language === 'ja-zh'
                    ? '含日文時保留原始字形，避免改變日文漢字。'
                    : '調整中文寫法，不會翻譯其他語言。'}
                </small>
              </label>
            </div>
            <label className="field context-field" htmlFor="context">
              <span>
                專有名詞與背景 <span className="optional">選填</span>
              </span>
              <textarea
                id="context"
                aria-label="專有名詞與背景"
                aria-describedby="context-help"
                rows={3}
                maxLength={8000}
                value={options.context}
                onChange={(event) => setOptions({ ...options, context: event.target.value })}
                placeholder="例如：山田先生、認知行為療法、ワークショップ、工作坊討論主題…"
              />
              <small id="context-help">
                加入人名、專業用語或課程主題，協助辨識；完成後仍建議核對重要內容。
              </small>
            </label>
            <details className="advanced-options">
              <summary>
                <Icon name="settings" size={18} />
                <span>進階設定</span>
                <Icon name="chevron" size={16} />
              </summary>
              <div className="advanced-body">
                <div className="field-grid">
                  <label className="field" htmlFor="backend">
                    <span>轉錄方式</span>
                    <select
                      id="backend"
                      value={options.asr_backend}
                      onChange={(event) => {
                        const next = config.backends.find(
                          (item) => item.id === event.target.value,
                        )!;
                        setOptions({
                          ...options,
                          asr_backend: next.id,
                          asr_model: next.models[0].id,
                        });
                      }}
                    >
                      {config.backends.map((item) => (
                        <option key={item.id} value={item.id} disabled={!item.available}>
                          {item.label}
                          {!item.available ? '（暫無法使用）' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field" htmlFor="model">
                    <span>品質偏好</span>
                    <select
                      id="model"
                      value={options.asr_model}
                      onChange={(event) =>
                        setOptions({ ...options, asr_model: event.target.value })
                      }
                    >
                      {backend?.models.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={options.timestamps}
                    onChange={(event) =>
                      setOptions({
                        ...options,
                        timestamps: event.target.checked,
                        diarize: event.target.checked && options.diarize,
                      })
                    }
                  />
                  <span>
                    <strong>標示時間與產生字幕</strong>
                    <small>方便逐段閱讀，並下載字幕檔。</small>
                  </span>
                </label>
                <label
                  className={`check-row ${!config.diarization_available ? 'unavailable' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={options.diarize}
                    disabled={!config.diarization_available}
                    onChange={(event) =>
                      setOptions({
                        ...options,
                        diarize: event.target.checked,
                        timestamps: event.target.checked || options.timestamps,
                      })
                    }
                  />
                  <span>
                    <strong>區分不同語者</strong>
                    <small>
                      {config.diarization_available
                        ? '適合訪談與多人課程，處理時間會較長。'
                        : '語者辨識目前尚未開放。'}
                    </small>
                  </span>
                </label>
              </div>
            </details>
          </section>
        </fieldset>
        {error ? (
          <div className="notice error" role="alert">
            <Icon name="info" />
            <span>{error}</span>
          </div>
        ) : null}
        {progress !== null ? (
          <div className="upload-progress" role="status">
            <div>
              <span>
                {progress === 100 ? '檔案已傳送，正在確認上傳…' : '正在上傳檔案，請保留此頁面。'}
              </span>
              <strong>{progress}%</strong>
            </div>
            <progress value={progress} max="100" aria-label="檔案上傳進度" />
          </div>
        ) : null}
        <div className="submit-row">
          <span>
            {busy
              ? source === 'url'
                ? '正在建立轉錄…'
                : '上傳完成後會開始轉錄'
              : '完成後可閱讀、搜尋與下載逐字稿。'}
          </span>
          <button className="button primary" type="submit" disabled={busy || !backend?.available}>
            {busy ? <span className="spinner" /> : null}
            {busy ? '正在送出' : '開始轉錄'}
            {!busy ? <Icon name="arrow" size={19} /> : null}
          </button>
        </div>
      </form>
    </div>
  );
}
