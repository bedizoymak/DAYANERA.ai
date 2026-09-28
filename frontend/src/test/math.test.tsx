import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import MathMarkdown from '../components/MathMarkdown';
import MessageView from '../components/MessageView';
import { msg } from './mockApi';

describe('KaTeX math presentation', () => {
  it('renders fractions, subscripts, Greek symbols and units without exposing TeX source', () => {
    const { container } = render(
      <MathMarkdown latex={String.raw`m_t = \frac{m_n}{\cos\beta} = 2.2068\,\mathrm{mm}`} display />,
    );
    expect(container.querySelector('.katex')).toBeInTheDocument();
    const visibleMath = container.querySelector('.katex-html')?.textContent ?? '';
    expect(visibleMath).not.toContain('\\frac');
    expect(visibleMath).not.toContain('\\cos');
  });

  it('keeps malformed math readable instead of throwing', () => {
    render(<MathMarkdown latex={String.raw`\frac{1`} />);
    expect(screen.getByTestId('math-markdown')).toHaveTextContent('\\frac{1');
  });

  it('renders a formula quoted from the engineering chunk LaTeX as display math', () => {
    // the verified context carries "LaTeX (19): d_{b} = d \cos \alpha_{t}"; the model copies it into $$ ... $$
    const { container } = render(
      <MemoryRouter>
        <MessageView message={msg({
          content: String.raw`Temel çap (ISO 97771, 4.3.1, Eşitlik (19)): $$d_{b} = d \cos \alpha_{t}$$` + '\n\n' +
            '$$\n' + String.raw`d_{b} = \frac{z m_{n} \cos \alpha_{t}}{\cos \beta}` + '\n$$\n\n[S1]',
          answer_mode: 'verified_source',
        }) as never} />
      </MemoryRouter>,
    );
    expect(container.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(2); // inline $$…$$ and a display block
    expect(container.querySelector('.katex-display')).toBeInTheDocument();
    const visible = Array.from(container.querySelectorAll('.katex-html')).map((e) => e.textContent).join(' ');
    expect(visible).not.toContain('\\cos');
    expect(visible).not.toContain('\\frac');
    expect(visible).toContain('cos');
  });

  it('keeps ordinary Markdown and citations working beside math', () => {
    const { container } = render(
      <MemoryRouter>
        <MessageView message={msg({
          content: String.raw`Sonuç: $m_t = \frac{m_n}{\cos\beta}$\n\n[ISO kaynağı](https://example.test/source)`,
          answer_mode: 'verified_source',
        }) as never} />
      </MemoryRouter>,
    );
    expect(container.querySelector('.katex')).toBeInTheDocument();
    expect(screen.getByText('ISO kaynağı')).toHaveAttribute('href', 'https://example.test/source');
  });
});
