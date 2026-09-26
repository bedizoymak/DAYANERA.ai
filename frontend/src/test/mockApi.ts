import { vi } from 'vitest';

export type Handler = (init: RequestInit | undefined, url: string) => Response | Promise<Response>;
export interface Route {
  method: string;
  path: RegExp;
  handler: Handler;
}

export const calls: Array<{ method: string; url: string; body: unknown; headers: Record<string, string> }> = [];

export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

export function sse(events: Array<{ event: string; data: unknown }>): Response {
  const text = events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join('');
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

export function installFetch(routes: Route[]) {
  calls.length = 0;
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const method = (init?.method ?? 'GET').toUpperCase();
    let body: unknown = undefined;
    if (typeof init?.body === 'string') body = JSON.parse(init.body);
    else if (init?.body instanceof FormData) body = init.body;
    const headers = (init?.headers ?? {}) as Record<string, string>;
    calls.push({ method, url, body, headers });
    const r = routes.find((rt) => rt.method === method && rt.path.test(url));
    if (!r) return json({ detail: `no mock for ${method} ${url}` }, 404);
    return r.handler(init, url);
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

export const adminUser = { id: 'u1', username: 'admin', display_name: 'Yönetici', role: 'owner_admin' };

export function msg(partial: Record<string, unknown>) {
  return {
    id: Math.random().toString(36).slice(2),
    conversation_id: 'c1',
    role: 'assistant',
    content: '',
    answer_mode: null,
    answer_mode_label: null,
    status: 'complete',
    error_code: null,
    show_sources: false,
    sources: [],
    attachments: [],
    metadata: {},
    created_at: new Date().toISOString(),
    ...partial,
  };
}
