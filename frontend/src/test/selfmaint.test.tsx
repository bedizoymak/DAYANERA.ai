import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Calculation } from '../api/types';
import CalcDetail from '../components/CalcDetail';

const calc: Calculation = {
  id: 'calc-sm', calc_type: 'cylindrical_gear_geometry', status: 'ok', engine_version: '1.0.0', mismatch: true,
  inputs: {}, llm_draft: { outputs: { d_b: 53.452, p_bt: 5.345 } }, created_at: '2026-09-27T00:00:00Z',
  comparison: {
    performed: true, mismatch: true,
    items: [
      { key: 'd_b', engine: 56.3816, llm: 53.452, status: 'mismatch' },
      { key: 'p_bt', engine: 5.9043, llm: 5.345, status: 'mismatch' },
    ],
    self_maintenance: {
      suspected_class: 'LLM_ARITHMETIC_ERROR', fields: ['d_b', 'p_bt'], draft_internally_consistent: false,
      formula_ids: ['gear.cyl.base_diameter', 'gear.cyl.transverse_base_pitch'], disposition: 'verified_correction',
      corrections: [{ id: 'c1', key: 'base_circle:LLM_ARITHMETIC_ERROR', family: 'base_circle', root_cause: 'LLM_ARITHMETIC_ERROR',
        calc_type: 'cylindrical_gear_geometry', status: 'VERIFIED', regression_status: 'passed', cases: 150, checks: 1621,
        occurrences: 1, stale: false }],
    },
  },
  result: {
    calc_type: 'cylindrical_gear_geometry', status: 'ok', engine_version: '1.0.0', message: 'ok',
    outputs: [{ key: 'd_b', label: 'Temel daire çapı d_b (base diameter)', value: 56.3816, unit: 'mm', formula_id: 'ISO21771:2007 Eş.(13)/(19)',
      expression: 'd_b = d·cos α_t', display: '56,3816 mm', unrounded: null }],
    inputs: [], constants: [], assumptions: [], evidence: [], diagnostics: [], trace: [],
  },
};

describe('self-maintenance in the detailed solution', () => {
  it('shows the engine-validation notice, the root cause and the verified correction', () => {
    render(<CalcDetail calc={calc} />);
    expect(screen.getByTestId('calc-detail')).toHaveTextContent(
      'Hesap motoru doğrulaması: LLM taslağında uyuşmazlık tespit edildi.');
    const panel = screen.getByTestId('self-maintenance');
    expect(panel).toHaveTextContent('LLM_ARITHMETIC_ERROR');
    expect(panel).toHaveTextContent('taslak kendi içinde tutarsız');
    expect(panel).toHaveTextContent('base_circle:LLM_ARITHMETIC_ERROR');
    expect(panel).toHaveTextContent('doğrulandı');
    expect(panel).toHaveTextContent('150 regresyon vakası');
    expect(panel).toHaveTextContent('LLM kendi düzeltmesini onaylayamaz');
    // the engine value is the displayed result
    expect(screen.getByTestId('calc-detail')).toHaveTextContent('56,3816 mm');
  });

  it('shows no diagnosis panel when the draft agrees', () => {
    render(<CalcDetail calc={{ ...calc, mismatch: false, comparison: { performed: true, mismatch: false, items: [] } }} />);
    expect(screen.queryByTestId('self-maintenance')).not.toBeInTheDocument();
    expect(screen.getByTestId('calc-detail')).toHaveTextContent('Qwen taslağı tolerans içinde uyumlu.');
  });
});
