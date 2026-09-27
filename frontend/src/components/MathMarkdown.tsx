import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkMath from 'remark-math';

interface Props {
  latex: string;
  display?: boolean;
  className?: string;
}

/** Render calculation presentation; KaTeX errors remain readable text. */
export default function MathMarkdown({ latex, display = false, className }: Props) {
  const source = display ? `$$\n${latex}\n$$` : `$${latex}$`;
  return (
    <div className={className} data-testid="math-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, errorColor: 'inherit' }]]}
        disallowedElements={['img', 'script', 'iframe', 'style']}
        unwrapDisallowed
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
