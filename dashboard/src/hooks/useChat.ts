import { useCallback, useRef, useState } from 'react';

export type ChatRole = 'user' | 'assistant';
export interface ChatMessage {
  role: ChatRole;
  content: string;
  ts: number;
  error?: string;
  streaming?: boolean;
}

/**
 * Streaming chat client.
 *
 * Server response is an SSE stream. Events:
 *   data: {"delta": "..."}                — append to the in-flight message
 *   data: {"done": true, "cost_usd": ...} — mark streaming complete
 *   data: {"error": "...", "detail": "..."} — terminal error
 *
 * The assistant message is created synchronously on send() with empty content
 * so the bubble appears immediately and grows as deltas arrive.
 */
export function useChat(): {
  messages: ChatMessage[];
  send: (text: string) => Promise<void>;
  clear: () => void;
  sending: boolean;
  lastError: string | null;
  cancel: () => void;
} {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || inFlight.current) return;

    inFlight.current = true;
    setSending(true);
    setLastError(null);

    const userMsg: ChatMessage = { role: 'user', content: trimmed, ts: Date.now() };
    const assistantPlaceholder: ChatMessage = {
      role: 'assistant', content: '', ts: Date.now(), streaming: true,
    };

    // Capture the snapshot-before-we-add-placeholder so it matches server expectations
    const historyForServer = [...messages, userMsg];
    setMessages([...historyForServer, assistantPlaceholder]);

    // Index of the placeholder in the array we just set — last element
    const placeholderIdx = historyForServer.length;  // same after prepend

    const patchAssistant = (fn: (m: ChatMessage) => ChatMessage) => {
      setMessages(prev => {
        const next = prev.slice();
        const cur = next[placeholderIdx];
        if (!cur) return prev;
        next[placeholderIdx] = fn(cur);
        return next;
      });
    };

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify({
          messages: historyForServer.map(m => ({ role: m.role, content: m.content })),
        }),
        signal: controller.signal,
      });

      if (!r.ok || !r.body) {
        const ct = r.headers.get('content-type') || '';
        let msg = `http_${r.status}`;
        if (ct.includes('application/json')) {
          try { const j = await r.json(); msg = j.error || msg; } catch { /* ignore */ }
        }
        patchAssistant(m => ({ ...m, content: `(chat failed: ${msg})`, streaming: false, error: msg }));
        setLastError(msg);
        return;
      }

      const reader = r.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let gotAnyDelta = false;
      let erroredWith: string | null = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE messages are separated by a blank line (\n\n)
        let boundary: number;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (!frame.trim()) continue;
          // A frame may have multiple data: lines — join them
          const payload = frame
            .split('\n')
            .filter(l => l.startsWith('data:'))
            .map(l => l.slice(5).trimStart())
            .join('\n');
          if (!payload) continue;
          try {
            const obj = JSON.parse(payload);
            if (typeof obj.delta === 'string') {
              gotAnyDelta = true;
              patchAssistant(m => ({ ...m, content: m.content + obj.delta, streaming: true }));
            } else if (obj.done) {
              patchAssistant(m => ({ ...m, streaming: false }));
            } else if (obj.error) {
              erroredWith = obj.error;
              const detail = obj.detail ? ` — ${obj.detail}` : '';
              patchAssistant(m => ({
                ...m,
                content: (m.content || '') + `\n\n(chat failed: ${obj.error}${detail})`,
                streaming: false,
                error: obj.error,
              }));
              setLastError(obj.error);
            }
          } catch {
            // ignore malformed frames
          }
        }
      }

      if (!gotAnyDelta && !erroredWith) {
        patchAssistant(m => ({
          ...m,
          content: '(no response from Claude)',
          streaming: false,
          error: 'empty_response',
        }));
        setLastError('empty_response');
      } else {
        patchAssistant(m => ({ ...m, streaming: false }));
      }
    } catch (e: any) {
      const aborted = e?.name === 'AbortError';
      const err = aborted ? 'cancelled' : String(e?.message || e);
      patchAssistant(m => ({
        ...m,
        content: m.content
          ? m.content + (aborted ? '\n\n(cancelled)' : `\n\n(network error: ${err})`)
          : (aborted ? '(cancelled)' : `(network error: ${err})`),
        streaming: false,
        error: err,
      }));
      if (!aborted) setLastError(err);
    } finally {
      setSending(false);
      inFlight.current = false;
      abortRef.current = null;
    }
  }, [messages]);

  const clear = useCallback(() => {
    if (inFlight.current) return;
    setMessages([]);
    setLastError(null);
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { messages, send, clear, sending, lastError, cancel };
}
