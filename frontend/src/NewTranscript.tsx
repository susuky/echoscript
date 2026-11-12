import { useLocale } from './i18n';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { formatBytes, request, uploadMedia } from './api';
import type { Config, Job, Options } from './api';
import { Icon } from './Icons';

type Props = {
  config: Config;
  onConfigChange: (config: Config) => void;
  onJobUpdate: (job: Job) => void;
  onSubmitted: (job: Job) => void;
  onBusyChange: (busy: boolean) => void;
  onDirtyChange: (dirty: boolean) => void;
};

export default function NewTranscript({
  config,
  onConfigChange,
  onJobUpdate,
  onSubmitted,
  onBusyChange,
  onDirtyChange,
}: Props) {
  const { t } = useLocale();
  const [source, setSource] = useState<'upload' | 'url'>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [options, setOptions] = useState<Options>(() => {
    const defaults = config.default_options;
    const initial =
      config.backends.find(
        (item) => item.id === defaults.asr_backend && item.available,
      ) || config.backends.find((item) => item.available);
    return {
      ...defaults,
      language: defaults.language || null,
      glossary: defaults.glossary || '',
      condition_on_previous_text: defaults.condition_on_previous_text !== false,
      asr_backend: initial?.id || defaults.asr_backend,
      asr_model: initial?.models.some((item) => item.id === defaults.asr_model)
        ? defaults.asr_model
        : initial?.models[0]?.id || defaults.asr_model,
      timestamps: initial?.id === 'openai' ? false : defaults.timestamps,
      diarize:
        initial?.id !== 'openai' &&
        defaults.diarize &&
        config.diarization_available,
    };
  });
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [keySaving, setKeySaving] = useState(false);
  const [keyError, setKeyError] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const keyInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const backend = config.backends.find(
    (item) => item.id === options.asr_backend,
  );
  const openai = options.asr_backend === 'openai';
  const initialOptions = useRef(options);
  const dirty = Boolean(
    file ||
    url.trim() ||
    apiKey ||
    Object.entries(options).some(
      ([key, value]) => value !== initialOptions.current[key as keyof Options],
    ),
  );

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  useEffect(() => {
    if (!busy && !dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [busy, dirty]);

  function chooseFile(next: File | undefined) {
    setError('');
    setSourceError('');
    if (!next) return;
    if (next.size === 0) {
      setFile(null);
      setSourceError('這個檔案是空的，請重新選取音訊或影片。');
      return;
    }
    if (config.max_upload_bytes > 0 && next.size > config.max_upload_bytes) {
      setFile(null);
      setSourceError('檔案超過上傳大小限制，請縮短錄音或壓縮檔案後再試。');
      return;
    }
    setFile(next);
  }

  async function persistApiKey() {
    await request('/api/settings/openai', {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey.trim() }),
    });
    onConfigChange({ ...config, openai_api_key_configured: true });
    setApiKey('');
    setKeyError('');
  }

  async function saveApiKey() {
    if (busy || keySaving || !apiKey.trim()) return;
    setKeySaving(true);
    onBusyChange(true);
    setKeyError('');
    try {
      await persistApiKey();
    } catch (issue) {
      setKeyError(
        issue instanceof Error
          ? issue.message
          : '無法儲存 OpenAI API key，請稍後再試。',
      );
    } finally {
      setKeySaving(false);
      onBusyChange(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || keySaving) return;
    if (source === 'upload' && !file) {
      setSourceError('請先選取要轉錄的音訊或影片。');
      fileInput.current?.focus();
      return;
    }
    if (!backend?.available) {
      setError('目前沒有可用的轉錄方式，請稍後再試。');
      return;
    }
    if (openai && !apiKey.trim() && !config.openai_api_key_configured) {
      setKeyError('請先輸入 OpenAI API key。');
      requestAnimationFrame(() => keyInput.current?.focus());
      return;
    }
    setError('');
    setBusy(true);
    onBusyChange(true);
    setProgress(source === 'upload' ? 0 : null);
    try {
      if (openai && apiKey.trim()) await persistApiKey();
      const body =
        source === 'upload'
          ? { source_type: 'upload', filename: file!.name, options }
          : { source_type: 'url', url: url.trim(), options };
      let job = await request<Job>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      onJobUpdate(job);
      if (source === 'upload') {
        job = await uploadMedia(job.id, file!, setProgress);
        onJobUpdate(job);
      }
      onSubmitted(job);
    } catch (issue) {
      setError(
        issue instanceof Error ? issue.message : '無法送出轉錄，請稍後再試。',
      );
    } finally {
      setBusy(false);
      onBusyChange(false);
      setProgress(null);
    }
  }

  return (
    <div className="composer page-enter">
      <div className="page-heading">
        <h1 id="new-transcript-title">{t('新增轉錄')}</h1>
      </div>
      {!config.backends.some((item) => item.available) ? (
        <div className="notice" role="status">
          <Icon name="info" />
          <span>{t('轉錄服務目前尚未開放，請稍後再試。')}</span>
        </div>
      ) : null}
      <form
        onSubmit={submit}
        aria-labelledby="new-transcript-title"
        action="/api/jobs"
        method="post"
      >
        <fieldset disabled={busy || keySaving} className="form-fields">
          <legend className="visually-hidden">{t('新增轉錄')}</legend>
          <div className="source-column">
            <section className="source-section" aria-label={t('音訊來源')}>
              <fieldset className="source-tabs">
                <legend className="visually-hidden">{t('音訊來源')}</legend>
                <label>
                  <input
                    type="radio"
                    name="source"
                    value="upload"
                    checked={source === 'upload'}
                    onChange={() => {
                      setSource('upload');
                      setError('');
                      setSourceError('');
                    }}
                  />
                  <Icon name="upload" size={18} />
                  <span>{t('上傳檔案')}</span>
                </label>
                <label>
                  <input
                    type="radio"
                    name="source"
                    value="url"
                    checked={source === 'url'}
                    onChange={() => {
                      setSource('url');
                      setError('');
                      setSourceError('');
                    }}
                  />
                  <Icon name="link" size={18} />
                  <span>{t('貼上連結')}</span>
                </label>
              </fieldset>
              {source === 'upload' ? (
                <div
                  className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`}
                  onDragOver={(event) => {
                    event.preventDefault();
                    if (!busy && !keySaving) setDragging(true);
                  }}
                  onDragLeave={(event) => {
                    if (
                      !event.currentTarget.contains(event.relatedTarget as Node)
                    )
                      setDragging(false);
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragging(false);
                    if (!busy && !keySaving)
                      chooseFile(event.dataTransfer.files[0]);
                  }}
                >
                  <input
                    ref={fileInput}
                    className="file-picker"
                    type="file"
                    name="media"
                    aria-label={t('選擇音訊或影片檔案')}
                    aria-invalid={Boolean(sourceError)}
                    aria-describedby={
                      sourceError ? 'file-help source-error' : 'file-help'
                    }
                    accept="audio/*,video/*,.mkv,.m4a,.flac,.opus,.ogg,.webm"
                    onChange={(event) => {
                      chooseFile(event.target.files?.[0]);
                      event.target.value = '';
                    }}
                  />
                  <span className="upload-symbol">
                    <Icon name={file ? 'file' : 'upload'} size={36} />
                  </span>
                  <strong className="filename">
                    {file ? file.name : t('將檔案拖放到這裡')}
                  </strong>
                  {file ? (
                    <span className="file-size">{formatBytes(file.size)}</span>
                  ) : null}
                  <span className="file-choose">
                    {file ? t('更換檔案') : t('選擇檔案')}
                  </span>
                  <small id="file-help">
                    {config.max_upload_bytes > 0
                      ? t('MP3、WAV、M4A、MP4 等格式 · 最大 {size}', {
                          size: formatBytes(config.max_upload_bytes),
                        })
                      : t('MP3、WAV、M4A、MP4 等格式')}
                  </small>
                </div>
              ) : (
                <div className="url-panel">
                  <label htmlFor="media-url">{t('影片或音訊連結')}</label>
                  <input
                    id="media-url"
                    type="url"
                    name="url"
                    inputMode="url"
                    enterKeyHint="go"
                    placeholder="https://www.youtube.com/watch?v=…"
                    value={url}
                    onChange={(event) => {
                      setUrl(event.target.value);
                      setError('');
                    }}
                    aria-describedby="url-help"
                    autoComplete="url"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    maxLength={4096}
                    required
                  />
                  <p id="url-help">{t('貼上 YouTube 或公開的音影片連結。')}</p>
                </div>
              )}
              {sourceError ? (
                <div
                  id="source-error"
                  className="notice error source-error"
                  role="alert"
                >
                  <Icon name="info" />
                  <span>{t(sourceError)}</span>
                </div>
              ) : null}
            </section>
            <details className="context-options">
              <summary>
                <span>{t('背景與詞庫')}</span>
                <Icon name="chevron" size={16} />
              </summary>
              <div className="context-body">
                <label className="field" htmlFor="context">
                  <span>
                    {t('內容背景')}{' '}
                    <span className="optional">{t('選填')}</span>
                  </span>
                  <textarea
                    id="context"
                    name="context"
                    rows={3}
                    maxLength={8000}
                    value={options.context}
                    onChange={(event) =>
                      setOptions({ ...options, context: event.target.value })
                    }
                    placeholder={t(
                      '例如：日文講師與中文口譯交替發言的動作學課程。',
                    )}
                    aria-describedby="context-help"
                  />
                  <small id="context-help">
                    {t('簡短描述錄音主題與說話情境，專有名詞請填在詞庫。')}
                  </small>
                </label>
                <label className="field" htmlFor="glossary">
                  <span>
                    {t('詞庫')} <span className="optional">{t('選填')}</span>
                  </span>
                  <textarea
                    id="glossary"
                    name="glossary"
                    rows={2}
                    maxLength={8000}
                    value={options.glossary}
                    onChange={(event) =>
                      setOptions({ ...options, glossary: event.target.value })
                    }
                    placeholder={t(
                      '例如：山田先生、運動学、動作學、EchoScript',
                    )}
                    aria-describedby="glossary-help"
                  />
                  <small id="glossary-help">
                    {t('列出重要人名與術語，以逗號或換行分隔。')}
                  </small>
                </label>
              </div>
            </details>
          </div>
          <section
            className="transcription-settings"
            aria-labelledby="settings-title"
          >
            <h2 id="settings-title">{t('轉錄設定')}</h2>
            <div className="field-grid">
              <label className="field" htmlFor="language">
                <span>{t('音訊語言')}</span>
                <select
                  id="language"
                  name="language"
                  value={options.language || 'auto'}
                  onChange={(event) =>
                    setOptions({
                      ...options,
                      language:
                        event.target.value === 'auto'
                          ? null
                          : event.target.value,
                    })
                  }
                >
                  {config.languages.map((item) => (
                    <option key={item.code} value={item.code}>
                      {t(item.code === 'auto' ? '自動辨識' : item.label)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field" htmlFor="chinese-script">
                <span>{t('中文文字')}</span>
                <select
                  id="chinese-script"
                  name="zh_script"
                  value={options.zh_script || 'none'}
                  aria-describedby={
                    options.language === 'ja' || options.language === 'ja-zh'
                      ? 'chinese-script-help'
                      : undefined
                  }
                  onChange={(event) =>
                    setOptions({
                      ...options,
                      zh_script:
                        event.target.value === 'none'
                          ? null
                          : event.target.value,
                    })
                  }
                >
                  <option value="tw">{t('繁體中文')}</option>
                  <option value="twp">{t('繁體中文（台灣慣用詞）')}</option>
                  <option value="none">{t('保留原始文字')}</option>
                </select>
                {options.language === 'ja' || options.language === 'ja-zh' ? (
                  <small id="chinese-script-help">
                    {t('保留日文原始字形。')}
                  </small>
                ) : null}
              </label>
              <label className="field" htmlFor="backend">
                <span>{t('轉錄方式')}</span>
                <select
                  id="backend"
                  name="asr_backend"
                  value={options.asr_backend}
                  onChange={(event) => {
                    const next = config.backends.find(
                      (item) => item.id === event.target.value,
                    )!;
                    setOptions({
                      ...options,
                      asr_backend: next.id,
                      asr_model: next.models[0].id,
                      timestamps:
                        next.id === 'openai' ? false : options.timestamps,
                      diarize: next.id === 'openai' ? false : options.diarize,
                    });
                    setKeyError('');
                  }}
                >
                  {config.backends.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                      disabled={!item.available}
                    >
                      {t(item.label)}
                      {!item.available ? t('（暫無法使用）') : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field" htmlFor="model">
                <span>{t('品質偏好')}</span>
                <select
                  id="model"
                  name="asr_model"
                  value={options.asr_model}
                  onChange={(event) =>
                    setOptions({ ...options, asr_model: event.target.value })
                  }
                >
                  {backend?.models.map((item) => (
                    <option key={item.id} value={item.id}>
                      {t(item.label)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {openai ? (
              <div className="field openai-key-field">
                <label htmlFor="openai-api-key">OpenAI API key</label>
                <div className="api-key-controls">
                  <input
                    ref={keyInput}
                    id="openai-api-key"
                    name="api_key"
                    type="password"
                    autoComplete="new-password"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    maxLength={512}
                    value={apiKey}
                    placeholder={
                      config.openai_api_key_configured
                        ? t('已設定；輸入可替換')
                        : 'sk-…'
                    }
                    aria-required={!config.openai_api_key_configured}
                    aria-invalid={Boolean(keyError)}
                    aria-describedby={`openai-key-help${keyError ? ' openai-key-error' : ''}`}
                    onChange={(event) => {
                      setApiKey(event.target.value);
                      setKeyError('');
                    }}
                  />
                  <button
                    type="button"
                    className="button"
                    disabled={!apiKey.trim() || keySaving}
                    onClick={saveApiKey}
                  >
                    {keySaving ? t('正在儲存…') : t('儲存')}
                  </button>
                </div>
                <small id="openai-key-help">
                  {t(
                    '金鑰儲存在此伺服器，供所有使用者轉錄。音訊會傳送至 OpenAI，並由你的 API 帳戶計費。',
                  )}
                </small>
                {keyError ? (
                  <div
                    id="openai-key-error"
                    className="notice error"
                    role="alert"
                  >
                    {t(keyError)}
                  </div>
                ) : null}
              </div>
            ) : null}
            <details
              className="advanced-options"
              open={advancedOpen}
              onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
            >
              <summary>
                <span>{t('進階設定')}</span>
                <Icon name="chevron" size={16} />
              </summary>
              <div className="advanced-body">
                <label className="check-row">
                  <input
                    type="checkbox"
                    name="condition_on_previous_text"
                    checked={options.condition_on_previous_text}
                    onChange={(event) =>
                      setOptions({
                        ...options,
                        condition_on_previous_text: event.target.checked,
                      })
                    }
                  />
                  <span>
                    <strong>{t('參考前文')}</strong>
                  </span>
                </label>
                <label className="check-row">
                  <input
                    type="checkbox"
                    name="timestamps"
                    checked={options.timestamps}
                    disabled={openai}
                    onChange={(event) =>
                      setOptions({
                        ...options,
                        timestamps: event.target.checked,
                        diarize: event.target.checked && options.diarize,
                      })
                    }
                  />
                  <span>
                    <strong>{t('標示時間與產生字幕')}</strong>
                    {openai ? (
                      <small>{t('gpt-transcribe 不提供字幕時間。')}</small>
                    ) : null}
                  </span>
                </label>
                <label
                  className={`check-row ${openai || !config.diarization_available ? 'unavailable' : ''}`}
                >
                  <input
                    type="checkbox"
                    name="diarize"
                    checked={options.diarize}
                    disabled={openai || !config.diarization_available}
                    onChange={(event) =>
                      setOptions({
                        ...options,
                        diarize: event.target.checked,
                        timestamps: event.target.checked || options.timestamps,
                      })
                    }
                  />
                  <span>
                    <strong>{t('區分不同語者')}</strong>
                    <small>
                      {openai
                        ? t('gpt-transcribe 不提供語者辨識。')
                        : config.diarization_available
                          ? t('適合訪談與多人課程，處理時間會較長。')
                          : t('語者辨識目前尚未開放。')}
                    </small>
                  </span>
                </label>
              </div>
            </details>
          </section>
        </fieldset>
        {error ? (
          <div className="notice error submit-error" role="alert">
            <Icon name="info" />
            <span>{t(error)}</span>
          </div>
        ) : null}
        {busy && progress !== null ? (
          <div className="upload-progress" role="status" aria-live="polite">
            <div>
              <span>
                {progress === 100
                  ? t('檔案已傳送，正在確認上傳…')
                  : t('正在上傳檔案，請保留此頁面。')}
              </span>
              <strong>{progress}%</strong>
            </div>
            <progress
              value={progress}
              max="100"
              aria-label={t('檔案上傳進度')}
            />
          </div>
        ) : null}
        <div className="submit-row">
          {busy ? (
            <span role="status">
              {source === 'url'
                ? t('正在建立轉錄…')
                : t('上傳完成後會開始轉錄')}
            </span>
          ) : null}
          <button
            className="button primary"
            type="submit"
            disabled={busy || keySaving || !backend?.available}
          >
            {busy ? <span className="spinner" /> : null}
            {busy ? t('正在送出') : t('開始轉錄')}
            {!busy ? <Icon name="arrow" size={19} /> : null}
          </button>
        </div>
      </form>
    </div>
  );
}
