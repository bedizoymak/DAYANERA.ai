import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { Calculation, CalcType, ExtractedValue, MemoryItem } from '../api/types';
import CalcDetail from '../components/CalcDetail';
import ModeBadge from '../components/ModeBadge';

const UNIT_OPTIONS: Record<string, string[]> = {
  length: ['mm', 'cm', 'm', 'µm'],
  angle: ['°', 'rad'],
  dimensionless: [''],
  integer: [''],
  grade: [''],
};

interface FieldState { value: string; unit: string; source: string }

export default function CalculatorPage() {
  const [types, setTypes] = useState<CalcType[]>([]);
  const [calcType, setCalcType] = useState('');
  const [fields, setFields] = useState<Record<string, FieldState>>({});
  const [compare, setCompare] = useState(true);
  const [confirmed, setConfirmed] = useState<ExtractedValue[]>([]);
  const [memValues, setMemValues] = useState<MemoryItem[]>([]);
  const [result, setResult] = useState<Calculation | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.get<CalcType[]>('/calculations/types').then((t) => {
      setTypes(t);
      if (t.length) setCalcType(t[0].calc_type);
    }).catch((e: Error) => setErr(e.message));
    api.get<{ items: ExtractedValue[] }>('/extractions/values?status=user_confirmed').then((r) => setConfirmed(r.items)).catch(() => undefined);
    api.get<MemoryItem[]>('/memory/items?kind=technical_value&status=user_confirmed').then(setMemValues).catch(() => undefined);
  }, []);

  const spec = useMemo(() => types.find((t) => t.calc_type === calcType), [types, calcType]);

  useEffect(() => {
    if (!spec) return;
    const init: Record<string, FieldState> = {};
    spec.inputs.forEach((i) => { init[i.key] = { value: '', unit: UNIT_OPTIONS[i.kind][0], source: 'user_input' }; });
    setFields(init);
    setResult(null);
  }, [spec]);

  async function run() {
    if (!spec) return;
    setBusy(true);
    setErr(null);
    try {
      const inputs: Record<string, unknown> = {};
      for (const i of spec.inputs) {
        const f = fields[i.key];
        if (!f) continue;
        if (f.source.startsWith('ev:')) {
          inputs[i.key] = { value: 0, unit: '', provenance: 'extracted_value', ref_id: f.source.slice(3) };
          continue;
        }
        if (f.source.startsWith('mem:')) {
          inputs[i.key] = { value: 0, unit: '', provenance: 'memory_item', ref_id: f.source.slice(4) };
          continue;
        }
        if (f.value.trim() === '') continue;
        let v = f.value.trim().replace(',', '.');
        if (i.kind === 'grade') {
          v = v.replace(/^IT\s*/i, '');
          if (v === '01') v = '-1'; // IT01 is encoded as -1 for the engine
        }
        inputs[i.key] = { value: Number(v), unit: f.unit, provenance: 'user_input' };
      }
      setResult(await api.post<Calculation>('/calculations', { calc_type: spec.calc_type, inputs, compare_with_llm: compare }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const r = result?.result;
  return (
    <div className="page">
      <header className="page-head"><h1>Dişli hesapları</h1></header>
      <p className="muted small">
        Deterministik, birim duyarlı hesap motoru. Formüller ve standart sabitler yalnızca etkin, doğrulanmış ISO pasajlarına
        bağlanabildiğinde çalışır; aksi hâlde “Bu kaynak setinde doğrulayamadım”. Qwen yalnızca karşılaştırma taslağı üretir.
      </p>
      {err && <p className="error" role="alert">{err}</p>}
      <form className="calc-form" onSubmit={(e) => { e.preventDefault(); void run(); }}>
        <label>Hesap türü
          <select value={calcType} onChange={(e) => setCalcType(e.target.value)}>
            {types.map((t) => (
              <option key={t.calc_type} value={t.calc_type}>{t.title}{t.available ? '' : ' (kaynak eksik)'}</option>
            ))}
          </select>
        </label>
        {spec && <p className="small">{spec.description}</p>}
        {spec && !spec.available && (
          <p className="warn small">Bu hesap için gereken bazı kaynak pasajları etkin korpusta yok; motor reddedecektir.</p>
        )}
        {spec?.inputs.map((i) => (
          <div key={i.key} className="calc-field">
            <label htmlFor={`in-${i.key}`}>{i.label}{i.required ? ' *' : ''}</label>
            <div className="calc-inputs">
              <select aria-label={`${i.label} kaynağı`} value={fields[i.key]?.source ?? 'user_input'}
                onChange={(e) => setFields((f) => ({ ...f, [i.key]: { ...f[i.key], source: e.target.value } }))}>
                <option value="user_input">Kullanıcı girdisi</option>
                {confirmed.map((c) => (
                  <option key={c.id} value={`ev:${c.id}`}>Onaylı çıkarım: {c.label} = {c.confirmed_value} {c.confirmed_unit} ({c.document_title})</option>
                ))}
                {memValues.map((m) => (
                  <option key={m.id} value={`mem:${m.id}`}>Hafıza: {m.title}</option>
                ))}
              </select>
              {(fields[i.key]?.source ?? 'user_input') === 'user_input' && (
                <>
                  <input id={`in-${i.key}`} inputMode="decimal" value={fields[i.key]?.value ?? ''}
                    onChange={(e) => setFields((f) => ({ ...f, [i.key]: { ...f[i.key], value: e.target.value } }))}
                    placeholder={i.description || ''} />
                  {UNIT_OPTIONS[i.kind].length > 1 && (
                    <select aria-label={`${i.label} birimi`} value={fields[i.key]?.unit}
                      onChange={(e) => setFields((f) => ({ ...f, [i.key]: { ...f[i.key], unit: e.target.value } }))}>
                      {UNIT_OPTIONS[i.kind].map((u) => <option key={u} value={u}>{u}</option>)}
                    </select>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        <label className="checkbox">
          <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Qwen taslağıyla karşılaştır (yerel model, yavaş olabilir)
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy || !spec}>{busy ? 'Hesaplanıyor…' : 'Hesapla'}</button>
      </form>
      {result && r && (
        <section className="calc-result" aria-label="Hesap sonucu">
          <ModeBadge mode={result.status === 'ok' ? 'calculation' : 'unverified'} />
          {result.mismatch && <span className="chip chip-bad">Qwen taslağı motorla uyuşmadı — motor sonucu gösteriliyor</span>}
          {result.status === 'ok' ? (
            <ul className="result-list">
              {r.outputs.map((o) => <li key={o.key}>{o.label}: <strong>{o.display}</strong></li>)}
            </ul>
          ) : (
            <p className="refusal">{r.message}</p>
          )}
          <details open={result.status !== 'ok'}>
            <summary>Ayrıntılı çözüm</summary>
            <CalcDetail calc={result} />
          </details>
        </section>
      )}
    </div>
  );
}
