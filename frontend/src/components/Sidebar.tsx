import { useState } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { Conversation } from '../api/types';
import AccountMenu from './AccountMenu';

interface Props {
  conversations: Conversation[];
  activeId?: string;
  showArchived: boolean;
  onToggleArchived: () => void;
  onNewChat: () => void;
  onChanged: (c: Conversation) => void;
  error: string | null;
}

export default function Sidebar({ conversations, activeId, showArchived, onToggleArchived, onNewChat, onChanged, error }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function rename(c: Conversation) {
    if (!title.trim()) return setEditing(null);
    setBusy(true);
    try {
      onChanged(await api.patch<Conversation>(`/conversations/${c.id}`, { title: title.trim() }));
    } finally {
      setBusy(false);
      setEditing(null);
    }
  }

  async function toggleArchive(c: Conversation) {
    const updated = await api.patch<Conversation>(`/conversations/${c.id}`, {
      status: c.status === 'active' ? 'archived' : 'active',
    });
    onChanged(updated);
    if (activeId === c.id && updated.status === 'archived') navigate('/');
  }

  return (
    <aside className="sidebar" aria-label="Konuşmalar">
      <div className="brand">
        <Link to="/" className="brand-link">DAYANERA<span>.ai</span></Link>
        <span className="brand-sub">yerel beta</span>
      </div>
      <button type="button" className="btn btn-primary new-chat" onClick={onNewChat}>
        + Yeni sohbet
      </button>
      <div className="conv-header">
        <span>{showArchived ? 'Arşivlenmiş sohbetler' : 'Sohbetler'}</span>
        <button type="button" className="link-btn" onClick={onToggleArchived}>
          {showArchived ? 'Etkinleri göster' : 'Arşivi göster'}
        </button>
      </div>
      {error && <p className="error small" role="alert">{error}</p>}
      <nav className="conv-list">
        {conversations.length === 0 && <p className="muted small">Henüz sohbet yok.</p>}
        {conversations.map((c) => (
          <div key={c.id} className={`conv-item${c.id === activeId ? ' active' : ''}`}>
            {editing === c.id ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void rename(c);
                }}
                className="rename-form"
              >
                <label className="sr-only" htmlFor={`rename-${c.id}`}>Sohbet adı</label>
                <input
                  id={`rename-${c.id}`}
                  autoFocus
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onBlur={() => void rename(c)}
                  disabled={busy}
                />
              </form>
            ) : (
              <NavLink to={`/c/${c.id}`} className="conv-link" title={c.title}>
                {c.title}
              </NavLink>
            )}
            <div className="conv-actions">
              <button
                type="button"
                className="icon-btn"
                aria-label={`"${c.title}" sohbetini yeniden adlandır`}
                onClick={() => {
                  setEditing(c.id);
                  setTitle(c.title);
                }}
              >
                ✎
              </button>
              <button
                type="button"
                className="icon-btn"
                aria-label={c.status === 'active' ? `"${c.title}" sohbetini arşivle` : `"${c.title}" sohbetini arşivden çıkar`}
                onClick={() => void toggleArchive(c)}
              >
                {c.status === 'active' ? '🗄' : '↩'}
              </button>
            </div>
          </div>
        ))}
      </nav>
      <AccountMenu />
    </aside>
  );
}
