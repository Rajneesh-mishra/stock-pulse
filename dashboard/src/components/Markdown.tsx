import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

/**
 * Markdown renderer for chat responses. Intentionally restrictive:
 *   • no raw HTML (react-markdown default)
 *   • no image embedding (we override img → nothing)
 *   • all links open in a new tab with noopener
 *
 * Styling is dense but breathable — matches the dashboard's Geist / tabular
 * numerics DNA. Inline code gets a pill, fenced code gets a bordered block
 * with horizontal scroll. Tables, lists, blockquotes are color-coordinated
 * with the bull/sky/amber tokens so responses feel native to the app.
 */
const components: Components = {
  h1: ({ children }) => (
    <h1 className="mt-4 mb-2 text-[15px] font-semibold tracking-tight text-fg first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-4 mb-2 text-[14px] font-semibold tracking-tight text-fg first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-3 mb-1.5 text-[13px] font-semibold tracking-tight text-fg first:mt-0">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-3 mb-1 text-[12px] font-semibold uppercase tracking-[0.12em] text-fg-subtle first:mt-0">{children}</h4>
  ),
  p: ({ children }) => (
    <p className="mb-2 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="mb-2 list-disc space-y-0.5 pl-5 marker:text-fg-subtle last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-2 list-decimal space-y-0.5 pl-5 marker:text-fg-subtle last:mb-0">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="leading-snug">{children}</li>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-bull underline decoration-bull/40 underline-offset-2 hover:decoration-bull"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-fg">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="italic text-fg-muted">{children}</em>
  ),
  code: ({ className, children, ...props }) => {
    // react-markdown gives us className 'language-xxx' for fenced blocks
    const isInline = !className;
    if (isInline) {
      return (
        <code className="num rounded-[4px] border border-line bg-ink-800 px-1 py-0.5 text-[0.85em] text-fg">
          {children}
        </code>
      );
    }
    return (
      <code {...props} className={`${className || ''} block`}>
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="num mb-2 max-h-80 overflow-x-auto rounded-lg border border-line bg-ink-950 p-3 text-[12px] leading-relaxed text-fg last:mb-0">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mb-2 rounded-r border-l-2 border-bull/60 bg-bull/5 py-1.5 pl-3 pr-2 italic text-fg-muted last:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-line" />,
  table: ({ children }) => (
    <div className="mb-2 overflow-x-auto last:mb-0">
      <table className="w-full border-collapse text-[12px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-ink-750/50 text-left text-[10px] uppercase tracking-[0.16em] text-fg-subtle">{children}</thead>
  ),
  th: ({ children }) => (
    <th className="border-b border-line px-2 py-1.5 font-medium">{children}</th>
  ),
  td: ({ children }) => (
    <td className="num border-b border-line/50 px-2 py-1.5 text-fg">{children}</td>
  ),
  del: ({ children }) => (
    <del className="text-fg-subtle line-through decoration-fg-subtle/40">{children}</del>
  ),
  // hard no on embedded images — keep the dock text-only
  img: () => null,
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-[13px] leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
