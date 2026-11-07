// Lyrbot: the floating help assistant, as on Lyrcloud, Lyrshop, Lyrtrip and Lyrsoft. A robot button opens a chat
// window answered by POST /api/lyrbot, with quick questions and Lyr Chat, WhatsApp and email for a person.
// The chat lasts for the browser tab.
import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';

type Turn = { role: 'user' | 'assistant'; content: string };

const STORAGE_KEY = 'lyrbotChat';
const WHATSAPP_URL = 'https://wa.me/16615809465';
const WHATSAPP_LABEL = '+1 661 580 9465';
const SUPPORT_EMAIL = 'support@lyrpay.com';
const LYR_CHAT_URL = 'https://lyrsoft.com/chat/#/new?to=support&site=Lyrpay';
const GREETING = "Hi, I'm Lyrbot! Ask me about sending, converting or adding money, gift cards, or your Lyrpay account.";
const QUICK_QUESTIONS = ['How do I add money?', 'What does converting cost?', 'How do I send money?', 'How do gift cards work?'];
const OFFLINE = `I couldn't connect just now. You can reach our team on Lyr Chat, WhatsApp ${WHATSAPP_LABEL} or at ${SUPPORT_EMAIL}.`;

// Links, emails, lyrpay.com paths and the WhatsApp number become anchors; everything else stays text (React escapes it).
const LINK = /(https?:\/\/[^\s<]+[^\s<.,;:!?)]|[\w.+-]+@[\w-]+\.[\w.-]*\w|\blyrpay\.com(?:\/[\w?=&-]*)*\/?|\+1[\s-]?661[\s-]?580[\s-]?9465)/g;

function linkify(text: string): ReactNode[] {
  return text.split('\n').flatMap((line, i) => {
    const parts: ReactNode[] = line.split(LINK).map((part, j) => {
      if (j % 2 === 0) return <Fragment key={j}>{part}</Fragment>;
      if (part.startsWith('http')) return <a key={j} href={part} target="_blank" rel="noopener">{part}</a>;
      if (part.includes('@')) return <a key={j} href={`mailto:${part}`}>{part}</a>;
      if (part.startsWith('+')) return <a key={j} href={WHATSAPP_URL} target="_blank" rel="noopener">{part}</a>;
      return <a key={j} href={`https://${part}`}>{part}</a>;
    });
    return i ? [<br key={`br${i}`} />, ...parts] : parts;
  });
}

function readHistory(): Turn[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function saveHistory(history: Turn[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-30)));
  } catch {
    // Private browsing can block storage; the chat still works for this page view.
  }
}

function RobotIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <line x1="16" y1="3" x2="16" y2="7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="16" cy="3" r="1.8" fill="currentColor" />
      <rect x="6" y="8" width="20" height="16" rx="5" fill="currentColor" />
      <rect x="2.5" y="13" width="3" height="6" rx="1.5" fill="currentColor" />
      <rect x="26.5" y="13" width="3" height="6" rx="1.5" fill="currentColor" />
      <circle className="lyrbot-eye" cx="12" cy="15.5" r="2.2" />
      <circle className="lyrbot-eye" cx="20" cy="15.5" r="2.2" />
      <path className="lyrbot-mouth" d="M12 20.2q4 2.6 8 0" fill="none" strokeWidth="1.8" strokeLinecap="round" />
      <rect x="11" y="25" width="10" height="4" rx="2" fill="currentColor" />
    </svg>
  );
}

function WhatsAppIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2Zm0 18.2a8.2 8.2 0 0 1-4.2-1.2l-.3-.2-3 .8.8-2.9-.2-.3A8.2 8.2 0 1 1 12 20.2Zm4.5-6.1c-.2-.1-1.5-.7-1.7-.8-.2-.1-.4-.1-.6.1l-.8 1c-.1.2-.3.2-.5.1a6.7 6.7 0 0 1-3.3-2.9c-.2-.4.2-.4.7-1.3.1-.2 0-.3 0-.5l-.8-1.8c-.2-.5-.4-.4-.6-.4h-.5a1 1 0 0 0-.7.3 3 3 0 0 0-.9 2.2 5.2 5.2 0 0 0 1.1 2.8 11.9 11.9 0 0 0 4.6 4c1.7.7 2.4.8 3.2.7a2.8 2.8 0 0 0 1.8-1.3 2.3 2.3 0 0 0 .2-1.3c-.1-.1-.2-.2-.5-.3Z" />
    </svg>
  );
}

export default function Lyrbot() {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<Turn[]>(readHistory);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const messages = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    if (open) input.current?.focus();
    else if (wasOpen.current) toggle.current?.focus();
    wasOpen.current = open;
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  useEffect(() => {
    messages.current?.scrollTo({ top: messages.current.scrollHeight });
  }, [history, busy, error, open]);

  async function ask(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    const asked: Turn[] = [...history, { role: 'user', content: text }];
    setHistory(asked);
    saveHistory(asked);
    setError('');
    setBusy(true);
    try {
      const res = await fetch('/api/lyrbot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: asked }),
      });
      const isJson = (res.headers.get('Content-Type') || '').includes('application/json');
      const data = isJson ? await res.json() : {};
      if (!res.ok || !data.reply) throw new Error(data.error || '');
      const answered: Turn[] = [...asked, { role: 'assistant', content: data.reply }];
      setHistory(answered);
      saveHistory(answered);
    } catch (err) {
      // Drop the unanswered question so a retry doesn't send it twice.
      setHistory(history);
      saveHistory(history);
      setError((err instanceof Error && err.message) || OFFLINE);
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft;
    setDraft('');
    ask(text);
  }

  return (
    <div className={`lyrbot${open ? ' is-open' : ''}`}>
      {open && (
        <section className="lyrbot-panel" id="lyrbot-panel" role="dialog" aria-label="Lyrbot AI assistant">
          <header className="lyrbot-header">
            <span className="lyrbot-avatar"><RobotIcon /></span>
            <div>
              <strong>Lyrbot</strong>
              <small>AI assistant · replies in seconds</small>
            </div>
            <button type="button" className="lyrbot-close" aria-label="Close Lyrbot" onClick={() => setOpen(false)}>&times;</button>
          </header>
          <div className="lyrbot-messages" ref={messages} aria-live="polite">
            <div className="lyrbot-message is-bot">{linkify(GREETING)}</div>
            {history.map((turn, i) => (
              <div key={i} className={`lyrbot-message ${turn.role === 'user' ? 'is-user' : 'is-bot'}`}>
                {turn.role === 'user' ? turn.content : linkify(turn.content)}
              </div>
            ))}
            {busy && (
              <div className="lyrbot-message is-bot">
                <span className="lyrbot-typing" aria-label="Lyrbot is typing"><i /><i /><i /></span>
              </div>
            )}
            {error && <div className="lyrbot-message is-bot is-error">{linkify(error)}</div>}
          </div>
          {history.length === 0 && !busy && (
            <div className="lyrbot-quick" aria-label="Suggested questions">
              {QUICK_QUESTIONS.map((question) => (
                <button type="button" key={question} onClick={() => ask(question)}>{question}</button>
              ))}
            </div>
          )}
          <form className="lyrbot-form" onSubmit={submit}>
            <input
              ref={input}
              type="text"
              className="lyrbot-input"
              maxLength={1000}
              placeholder="Ask Lyrbot a question…"
              aria-label="Your question"
              autoComplete="off"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
            />
            <button type="submit" className="lyrbot-send" aria-label="Send" disabled={busy}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3.4 20.4 21 12 3.4 3.6 3.3 10l12.6 2-12.6 2z" /></svg>
            </button>
          </form>
          <div className="lyrbot-contact">
            <span>Prefer a person?</span>
            <a className="lyrbot-lyrchat" href={LYR_CHAT_URL} target="_blank" rel="noopener">💬 Lyr Chat</a>
            <a className="lyrbot-whatsapp" href={WHATSAPP_URL} target="_blank" rel="noopener"><WhatsAppIcon /> WhatsApp</a>
            <a className="lyrbot-email" href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          </div>
        </section>
      )}
      <button
        ref={toggle}
        type="button"
        className="lyrbot-toggle"
        aria-expanded={open}
        aria-controls="lyrbot-panel"
        aria-label={open ? 'Close Lyrbot' : 'Open Lyrbot, AI assistant'}
        onClick={() => setOpen(!open)}
      >
        <RobotIcon />
        <span className="lyrbot-toggle-label">Lyrbot</span>
      </button>
    </div>
  );
}
