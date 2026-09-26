import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, formatDate } from '../api/client';
import type { DocumentInfo, KnowledgeArea, User } from '../api/types';

interface Scope { id: string; scope_type: string; scope_id: string; label: string | null; granted_at: string }

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [areas, setAreas] = useState<KnowledgeArea[]>([]);
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [sel, setSel] = useState<User | null>(null);
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [form, setForm] = useState({ username: '', display_name: '', password: '', role: 'member' });
  const [scopeType, setScopeType] = useState('knowledge_area');
  const [scopeId, setScopeId] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await api.get<User[]>('/users'));
      setAreas(await api.get<KnowledgeArea[]>('/knowledge-areas'));
      setDocs((await api.get<{ items: DocumentInfo[] }>('/documents?limit=500')).items);
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function pick(u: User) {
    setSel(u);
    try { setScopes(await api.get<Scope[]>(`/users/${u.id}/scopes`)); } catch (e) { setErr((e as Error).message); }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await api.post('/users', form);
      setMsg(`Kullanıcı oluşturuldu: ${form.username}`);
      setForm({ username: '', display_name: '', password: '', role: 'member' });
      await load();
    } catch (er) { setErr((er as Error).message); }
  }

  async function update(u: User, body: Record<string, unknown>) {
    try { await api.patch(`/users/${u.id}`, body); await load(); setMsg('Güncellendi.'); } catch (e) { setErr((e as Error).message); }
  }

  async function grant(e: FormEvent) {
    e.preventDefault();
    if (!sel || !scopeId) return;
    try { await api.post(`/users/${sel.id}/scopes`, { scope_type: scopeType, scope_id: scopeId }); await pick(sel); } catch (er) { setErr((er as Error).message); }
  }

  async function revoke(s: Scope) {
    if (!sel) return;
    try { await api.del(`/users/${sel.id}/scopes/${s.id}`); await pick(sel); } catch (e) { setErr((e as Error).message); }
  }

  return (
    <div className="page">
      <header className="page-head"><h1>Kullanıcılar ve veri kapsamları</h1></header>
      <p className="muted small">
        <strong>owner_admin</strong> tüm verileri görür; <strong>member</strong> yalnızca açıkça atanan konuşma, belge ve bilgi alanlarını görür.
        Beta yalnızca yerel hesaplarla çalışır; şirket hesabı/SSO gelecekteki şirket içi aşamadır. Tüm rol/kapsam değişiklikleri denetlenir.
      </p>
      {msg && <p className="ok" role="status">{msg}</p>}
      {err && <p className="error" role="alert">{err}</p>}
      <div className="split">
        <div>
          <table className="table">
            <thead><tr><th>Kullanıcı</th><th>Rol</th><th>Durum</th><th /></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className={sel?.id === u.id ? 'row-selected' : ''}>
                  <td><button type="button" className="link-btn" onClick={() => void pick(u)}>{u.display_name} ({u.username})</button></td>
                  <td>
                    <select aria-label={`${u.username} rolü`} value={u.role} onChange={(e) => void update(u, { role: e.target.value })}>
                      <option value="owner_admin">owner_admin</option><option value="member">member</option>
                    </select>
                  </td>
                  <td>{u.is_active ? 'etkin' : 'devre dışı'}</td>
                  <td><button type="button" className="link-btn" onClick={() => void update(u, { is_active: !u.is_active })}>{u.is_active ? 'devre dışı bırak' : 'etkinleştir'}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <form className="card-form" onSubmit={create}>
            <h2>Yeni kullanıcı (gelecekteki ekip üyeleri)</h2>
            <label>Kullanıcı adı <input value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} required pattern="[a-zA-Z0-9._\-]+" /></label>
            <label>Görünen ad <input value={form.display_name} onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))} required /></label>
            <label>Geçici parola (en az 8 karakter) <input type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} required minLength={8} /></label>
            <label>Rol
              <select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}>
                <option value="member">member</option><option value="owner_admin">owner_admin</option>
              </select>
            </label>
            <button type="submit" className="btn btn-primary">Oluştur</button>
          </form>
        </div>
        {sel && (
          <section className="detail">
            <h2>{sel.display_name} — kapsamlar</h2>
            {sel.role === 'owner_admin' && <p className="muted">owner_admin tüm verileri görür; kapsam gerekmez.</p>}
            <ul>
              {scopes.map((s) => (
                <li key={s.id}>{s.scope_type}: {s.label ?? s.scope_id} <span className="muted small">({formatDate(s.granted_at)})</span>{' '}
                  <button type="button" className="link-btn" onClick={() => void revoke(s)}>kaldır</button></li>
              ))}
            </ul>
            <form onSubmit={grant} className="card-form">
              <label>Kapsam türü
                <select value={scopeType} onChange={(e) => { setScopeType(e.target.value); setScopeId(''); }}>
                  <option value="knowledge_area">bilgi alanı</option><option value="document">belge</option><option value="conversation">konuşma (kimlik)</option>
                </select>
              </label>
              {scopeType === 'knowledge_area' && (
                <select aria-label="Bilgi alanı" value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
                  <option value="">seçin…</option>{areas.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              )}
              {scopeType === 'document' && (
                <select aria-label="Belge" value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
                  <option value="">seçin…</option>{docs.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
                </select>
              )}
              {scopeType === 'conversation' && (
                <input aria-label="Konuşma kimliği" value={scopeId} onChange={(e) => setScopeId(e.target.value)} placeholder="konuşma UUID" />
              )}
              <button type="submit" className="btn" disabled={!scopeId}>Kapsam ata</button>
            </form>
          </section>
        )}
      </div>
    </div>
  );
}
