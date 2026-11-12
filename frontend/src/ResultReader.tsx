import { useLocale } from './i18n';
import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
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
import ConfirmDialog from './ConfirmDialog';

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
  onDirtyChange,
}: {
  job: Job;
  config: Config | null;
  onNew: () => void;
  onJobUpdate: (job: Job) => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { t, locale } = useLocale();
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [view, setView] = useState<'segments' | 'text'>('segments');
  const [query, setQuery] = useState('');
  const [copyMessage, setCopyMessage] = useState('');
  const [chunks, setChunks] = useState<ChunkState>({
    chunks: [],
    progress: null,
    result_available: false,
  });
  const [chunkError, setChunkError] = useState('');
  const [actionError, setActionError] = useState('');
  const [actionBusy, setActionBusy] = useState(false);
  const [editing, setEditing] = useState<{
    id: string;
    revision: string;
    text: string;
    originalText: string;
  } | null>(null);
  const [confirmation, setConfirmation] = useState<
    'discard' | { chunkId: string } | null
  >(null);
  const [audioError, setAudioError] = useState(false);
  const player = useRef<HTMLAudioElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const active = job.status === 'running' || job.status === 'queued';
  const settled = ['done', 'failed', 'cancelled'].includes(job.status);
  const isEditing = Boolean(editing);
  const readable =
    job.status === 'done' || Boolean(result) || chunks.result_available;
  const deferredQuery = useDeferredValue(query.trim());
  const dirty = Boolean(editing && editing.text !== editing.originalText);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);

  useEffect(() => {
    if (job.status === 'uploading') return;
    const controller = new AbortController();
    setError('');
    if (!isEditing && readable) {
      request<Result>(`/api/jobs/${encodeURIComponent(job.id)}/result`, {
        signal: controller.signal,
      })
        .then((data) => {
          if (!controller.signal.aborted) setResult(data);
        })
        .catch((issue) => {
          if (!controller.signal.aborted && job.status === 'done')
            setError(issue.message);
        });
    }
    request<ChunkState>(`/api/jobs/${encodeURIComponent(job.id)}/chunks`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) {
          setChunks(data);
          setChunkError('');
        }
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setChunkError('無法更新片段進度，請重新載入。');
      });
    return () => controller.abort();
  }, [job.id, job.status, job.updated_at, retry, isEditing, readable]);

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

  async function resume(
    stage: 'failed' | 'asr' | 'alignment',
    chunkId?: string,
  ) {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError('');
    try {
      const next = await request<Job>(
        `/api/jobs/${encodeURIComponent(job.id)}/resume`,
        {
          method: 'POST',
          body: JSON.stringify({
            stage,
            chunk_ids: chunkId ? [chunkId] : [],
            revision: result?.transcript.metadata?.revision,
          }),
        },
      );
      setEditing(null);
      onJobUpdate(next);
    } catch (issue) {
      setActionError(
        issue instanceof Error ? issue.message : '無法送出，請稍後再試。',
      );
    } finally {
      setActionBusy(false);
    }
  }

  function transcribeAgain(chunkId: string) {
    if (
      result?.transcript.segments.some(
        (segment) =>
          segment.diagnostics?.chunk_id === chunkId &&
          segment.diagnostics.edited,
      )
    ) {
      setConfirmation({ chunkId });
    } else {
      resume('asr', chunkId);
    }
  }

  async function cancel() {
    if (actionBusy) return;
    setActionBusy(true);
    setActionError('');
    try {
      onJobUpdate(
        await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {
          method: 'POST',
        }),
      );
    } catch (issue) {
      setActionError(
        issue instanceof Error ? issue.message : '無法停止，請稍後再試。',
      );
    } finally {
      setActionBusy(false);
    }
  }

  async function save() {
    if (!editing || actionBusy) return;
    if (!editing.text.trim()) {
      setActionError('修正文字不可為空白。');
      editor.current?.focus();
      return;
    }
    setActionBusy(true);
    setActionError('');
    try {
      const updated = await request<Result>(
        `/api/jobs/${encodeURIComponent(job.id)}/segments/${encodeURIComponent(editing.id)}`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            text: editing.text,
            revision: editing.revision,
          }),
        },
      );
      setResult(updated);
      setEditing(null);
      setCopyMessage('已儲存修正，下載內容已更新。');
    } catch (issue) {
      setActionError(
        issue instanceof Error ? issue.message : '無法儲存，請稍後再試。',
      );
    } finally {
      setActionBusy(false);
    }
  }

  function startEditing(segment: Segment) {
    const revision = result?.transcript.metadata?.revision;
    if (segment.id && revision) {
      setActionError('');
      setCopyMessage('');
      setEditing({
        id: segment.id,
        revision,
        text: segment.text,
        originalText: segment.text,
      });
    }
  }

  function discardEdit() {
    setEditing(null);
    setActionError('');
  }

  const transcript = result?.transcript;
  const segments = transcript?.segments || [];
  const hasSegments = segments.length > 0;
  const readingSegments = view === 'segments' && hasSegments;
  const plainText = transcript?.text || result?.text || '';
  const { visibleSegments, matchCount } = useMemo(() => {
    if (!deferredQuery) return { visibleSegments: segments, matchCount: 0 };
    const needle = deferredQuery.toLocaleLowerCase();
    const matchingSegments = segments.filter((segment) =>
      segment.text.toLocaleLowerCase().includes(needle),
    );
    const searchText = readingSegments
      ? matchingSegments.map((segment) => segment.text)
      : [plainText];
    return {
      visibleSegments: matchingSegments,
      matchCount: searchText.reduce(
        (count, text) =>
          count + text.toLocaleLowerCase().split(needle).length - 1,
        0,
      ),
    };
  }, [segments, plainText, deferredQuery, readingSegments]);
  const readerError = (!editing && actionError) || chunkError;
  const unavailableAlignment =
    job.options.timestamps &&
    ['unavailable', 'partial'].includes(
      transcript?.metadata?.alignment?.status || '',
    );
  const subtitleGaps =
    job.options.timestamps &&
    Boolean(transcript?.metadata?.subtitles?.gaps?.length);
  const preservedJapanese =
    transcript?.metadata?.normalization?.reason === 'japanese_text_preserved' &&
    job.options.language !== 'ja';
  const unavailableSpeakers = Boolean(
    job.options?.diarize &&
    transcript &&
    !segments.some((segment) => segment.speaker),
  );
  const language = config?.languages.find(
    (item) => item.code === (transcript?.language || job.options.language || 'auto'),
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
        <h1>{job.source_value ? jobTitle(job) : t('未命名轉錄')}</h1>
        <div className="result-meta">
          <span className={`status-label ${job.status}`} role="status">
            <span className={`status-dot ${job.status}`} />
            {t(statusLabel(job))}
          </span>
          <time dateTime={isoDate(job.created_at)}>
            {formatDate(job.created_at, locale)}
          </time>
          {transcript?.duration != null ? (
            <span>
              <Icon name="clock" size={14} />
              {formatTime(transcript.duration)}
            </span>
          ) : null}
          {language ? <span>{t(language)}</span> : null}
        </div>
      </div>

      {readerError ? (
        <div className="notice error" role="alert">
          <Icon name="info" />
          <span>{t(readerError)}</span>
          <button
            type="button"
            onClick={() => {
              setActionError('');
              setChunkError('');
              setRetry((value) => value + 1);
            }}
          >
            {t('重新載入')}
          </button>
        </div>
      ) : null}
      {job.status !== 'done' ? (
        <section
          className={`processing-state ${settled ? 'failed' : ''} ${result ? 'with-result' : ''}`}
          aria-label={t(statusLabel(job))}
        >
          {!result ? (
            <span className="processing-symbol">
              {settled ? (
                <Icon name="info" size={34} />
              ) : (
                <span className="spinner" />
              )}
            </span>
          ) : null}
          {settled || job.cancel_requested || job.status === 'uploading' ? (
            <p>
              {job.status === 'failed'
                ? t(friendlyError(500, job.error || ''))
                : job.status === 'cancelled'
                  ? t('可繼續處理；已完成的片段會沿用。')
                  : job.cancel_requested
                    ? t('目前片段完成後會停止，已完成的內容會保留。')
                    : t('檔案尚未上傳完成。若上傳已中斷，請重新建立轉錄。')}
            </p>
          ) : null}
          <div className="processing-actions">
            {settled ? (
              <button
                type="button"
                className="button primary"
                disabled={actionBusy || Boolean(editing)}
                onClick={() => resume('failed')}
              >
                {t('繼續未完成片段')}
              </button>
            ) : null}
            {active ? (
              <button
                type="button"
                className="button"
                disabled={actionBusy || job.cancel_requested}
                onClick={cancel}
              >
                {t(job.cancel_requested ? '正在停止' : '停止處理')}
              </button>
            ) : null}
            {job.status === 'uploading' ? (
              <button type="button" className="button" onClick={onNew}>
                {t('重新建立轉錄')}
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
      {chunks.progress && job.status !== 'done' ? (
        <section className="chunk-progress" aria-label={t('片段進度')}>
          <div>
            <strong>
              {t('已辨識 {done} / {total} 個片段', {
                done: chunks.progress.recognized,
                total: chunks.progress.total,
              })}
            </strong>
            <span>
              {formatTime(chunks.progress.processed_seconds)} /{' '}
              {formatTime(chunks.progress.duration)}
            </span>
          </div>
          <progress
            value={chunks.progress.recognized}
            max={chunks.progress.total || 1}
            aria-label={t('片段進度')}
          />
          {job.options.timestamps || chunks.progress.failed ? (
            <p>
              {job.options.timestamps
                ? t('字幕時間已完成 {count} 段', {
                    count: chunks.progress.aligned,
                  })
                : null}
              {job.options.timestamps && chunks.progress.failed ? ' · ' : null}
              {chunks.progress.failed
                ? t('{count} 段需要重試', { count: chunks.progress.failed })
                : null}
            </p>
          ) : null}
          {chunks.chunks.some((chunk) => chunk.asr_status === 'failed') ? (
            <div className="failed-chunks">
              {chunks.chunks
                .filter((chunk) => chunk.asr_status === 'failed')
                .map((chunk) => (
                  <div key={chunk.id}>
                    <button
                      type="button"
                      className="time-button"
                      onClick={() => playFrom(chunk.start)}
                      aria-label={t('從 {time} 回聽', {
                        time: formatTime(chunk.start),
                      })}
                    >
                      {formatTime(chunk.start)}–{formatTime(chunk.end)}
                    </button>
                    <span>{t('此段尚未取得文字')}</span>
                    <button
                      type="button"
                      className="button compact"
                      disabled={!settled || actionBusy || Boolean(editing)}
                      aria-label={t('重新辨識此片段：{time}', {
                        time: formatTime(chunk.start),
                      })}
                      onClick={() => transcribeAgain(chunk.id)}
                    >
                      {t('重新辨識')}
                    </button>
                  </div>
                ))}
            </div>
          ) : null}
        </section>
      ) : null}
      {result || chunks.chunks.length ? (
        <section className="audio-review" aria-label={t('原音核對')}>
          <div>
            <strong>{t('原音核對')}</strong>
          </div>
          <audio
            ref={player}
            controls
            preload="metadata"
            src={`/api/jobs/${encodeURIComponent(job.id)}/audio`}
            aria-label={t('播放原音')}
            onError={() => setAudioError(true)}
            onCanPlay={() => setAudioError(false)}
          />
          {audioError ? (
            <div className="notice error" role="alert">
              <span>{t('目前無法播放原音，請稍後重新載入。')}</span>
              <button type="button" onClick={() => player.current?.load()}>
                {t('重新載入')}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}
      {error ? (
        <div className="notice error" role="alert">
          <Icon name="info" />
          <span>{t(error)}</span>
          <button type="button" onClick={() => setRetry((value) => value + 1)}>
            {t('重新載入')}
          </button>
        </div>
      ) : null}
      {!result && job.status === 'done' && !error ? (
        <div className="loading-screen" role="status">
          <span className="spinner" />
          <p>{t('正在載入逐字稿…')}</p>
        </div>
      ) : null}
      {result ? (
        <>
          {transcript?.metadata?.unapplied_edits?.length ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <div>
                <strong>
                  {t('有 {count} 筆修正尚未套用', {
                    count: transcript.metadata.unapplied_edits.length,
                  })}
                </strong>
                <p>{t('分段或原文已變更，請核對後重新修正。')}</p>
                <details className="original-text">
                  <summary>{t('查看保留的修正')}</summary>
                  {transcript.metadata.unapplied_edits.map((edit) => (
                    <p key={edit.segment_id}>{edit.text}</p>
                  ))}
                </details>
              </div>
            </div>
          ) : null}
          {preservedJapanese ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t('已保留日文原始字形。')}</span>
            </div>
          ) : null}
          {unavailableAlignment || subtitleGaps ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t('部分段落未納入字幕，請核對文字與時間。')}</span>
            </div>
          ) : null}
          {unavailableSpeakers ? (
            <div className="notice" role="status">
              <Icon name="info" />
              <span>{t('未取得語者標記。')}</span>
            </div>
          ) : null}
          <section className="transcript-sheet" aria-label={t('逐字稿')}>
            <div className="reader-toolbar">
              <div
                className="reading-tabs"
                role="group"
                aria-label={t('閱讀模式')}
              >
                <button
                  type="button"
                  className={readingSegments ? 'active' : ''}
                  aria-pressed={readingSegments}
                  disabled={!hasSegments || Boolean(editing)}
                  onClick={() => setView('segments')}
                >
                  {t('分段閱讀')}
                </button>
                <button
                  type="button"
                  className={!readingSegments ? 'active' : ''}
                  aria-pressed={!readingSegments}
                  disabled={Boolean(editing)}
                  onClick={() => setView('text')}
                >
                  {t('完整文字')}
                </button>
              </div>
              <div className="reader-actions">
                <button
                  type="button"
                  className="button compact"
                  onClick={copy}
                  disabled={Boolean(editing)}
                >
                  <Icon name="copy" size={16} />
                  {t('複製')}
                </button>
                {editing ? (
                  <button type="button" className="button compact" disabled>
                    <Icon name="download" size={16} />
                    {t('下載')}
                  </button>
                ) : (
                  <details
                    className="download-menu"
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') {
                        event.currentTarget.open = false;
                        event.currentTarget.querySelector('summary')?.focus();
                      }
                    }}
                  >
                    <summary className="button compact">
                      <Icon name="download" size={16} />
                      {t('下載')}
                    </summary>
                    <div className="download-options">
                      {result.files.length ? (
                        result.files.map((file) => (
                          <a
                            key={file.format}
                            href={file.url}
                            download
                            onClick={(event) => {
                              const details =
                                event.currentTarget.closest('details');
                              if (details) details.open = false;
                            }}
                          >
                            <span>
                              {{
                                txt: t('純文字'),
                                srt: t('SRT 字幕'),
                                vtt: t('VTT 字幕'),
                                json: t('完整資料'),
                              }[file.format] || file.format.toUpperCase()}
                            </span>
                            <small>.{file.format}</small>
                          </a>
                        ))
                      ) : (
                        <span>{t('目前沒有可下載的檔案')}</span>
                      )}
                    </div>
                  </details>
                )}
              </div>
            </div>
            <search className="search-row" aria-label={t('搜尋逐字稿')}>
              <label className="search-field" htmlFor="reader-search">
                <Icon name="search" size={18} />
                <span>{t('搜尋逐字稿')}</span>
                <input
                  id="reader-search"
                  name="transcript-search"
                  type="search"
                  autoComplete="off"
                  autoCorrect="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  value={query}
                  disabled={Boolean(editing)}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <span className="search-count" role="status">
                {deferredQuery
                  ? t('{count} 處符合', { count: matchCount })
                  : readingSegments
                    ? t('{count} 個段落', { count: segments.length })
                    : ''}
              </span>
              {query ? (
                <button
                  type="button"
                  className="button compact search-clear"
                  disabled={Boolean(editing)}
                  onClick={() => setQuery('')}
                >
                  {t('清除搜尋')}
                </button>
              ) : null}
            </search>
            <div className="transcript-content">
              {!plainText.trim() && !hasSegments ? (
                <div className="no-matches">
                  <p>{t('目前沒有辨識到文字，請回聽確認是否有語音。')}</p>
                </div>
              ) : readingSegments ? (
                visibleSegments.length ? (
                  visibleSegments.map((segment, index) => (
                    <div
                      className={`transcript-segment ${job.options.timestamps && segment.alignment === 'unavailable' ? 'alignment-gap' : ''}`}
                      key={segment.id || `${segment.start}-${index}`}
                    >
                      <div className="segment-meta">
                        <button
                          type="button"
                          className="time-button"
                          onClick={() => playFrom(segment.start)}
                          aria-label={t('從 {time} 回聽', {
                            time: formatTime(segment.start),
                          })}
                        >
                          <time>{formatTime(segment.start)}</time>
                        </button>
                        {segment.alignment === 'unavailable' ||
                        segment.alignment === 'disabled' ? (
                          <span>{t('片段起點')}</span>
                        ) : null}
                        {segment.speaker ? (
                          <span>{speakerLabel(segment.speaker)}</span>
                        ) : null}
                      </div>
                      <div className="segment-body">
                        {editing && editing.id === segment.id ? (
                          <form
                            className="segment-editor"
                            method="post"
                            aria-label={t('修正文字：{time}', {
                              time: formatTime(segment.start),
                            })}
                            onSubmit={(event) => {
                              event.preventDefault();
                              save();
                            }}
                          >
                            <label htmlFor="segment-text">
                              {t('修正文字')}{' '}
                              {dirty ? (
                                <span className="optional">
                                  {t('尚未儲存')}
                                </span>
                              ) : null}
                            </label>
                            <textarea
                              ref={editor}
                              id="segment-text"
                              name="text"
                              autoFocus
                              required
                              value={editing.text}
                              maxLength={20000}
                              aria-describedby={
                                actionError ? 'segment-edit-error' : undefined
                              }
                              disabled={actionBusy}
                              onChange={(event) => {
                                setEditing({
                                  ...editing,
                                  text: event.target.value,
                                });
                                setActionError('');
                              }}
                            />
                            {actionError ? (
                              <p
                                id="segment-edit-error"
                                className="field-error"
                                role="alert"
                              >
                                {t(actionError)}
                              </p>
                            ) : null}
                            <div className="segment-actions">
                              <button
                                className="button compact primary"
                                type="submit"
                                disabled={actionBusy}
                              >
                                {t(actionBusy ? '正在儲存…' : '儲存修正')}
                              </button>
                              <button
                                className="button compact"
                                type="button"
                                disabled={actionBusy}
                                onClick={() =>
                                  dirty
                                    ? setConfirmation('discard')
                                    : discardEdit()
                                }
                              >
                                {t('取消修正')}
                              </button>
                            </div>
                          </form>
                        ) : (
                          <p>
                            <Highlight
                              text={segment.text || t('此段尚未取得文字')}
                              query={deferredQuery}
                            />
                          </p>
                        )}
                        {job.options.timestamps &&
                        segment.alignment === 'unavailable' ? (
                          <span className="segment-quality">
                            {t('字幕時間待核對')}
                          </span>
                        ) : null}
                        {job.options.timestamps &&
                        segment.diagnostics?.subtitle_errors?.length ? (
                          <span className="segment-quality">
                            {t('此段未納入字幕，請核對文字與時間。')}
                          </span>
                        ) : null}
                        {[...new Set(segment.diagnostics?.issues || [])]
                          .filter((issue) => qualityLabels[issue])
                          .map((issue) => (
                            <span className="segment-quality" key={issue}>
                              {t(qualityLabels[issue])}
                            </span>
                          ))}
                        {segment.diagnostics?.edited ? (
                          <span className="segment-edited">{t('已修正')}</span>
                        ) : null}
                        {!editing ? (
                          <div className="segment-actions">
                            {segment.id && transcript?.metadata?.revision ? (
                              <button
                                type="button"
                                className="button compact"
                                disabled={!settled || actionBusy}
                                aria-label={t('修正文字：{time}', {
                                  time: formatTime(segment.start),
                                })}
                                onClick={() => startEditing(segment)}
                              >
                                {t('修正文字')}
                              </button>
                            ) : null}
                            {segment.diagnostics?.chunk_id ? (
                              <>
                                <button
                                  type="button"
                                  className="button compact"
                                  disabled={!settled || actionBusy}
                                  aria-label={t('重新辨識此片段：{time}', {
                                    time: formatTime(segment.start),
                                  })}
                                  onClick={() =>
                                    transcribeAgain(
                                      segment.diagnostics!.chunk_id!,
                                    )
                                  }
                                >
                                  {t('重新辨識此片段')}
                                </button>
                                {job.options.timestamps &&
                                segment.alignment === 'unavailable' &&
                                segment.text.trim() ? (
                                  <button
                                    type="button"
                                    className="button compact"
                                    disabled={!settled || actionBusy}
                                    aria-label={t('重試字幕時間：{time}', {
                                      time: formatTime(segment.start),
                                    })}
                                    onClick={() =>
                                      resume(
                                        'alignment',
                                        segment.diagnostics?.chunk_id,
                                      )
                                    }
                                  >
                                    {t('重試字幕時間')}
                                  </button>
                                ) : null}
                              </>
                            ) : null}
                          </div>
                        ) : null}
                        {segment.raw_text &&
                        segment.raw_text !== segment.text ? (
                          <details className="original-text">
                            <summary>{t('查看辨識原文')}</summary>
                            <p>{segment.raw_text}</p>
                          </details>
                        ) : null}
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="no-matches">
                    <Icon name="search" size={25} />
                    <p>{t('找不到符合的段落')}</p>
                  </div>
                )
              ) : (
                <div className="full-transcript">
                  <Highlight text={plainText} query={deferredQuery} />
                </div>
              )}
            </div>
          </section>
          <span className="copy-feedback" role="status">
            {t(copyMessage)}
          </span>
        </>
      ) : null}
      {confirmation ? (
        <ConfirmDialog
          title={
            confirmation === 'discard' ? '捨棄這次修正？' : '重新辨識這段原音？'
          }
          message={
            confirmation === 'discard'
              ? '尚未儲存的文字會被捨棄。'
              : '重新辨識會取代這段原音範圍內的文字修正。'
          }
          confirmLabel={
            confirmation === 'discard' ? '捨棄修正' : '取代修正並重新辨識'
          }
          cancelLabel={confirmation === 'discard' ? '繼續修正' : '保留修正'}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => {
            if (confirmation === 'discard') discardEdit();
            else resume('asr', confirmation.chunkId);
            setConfirmation(null);
          }}
        />
      ) : null}
    </div>
  );
}
