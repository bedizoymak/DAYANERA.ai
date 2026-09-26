import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { DraftPage, ExtractedValue } from '../api/types';

type Tab = 'values' | 'pages';

function ValueRow({ v, onDone }: { v: ExtractedValue; onDone: (msg: string) => void }) {
  const [value, setValue] = useState(v.value !== null ? String(v.value) : '');
  const [unit, setUnit] = useState(v.unit ?? '');
  const [note, setNote] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(kind: 'confirm' | 'reject') {
    setBusy(true);
    setErr(null);
    try {
      if (kind === 'confirm') {
        const num = Number(value.replace(',', '.'));
        if (!Number.isFinite(num)) throw new Error('Geçerli bir sayı girin.');
        await api.post(`/extractions/values/${v.id}/confirm`, { value: num, unit, note: note || null });
        onDone(`“${v.label}” onaylandı (kullanıcı onaylı).`);
      } else {
        await api.post(`/extractions/values/${v.id}/reject`, { note: note || null });
        onDone(`“${v.label}” reddedildi.`);
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr data-testid="draft-value-row">
      <td>
        <span className="mode-badge mode-draft_extraction">Taslak çıkarım</span>
        <div className="small">{v.document_title}</div>
        <div className="muted small">{v.locator} · sürüm {v.version_number} · {v.extraction_method}</div>
      </td>
      <td><strong>{v.label}</strong><div className="muted small mono">{v.raw_text}</div></td>
      <td className="small">{v.context}</td>
      <td>
        <label className="sr-only" htmlFor={`val-${v.id}`}>Değer</label>
        <input id={`val-${v.id}`} className="num-input" value={value} onChange={(e) => setValue(e.target.value)} />
        <label className="sr-only" htmlFor={`unit-${v.id}`}>Birim</label>
        <input id={`unit-${v.id}`} className="unit-input" value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="birim" />
      </td>
      <td>
        <label className="sr-only" htmlFor={`note-${v.id}`}>Not</label>
        <input id={`note-${v.id}`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="not (isteğe bağlı)" />
        <div className="row-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void run('confirm')}>Onayla</button>
          <button type="button" className="btn" disabled={busy} onClick={() => void run('reject')}>Reddet</button>
        </div>
        {err && <p className="error small">{err}</p>}
      </td>
    </tr>
  );
}

export default function ReviewPage() {
  const [tab, setTab] = useState<Tab>('values');
  const [values, setValues] = useState<ExtractedValue[]>([]);
  const [pages, setPages] = useState<DraftPage[]>([]);
  const [pageTotal, setPageTotal] = useState(0);
  const [selected, setSelected] = useState<DraftPage | null>(null);
  const [text, setText] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const v = await api.get<{ items: ExtractedValue[] }>('/extractions/values?status=draft_extraction');
      setValues(v.items);
      const p = await api.get<{ items: DraftPage[]; total: number }>('/extractions/pages?status=draft_extraction&limit=200');
      setPages(p.items);
      setPageTotal(p.total);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function openPage(p: DraftPage) {
    const full = await api.get<DraftPage>(`/extractions/pages/${p.id}`);
    setSelected(full);
    setText(full.text);
  }

  async function decidePage(kind: 'confirm' | 'reject') {
    if (!selected) return;
    setErr(null);
    try {
      if (kind === 'confirm') await api.post(`/extractions/pages/${selected.id}/confirm`, { text, note: null });
      else await api.post(`/extractions/pages/${selected.id}/reject`, { note: null });
      setMsg(kind === 'confirm' ? 'Sayfa metni onaylandı; artık kullanıcı onaylı kaynak olarak aranabilir.' : 'Sayfa reddedildi.');
      setSelected(null);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <div className="page">
      <header className="page-head"><h1>İnceleme kuyruğu — Taslak çıkarım</h1></header>
      <p className="muted small">
        OCR, görsel çıkarım ve ses dökümünden gelen değerler <strong>Taslak çıkarım</strong>dır. Yetkili kullanıcı onaylamadan
        hesaplara, doğrulanmış hafızaya veya etkin teknik bilgilere giremez. Onay; işlem yapan kişi, zaman, özgün değer ve
        kaynak sürümüyle birlikte denetim kaydına yazılır.
      </p>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'values'} className={tab === 'values' ? 'tab active' : 'tab'} onClick={() => setTab('values')}>
          Değerler ({values.length})
        </button>
        <button type="button" role="tab" aria-selected={tab === 'pages'} className={tab === 'pages' ? 'tab active' : 'tab'} onClick={() => setTab('pages')}>
          OCR/döküm sayfaları ({pageTotal})
        </button>
      </div>
      {msg && <p className="ok" role="status">{msg}</p>}
      {err && <p className="error" role="alert">{err}</p>}
      {tab === 'values' && (
        <div className="table-wrap">
          {values.length === 0 ? <p className="muted">Bekleyen taslak değer yok.</p> : (
            <table className="table">
              <thead><tr><th>Kaynak</th><th>Etiket / ham metin</th><th>Bağlam</th><th>Değer (düzenlenebilir)</th><th>Karar</th></tr></thead>
              <tbody>
                {values.map((v) => (
                  <ValueRow key={v.id} v={v} onDone={(m) => { setMsg(m); void load(); }} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {tab === 'pages' && (
        <div className="split">
          <ul className="page-list">
            {pages.map((p) => (
              <li key={p.id}>
                <button type="button" className="link-btn" onClick={() => void openPage(p)}>
                  {p.document_title} — {p.locator}
                </button>
                <span className="muted small"> · {p.extraction_method}{p.ocr_mean_confidence ? ` · güven ${(p.ocr_mean_confidence * 100).toFixed(0)}%` : ''}</span>
              </li>
            ))}
          </ul>
          {selected && (
            <section className="detail">
              <h2>{selected.document_title} — {selected.locator}</h2>
              <span className="mode-badge mode-draft_extraction">Taslak çıkarım</span>
              <label htmlFor="page-edit">Metni kontrol edip düzeltin</label>
              <textarea id="page-edit" className="page-edit" value={text} onChange={(e) => setText(e.target.value)} rows={18} />
              <div className="actions">
                <button type="button" className="btn btn-primary" onClick={() => void decidePage('confirm')}>Onayla</button>
                <button type="button" className="btn" onClick={() => void decidePage('reject')}>Reddet</button>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
