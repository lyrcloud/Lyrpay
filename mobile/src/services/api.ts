import { getIdToken } from './auth';

// The deployed site; CloudFront serves the API under /api on the same domain
export const SITE_URL = 'https://d2figysrc1xdl9.cloudfront.net';

export type Coin = { id: string; symbol: string; name: string; price: number; change24h: number | null };

export type Transaction = {
  id: string;
  at: string;
  kind: 'topup' | 'send' | 'receive' | 'convert' | 'withdraw';
  currency: string;
  amount: string;
  counterparty?: string;
  status?: 'pending' | 'paid' | 'failed';
};

export type Statement = { balances: Record<string, string>; transactions: Transaction[] };

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getIdToken();
  const response = await fetch(`${SITE_URL}/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : 'Something went wrong. Please try again.';
    throw new ApiError(detail, response.status);
  }
  return body as T;
}

export const fetchCrypto = (vs = 'usd') => request<{ vs: string; coins: Coin[] }>(`/crypto/markets?vs=${vs}`);

export const fetchRates = (base = 'USD') =>
  request<{ base: string; updated: string; rates: Record<string, number> }>(`/rates?base=${base}`);

export const fetchMe = () =>
  request<{ profile: { name: string; currency: string } } & Statement>('/me');

export const sendMoney = (to: string, amount: string, currency: string, note: string) =>
  request<{ sentTo: string } & Statement>('/transfer', {
    method: 'POST',
    body: JSON.stringify({ to, amount, currency, note }),
  });
