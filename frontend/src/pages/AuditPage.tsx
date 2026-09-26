import { Fragment, useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, formatDate } from '../api/client';
import type { AuditEvent } from '../api/types';
import { useAuth } from '../auth/AuthContext';

const PAGE = 50;

export default function AuditPage() {
  const { user } = useAuth();
  const owner = user?.role === 'owner_admin';
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [types, setTypes] = useState<string[]>([]);
  const [eventType, setEventType] = useState('');
  const [actor, setActor] = useState('');
  const [outcome, setOutcome] = useState('');
  const [since, setSince] = useState('');
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (off = 0) => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(off) });
    if (eventType) p.set('event_type', eventType);
    if (actor) p.set('actor', actor);
    if (outcome) p.set('outcome', outcome);
    if (since) p.set('since', new Date(since).toISOString());
    try {
      const r = await api.get<{ total: number; items: AuditEvent[] }>(`/audit?${p.toString()}`);
      setItems(r.items);
      setTotal(r.total);
      setOffset(off);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [eventType, actor, outcome, since]);

  useEffect(() => {
    void load(0);
    api.get<string[]>('/audit/event-types').then(setTypes).catch(() => undefined);
  }, [load]);

  function submit(e: FormEvent) {
    e.preventDefault();
    void load(0);
  }

  return (
    <div className="page">
      <header className="page-head"><h1>{owner ? 'Denetim kaydı' : 'İşlem geçmişim'}</h1></header>
      <p className="muted small">
        Kayıtlar yalnızca eklenebilir (değiştirilemez/silinemez). {owner ? 'Sahip tüm kayıtları görür.' : 'Yalnızca kendi işlemlerinizi görürsünüz.'}
      </p>
      <form className="filters" onSubmit={submit}>
        <label>Olay türü
          <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
            <option value="">tümü</option>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        {owner && <label>Kullanıcı <input value={actor} onChange={(e) => setActor(e.target.value)} /></label>}
        <label>Sonuç
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">tümü</option><option value="success">success</option><option value="denied">denied</option>
            <option value="failure">failure</option><option value="info">info</option>
          </select>
        </label>
        <label>Başlangıç <input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} /></label>
        <button type="submit" className="btn">Filtrele</button>
      </form>
      {err && <p className="error" role="alert">{err}</p>}
      <table className="table" data-testid="audit-table">
        <thead><tr><th>#</th><th>Zaman</th><th>Kullanıcı</th><th>Olay</th><th>Hedef</th><th>Sonuç</th></tr></thead>
        <tbody>
          {items.map((e) => (
            <Fragment key={e.id}>
              <tr onClick={() => setExpanded(expanded === e.id ? null : e.id)} className="clickable">
                <td>{e.id}</td><td className="small">{formatDate(e.occurred_at)}</td><td>{e.actor_username ?? '—'}</td>
                <td className="mono small">{e.event_type}</td><td className="small">{e.target_type}{e.target_id ? `: ${e.target_id.slice(0, 40)}` : ''}</td>
                <td><span className={`chip out-${e.outcome}`}>{e.outcome}</span></td>
              </tr>
              {expanded === e.id && (
                <tr><td colSpan={6}><pre className="json">{JSON.stringify(e.details, null, 2)}</pre></td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      <div className="pager">
        <button type="button" className="btn" disabled={offset === 0} onClick={() => void load(Math.max(0, offset - PAGE))}>Önceki</button>
        <span className="muted small">{total ? `${offset + 1}–${Math.min(offset + PAGE, total)} / ${total}` : '0'}</span>
        <button type="button" className="btn" disabled={offset + PAGE >= total} onClick={() => void load(offset + PAGE)}>Sonraki</button>
      </div>
    </div>
  );
}
