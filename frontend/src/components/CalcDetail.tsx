import type { Calculation, SelfMaintenanceSummary } from '../api/types';
import MathMarkdown from './MathMarkdown';

function fmt(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  if (v === null || v === undefined) return '—';
  return String(v);
}

function displayLabel(label: string, key: string): string {
  let text = label.replace(/\s*\([^)]*\)/g, '').trim();
  const aliases: Record<string, string[]> = {
    alpha_t: ['alpha_t', 'α_t'],
    alpha_wt: ['alpha_wt', 'α_wt'],
  };
  for (const symbol of [key, ...(aliases[key] ?? [])]) {
    const escaped = symbol.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    text = text.replace(new RegExp(`\\s${escaped}(?=\\s|$)`, 'g'), '');
  }
  return text.trim();
}

/** "Ayrıntılı çözüm": inputs, formulas, units, source identifiers and validation trace. */
export default function CalcDetail({ calc }: { calc: Calculation }) {
  const r = calc.result;
  const cmp = calc.comparison;
  return (
    <div className="calc-detail" data-testid="calc-detail">
      <p className="small muted">
        Motor sürümü {calc.engine_version} · durum: <strong>{calc.status}</strong>
      </p>
      {r.inputs?.length > 0 && (
        <>
          <h5>Girdiler ve kökenleri</h5>
          <table className="table compact">
            <thead>
              <tr><th>Girdi</th><th>Değer</th><th>Birim</th><th>Köken</th></tr>
            </thead>
            <tbody>
              {r.inputs.map((i) => {
                const prov = i.provenance as { kind?: string; status?: string; note?: string } | undefined;
                return (
                  <tr key={String(i.key)}>
                    <td>{String(i.label)}</td>
                    <td>{fmt(i.value)}</td>
                    <td>{String(i.unit || '—')}</td>
                    <td>{prov?.kind}{prov?.status && prov.status !== 'user_input' ? ` (${prov.status})` : ''}{prov?.note ? ` · ${prov.note}` : ''}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
      {r.constants?.length > 0 && (
        <>
          <h5>Standart sabitler (kaynaktan)</h5>
          <ul>
            {r.constants.map((c) => (
              <li key={String(c.key)}>
                {String(c.label)} — {String(c.note ?? '')}
              </li>
            ))}
          </ul>
        </>
      )}
      {r.assumptions?.length > 0 && (
        <>
          <h5>Varsayımlar</h5>
          <ul>{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </>
      )}
      {r.outputs?.length > 0 && (
        <>
          <h5>Formüller ve sonuçlar</h5>
          <table className="table compact">
            <thead>
              <tr><th>Büyüklük</th><th>Formül</th><th>Kural</th><th>Sonuç</th></tr>
            </thead>
            <tbody>
              {r.outputs.map((o) => (
                <tr key={o.key}>
                  <td>{displayLabel(o.label, o.key)}</td>
                  <td>
                    {o.formula_latex ? (
                      <>
                        <MathMarkdown latex={o.formula_latex} display />
                        {o.substitution_latex && <MathMarkdown latex={o.substitution_latex} display />}
                      </>
                    ) : <code>{o.expression}</code>}
                  </td>
                  <td>{o.formula_id}</td>
                  <td>
                    {o.result_latex ? <MathMarkdown latex={o.result_latex} /> : <strong>{o.display}</strong>}
                    {o.unrounded !== null && o.unrounded !== undefined && (
                      <span className="muted small"> (yuvarlanmamış {fmt(o.unrounded)})</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {(r.trace_latex?.length || r.trace?.length) ? (
        <>
          <h5>Adım adım çözüm</h5>
          <ol className="trace">
            {(r.trace_latex?.length ? r.trace_latex : r.trace).map((t, i) => (
              <li key={i}>{r.trace_latex?.length ? <MathMarkdown latex={t} display /> : <code>{t}</code>}</li>
            ))}
          </ol>
        </>
      ) : null}
      {r.evidence?.length > 0 && (
        <>
          <h5>Kaynak tanımlayıcıları</h5>
          <ul className="evidence">
            {r.evidence.map((e) => (
              <li key={e.requirement_id}>
                <strong>{e.standard_code ?? e.document_title}</strong> — {e.locator} (sürüm {e.version_number}): {e.description}
              </li>
            ))}
          </ul>
        </>
      )}
      {r.diagnostics?.length > 0 && (
        <>
          <h5>Doğrulama izi</h5>
          <ul>
            {r.diagnostics.map((d, i) => (
              <li key={i} className={d.level === 'error' ? 'error' : d.level === 'warning' ? 'warn' : ''}>
                [{d.code}] {d.message}
              </li>
            ))}
          </ul>
        </>
      )}
      {cmp && (
        <>
          <h5>Qwen taslağı karşılaştırması</h5>
          {!cmp.performed ? (
            <p className="muted small">Karşılaştırma yapılmadı ({cmp.reason ?? 'istenmedi'}).</p>
          ) : (
            <>
              <p className={cmp.mismatch ? 'error' : 'ok'}>
                {cmp.mismatch
                  ? 'Hesap motoru doğrulaması: LLM taslağında uyuşmazlık tespit edildi. Uyuşmazlık: gösterilen değerler hesap motorunundur; denetim kaydı oluşturuldu.'
                  : 'Qwen taslağı tolerans içinde uyumlu.'}
              </p>
              <table className="table compact">
                <thead><tr><th>Anahtar</th><th>Motor</th><th>Qwen</th><th>Durum</th></tr></thead>
                <tbody>
                  {cmp.items?.map((it) => (
                    <tr key={it.key} className={it.status === 'mismatch' ? 'row-bad' : ''}>
                      <td>{it.key}</td><td>{fmt(it.engine)}</td><td>{fmt(it.llm)}</td><td>{it.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {cmp.self_maintenance?.suspected_class && <SelfMaintenance sm={cmp.self_maintenance} />}
            </>
          )}
        </>
      )}
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  VERIFIED: 'doğrulandı', REJECTED: 'reddedildi', CANDIDATE: 'aday', TESTING: 'test ediliyor',
};

/** Mismatch diagnosis and the correction rule it produced (deterministic; Qwen never self-certifies). */
function SelfMaintenance({ sm }: { sm: SelfMaintenanceSummary }) {
  return (
    <div className="self-maintenance" data-testid="self-maintenance">
      <h5>Öz-bakım: kök neden ve düzeltme</h5>
      <p>
        Olası kök neden: <strong>{sm.suspected_class}</strong>
        {sm.fields?.length ? <> · alanlar: {sm.fields.join(', ')}</> : null}
        {sm.draft_internally_consistent === false && <> · taslak kendi içinde tutarsız</>}
      </p>
      {sm.formula_ids?.length ? <p className="small">Formül kayıtları: <code>{sm.formula_ids.join(', ')}</code></p> : null}
      <ul className="small">
        {sm.corrections?.map((c) => (
          <li key={c.id}>
            <code>{c.key}</code>:{' '}
            <span className={`chip ${c.status === 'VERIFIED' ? 'chip-ok' : c.status === 'REJECTED' ? 'chip-bad' : 'chip-warn'}`}>
              {STATUS_TEXT[c.status] ?? c.status}
            </span>
            {c.cases > 0 && <> · {c.cases} regresyon vakası, {c.checks} kontrol</>}
          </li>
        ))}
      </ul>
      <p className="small muted">Sınıf ve doğrulama deterministiktir; LLM kendi düzeltmesini onaylayamaz.</p>
    </div>
  );
}
