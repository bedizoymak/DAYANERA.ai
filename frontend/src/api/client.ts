// DAYANERA Core API client. Talks only to the local FastAPI (same origin via
// the Vite proxy). Session = HttpOnly cookie; nothing secret is stored in the
// browser (no localStorage/sessionStorage for credentials or tokens).

export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api/v1';
const CSRF_HEADER = 'X-DAYANERA-CSRF';

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

const OFFLINE_MSG =
  'Yerel sunucuya ulaşılamıyor. Arka uç çalışıyor mu? (scripts\\start-local.ps1 ile başlatın)';

async function toError(res: Response): Promise<ApiError> {
  let detail = `İstek başarısız (HTTP ${res.status}).`;
  let code: string | undefined;
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') detail = body.detail;
    else if (Array.isArray(body?.detail)) detail = 'Geçersiz giriş: ' + body.detail.map((d: { msg: string }) => d.msg).join('; ');
    code = body?.code;
  } catch {
    /* non-JSON error */
  }
  if (res.status === 502 || res.status === 504) detail = OFFLINE_MSG;
  return new ApiError(res.status, detail, code);
}

export async function request<T>(
  path: string,
  opts: { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal } = {},
): Promise<T> {
  const method = opts.method ?? 'GET';
  const headers: Record<string, string> = {};
  if (method !== 'GET') headers[CSRF_HEADER] = '1';
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { method, headers, body, credentials: 'same-origin', signal: opts.signal });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError(0, OFFLINE_MSG);
  }
  if (res.status === 401 && !path.startsWith('/auth/')) unauthorizedListeners.forEach((l) => l());
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) => request<T>(p, { method: 'POST', body: body ?? {} }),
  patch: <T>(p: string, body: unknown) => request<T>(p, { method: 'PATCH', body }),
  del: <T>(p: string, body?: unknown) => request<T>(p, { method: 'DELETE', body }),
  upload: <T>(p: string, file: File, extra: Record<string, string> = {}) => {
    const form = new FormData();
    form.append('file', file);
    Object.entries(extra).forEach(([k, v]) => form.append(k, v));
    return request<T>(p, { method: 'POST', form });
  },
};

export interface StreamEvent {
  event: string;
  data: unknown;
}

/** POST a chat message and consume the server-sent-event stream. */
export async function streamChat(
  conversationId: string,
  payload: { content: string; attachment_ids: string[] },
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/conversations/${conversationId}/messages/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', [CSRF_HEADER]: '1', Accept: 'text/event-stream' },
      body: JSON.stringify(payload),
      credentials: 'same-origin',
      signal,
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new ApiError(0, OFFLINE_MSG);
  }
  if (res.status === 401) unauthorizedListeners.forEach((l) => l());
  if (!res.ok || !res.body) throw await toError(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = 'message';
      const dataLines: string[] = [];
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
      }
      if (dataLines.length) {
        try {
          onEvent({ event, data: JSON.parse(dataLines.join('\n')) });
        } catch {
          onEvent({ event, data: dataLines.join('\n') });
        }
      }
    }
  }
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('tr-TR');
}
