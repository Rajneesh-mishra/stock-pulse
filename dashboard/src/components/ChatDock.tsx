import { useEffect, useRef, useState, KeyboardEvent } from 'react';
import type { Mode } from '../types';
import { useChat } from '../hooks/useChat';
import { Markdown } from './Markdown';

const PROMPTS = [
  'What\'s the conviction read across the pairs right now?',
  'Should I be worried about any open position?',
  'Summarize the regime note in 3 bullet points.',
  'Which watchlist alert is closest to firing?',
];

type Size = 'compact' | 'expanded';

export function ChatDock({ mode }: { mode: Mode }) {
  const { messages, send, clear, sending, lastError } = useChat();
  const [open, setOpen] = useState(false);
  const [size, setSize] = useState<Size>('compact');
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Autoscroll on new message
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, sending]);

  // Focus input when opening
  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (input.trim() && !sending) {
        const msg = input.trim();
        setInput('');
        send(msg);
      }
    }
  };

  const disabled = mode !== 'live';

  // ─── collapsed trigger pill ────────────────────────────────────────────
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-20 right-4 z-40 flex items-center gap-2 rounded-full border border-line bg-ink-800 px-4 py-3 text-sm font-medium text-fg shadow-lift transition-all hover:border-bull/50 hover:-translate-y-0.5 sm:bottom-6"
        title={disabled ? 'Chat requires live API — not available on GitHub Pages' : 'Ask Claude about your trading setup'}
      >
        <span className="relative grid h-6 w-6 place-items-center rounded-full bg-gradient-to-br from-bull to-sky text-ink-900">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M2.5 4.5h11v7h-4l-3 3v-3h-4z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="hidden sm:inline">Ask Claude</span>
        {messages.length > 0 && (
          <span className="num rounded-full bg-ink-700 px-1.5 py-0.5 text-[10px] text-fg-muted">{messages.length}</span>
        )}
      </button>
    );
  }

  // ─── panel ─────────────────────────────────────────────────────────────
  const panelClass = size === 'expanded'
    ? 'sm:w-[min(860px,calc(100vw-48px))] sm:h-[min(80vh,820px)]'
    : 'sm:w-[420px] sm:h-[560px]';

  return (
    <>
      {/* Scrim — always on mobile, only when expanded on desktop */}
      <div
        onClick={() => setOpen(false)}
        className={`fixed inset-0 z-40 bg-ink-950/60 backdrop-blur-sm ${
          size === 'expanded' ? 'sm:block' : 'sm:hidden'
        }`}
      />
      <div className={`fixed inset-x-0 bottom-0 z-50 sm:inset-auto sm:bottom-6 sm:right-6 ${panelClass}`}>
        <div className={
          `flex h-[75vh] flex-col rounded-t-xl2 border border-line bg-ink-850 shadow-lift sm:rounded-xl2 ` +
          (size === 'expanded' ? 'sm:h-full' : 'sm:h-full')
        }>
          {/* Header */}
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="relative grid h-7 w-7 shrink-0 place-items-center rounded-md bg-gradient-to-br from-bull to-sky text-ink-900">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2.5 4.5h11v7h-4l-3 3v-3h-4z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/></svg>
              </span>
              <div className="leading-tight min-w-0">
                <div className="text-sm font-semibold text-fg">Ask Claude</div>
                <div className="truncate text-[10px] uppercase tracking-[0.18em] text-fg-subtle">
                  {disabled ? 'unavailable · static mode' : 'grounded in your live state'}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              {messages.length > 0 && (
                <button
                  onClick={clear}
                  disabled={sending}
                  className="rounded-md px-2 py-1 text-[11px] text-fg-muted hover:text-fg disabled:opacity-40"
                >
                  clear
                </button>
              )}
              <button
                onClick={() => setSize(size === 'compact' ? 'expanded' : 'compact')}
                className="hidden h-7 w-7 place-items-center rounded-md text-fg-muted hover:bg-ink-700 hover:text-fg sm:grid"
                title={size === 'compact' ? 'Expand' : 'Collapse'}
                aria-label="toggle size"
              >
                {size === 'compact' ? (
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path d="M3 1H1v2M1 9v2h2M9 11h2V9M11 3V1H9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                ) : (
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path d="M4 4H1M4 4V1M8 4h3M8 4V1M4 8H1M4 8v3M8 8h3M8 8v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                )}
              </button>
              <button
                onClick={() => setOpen(false)}
                className="grid h-7 w-7 place-items-center rounded-md text-fg-muted hover:bg-ink-700 hover:text-fg"
                aria-label="close"
              >
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
              </button>
            </div>
          </div>

          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
            {messages.length === 0 ? (
              <div className="space-y-4">
                <div className="text-sm text-fg-muted">
                  Claude has read-only access to your live dashboard state — capital,
                  positions, watchlist, regime note, recent events. Ask anything grounded
                  in that context.
                </div>
                {!disabled && (
                  <div className="space-y-1.5">
                    <div className="text-[10px] uppercase tracking-[0.18em] text-fg-subtle">try asking</div>
                    {PROMPTS.map(p => (
                      <button
                        key={p}
                        onClick={() => send(p)}
                        className="block w-full rounded-lg border border-line px-3 py-2 text-left text-[13px] text-fg-muted hover:border-line-strong hover:bg-ink-700 hover:text-fg"
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                )}
                {disabled && (
                  <div className="rounded-lg border border-amber/30 bg-amber-dim p-3 text-[12px] text-amber">
                    Chat is only available when the dashboard is connected to the local
                    Python server (http://127.0.0.1:8787). On GitHub Pages the data is
                    read from static snapshots and Claude isn't wired in.
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-3">
                {messages.map((m, i) => (
                  <MessageBubble key={i} role={m.role} content={m.content} error={!!m.error} expanded={size === 'expanded'} />
                ))}
                {sending && (
                  <div className="flex items-center gap-2 pl-1 text-[12px] text-fg-muted">
                    <span className="relative flex h-2 w-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-bull opacity-75" />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-bull" />
                    </span>
                    thinking…
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Input */}
          <div className="border-t border-line p-3">
            <div className="flex items-end gap-2 rounded-xl2 border border-line bg-ink-800 focus-within:border-line-strong">
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={onKey}
                rows={1}
                placeholder={disabled ? 'Chat unavailable in static mode' : 'Ask about your setup…'}
                disabled={disabled || sending}
                className="max-h-32 flex-1 resize-none bg-transparent px-3 py-2.5 text-[13px] text-fg placeholder:text-fg-subtle focus:outline-none disabled:opacity-50"
              />
              <button
                onClick={() => {
                  if (input.trim() && !sending && !disabled) {
                    const msg = input.trim();
                    setInput('');
                    send(msg);
                  }
                }}
                disabled={disabled || sending || !input.trim()}
                className="mb-1 mr-1 grid h-8 w-8 place-items-center rounded-lg bg-bull text-ink-900 transition-colors hover:bg-bull/90 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-fg-subtle"
                aria-label="send"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2 8l12-6-5 14-2-6z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/></svg>
              </button>
            </div>
            {lastError && (
              <div className="mt-1.5 px-1 text-[11px] text-bear">
                last error: {lastError}
              </div>
            )}
            <div className="mt-1.5 px-1 text-[10px] uppercase tracking-[0.16em] text-fg-subtle">
              ⏎ send · shift+⏎ newline · read-only · no trading
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function MessageBubble({
  role, content, error, expanded,
}: {
  role: 'user' | 'assistant';
  content: string;
  error: boolean;
  expanded: boolean;
}) {
  const maxW = expanded ? 'max-w-[90%]' : 'max-w-[85%]';
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className={`rounded-2xl rounded-br-sm bg-bull/20 px-3.5 py-2 text-[13px] text-fg ${maxW} whitespace-pre-wrap`}>
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md bg-gradient-to-br from-bull to-sky text-ink-900 text-[10px] font-semibold">C</div>
      <div className={`rounded-2xl rounded-tl-sm bg-ink-750 px-3.5 py-2 ${maxW} ${error ? 'text-bear' : 'text-fg'}`}>
        {error ? (
          <div className="whitespace-pre-wrap text-[13px]">{content}</div>
        ) : (
          <Markdown>{content}</Markdown>
        )}
      </div>
    </div>
  );
}
