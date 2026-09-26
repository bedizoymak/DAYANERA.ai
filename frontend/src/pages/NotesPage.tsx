import { useCallback, useEffect, useState, type FormEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../api/client';
import type { NoteSummary } from '../api/types';

const STATUSES = ['öneri', 'inceleniyor', 'uygulandı', 'reddedildi'];

interface NoteFull { filename: string; markdown: string; title: string; status: string }

export default function NotesPage() {
  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [open, setOpen] = useState<NoteFull | null>(null);
  const [form, setForm] = useState({ title: '', recommendation: '', rationale: '', affected_areas: '', expected_benefit: '', risks: '' });
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setNotes(await api.get<NoteSummary[]>('/notes')); } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function show(n: NoteSummary) {
    try { setOpen(await api.get<NoteFull>(`/notes/${encodeURIComponent(n.filename)}`)); } catch (e) { setErr((e as Error).message); }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const n = await api.post<NoteFull>('/notes', { ...form, status: 'öneri' });
      setMsg(`Not oluşturuldu: agent-notes/${n.filename}`);
      setForm({ title: '', recommendation: '', rationale: '', affected_areas: '', expected_benefit: '', risks: '' });
      setOpen(n);
      await load();
    } catch (er) { setErr((er as Error).message); }
  }

  async function setStatus(status: string) {
    if (!open) return;
    try {
      setOpen(await api.patch<NoteFull>(`/notes/${encodeURIComponent(open.filename)}`, { status }));
      await load();
    } catch (er) { setErr((er as Error).message); }
  }

  const field = (k: keyof typeof form, label: string, rows = 2) => (
    <label key={k}>{label}
      <textarea value={form[k]} rows={rows} onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} required={k === 'recommendation'} />
    </label>
  );

  return (
    <div className="page">
      <header className="page-head"><h1>Öneri notları</h1></header>
      <p className="muted small">
        DAYANERA yalnızca <code>agent-notes/</code> klasörüne ayrı Markdown öneri dosyaları yazabilir
        (<code>YYYY-AA-GG_kisa-konu.md</code>). Bu sayfa kaynak kodu değiştirmez; kod değişiklikleri ayrı MCP/ajan iş akışlarıyla yapılır.
        Her oluşturma/düzenleme denetim kaydına yazılır.
      </p>
      {msg && <p className="ok" role="status">{msg}</p>}
      {err && <p className="error" role="alert">{err}</p>}
      <div className="split">
        <div>
          <table className="table">
            <thead><tr><th>Dosya</th><th>Başlık</th><th>Durum</th><th>Tarih</th></tr></thead>
            <tbody>
              {notes.map((n) => (
                <tr key={n.filename}>
                  <td className="mono small"><button type="button" className="link-btn" onClick={() => void show(n)}>{n.filename}</button></td>
                  <td>{n.title}</td><td><span className="chip">{n.status}</span></td><td>{n.date}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <form className="card-form" onSubmit={create}>
            <h2>Yeni öneri notu</h2>
            <label>Başlık <input value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} required minLength={3} /></label>
            {field('recommendation', 'Öneri', 3)}
            {field('rationale', 'Gerekçe')}
            {field('affected_areas', 'Etkilenen alanlar')}
            {field('expected_benefit', 'Beklenen fayda')}
            {field('risks', 'Riskler / varsayımlar')}
            <button type="submit" className="btn btn-primary">Not oluştur</button>
          </form>
        </div>
        {open && (
          <section className="detail">
            <div className="actions">
              <label>Durum{' '}
                <select value={open.status} onChange={(e) => void setStatus(e.target.value)}>
                  {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            </div>
            <div className="markdown"><ReactMarkdown disallowedElements={['img']} unwrapDisallowed>{open.markdown}</ReactMarkdown></div>
          </section>
        )}
      </div>
    </div>
  );
}
