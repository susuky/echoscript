export type Options = {
  asr_backend: string;
  asr_model: string;
  language: string | null;
  timestamps: boolean;
  diarize: boolean;
  zh_script: string | null;
  context: string;
  glossary: string;
  condition_on_previous_text: boolean;
};

export type Config = {
  languages: { code: string; label: string }[];
  backends: {
    id: string;
    label: string;
    models: { id: string; label: string }[];
    available: boolean;
  }[];
  max_upload_bytes: number;
  diarization_available: boolean;
  default_options: Options;
};

export type Job = {
  id: string;
  source_value: string;
  source_type: string;
  status: string;
  stage: string;
  error: string | null;
  created_at: number;
  updated_at: number;
  options: Options;
  cancel_requested?: boolean;
};

export type Segment = {
  id?: string | null;
  start: number;
  end: number;
  text: string;
  raw_text?: string | null;
  speaker?: string | null;
  alignment?: string;
  diagnostics?: { chunk_id?: string; issues?: string[]; edited?: boolean; subtitle_errors?: string[] };
};

export type ChunkState = {
  chunks: { id: string; start: number; end: number; asr_status: string; alignment_status: string;
    diagnostics?: { issues?: string[] } }[];
  progress: { total: number; recognized: number; aligned: number; failed: number;
    processed_seconds: number; duration: number } | null;
};

export type Result = {
  transcript: {
    text: string;
    language: string | null;
    duration: number | null;
    segments: Segment[];
    speakers: string[];
    metadata?: {
      timestamps?: boolean;
      revision?: string;
      unapplied_edits?: { text: string; raw_text?: string; segment_id: string }[];
      subtitles?: { status: string; gap_count?: number; gaps?: unknown[] };
      alignment?: { status: string; reason?: string };
      normalization?: { status: string; reason?: string };
    };
  };
  text: string;
  files: { format: string; url: string }[];
};

export function friendlyError(status: number, detail = ''): string {
  // The API translates and sanitizes errors before returning public messages.
  if (/[\u3400-\u9fff]/.test(detail)) return detail;
  if (status === 413 || /size limit|too large|exceeds.*limit/i.test(detail))
    return '檔案超過上傳大小限制，請縮短錄音或壓縮檔案後再試。';
  if (status === 404) return '找不到這筆轉錄，可能已超過保留期限。';
  if (status === 409) return '這筆轉錄的狀態已變更，請重新整理後再試。';
  if (/private|loopback|public|reserved|local address|not allowed|forbidden/i.test(detail))
    return '此連結無法讀取。請提供公開的音影片連結，或下載檔案後上傳。';
  if (/diariz|HF_TOKEN/i.test(detail)) return '語者辨識目前無法使用，請關閉此選項後再試。';
  if (/URL|redirect|download|unsupported|manifest|playlist/i.test(detail))
    return '無法讀取這個連結，請確認影片可公開觀看，或下載檔案後上傳。';
  if (status === 400 || status === 422) return '無法送出轉錄，請確認檔案、連結與轉錄設定後再試。';
  if (status === 401 || status === 403) return '目前無法存取轉錄服務，請確認存取權限後再試。';
  return '暫時無法連線到轉錄服務，請稍後再試。';
}

export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new Error(friendlyError(0));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      friendlyError(response.status, typeof body.detail === 'string' ? body.detail : ''),
    );
  }
  return response.json();
}

export function uploadMedia(
  id: string,
  file: File,
  progress: (percent: number) => void,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/jobs/${encodeURIComponent(id)}/media`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) progress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300 && xhr.response) resolve(xhr.response);
      else reject(new Error(friendlyError(xhr.status, xhr.response?.detail)));
    };
    xhr.onerror = () => reject(new Error('上傳中斷，請確認網路連線後重新上傳。'));
    xhr.onabort = () => reject(new Error('上傳已中止，請重新選取檔案。'));
    xhr.send(file);
  });
}

export function jobTitle(job: Job): string {
  if (job.source_type === 'url') {
    try {
      const url = new URL(job.source_value);
      return `${url.hostname.replace(/^www\./, '')}${url.pathname === '/' ? '' : url.pathname}${url.search}`;
    } catch {
      /* Older jobs may store a title instead of a URL. */
    }
  }
  return job.source_value || '未命名轉錄';
}

export function statusLabel(job: Job): string {
  if (job.status === 'cancelled') return '已停止';
  if (job.cancel_requested && job.status === 'running') return '正在停止';
  if (job.status === 'done') return '已完成';
  if (job.status === 'failed') return '未完成';
  if (job.status === 'queued') return '等待轉錄';
  if (job.status === 'uploading') return '正在上傳';
  const stages: Record<string, string> = {
    ingesting: '正在讀取音影片',
    extracting_audio: '正在整理音訊',
    transcribing: '正在辨識內容',
    planning: '正在準備音訊片段',
    aligning: '正在標示字幕時間',
    diarizing: '正在辨識語者',
    normalizing: '正在整理文字',
    rendering: '正在整理逐字稿',
  };
  return stages[job.stage] || '正在轉錄';
}

export function isoDate(value: number): string {
  return new Date(value * 1000).toISOString();
}

export function formatDate(value: number, locale = 'en'): string {
  const date = new Date(value * 1000);
  return Number.isNaN(date.valueOf())
    ? ''
    : new Intl.DateTimeFormat(locale, {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date);
}

export function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(total / 60);
  return total >= 3600
    ? `${Math.floor(total / 3600)}:${String(minutes % 60).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1).replace(/\.0$/, '')}\u00a0GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1).replace(/\.0$/, '')}\u00a0MB`;
  if (bytes >= 1024) return `${Math.ceil(bytes / 1024)}\u00a0KB`;
  return `${bytes}\u00a0B`;
}
