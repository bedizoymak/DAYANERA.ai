import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useOutletContext, useParams } from 'react-router-dom';
import { api, streamChat } from '../api/client';
import type { Conversation, Message } from '../api/types';
import Composer from '../components/Composer';
import type { LayoutContext } from '../components/Layout';
import MessageView from '../components/MessageView';

export default function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const { upsertConversation } = useOutletContext<LayoutContext>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const skipLoadFor = useRef<string | null>(null);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    if (skipLoadFor.current === conversationId) {
      skipLoadFor.current = null;
      return;
    }
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .get<Message[]>(`/conversations/${conversationId}/messages`)
      .then((m) => alive && setMessages(m))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [conversationId]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
  }, [messages, draft, stage]);

  const send = useCallback(
    async (text: string, attachmentIds: string[]) => {
      setError(null);
      setBusy(true);
      setDraft('');
      setStage('Gönderiliyor…');
      let convId = conversationId;
      let created = false;
      try {
        if (!convId) {
          const c = await api.post<Conversation>('/conversations', {});
          convId = c.id;
          created = true;
          upsertConversation(c);
        }
        await streamChat(convId, { content: text, attachment_ids: attachmentIds }, (ev) => {
          if (ev.event === 'user_message') setMessages((prev) => [...prev, ev.data as Message]);
          else if (ev.event === 'status') setStage((ev.data as { text: string }).text);
          else if (ev.event === 'token') setDraft((d) => d + (ev.data as { text: string }).text);
          else if (ev.event === 'assistant_message') {
            setDraft('');
            setMessages((prev) => [...prev, ev.data as Message]);
          } else if (ev.event === 'error') setError((ev.data as { message: string }).message);
        });
        const convs = await api.get<Conversation>(`/conversations/${convId}`);
        upsertConversation(convs);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
        setStage(null);
        setDraft('');
        if (created && convId) {
          skipLoadFor.current = convId;
          navigate(`/c/${convId}`);
        }
      }
    },
    [conversationId, navigate, upsertConversation],
  );

  return (
    <div className="chat-page">
      <div className="chat-scroll" aria-live="polite">
        {!conversationId && messages.length === 0 && (
          <section className="welcome">
            <h1>DAYANERA.ai</h1>
            <p>
              Şirket içi, tamamen yerel çalışan Türkçe mühendislik asistanı. Genel sohbet edebilir; dişli tasarımı, üretimi,
              ölçümü ve kalitesiyle ilgili teknik soruları yalnızca yerel ISO korpusundan doğrular.
            </p>
            <ul className="mode-legend">
              <li><span className="mode-badge mode-general">Genel sohbet</span> doğrulanmamış genel yanıt</li>
              <li><span className="mode-badge mode-verified_source">Doğrulanmış kaynak cevabı</span> yalnızca etkin ISO pasajlarından</li>
              <li><span className="mode-badge mode-calculation">Hesap sonucu</span> deterministik hesap motoru</li>
              <li><span className="mode-badge mode-draft_extraction">Taslak çıkarım</span> OCR/döküm, onay gerekir</li>
              <li><span className="mode-badge mode-unverified">Doğrulanamadı</span> “Bu kaynak setinde doğrulayamadım”</li>
            </ul>
            <p className="muted small">
              Örnekler: “ISO 53 standart temel kremayer profilinde basınç açısı nedir?” · “z=20, m=2 mm dişli geometrisini hesapla” ·
              “40 mm anma ölçüsü için IT6 kaç µm?” · Kaynakları görmek için yanıttan sonra “kaynak ver”.
            </p>
          </section>
        )}
        {loading && <p className="muted" role="status">Mesajlar yükleniyor…</p>}
        {messages.map((m) => (
          <MessageView key={m.id} message={m} busy={busy} onRequestSources={() => void send('kaynak ver', [])} />
        ))}
        {busy && (
          <article className="msg msg-assistant pending" data-testid="pending-message">
            {stage && <p className="stage" role="status">⏳ {stage}</p>}
            {draft && <p className="pre">{draft}</p>}
          </article>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div ref={endRef} />
      </div>
      <Composer disabled={busy} onSend={(t, ids) => void send(t, ids)} />
    </div>
  );
}
