import { useEffect, useState } from 'react';
import { api, type Transaction } from '../lib/api';
import { formatDate, formatMoney, trackingNumber } from '../lib/format';

const KIND_LABEL: Record<Transaction['kind'], string> = {
  topup: 'Added money', payment: 'Paid', send: 'Sent', receive: 'Received', convert: 'Converted', withdraw: 'Withdrew to bank',
  giftcard: 'Gift card', redeem: 'Redeemed gift card', refund: 'Refund', bonus: 'Bonus', card: 'Card', adjust: 'Adjustment',
};

const KIND_ICON: Record<Transaction['kind'], string> = {
  topup: '＋', payment: '🛍', send: '↗', receive: '↙', convert: '⇄', withdraw: '🏦',
  giftcard: '🎁', redeem: '🎟', refund: '↩', bonus: '⭐', card: '💳', adjust: '✎',
};

const STATUS_LABEL = { pending: 'Processing', failed: 'Failed, returned to balance', paid: '' };
const CARD_STATUS_LABEL = { pending: 'Pending', failed: 'Cancelled, returned to balance', paid: '' };
const TOPUP_STATUS_LABEL = { pending: 'On its way from your bank', failed: "Your bank didn't pay", paid: '' };
const statusLabels = (kind: Transaction['kind']) =>
  kind === 'card' ? CARD_STATUS_LABEL : kind === 'topup' ? TOPUP_STATUS_LABEL : STATUS_LABEL;

const RAIL: Record<string, string> = { wise: 'Wise', plaid: 'Plaid', stripe: 'Stripe' };

/** Where a withdrawal or bank top-up has got to, step by step, with what it's waiting on. */
function Tracking({ tx }: { tx: Transaction }) {
  const failed = tx.status === 'failed';
  const done = failed || tx.status === 'paid' || !tx.status;
  let steps: string[];
  let reached: number;
  let note = '';
  if (tx.kind === 'withdraw') {
    steps = ['Requested', 'Sending', 'With your bank', failed ? 'Returned to your balance' : 'Paid out'];
    reached = done ? 4 : tx.sent || tx.reference ? 2 : 1;
    if (!done) {
      note = tx.waiting === 'funds'
        ? "Waiting for Lyrpay's payout funds to clear. It goes out automatically, usually within 2 business days, and we'll email you."
        : tx.sent || tx.reference ? 'Your bank has it. Banks usually take 1 to 3 business days to show it.' : "We're sending it now.";
    }
  } else {
    steps = ['Started', 'On its way from your bank', failed ? "Your bank didn't pay" : 'Added to your balance'];
    reached = done ? 3 : 1;
    if (!done) note = 'Bank payments usually take 1 to 4 business days.';
  }
  return (
    <>
      <ol className={`tracking${failed ? ' failed' : ''}`}>
        {steps.map((step, i) => <li key={step} className={i < reached ? 'done' : i === reached ? 'now' : ''}>{step}</li>)}
      </ol>
      {note && <p className="hint">{note}</p>}
    </>
  );
}

/** Keeps a payment that's still on its way up to date: asks the provider through /api/track every 15 seconds. */
function useLive(initial: Transaction) {
  const [tx, setTx] = useState(initial);
  useEffect(() => setTx(initial), [initial]);
  const moving = tx.status === 'pending' && ['withdraw', 'topup'].includes(tx.kind);
  useEffect(() => {
    if (!moving) return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') api.track(tx.id).then(r => setTx(r.tx)).catch(() => {});
    }, 15000);
    return () => clearInterval(timer);
  }, [moving, tx.id]);
  return { tx, moving };
}

export function Details({ tx: initial }: { tx: Transaction }) {
  const { tx, moving } = useLive(initial);
  const rows: [string, string][] = [
    ['Type', KIND_LABEL[tx.kind] ?? tx.kind],
    ['Amount', formatMoney(tx.amount, tx.currency)],
  ];
  if (tx.kind === 'convert' && tx.toAmount) rows.push(['You got', formatMoney(tx.toAmount, tx.toCurrency!)]);
  const extra = tx as Transaction & { rate?: string; source?: string };
  if (extra.rate) rows.push(['Rate', `1 ${tx.currency} = ${Number(extra.rate).toPrecision(6)} ${tx.toCurrency ?? ''}`]);
  if (tx.counterparty) rows.push([tx.amount.startsWith('-') ? 'To' : 'From', tx.counterparty]);
  if (tx.note) rows.push(['Note', tx.note]);
  rows.push(['Status', tx.status === 'pending' ? 'Processing' : tx.status === 'failed' ? 'Failed, nothing was taken' : 'Completed']);
  // Only the super admin's API answers carry the provider and its reference
  if (tx.rail) rows.push(['Provider ★', RAIL[tx.rail] ?? tx.rail]);
  rows.push(['Date', new Date(tx.at).toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'medium' })]);
  if (tx.batch) rows.push(['Batch', 'Part of a bulk payment']);
  rows.push(['Tracking number', trackingNumber(tx.id)]);
  if (tx.reference) rows.push(['Provider reference ★', tx.reference]);
  return (
    <div className="tx-details">
      {moving && <span className="badge live">● Live</span>}
      {['withdraw', 'topup'].includes(tx.kind) && tx.status && <Tracking tx={tx} />}
      <dl>{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    </div>
  );
}

/** Find a payment by its tracking number (from the app or a receipt email) and follow it live. */
export function TrackPayment({ initial = '' }: { initial?: string }) {
  const [number, setNumber] = useState(initial);
  const [tx, setTx] = useState<Transaction | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const find = async (value: string) => {
    if (!value.trim()) return;
    setBusy(true);
    setError('');
    try {
      setTx((await api.track(value.trim())).tx);
    } catch (err) {
      setTx(null);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { if (initial) find(initial); }, [initial]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="track-payment">
      <form className="form" onSubmit={e => { e.preventDefault(); find(number); }}>
        <label>Track a payment <input value={number} onChange={e => setNumber(e.target.value)} placeholder="LP-XXXX-XXXX-XXXX-XXXX" autoComplete="off" spellCheck={false} /></label>
        <button className="button" disabled={busy || !number.trim()}>{busy ? 'Finding…' : 'Track'}</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      {tx && <Details tx={tx} />}
    </div>
  );
}

export function Activity({ transactions, openId = '' }: { transactions: Transaction[]; openId?: string }) {
  const [open, setOpen] = useState(openId);
  useEffect(() => { if (openId) setOpen(openId); }, [openId]);
  if (!transactions.length) return <p className="empty">No activity yet. Add money to get started.</p>;
  return (
    <ul className="activity">
      {transactions.map(tx => {
        const negative = tx.amount.startsWith('-');
        const expanded = open === tx.id;
        return (
          <li key={tx.id} className={expanded ? 'open' : ''}>
            <button className="activity-row" aria-expanded={expanded} onClick={() => setOpen(expanded ? '' : tx.id)}>
              <span className={`tx-icon kind-${tx.kind}${tx.status === 'pending' ? ' moving' : ''}`} aria-hidden="true">{KIND_ICON[tx.kind] ?? '•'}</span>
              <div className="tx-main">
                <strong>{KIND_LABEL[tx.kind]}{tx.counterparty && !['topup', 'withdraw'].includes(tx.kind) ? ` · ${tx.counterparty}` : ''}{tx.batch ? ' · bulk' : ''}</strong>
                <span>
                  {formatDate(tx.at)}{tx.note ? ` · ${tx.note}` : ''}
                  {tx.status && statusLabels(tx.kind)[tx.status] ? ` · ${statusLabels(tx.kind)[tx.status]}` : ''}
                </span>
              </div>
              <div className="tx-amount">
                <strong className={tx.status === 'failed' ? 'muted' : negative ? '' : 'positive'}>
                  {negative ? '' : '+'}{formatMoney(tx.amount, tx.currency)}
                </strong>
                {tx.kind === 'convert' && tx.toAmount && <span>+{formatMoney(tx.toAmount, tx.toCurrency!)}</span>}
              </div>
            </button>
            {expanded && <Details tx={tx} />}
          </li>
        );
      })}
    </ul>
  );
}

type Provider = { name: string; kind: string; status: 'live' | 'soon' };

// What actually moves money today vs. rails that need partner accounts and API approval first
const PROVIDERS: Provider[] = [
  { name: 'Stripe', kind: 'Cards, wallets and local methods in 135+ currencies', status: 'live' },
  { name: 'Lyrpay transfers', kind: 'Instant payments between Lyrpay accounts', status: 'live' },
  { name: 'Bank withdrawals', kind: 'To your bank account via Stripe Connect', status: 'live' },
  { name: 'Web3 wallets', kind: 'MetaMask, Coinbase Wallet and other browser wallets', status: 'live' },
  { name: 'PayPal', kind: 'PayPal and Venmo balances', status: 'soon' },
  { name: 'Wise', kind: 'Bank payouts in 40+ currencies', status: 'soon' },
  { name: 'Western Union', kind: 'Cash pickup in 200+ countries', status: 'soon' },
  { name: 'Ria', kind: 'Cash pickup and bank deposits', status: 'soon' },
  { name: 'Robinhood', kind: 'Stocks and crypto', status: 'soon' },
  { name: 'Kalshi', kind: 'Event contracts', status: 'soon' },
  { name: 'Gemini', kind: 'Crypto exchange', status: 'soon' },
  { name: 'Coinbase', kind: 'Crypto exchange', status: 'soon' },
  { name: 'Kraken', kind: 'Crypto exchange', status: 'soon' },
];

type Eth = { request(args: { method: string; params?: unknown[] }): Promise<unknown> };
const ethereum = () => (window as unknown as { ethereum?: Eth }).ethereum;

const CHAINS: Record<string, string> = {
  '0x1': 'Ethereum', '0x89': 'Polygon', '0x2105': 'Base', '0xa': 'Optimism', '0xa4b1': 'Arbitrum', '0x38': 'BNB Chain',
};

export function Connections() {
  const [wallet, setWallet] = useState<{ address: string; chain: string; balance: string } | null>(null);
  const [error, setError] = useState('');

  const connect = async () => {
    setError('');
    const provider = ethereum();
    if (!provider) return setError('No browser wallet found. Install MetaMask or Coinbase Wallet and reload.');
    try {
      const [address] = (await provider.request({ method: 'eth_requestAccounts' })) as string[];
      const chainId = (await provider.request({ method: 'eth_chainId' })) as string;
      const wei = BigInt((await provider.request({ method: 'eth_getBalance', params: [address, 'latest'] })) as string);
      setWallet({ address, chain: CHAINS[chainId] ?? `Chain ${parseInt(chainId, 16)}`, balance: (Number(wei) / 1e18).toFixed(5) });
    } catch (err) {
      setError((err as Error).message || 'The wallet did not connect.');
    }
  };

  return (
    <>
      <div className="card inset">
        <h3>Web3 wallet</h3>
        {wallet ? (
          <p className="wallet">
            <code>{wallet.address.slice(0, 6)}…{wallet.address.slice(-4)}</code> on {wallet.chain} · <strong>{wallet.balance}</strong> native balance
          </p>
        ) : (
          <button className="button" onClick={connect}>Connect wallet</button>
        )}
        {error && <p className="error">{error}</p>}
      </div>
      <ul className="providers">
        {PROVIDERS.map(p => (
          <li key={p.name}>
            <div><strong>{p.name}</strong><span>{p.kind}</span></div>
            <span className={`badge ${p.status}`}>{p.status === 'live' ? 'Live' : 'Coming soon'}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
