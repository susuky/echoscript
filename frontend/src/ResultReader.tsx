import { useLocale } from './i18n';
import { useDeferredValue, useEffect, useRef, useState } from 'react';
import {
  formatDate,
  formatTime,
  friendlyError,
  isoDate,
  jobTitle,
  request,
  statusLabel,
} from './api';
import type { ChunkState, Config, Job, Result, Segment } from './api';
import { Icon } from './Icons';

const qualityLabels: Record<string, string> = {
  no_speech: '此段未偵測到聲音',
  possible_omission: '可能漏辨，請回聽確認',
  possible_truncation: '內容可能截斷，請核對句尾',
  possible_repetition: '內容可能重複，請核對原音',
  low_confidence: '辨識較不確定，請核對原音',
};

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
  onJobUpdate,
}: {
  job: Job;
  config: Config | null;
  onNew: () => void;
  onJobUpdate: (job: Job) => void;
}) {
  const { t, locale } = useLocale();
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [view, setView] = useState<'segments' | 'text'>('segments');
  const [query, setQuery] = useState('');
  const [copyMessage, setCopyMessage] = useState('');
  const [chunks, setChunks] = useState<ChunkState>({ chunks: [], progress: null });
  const [actionError, setActionError] = useState('');
  const [actionBusy, setActionBusy] = useState(false);
  const [editing, setEditing] = useState<{ id: string; revision: string; text: string } | null>(null);
  const [audioError, setAudioError] = useState(false);
  const player = useRef<HTMLAudioElement>(null);
  const active = job.status === 'running' || job.status === 'queued';
  const settled = ['done', 'failed', 'cancelled'].includes(job.status);
  const deferredQuery = useDeferredValue(query.trim());

  useEffect(() => {
    if (job.status === 'uploading') return;
    const controller = new AbortController();
    setError('');
    request<Result>(`/api/jobs/${encodeURIComponent(job.id)}/result`, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setResult(data);
      })
      .catch((issue) => {
        if (!controller.signal.aborted && job.status === 'done') setError(issue.message);
      });
    request<ChunkState>(`/api/jobs/${encodeURIComponent(job.id)}/chunks`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setChunks(data); })
      .catch(() => { if (!controller.signal.aborted) setActionError('無法更新片段進度，請重新載入。'); });
    return () => controller.abort();
  }, [job.id, job.status, job.updated_at, retry]);

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

  async function playFrom(seconds: number) {
    if (!player.current) return;
    try {
      player.current.currentTime = Math.max(0, seconds);
      await player.current.play();
      setAudioError(false);
    } catch {
      setAudioError(true);
    }
  }

  async function resume(stage: 'failed' | 'asr' | 'alignment', chunkId?: string) {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError('');
    try {
      const next = await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}/resume`, {
        method: 'POST', body: JSON.stringify({ stage, chunk_ids: chunkId ? [chunkId] : [],
          revision: result?.transcript.metadata?.revision }),
      });
      setEditing(null);
      onJobUpdate(next);
    } catch (issue) {
      setActionError(issue instanceof Error ? issue.message : '無法送出，請稍後再試。');
    } finally { setActionBusy(false); }
  }

  async function cancel() {
    setActionBusy(true);
    setActionError('');
    try {
      onJobUpdate(await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: 'POST' }));
    } catch (issue) {
      setActionError(issue instanceof Error ? issue.message : '無法停止，請稍後再試。');
    } finally { setActionBusy(false); }
  }

  async function save() {
    if (!editing || actionBusy) return;
    setActionBusy(true);
    setActionError('');
    try {
      const updated = await request<Result>(`/api/jobs/${encodeURIComponent(job.id)}/segments/${encodeURIComponent(editing.id)}`, {
        method: 'PATCH', body: JSON.stringify({ text: editing.text, revision: editing.revision }),
      });
      setResult(updated);
      setEditing(null);
      setCopyMessage('已儲存修正，下載內容已更新。');
    } catch (issue) {
      setActionError(issue instanceof Error ? issue.message : '無法儲存，請稍後再試。');
    } finally { setActionBusy(false); }
  }

  function startEditing(segment: Segment) {
    const revision = result?.transcript.metadata?.revision;
    if (segment.id && revision) {
      setActionError('');
      setEditing({ id: segment.id, revision, text: segment.text });
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
  const unavailableAlignment = ['unavailable', 'partial'].includes(transcript?.metadata?.alignment?.status || '');
  const subtitleGaps = Boolean(transcript?.metadata?.subtitles?.gaps?.length);
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

      {actionError ? (
        <div className="notice error" role="alert"><Icon name="info" /><span>{t(actionError)}</span>
          <button onClick={() => { setActionError(''); setRetry((value) => value + 1); }}>{t("重新載入")}</button>
        </div>
      ) : null}
      {job.status !== 'done' ? (
        <section className={`processing-state ${settled ? 'failed' : ''} ${result ? 'with-result' : ''}`} aria-live="polite">
          {!result ? <span className="processing-symbol">{settled ? <Icon name="info" size={34} /> : <span className="spinner" />}</span> : null}
          <h2>{job.status === 'failed' ? t("轉錄尚未完成") : t(statusLabel(job))}</h2>
          <p>{job.status === 'failed'
            ? t(friendlyError(500, job.error || ''))
            : job.status === 'cancelled' ? t("可繼續處理；已完成的片段會沿用。")
            : job.cancel_requested ? t("目前片段完成後會停止，已完成的內容會保留。")
            : job.status === 'uploading' ? t("檔案尚未上傳完成。若上傳已中斷，請重新建立轉錄。")
            : t("已完成的片段會保留，下方可查看目前進度。")}</p>
          <div className="processing-actions">
            {settled ? <button className="button primary" disabled={actionBusy} onClick={() => resume('failed')}>{t("繼續未完成片段")}</button> : null}
            {active ? <button className="button" disabled={actionBusy || job.cancel_requested} onClick={cancel}>{t(job.cancel_requested ? "正在停止" : "停止處理")}</button> : null}
            {job.status === 'uploading' ? <button className="button" onClick={onNew}>{t("重新建立轉錄")}</button> : null}
          </div>
        </section>
      ) : null}
      {chunks.progress ? (
        <section className="chunk-progress" aria-label={t("片段進度")}>
          <div><strong>{t("已辨識 {done} / {total} 個片段", { done: chunks.progress.recognized, total: chunks.progress.total })}</strong>
            <span>{formatTime(chunks.progress.processed_seconds)} / {formatTime(chunks.progress.duration)}</span></div>
          <progress value={chunks.progress.recognized} max={chunks.progress.total || 1} aria-label={t("片段進度")} />
          <p>{t("字幕時間已完成 {count} 段", { count: chunks.progress.aligned })}
            {chunks.progress.failed ? ` · ${t("{count} 段需要重試", { count: chunks.progress.failed })}` : ''}</p>
          {chunks.chunks.some((chunk) => chunk.asr_status === 'failed') ? (
            <div className="failed-chunks">{chunks.chunks.filter((chunk) => chunk.asr_status === 'failed').map((chunk) => (
              <div key={chunk.id}><button className="time-button" onClick={() => playFrom(chunk.start)}>{formatTime(chunk.start)}–{formatTime(chunk.end)}</button>
                <span>{t("此段尚未取得文字")}</span><button className="button compact" disabled={!settled || actionBusy || Boolean(editing)} onClick={() => resume('asr', chunk.id)}>{t("重新辨識")}</button></div>
            ))}</div>
          ) : null}
        </section>
      ) : null}
      {result || chunks.chunks.length ? (
        <section className="audio-review" aria-label={t("原音核對")}>
          <div><strong>{t("原音核對")}</strong><span>{t("點選段落時間，從該處開始回聽。")}</span></div>
          <audio ref={player} controls preload="metadata" src={`/api/jobs/${encodeURIComponent(job.id)}/audio`}
            aria-label={t("播放原音")} onError={() => setAudioError(true)} onCanPlay={() => setAudioError(false)} />
          {audioError ? <p role="alert">{t("目前無法播放原音，請稍後重新載入。")}</p> : null}
        </section>
      ) : null}
      {error ? (
        <div className="notice error" role="alert"><Icon name="info" /><span>{t(error)}</span>
          <button onClick={() => setRetry((value) => value + 1)}>{t("重新載入")}</button></div>
      ) : null}
      {!result && job.status === 'done' && !error ? (
        <div className="loading-screen" role="status"><span className="spinner" /><p>{t("正在載入逐字稿…")}</p></div>
      ) : null}
      {result ? (
        <>
          {transcript?.metadata?.unapplied_edits?.length ? (
            <div className="notice" role="status"><Icon name="info" />
              <div><strong>{t("有 {count} 筆修正尚未套用", { count: transcript.metadata.unapplied_edits.length })}</strong>
                <p>{t("分段或原文已變更，請核對後重新修正。")}</p>
                <details className="original-text"><summary>{t("查看保留的修正")}</summary>
                  {transcript.metadata.unapplied_edits.map((edit) => <p key={edit.segment_id}>{edit.text}</p>)}
                </details>
              </div>
            </div>
          ) : null}
          {preservedJapanese ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t("為保留日文字形，這份逐字稿未自動轉換繁體中文。")}</span>
            </div>
          ) : null}
          {unavailableAlignment || subtitleGaps ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t("部分段落的字幕時間仍需核對；下載的字幕僅包含通過檢查的段落。")}</span>
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
              {!plainText.trim() && !hasSegments ? (
                <div className="no-matches">
                  <p>{t("目前沒有辨識到文字，請回聽確認是否有語音。")}</p>
                  <span>{t("請確認音訊內容與音量後重新上傳。")}</span>
                </div>
              ) : view === 'segments' && hasSegments ? (
                visibleSegments.length ? (
                  visibleSegments.map((segment, index) => (
                    <div className={`transcript-segment ${segment.alignment === 'unavailable' ? 'alignment-gap' : ''}`} key={segment.id || `${segment.start}-${index}`}>
                      <div className="segment-meta">
                        <button className="time-button" onClick={() => playFrom(segment.start)} aria-label={t("從 {time} 回聽", { time: formatTime(segment.start) })}>
                          <time>{formatTime(segment.start)}</time>
                        </button>
                        {segment.alignment === 'unavailable' || segment.alignment === 'disabled' ? <span>{t("片段起點")}</span> : null}
                        {segment.speaker ? <span>{speakerLabel(segment.speaker)}</span> : null}
                      </div>
                      <div className="segment-body">
                        {editing && editing.id === segment.id ? (
                          <form className="segment-editor" onSubmit={(event) => { event.preventDefault(); save(); }}>
                            <label htmlFor="segment-text">{t("修正文字")}</label>
                            <textarea id="segment-text" autoFocus value={editing.text} maxLength={20000}
                              onChange={(event) => setEditing({ ...editing, text: event.target.value })} />
                            <div className="segment-actions"><button className="button compact primary" type="submit" disabled={actionBusy || !editing.text.trim()}>{t(actionBusy ? "正在儲存…" : "儲存修正")}</button>
                              <button className="button compact" type="button" disabled={actionBusy} onClick={() => setEditing(null)}>{t("取消修正")}</button></div>
                          </form>
                        ) : <p><Highlight text={segment.text || t("此段尚未取得文字")} query={deferredQuery} /></p>}
                        {segment.alignment === 'unavailable' ? <span className="segment-quality">{t("字幕時間待核對")}</span> : null}
                        {segment.diagnostics?.subtitle_errors?.length ? <span className="segment-quality">{t("此段未納入字幕，請核對文字與時間。")}</span> : null}
                        {[...new Set(segment.diagnostics?.issues || [])].filter((issue) => qualityLabels[issue]).map((issue) => (
                          <span className="segment-quality" key={issue}>{t(qualityLabels[issue])}</span>
                        ))}
                        {segment.diagnostics?.edited ? <span className="segment-edited">{t("已修正")}</span> : null}
                        {!editing ? <div className="segment-actions">
                          {segment.id && transcript?.metadata?.revision ? <button className="button compact" disabled={!settled || actionBusy} onClick={() => startEditing(segment)}>{t("修正文字")}</button> : null}
                          {segment.diagnostics?.chunk_id ? <>
                            <button className="button compact" disabled={!settled || actionBusy} onClick={() => resume('asr', segment.diagnostics?.chunk_id)}>{t("重新辨識此片段")}</button>
                            {segment.alignment === 'unavailable' && segment.text.trim() ? <button className="button compact" disabled={!settled || actionBusy} onClick={() => resume('alignment', segment.diagnostics?.chunk_id)}>{t("重試字幕時間")}</button> : null}
                          </> : null}
                        </div> : null}
                        {segment.raw_text && segment.raw_text !== segment.text ? <details className="original-text"><summary>{t("查看辨識原文")}</summary><p>{segment.raw_text}</p></details> : null}
                        {segment.diagnostics?.edited && segment.diagnostics?.chunk_id ? <small className="retry-note">{t("重新辨識會取代這段原音範圍內的文字修正。")}</small> : null}
                      </div>
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
      ) : null}
    </div>
  );
}
