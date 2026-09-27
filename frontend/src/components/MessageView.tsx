import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkMath from 'remark-math';
import { api } from '../api/client';
import type { Calculation, Message } from '../api/types';
import CalcDetail from './CalcDetail';
import ModeBadge from './ModeBadge';
import SourceList from './SourceList';

interface Refusal {
  reason?: string;
  codes_requested?: string[];
  codes_missing?: string[];
  codes_unverified?: string[];
}

/** Muted explanation shown under a refusal; never part of the (exact) refusal content. */
export function refusalHint(refusal: Refusal | undefined): string | null {
  if (!refusal) return null;
  if (refusal.codes_unverified && refusal.codes_unverified.length > 0) {
    return `İstenen standart yüklü ama henüz doğrulanmış korpusa alınmadı (inceleme/onay bekliyor): ${refusal.codes_unverified.join(', ')}.`;
  }
  if (refusal.codes_missing && refusal.codes_missing.length > 0) {
    return `İstenen standart yüklü değil: ${refusal.codes_missing.join(', ')}. "hangi standartlar var" yazarak listeyi görebilirsiniz.`;
  }
  switch (refusal.reason) {
    case 'no_passages':
    case 'low_relevance':
      return 'Bu konu yüklü standartlarda bulunamadı.';
    case 'unsupported_numbers':
      return 'Model yanıtındaki bazı değerler kaynakta doğrulanamadı.';
    case 'model_refused':
      return 'Bulunan kaynak pasajları bu soruyu yanıtlamıyor.';
    case 'invalid_citation':
    case 'empty_answer':
      return 'Model yanıtı kaynak pasajlarıyla doğrulanamadı.';
    case 'unsupported_calculation':
      return 'Bu hesap türü yüklü standartlarla desteklenmiyor.';
    case 'calculation_refused':
      return 'Hesap için gereken kaynak pasajı etkin korpusta yok veya değerler standardın uygulama aralığı dışında.';
    default:
      return null;
  }
}

interface Props {
  message: Message;
  onRequestSources?: () => void;
  busy?: boolean;
}

export default function MessageView({ message, onRequestSources, busy }: Props) {
  const [calc, setCalc] = useState<Calculation | null>(null);
  const [open, setOpen] = useState<boolean>(Boolean(message.metadata?.detail_open));
  const [err, setErr] = useState<string | null>(null);
  const calcId = message.metadata?.calculation_id as string | undefined;
  const isUser = message.role === 'user';

  useEffect(() => {
    if (!open || calc || !calcId) return;
    let alive = true;
    api
      .get<Calculation>(`/messages/${message.id}/calculation`)
      .then((c) => alive && setCalc(c))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [open, calc, calcId, message.id]);

  function toggleDetail() {
    setOpen((v) => !v);
  }

  const hint = refusalHint(message.metadata?.refusal as Refusal | undefined);
  const canAskSources =
    !isUser && !message.show_sources && (message.answer_mode === 'verified_source' || message.answer_mode === 'calculation');

  return (
    <article className={`msg ${isUser ? 'msg-user' : 'msg-assistant'}${message.status === 'error' ? ' msg-error' : ''}`}
      data-testid={isUser ? 'user-message' : 'assistant-message'}>
      {!isUser && (
        <div className="msg-meta">
          <ModeBadge mode={message.answer_mode} />
          {message.metadata?.mismatch === true && <span className="chip chip-bad">Qwen ≠ motor</span>}
          {message.metadata?.kind === 'corpus_inventory' && (
            <span className="chip" title="Belge listesi doğrudan veritabanından; yerel model kullanılmadı">Sistem bilgisi</span>
          )}
        </div>
      )}
      {message.attachments.length > 0 && (
        <ul className="att-list" aria-label="Ekler">
          {message.attachments.map((a) => (
            <li key={a.document_id} className="chip">📎 {a.filename}</li>
          ))}
        </ul>
      )}
      <div className="msg-body">
        {isUser ? <p className="pre">{message.content}</p> : (
          <ReactMarkdown
            remarkPlugins={[remarkMath]}
            rehypePlugins={[[rehypeKatex, { throwOnError: false, errorColor: 'inherit' }]]}
            disallowedElements={['img', 'script', 'iframe', 'style']}
            unwrapDisallowed
          >
            {message.content}
          </ReactMarkdown>
        )}
      </div>
      {!isUser && hint && (
        <p className="refusal-hint muted small" data-testid="refusal-hint">{hint}</p>
      )}
      {message.show_sources && <SourceList sources={message.sources} />}
      {!isUser && (calcId || canAskSources) && (
        <div className="msg-actions">
          {calcId && (
            <button type="button" className="btn btn-ghost" aria-expanded={open} onClick={toggleDetail}>
              {open ? 'Ayrıntılı çözümü gizle' : 'Ayrıntılı çözüm'}
            </button>
          )}
          {canAskSources && onRequestSources && (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={onRequestSources}>
              Kaynak ver
            </button>
          )}
        </div>
      )}
      {err && <p className="error small">{err}</p>}
      {open && calc && <CalcDetail calc={calc} />}
    </article>
  );
}
