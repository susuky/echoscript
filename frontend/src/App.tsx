import { useLocale } from './i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import { formatDate, isoDate, jobTitle, request, statusLabel } from './api';
import type { Config, Job } from './api';
import { Brand, Icon } from './Icons';
import NewTranscript from './NewTranscript';
import ResultReader from './ResultReader';
import ConfirmDialog from './ConfirmDialog';

export default function App() {
  const { t, locale, setLocale } = useLocale();
  const [config, setConfig] = useState<Config | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [mobile, setMobile] = useState(
    () => window.matchMedia('(max-width: 800px)').matches,
  );
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<{
    job: Job | null;
  } | null>(null);
  const sidebar = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLDivElement>(null);
  const selected =
    jobs.find((item) => item.id === selectedId) ||
    (selectedJob?.id === selectedId ? selectedJob : null);

  useEffect(() => {
    const controller = new AbortController();
    if (!config) setLoading(true);
    Promise.all([
      request<Config>('/api/config', { signal: controller.signal }),
      request<{ jobs: Job[] }>('/api/jobs?limit=50', {
        signal: controller.signal,
      }),
    ])
      .then(([settings, history]) => {
        setConfig(settings);
        setJobs(history.jobs);
        setError('');
      })
      .catch((issue) => {
        if (!controller.signal.aborted) setError(issue.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    if (loading || !config) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [history, detail] = await Promise.all([
          request<{ jobs: Job[] }>('/api/jobs?limit=50', {
            signal: controller.signal,
          }),
          selectedId
            ? request<Job>(`/api/jobs/${encodeURIComponent(selectedId)}`, {
                signal: controller.signal,
              })
            : Promise.resolve(null),
        ]);
        if (!controller.signal.aborted) {
          setJobs(history.jobs);
          setSelectedJob(detail);
          setError('');
        }
      } catch (issue) {
        if (!controller.signal.aborted)
          setError(
            issue instanceof Error ? issue.message : '無法更新轉錄狀態。',
          );
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 2000);
      }
    }
    timer = setTimeout(refresh, 2000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [loading, config, selectedId]);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 800px)');
    const resize = () => {
      setMobile(media.matches);
      setSidebarOpen(false);
    };
    media.addEventListener('change', resize);
    return () => media.removeEventListener('change', resize);
  }, []);

  useEffect(() => {
    if (!sidebarOpen || !mobile) return;
    const dialog = sidebar.current!;
    dialog.showModal();
    return () => dialog.close();
  }, [sidebarOpen, mobile]);

  const updateJob = useCallback((job: Job) => {
    setJobs((current) =>
      [job, ...current.filter((item) => item.id !== job.id)].slice(0, 50),
    );
  }, []);

  function openJob(job: Job | null) {
    setHasUnsavedChanges(false);
    setSelectedId(job?.id || null);
    setSelectedJob(job);
    setSidebarOpen(false);
    window.scrollTo({ top: 0 });
    requestAnimationFrame(() =>
      heading.current?.focus({ preventScroll: true }),
    );
  }

  function selectJob(job: Job | null) {
    if ((job?.id || null) === selectedId) {
      setSidebarOpen(false);
      return;
    }
    if (hasUnsavedChanges) {
      setSidebarOpen(false);
      setPendingNavigation({ job });
    } else {
      openJob(job);
    }
  }

  const historyContent = (
    <>
      <button
        type="button"
        className="icon-button mobile-close"
        aria-label={t('關閉轉錄紀錄')}
        autoFocus={mobile}
        onClick={() => setSidebarOpen(false)}
      >
        <Icon name="close" size={18} />
      </button>
      <Brand />
      <button
        type="button"
        className={`new-button ${!selectedId ? 'selected' : ''}`}
        disabled={uploading}
        onClick={() => selectJob(null)}
      >
        <Icon name="plus" size={19} />
        {t('新增轉錄')}
      </button>
      <div className="history-heading">
        <h2>{t('轉錄紀錄')}</h2>
        <span>{jobs.length ? jobs.length : ''}</span>
      </div>
      <nav className="history-list" aria-label={t('轉錄紀錄')}>
        {loading ? (
          <p className="history-empty">{t('正在載入紀錄…')}</p>
        ) : jobs.length ? (
          jobs.map((job) => (
            <button
              key={job.id}
              type="button"
              className={`history-item ${job.id === selectedId ? 'selected' : ''}`}
              onClick={() => selectJob(job)}
              disabled={uploading}
              aria-current={job.id === selectedId ? 'page' : undefined}
            >
              <Icon
                name={job.source_type === 'url' ? 'link' : 'file'}
                size={18}
              />
              <span>
                <strong
                  title={job.source_value ? jobTitle(job) : t('未命名轉錄')}
                >
                  {job.source_value ? jobTitle(job) : t('未命名轉錄')}
                </strong>
                <span className="history-meta">
                  <time dateTime={isoDate(job.created_at)}>
                    {formatDate(job.created_at, locale)}
                  </time>
                  <span className={`status-dot ${job.status}`} />
                  <span>{t(statusLabel(job))}</span>
                </span>
              </span>
            </button>
          ))
        ) : (
          <div className="history-empty">
            <Icon name="clock" size={23} />
            <p>{error ? t('目前無法載入紀錄。') : t('還沒有轉錄紀錄')}</p>
          </div>
        )}
      </nav>
      <div className="sidebar-footer">
        <select
          className="locale-switch"
          name="locale"
          aria-label="Interface language / 介面語言"
          value={locale}
          onChange={(event) =>
            setLocale(event.target.value === 'zh-Hant' ? 'zh-Hant' : 'en')
          }
        >
          <option value="en">English</option>
          <option value="zh-Hant">繁體中文</option>
        </select>
      </div>
    </>
  );

  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">
        {t('跳到主要內容')}
      </a>
      {mobile ? (
        <dialog
          ref={sidebar}
          id="history-sidebar"
          className={`sidebar sidebar-dialog ${sidebarOpen ? 'open' : ''}`}
          aria-label={t('轉錄紀錄')}
          closedby="any"
          onClose={() => setSidebarOpen(false)}
        >
          {historyContent}
        </dialog>
      ) : (
        <aside id="history-sidebar" className="sidebar">
          {historyContent}
        </aside>
      )}
      <div className="workspace">
        <header className="topbar">
          <button
            type="button"
            className="icon-button mobile-menu"
            aria-label={sidebarOpen ? t('關閉轉錄紀錄') : t('開啟轉錄紀錄')}
            aria-expanded={sidebarOpen}
            aria-controls="history-sidebar"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            <Icon name="menu" />
          </button>
          <span>{t('我的工作空間')}</span>
          <Icon name="chevron" size={13} />
          <strong>{selectedId ? t('轉錄內容') : t('新增轉錄')}</strong>
        </header>
        <main id="workspace" ref={heading} tabIndex={-1}>
          {error ? (
            <div className="notice error connection-error" role="alert">
              <Icon name="info" />
              <span>{t(error)}</span>
              <button
                type="button"
                onClick={() => setRetry((value) => value + 1)}
              >
                {t('重新連線')}
              </button>
            </div>
          ) : null}
          {loading ? (
            <div className="loading-screen" role="status">
              <span className="spinner" />
              <p>{t('正在準備轉錄工作台…')}</p>
            </div>
          ) : selected ? (
            <ResultReader
              key={selected.id}
              job={selected}
              config={config}
              onNew={() => selectJob(null)}
              onJobUpdate={updateJob}
              onDirtyChange={setHasUnsavedChanges}
            />
          ) : config ? (
            <NewTranscript
              config={config}
              onConfigChange={setConfig}
              onJobUpdate={updateJob}
              onSubmitted={openJob}
              onBusyChange={setUploading}
              onDirtyChange={setHasUnsavedChanges}
            />
          ) : (
            <div className="loading-screen">
              <Icon name="info" size={32} />
              <h1>{t('暫時無法開啟工作台')}</h1>
              <p>{t('請稍後重新連線，載入轉錄設定。')}</p>
            </div>
          )}
        </main>
      </div>
      {pendingNavigation ? (
        <ConfirmDialog
          title={selectedId ? '修正尚未儲存' : '轉錄尚未送出'}
          message={
            selectedId
              ? '離開會捨棄尚未儲存的文字修正。'
              : '離開會清除已選取的檔案、連結與轉錄設定。'
          }
          confirmLabel="捨棄並離開"
          cancelLabel="留在此頁"
          onCancel={() => setPendingNavigation(null)}
          onConfirm={() => {
            openJob(pendingNavigation.job);
            setPendingNavigation(null);
          }}
        />
      ) : null}
    </div>
  );
}
