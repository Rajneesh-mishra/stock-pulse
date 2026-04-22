import { useCallback, useRef, useState } from 'react';

export type ChatRole = 'user' | 'assistant';
export interface ChatMessage {
  role: ChatRole;
  content: string;
  ts: number;
  error?: string;
}

export function useChat(): {
  messages: ChatMessage[];
  send: (text: string) => Promise<void>;
  clear: () => void;
  sending: boolean;
  lastError: string | null;
} {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const inFlight = useRef(false);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || inFlight.current) return;

    inFlight.current = true;
    setSending(true);
    setLastError(null);

    // 1) Push user message synchronously so UI updates immediately
    const userMsg: ChatMessage = { role: 'user', content: trimmed, ts: Date.now() };
    const nextHistory = [...messages, userMsg];
    setMessages(nextHistory);

    try {
      const body = JSON.stringify({
        messages: nextHistory.map(m => ({ role: m.role, content: m.content })),
      });
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        const err = data?.error || `http_${r.status}`;
        const detail = data?.detail ? ` — ${data.detail}` : '';
        setMessages(h => [...h, {
          role: 'assistant',
          content: `(chat failed: ${err}${detail})`,
          ts: Date.now(),
          error: err,
        }]);
        setLastError(err);
      } else {
        setMessages(h => [...h, {
          role: 'assistant',
          content: String(data.response || '').trim() || '(empty response)',
          ts: Date.now(),
        }]);
      }
    } catch (e: any) {
      const err = String(e?.message || e);
      setMessages(h => [...h, {
        role: 'assistant',
        content: `(network error: ${err})`,
        ts: Date.now(),
        error: err,
      }]);
      setLastError(err);
    } finally {
      setSending(false);
      inFlight.current = false;
    }
  }, [messages]);

  const clear = useCallback(() => {
    if (inFlight.current) return;
    setMessages([]);
    setLastError(null);
  }, []);

  return { messages, send, clear, sending, lastError };
}
