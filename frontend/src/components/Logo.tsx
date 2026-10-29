import { useId } from 'react';

/** The Lyrpay mark: an L whose foot sends money up and away. Same drawing as public/favicon.svg. */
export function LogoMark({ size = 32 }: { size?: number }) {
  const id = useId();
  return (
    <svg className="logo-mark" viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id={`${id}bg`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#7c5cff" />
          <stop offset="0.55" stopColor="#5b5bf6" />
          <stop offset="1" stopColor="#18c8e8" />
        </linearGradient>
        <linearGradient id={`${id}shine`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.18" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="18" fill={`url(#${id}bg)`} />
      <path d="M4 22C16 10 40 6 60 14V0H4z" fill={`url(#${id}shine)`} />
      <path d="M22 15v26a6 6 0 0 0 6 6h16" fill="none" stroke="#fff" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
      <path className="logo-arrow" d="M33 31l13-13M37 17.5h9.5V27" fill="none" stroke="#fff" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" opacity="0.92" />
    </svg>
  );
}

export default function Logo({ size = 32, large = false }: { size?: number; large?: boolean }) {
  return (
    <a href="/" className={`brand${large ? ' brand-large' : ''}`} aria-label="Lyrpay home">
      <LogoMark size={size} />
      <span className="wordmark">Lyr<span>pay</span></span>
    </a>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <span className="site-footer-brand"><LogoMark size={16} />© {new Date().getFullYear()} Lyrpay</span>
      <span>Powered by Lyrcloud</span>
    </footer>
  );
}
