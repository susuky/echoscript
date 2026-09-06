import { useCallback, useEffect, useRef, useState } from 'react';
import { formatDate, isoDate, jobTitle, request, statusLabel } from './api';
import type { Config, Job } from './api';
import { Brand, Icon } from './Icons';
import NewTranscript from './NewTranscript';
import ResultReader from './ResultReader';

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const heading = useRef<HTMLDivElement>(null);
  const selected =
    jobs.find((item) => item.id === selectedId) ||
    (selectedJob?.id === selectedId ? selectedJob : null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    Promise.all([
      request<Config>('/api/config', { signal: controller.signal }),
      request<{ jobs: Job[] }>('/api/jobs?limit=50', { signal: controller.signal }),
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
          request<{ jobs: Job[] }>('/api/jobs?limit=50', { signal: controller.signal }),
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
          setError(issue instanceof Error ? issue.message : '無法更新轉錄狀態。');
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
    if (!sidebarOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [sidebarOpen]);

  const updateJob = useCallback((job: Job) => {
    setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)].slice(0, 50));
  }, []);

  function selectJob(job: Job | null) {
    setSelectedId(job?.id || null);
    setSelectedJob(job);
    setSidebarOpen(false);
    window.scrollTo({ top: 0 });
    requestAnimationFrame(() => heading.current?.focus({ preventScroll: true }));
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#workspace">
        跳到主要內容
      </a>
      {sidebarOpen ? (
        <button
          className="sidebar-backdrop"
          aria-label="關閉轉錄紀錄"
          onClick={() => setSidebarOpen(false)}
        />
      ) : null}
      <aside id="history-sidebar" className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <button
          className="icon-button mobile-close"
          aria-label="關閉轉錄紀錄"
          onClick={() => setSidebarOpen(false)}
        >
          <Icon name="close" size={18} />
        </button>
        <Brand />
        <button
          className={`new-button ${!selectedId ? 'selected' : ''}`}
          disabled={uploading}
          onClick={() => selectJob(null)}
        >
          <Icon name="plus" size={19} />
          新增轉錄
        </button>
        <div className="history-heading">
          <h2>轉錄紀錄</h2>
          <span>{jobs.length ? jobs.length : ''}</span>
        </div>
        <nav className="history-list" aria-label="轉錄紀錄">
          {loading ? (
            <p className="history-empty">正在載入紀錄…</p>
          ) : jobs.length ? (
            jobs.map((job) => (
              <button
                key={job.id}
                className={`history-item ${job.id === selectedId ? 'selected' : ''}`}
                onClick={() => selectJob(job)}
                disabled={uploading}
                aria-current={job.id === selectedId ? 'page' : undefined}
              >
                <Icon name={job.source_type === 'url' ? 'link' : 'file'} size={18} />
                <span>
                  <strong title={jobTitle(job)}>{jobTitle(job)}</strong>
                  <span className="history-meta">
                    <time dateTime={isoDate(job.created_at)}>{formatDate(job.created_at)}</time>
                    <span className={`status-dot ${job.status}`} />
                    <span>{statusLabel(job)}</span>
                  </span>
                </span>
              </button>
            ))
          ) : (
            <div className="history-empty">
              <Icon name="clock" size={23} />
              <p>
                {error ? '目前無法載入紀錄。' : '還沒有轉錄紀錄'}
                <small>{error ? '連線恢復後會重新載入。' : '送出轉錄後，可在這裡查看。'}</small>
              </p>
            </div>
          )}
        </nav>
        <div className="sidebar-footer">
          <span className="footer-mark" />
          <span>
            EchoScript<span>逐字稿與字幕</span>
          </span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label={sidebarOpen ? '關閉轉錄紀錄' : '開啟轉錄紀錄'}
            aria-expanded={sidebarOpen}
            aria-controls="history-sidebar"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            <Icon name="menu" />
          </button>
          <span>我的工作空間</span>
          <Icon name="chevron" size={13} />
          <strong>{selectedId ? '轉錄內容' : '新增轉錄'}</strong>
          <span className="topbar-end">聲音，成為文字。</span>
        </header>
        <main id="workspace" ref={heading} tabIndex={-1}>
          {error ? (
            <div className="notice error connection-error" role="alert">
              <Icon name="info" />
              <span>{error}</span>
              <button type="button" onClick={() => setRetry((value) => value + 1)}>
                重新連線
              </button>
            </div>
          ) : null}
          {loading ? (
            <div className="loading-screen" role="status">
              <span className="spinner" />
              <p>正在準備轉錄工作台…</p>
            </div>
          ) : selected ? (
            <ResultReader
              key={selected.id}
              job={selected}
              config={config}
              onNew={() => selectJob(null)}
            />
          ) : config ? (
            <NewTranscript
              config={config}
              onJobUpdate={updateJob}
              onSubmitted={selectJob}
              onBusyChange={setUploading}
            />
          ) : (
            <div className="loading-screen">
              <Icon name="info" size={32} />
              <h1>暫時無法開啟工作台</h1>
              <p>請稍後重新連線，載入轉錄設定。</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
