import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type Coin } from '../lib/api';
import { currencyName, flag, formatPrice } from '../lib/format';

const CRYPTO_EVERY_MS = 60_000;
const FX_EVERY_MS = 600_000;
// Shown first and in the ticker; everything else follows alphabetically
const MAJORS = ['USD', 'EUR', 'GBP', 'JPY', 'CNY', 'INR', 'CAD', 'AUD', 'CHF', 'HKD', 'SGD', 'AED', 'SAR', 'BRL', 'MXN', 'ZAR', 'NGN', 'KRW', 'TRY', 'PKR'];

type Fx = { base: string; updated: string; rates: Record<string, number> };

/** Calls load now and on an interval while the tab is visible, keeping the last good value. */
function useLive<T>(load: () => Promise<T>, everyMs: number, deps: unknown[]) {
  const [value, setValue] = useState<T | null>(null);
  const [at, setAt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    const run = () => {
      if (document.hidden) return;
      load().then(v => {
        if (!alive) return;
        setValue(v);
        setAt(Date.now());
        setFailed(false);
      }).catch(() => alive && setFailed(true));
    };
    run();
    const timer = setInterval(run, everyMs);
    document.addEventListener('visibilitychange', run);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', run);
    };
  }, deps);
  return { value, at, failed };
}

function useNow(everyMs = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}

function ago(at: number, now: number) {
  if (!at) return 'connecting…';
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  return seconds < 60 ? `updated ${seconds}s ago` : `updated ${Math.round(seconds / 60)} min ago`;
}

/** Crypto prices in the requested currency, falling back to US dollars where CoinGecko has none. */
function useCoins(vs: string) {
  const [shown, setShown] = useState(vs);
  useEffect(() => setShown(vs), [vs]);
  const live = useLive(
    () => api.crypto(shown).then(r => r.coins).catch(err => {
      if (shown !== 'USD') setShown('USD');
      throw err;
    }),
    CRYPTO_EVERY_MS, [shown],
  );
  return { ...live, vs: shown };
}

/** Which way each price moved since the last refresh, for a brief green or red flash. */
function useMoves(coins: Coin[] | null) {
  const last = useRef<Record<string, number>>({});
  return useMemo(() => {
    const moves: Record<string, 'up' | 'down'> = {};
    for (const coin of coins ?? []) {
      const before = last.current[coin.id];
      if (before !== undefined && before !== coin.price) moves[coin.id] = coin.price > before ? 'up' : 'down';
      last.current[coin.id] = coin.price;
    }
    return moves;
  }, [coins]);
}

const currencyFlag = (code: string) => (code.startsWith('X') ? '🌐' : flag(code.slice(0, 2)));

export function Sparkline({ points, positive }: { points: number[]; positive: boolean }) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const span = Math.max(...points) - min || 1;
  const d = points
    .map((p, i) => `${i ? 'L' : 'M'}${((i / (points.length - 1)) * 100).toFixed(2)},${(30 - ((p - min) / span) * 28 - 1).toFixed(2)}`)
    .join('');
  return (
    <svg className={`spark ${positive ? 'up' : 'down'}`} viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
      <path d={`${d}L100,30L0,30Z`} className="spark-fill" />
      <path d={d} className="spark-line" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Change({ value }: { value: number | null }) {
  if (value == null) return <span className="pill">–</span>;
  return <span className={`pill ${value >= 0 ? 'up' : 'down'}`}>{value >= 0 ? '▲' : '▼'} {Math.abs(value).toFixed(2)}%</span>;
}

/** A scrolling strip of the biggest coins and major currencies. */
export function Ticker() {
  const { value: coins } = useCoins('USD');
  const { value: fx } = useLive(() => api.rates('USD'), FX_EVERY_MS, []);
  const items: { key: string; label: string; image: string; flag: string; price: string; change: number | null }[] = [
    ...(coins ?? []).slice(0, 15).map(c => ({ key: c.id, label: c.symbol, image: c.image, flag: '', price: formatPrice(c.price, 'USD'), change: c.change24h })),
    ...MAJORS.filter(c => c !== 'USD' && fx?.rates[c]).map(c => ({
      key: c, label: `USD/${c}`, image: '', flag: currencyFlag(c), price: fx!.rates[c].toPrecision(5), change: null,
    })),
  ];
  if (!items.length) return <div className="ticker" aria-hidden="true" />;
  const strip = items.map(item => (
    <span className="tick" key={item.key}>
      {item.image ? <img src={item.image} alt="" width={16} height={16} /> : <span>{item.flag}</span>}
      <strong>{item.label}</strong>
      <span>{item.price}</span>
      {item.change != null && <em className={item.change >= 0 ? 'positive' : 'negative'}>{item.change >= 0 ? '+' : ''}{item.change.toFixed(2)}%</em>}
    </span>
  ));
  return (
    <div className="ticker" aria-label="Live prices">
      <div className="ticker-track">
        <div className="ticker-strip">{strip}</div>
        <div className="ticker-strip" aria-hidden="true">{strip}</div>
      </div>
    </div>
  );
}

type View = 'crypto' | 'fx';
type Filter = 'all' | 'gainers' | 'losers';

/** Every coin and every world currency, refreshed while the page is open. */
export default function LiveMarkets({ home }: { home: string }) {
  const [view, setView] = useState<View>('crypto');
  const [base, setBase] = useState(home);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const now = useNow();

  const crypto = useCoins(base);
  const fx = useLive<Fx>(() => api.rates(base), FX_EVERY_MS, [base]);
  const moves = useMoves(crypto.value);
  const currencies = useMemo(() => Object.keys(fx.value?.rates ?? { [base]: 1 }).sort(), [fx.value, base]);

  const q = query.trim().toLowerCase();
  const coins = useMemo(() => {
    let list = (crypto.value ?? []).filter(c => !q || c.name.toLowerCase().includes(q) || c.symbol.toLowerCase().includes(q));
    if (filter !== 'all') {
      list = list.filter(c => (filter === 'gainers' ? (c.change24h ?? 0) > 0 : (c.change24h ?? 0) < 0))
        .sort((a, b) => (filter === 'gainers' ? 1 : -1) * ((b.change24h ?? 0) - (a.change24h ?? 0)));
    }
    return list;
  }, [crypto.value, q, filter]);

  const rates = useMemo(() => {
    const all = fx.value?.rates ?? {};
    const rank = (c: string) => (MAJORS.includes(c) ? MAJORS.indexOf(c) : MAJORS.length);
    return Object.keys(all)
      .filter(c => c !== base)
      .filter(c => !q || c.toLowerCase().includes(q) || currencyName(c).toLowerCase().includes(q))
      .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
      .map(code => ({ code, rate: all[code] }));
  }, [fx.value, base, q]);

  const live = view === 'crypto' ? crypto : fx;

  return (
    <section className="markets" id="markets">
      <div className="markets-head">
        <div>
          <h2><span className={`live-dot${live.failed ? ' off' : ''}`} /> Live markets</h2>
          <p className="muted">
            {view === 'crypto' ? `Top ${crypto.value?.length ?? 50} cryptocurrencies by market cap` : `${rates.length || 160}+ world currencies at the mid-market rate`}
            {' · '}{live.failed && !live.value ? 'unavailable right now' : ago(live.at, now)}
          </p>
        </div>
        <div className="tabs markets-tabs" role="tablist">
          <button role="tab" aria-selected={view === 'crypto'} onClick={() => setView('crypto')}>Crypto</button>
          <button role="tab" aria-selected={view === 'fx'} onClick={() => setView('fx')}>Currencies</button>
        </div>
      </div>

      <div className="panel-tools">
        <input type="search" placeholder={view === 'crypto' ? 'Search coins' : 'Search currency or country'} value={query} onChange={e => setQuery(e.target.value)} />
        <label className="inline-label">Prices in
          <select value={base} onChange={e => setBase(e.target.value)}>
            {currencies.map(c => <option key={c} value={c}>{c} · {currencyName(c)}</option>)}
          </select>
        </label>
        {view === 'crypto' && (
          <div className="chips">
            {(['all', 'gainers', 'losers'] as Filter[]).map(f => (
              <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>{{ all: 'All', gainers: 'Gainers', losers: 'Losers' }[f]}</button>
            ))}
          </div>
        )}
      </div>

      {view === 'crypto' && crypto.vs !== base && <p className="hint">Crypto prices aren't quoted in {base}, so they're shown in US dollars.</p>}

      {view === 'crypto' ? (
        !crypto.value ? <SkeletonGrid /> : (
          <div className="coin-grid">
            {coins.map((coin, i) => (
              <article className="coin" key={coin.id} style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}>
                <header>
                  <img src={coin.image} alt="" width={28} height={28} loading="lazy" />
                  <div><strong>{coin.name}</strong><span>{coin.symbol}</span></div>
                  <Change value={coin.change24h} />
                </header>
                <p className={`coin-price ${moves[coin.id] ? `flash-${moves[coin.id]}` : ''}`} key={`${coin.id}-${coin.price}`}>
                  {formatPrice(coin.price, crypto.vs)}
                </p>
                <Sparkline points={coin.sparkline ?? []} positive={(coin.sparkline?.at(-1) ?? 0) >= (coin.sparkline?.[0] ?? 0)} />
                <footer>
                  <span>Market cap</span>
                  <span>{coin.marketCap ? new Intl.NumberFormat(undefined, { style: 'currency', currency: crypto.vs, notation: 'compact' }).format(coin.marketCap) : '–'}</span>
                </footer>
              </article>
            ))}
            {!coins.length && <p className="empty">No coins match “{query}”.</p>}
          </div>
        )
      ) : (
        <>
          {fx.value && <QuickConvert rates={fx.value.rates} base={base} />}
          {!fx.value ? <SkeletonGrid /> : (
            <div className="fx-grid">
              {rates.map(({ code, rate }) => (
                <article className="fx" key={code} onClick={() => setBase(code)} title={`Show prices in ${code}`}>
                  <span className="fx-flag" aria-hidden="true">{currencyFlag(code)}</span>
                  <div className="fx-name"><strong>{code}</strong><span>{currencyName(code)}</span></div>
                  <div className="fx-rate">
                    <strong>{rate.toPrecision(rate >= 1000 ? 7 : 5)}</strong>
                    <span>1 {code} = {formatPrice(1 / rate, base)}</span>
                  </div>
                </article>
              ))}
              {!rates.length && <p className="empty">No currencies match “{query}”.</p>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function QuickConvert({ rates, base }: { rates: Record<string, number>; base: string }) {
  const [amount, setAmount] = useState('100');
  const [to, setTo] = useState(base === 'EUR' ? 'USD' : 'EUR');
  const value = Number(amount) * (rates[to] ?? 0);
  return (
    <div className="quick-convert">
      <input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value.replace(/[^\d.]/g, ''))} aria-label={`Amount in ${base}`} />
      <span className="muted">{base} =</span>
      <strong className="quick-result">{Number.isFinite(value) ? new Intl.NumberFormat(undefined, { maximumFractionDigits: value < 1 ? 6 : 2 }).format(value) : '–'}</strong>
      <select value={to} onChange={e => setTo(e.target.value)} aria-label="Convert to">
        {Object.keys(rates).sort().map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="coin-grid" aria-label="Loading">
      {Array.from({ length: 8 }, (_, i) => <div className="coin skeleton" key={i} />)}
    </div>
  );
}
