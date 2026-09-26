import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api, formatDate } from '../api/client';
import type { MemoryItem } from '../api/types';

interface SearchResult {
  memory: Array<{ id: string; kind: string; title: string; content: string; status: string; created_at: string }>;
  messages: Array<{ id: string; conversation_id: string; conversation_title: string; role: string; content: string; created_at: string }>;
}

const STATUS_TR: Record<string, string> = {
  verified_source: 'doğrulanmış kaynak', user_confirmed: 'kullanıcı onaylı', draft_extraction: 'taslak çıkarım',
  unverified: 'doğrulanmamış', superseded: 'yerine yenisi geldi', deleted: 'silindi', archived: 'arşiv',
};

export default function MemoryPage() {
  const [q, setQ] = useState('');
  const [res, setRes] = useState<SearchResult | null>(null);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [kind, setKind] = useState('fact');
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.get<MemoryItem[]>('/memory/items?limit=200'));
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    try { setRes(await api.get<SearchResult>(`/memory/search?q=${encodeURIComponent(q)}`)); } catch (er) { setErr((er as Error).message); }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post('/memory/items', { kind, title: title || content.slice(0, 60), content, status: 'user_confirmed' });
      setTitle(''); setContent('');
      await load();
    } catch (er) { setErr((er as Error).message); }
  }

  async function saveEdit(m: MemoryItem) {
    try {
      await api.patch(`/memory/items/${m.id}`, { content: editText });
      setEditing(null);
      await load();
    } catch (er) { setErr((er as Error).message); }
  }

  async function setStatus(m: MemoryItem, status: string) {
    try { await api.patch(`/memory/items/${m.id}`, { status }); await load(); } catch (er) { setErr((er as Error).message); }
  }

  return (
    <div className="page">
      <header className="page-head"><h1>Hafıza</h1></header>
      <p className="muted small">
        Ham sohbetler ve ekler asla silinmez veya özetle değiştirilmez. Hafıza kayıtları ek, yapılandırılmış bilgilerdir;
        güncellemeler yeni kayıt oluşturur ve eskisini “yerine yenisi geldi” olarak saklar. Sohbette “hatırla: …” yazarak da kayıt ekleyebilirsiniz.
      </p>
      {err && <p className="error" role="alert">{err}</p>}
      <form onSubmit={search} className="filters" role="search">
        <label>Hafızada ve sohbet geçmişinde ara <input value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <button type="submit" className="btn">Ara</button>
      </form>
      {res && (
        <section>
          <h2>Sonuçlar</h2>
          <h3>Hafıza kayıtları ({res.memory.length})</h3>
          <ul>{res.memory.map((m) => <li key={m.id}><strong>{m.title}</strong> — {m.content} <span className="chip">{STATUS_TR[m.status] ?? m.status}</span></li>)}</ul>
          <h3>Mesajlar ({res.messages.length})</h3>
          <ul>{res.messages.map((m) => <li key={m.id}><Link to={`/c/${m.conversation_id}`}>{m.conversation_title}</Link> · {m.role}: {m.content.slice(0, 200)}</li>)}</ul>
        </section>
      )}
      <form onSubmit={add} className="card-form">
        <h2>Bilgi / tercih ekle</h2>
        <label>Tür
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="fact">bilgi</option><option value="preference">tercih</option><option value="note">not</option>
          </select>
        </label>
        <label>Başlık <input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label>İçerik <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={3} required /></label>
        <button type="submit" className="btn btn-primary" disabled={!content.trim()}>Kaydet (kullanıcı onaylı)</button>
      </form>
      <h2>Kayıtlar</h2>
      <table className="table">
        <thead><tr><th>Tür</th><th>Başlık / içerik</th><th>Durum</th><th>Tarih</th><th /></tr></thead>
        <tbody>
          {items.map((m) => (
            <tr key={m.id}>
              <td>{m.kind}</td>
              <td>
                <strong>{m.title}</strong>
                {editing === m.id ? (
                  <div>
                    <textarea aria-label="İçeriği düzenle" value={editText} onChange={(e) => setEditText(e.target.value)} rows={3} />
                    <button type="button" className="btn btn-primary" onClick={() => void saveEdit(m)}>Kaydet</button>
                    <button type="button" className="btn" onClick={() => setEditing(null)}>Vazgeç</button>
                  </div>
                ) : <div className="small">{m.content}</div>}
              </td>
              <td><span className="chip">{STATUS_TR[m.status] ?? m.status}</span></td>
              <td className="small">{formatDate(m.created_at)}</td>
              <td>
                {!['superseded', 'deleted'].includes(m.status) && (
                  <>
                    <button type="button" className="link-btn" onClick={() => { setEditing(m.id); setEditText(m.content); }}>düzenle</button>{' '}
                    {m.status !== 'archived' && <button type="button" className="link-btn" onClick={() => void setStatus(m, 'archived')}>arşivle</button>}
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
