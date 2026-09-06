import { useLocale } from './i18n';
import { useDeferredValue, useEffect, useState } from 'react';
import {
  formatDate,
  formatTime,
  friendlyError,
  isoDate,
  jobTitle,
  request,
  statusLabel,
} from './api';
import type { Config, Job, Result } from './api';
import { Icon } from './Icons';

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const lower = text.toLocaleLowerCase();
  const needle = query.toLocaleLowerCase();
  const parts = [];
  let start = 0;
  let match = lower.indexOf(needle);
  while (match !== -1) {
    parts.push(
      text.slice(start, match),
      <mark key={match}>{text.slice(match, match + query.length)}</mark>,
    );
    start = match + query.length;
    match = lower.indexOf(needle, start);
  }
  parts.push(text.slice(start));
  return <>{parts}</>;
}

export default function ResultReader({
  job,
  config,
  onNew,
}: {
  job: Job;
  config: Config | null;
  onNew: () => void;
}) {
  const { t, locale } = useLocale();
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [view, setView] = useState<'segments' | 'text'>('segments');
  const [query, setQuery] = useState('');
  const [copyMessage, setCopyMessage] = useState('');
  const deferredQuery = useDeferredValue(query.trim());

  useEffect(() => {
    if (job.status !== 'done') return;
    const controller = new AbortController();
    setError('');
    request<Result>(`/api/jobs/${encodeURIComponent(job.id)}/result`, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setResult(data);
      })
      .catch((issue) => {
        if (!controller.signal.aborted) setError(issue.message);
      });
    return () => controller.abort();
  }, [job.id, job.status, retry]);

  useEffect(() => {
    if (!copyMessage) return;
    const timer = setTimeout(() => setCopyMessage(''), 4000);
    return () => clearTimeout(timer);
  }, [copyMessage]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(result!.text);
      setCopyMessage('已複製逐字稿');
    } catch {
      setCopyMessage('無法複製，請下載文字檔或選取內容複製。');
    }
  }

  const transcript = result?.transcript;
  const segments = transcript?.segments || [];
  const visibleSegments = deferredQuery
    ? segments.filter((segment) =>
        segment.text.toLocaleLowerCase().includes(deferredQuery.toLocaleLowerCase()),
      )
    : segments;
  const hasSegments = segments.length > 0;
  const plainText = transcript?.text || result?.text || '';
  const matchCount = deferredQuery
    ? plainText.toLocaleLowerCase().split(deferredQuery.toLocaleLowerCase()).length - 1
    : 0;
  const unavailableAlignment = transcript?.metadata?.alignment?.status === 'unavailable';
  const preservedJapanese =
    transcript?.metadata?.normalization?.reason === 'japanese_text_preserved' &&
    job.options.language !== 'ja';
  const unavailableSpeakers = Boolean(
    job.options?.diarize && transcript && !segments.some((segment) => segment.speaker),
  );
  const language = config?.languages.find(
    (item) => item.code === (job.options.language || 'auto'),
  )?.label;
  const speakerLabel = (speaker: string) => {
    const index = transcript?.speakers.indexOf(speaker) ?? -1;
    if (index >= 0) return t('語者 {count}', { count: index + 1 });
    const match = speaker.match(/^(?:SPEAKER[_ -]?)(\d+)$/i);
    return match ? t('語者 {count}', { count: Number(match[1]) + 1 }) : speaker;
  };

  return (
    <div className="reader page-enter">
      <div className="page-heading result-heading">
        <div className="result-file-icon">
          <Icon name={job.source_type === 'url' ? 'link' : 'file'} size={25} />
        </div>
        <h1>{job.source_value ? jobTitle(job) : t('未命名轉錄')}</h1>
        <div className="result-meta">
          <span className={`status-label ${job.status}`}>
            <span className={`status-dot ${job.status}`} />
            {t(statusLabel(job))}
          </span>
          <time dateTime={isoDate(job.created_at)}>{formatDate(job.created_at, locale)}</time>
          {transcript?.duration != null ? (
            <span>
              <Icon name="clock" size={14} />
              {formatTime(transcript.duration)}
            </span>
          ) : null}
          {language ? <span>{t("語言設定：")}{t(language)}</span> : null}
        </div>
      </div>

      {job.status !== 'done' ? (
        <section
          className={`processing-state ${job.status === 'failed' ? 'failed' : ''}`}
          aria-live="polite"
        >
          <span className="processing-symbol">
            {job.status === 'failed' ? (
              <Icon name="info" size={34} />
            ) : (
              <span className="spinner" />
            )}
          </span>
          <h2>{job.status === 'failed' ? t("這次轉錄未能完成") : t(statusLabel(job))}</h2>
          <p>
            {job.status === 'failed'
              ? t(friendlyError(500, job.error || '').replace(
                  '暫時無法連線到轉錄服務，請稍後再試。',
                  '音訊處理未能完成，請確認檔案可播放，或稍後重新上傳。',
                ))
              : job.status === 'queued'
                ? t("音訊已送出，輪到這筆內容時就會開始。")
                : job.status === 'uploading'
                  ? t("檔案尚未上傳完成。若上傳已中斷，請重新建立轉錄。")
                  : t("正在把音訊整理成文字，完成後會顯示在這裡。")}
          </p>
          {job.status === 'failed' || job.status === 'uploading' ? (
            <button className="button primary" onClick={onNew}>
              <Icon name="plus" size={18} />
              {t("重新建立轉錄")}
            </button>
          ) : (
            <small>{t("你可以在左側切換其他紀錄，稍後再回來。")}</small>
          )}
        </section>
      ) : error ? (
        <div className="notice error" role="alert">
          <Icon name="info" />
          <span>{t(error)}</span>
          <button onClick={() => setRetry((value) => value + 1)}>{t("重新載入")}</button>
        </div>
      ) : !result ? (
        <div className="loading-screen" role="status">
          <span className="spinner" />
          <p>{t("正在載入逐字稿…")}</p>
        </div>
      ) : (
        <>
          {preservedJapanese ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t("為保留日文字形，這份逐字稿未自動轉換繁體中文。")}</span>
            </div>
          ) : null}
          {unavailableAlignment ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t("逐字稿已完成，此次無法取得可靠的字幕時間。")}</span>
            </div>
          ) : null}
          {unavailableSpeakers ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t("此次無法可靠區分語者，請以逐字稿內容為準。")}</span>
            </div>
          ) : null}
          <section className="transcript-sheet" aria-label={t("逐字稿")}>
            <div className="reader-toolbar">
              <div className="reading-tabs" aria-label={t("閱讀模式")}>
                <button
                  className={view === 'segments' && hasSegments ? 'active' : ''}
                  aria-pressed={view === 'segments' && hasSegments}
                  disabled={!hasSegments}
                  onClick={() => setView('segments')}
                >
                  {t("分段閱讀")}
                </button>
                <button
                  className={view === 'text' || !hasSegments ? 'active' : ''}
                  aria-pressed={view === 'text' || !hasSegments}
                  onClick={() => setView('text')}
                >
                  {t("完整文字")}
                </button>
              </div>
              <div className="reader-actions">
                <button className="button compact" onClick={copy}>
                  <Icon name="copy" size={16} />
                  {t("複製")}
                </button>
                <details className="download-menu">
                  <summary className="button compact">
                    <Icon name="download" size={16} />
                    {t("下載")}
                  </summary>
                  <div className="download-options">
                    {result.files.length ? (
                      result.files.map((file) => (
                        <a
                          key={file.format}
                          href={file.url}
                          download
                          onClick={(event) => {
                            const details = event.currentTarget.closest('details');
                            if (details) details.open = false;
                          }}
                        >
                          <span>
                            {{ txt: t("純文字"), srt: t("SRT 字幕"), vtt: t("VTT 字幕"), json: t("完整資料") }[
                              file.format
                            ] || file.format.toUpperCase()}
                          </span>
                          <small>.{file.format}</small>
                        </a>
                      ))
                    ) : (
                      <span>{t("目前沒有可下載的檔案")}</span>
                    )}
                  </div>
                </details>
              </div>
            </div>
            <div className="search-row">
              <label className="search-field">
                <Icon name="search" size={18} />
                <input
                  type="search"
                  placeholder={t("搜尋逐字稿…")}
                  aria-label={t("搜尋逐字稿")}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <span className="search-count" aria-live="polite">
                {deferredQuery
                  ? t('{count} 處符合', { count: matchCount })
                  : hasSegments
                    ? t('{count} 個段落', { count: segments.length })
                    : t("完整逐字稿")}
              </span>
            </div>
            <div className="transcript-content">
              {!plainText.trim() ? (
                <div className="no-matches">
                  <p>{t("這段音訊沒有辨識到可轉錄的語音。")}</p>
                  <span>{t("請確認音訊內容與音量後重新上傳。")}</span>
                </div>
              ) : view === 'segments' && hasSegments ? (
                visibleSegments.length ? (
                  visibleSegments.map((segment, index) => (
                    <div className="transcript-segment" key={`${segment.start}-${index}`}>
                      <div className="segment-meta">
                        <time>{formatTime(segment.start)}</time>
                        {segment.speaker ? <span>{speakerLabel(segment.speaker)}</span> : null}
                      </div>
                      <p>
                        <Highlight text={segment.text} query={deferredQuery} />
                      </p>
                    </div>
                  ))
                ) : (
                  <div className="no-matches">
                    <Icon name="search" size={25} />
                    <p>{t("找不到符合的段落")}</p>
                    <span>{t("試試其他關鍵字，或清除搜尋。")}</span>
                  </div>
                )
              ) : (
                <div className="full-transcript">
                  <Highlight text={plainText} query={deferredQuery} />
                </div>
              )}
            </div>
            <div className="reader-footnote">{t("轉錄可能有誤，引用前請核對人名、數字與專有名詞。")}</div>
          </section>
          <span className="copy-feedback" role="status">
            {t(copyMessage)}
          </span>
        </>
      )}
    </div>
  );
}
