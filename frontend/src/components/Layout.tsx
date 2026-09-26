import { useCallback, useEffect, useState } from 'react';
import { Outlet, useMatch, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { Conversation } from '../api/types';
import Sidebar from './Sidebar';
import StatusBanner from './StatusBanner';

export interface LayoutContext {
  conversations: Conversation[];
  refreshConversations: () => Promise<void>;
  upsertConversation: (c: Conversation) => void;
}

export default function Layout() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const conversationId = useMatch('/c/:conversationId')?.params.conversationId;

  const refreshConversations = useCallback(async () => {
    try {
      const list = await api.get<Conversation[]>(`/conversations?status=${showArchived ? 'archived' : 'active'}`);
      setConversations(list);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [showArchived]);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  const upsertConversation = useCallback((c: Conversation) => {
    setConversations((prev) => {
      const rest = prev.filter((p) => p.id !== c.id);
      return c.status === (showArchived ? 'archived' : 'active') ? [c, ...rest] : rest;
    });
  }, [showArchived]);

  const newChat = useCallback(async () => {
    try {
      const c = await api.post<Conversation>('/conversations', {});
      upsertConversation(c);
      navigate(`/c/${c.id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [navigate, upsertConversation]);

  return (
    <div className="app-shell">
      <Sidebar
        conversations={conversations}
        activeId={conversationId}
        showArchived={showArchived}
        onToggleArchived={() => setShowArchived((v) => !v)}
        onNewChat={newChat}
        onChanged={upsertConversation}
        error={error}
      />
      <main className="main-pane">
        <StatusBanner />
        <Outlet context={{ conversations, refreshConversations, upsertConversation } satisfies LayoutContext} />
      </main>
    </div>
  );
}
