import { getIdToken } from './auth';

export type Profile = { name: string; email: string; phone: string; currency: string; country: string };

export type Transaction = {
  id: string;
  at: string;
  kind: 'topup' | 'send' | 'receive' | 'convert' | 'withdraw';
  currency: string;
  amount: string;
  status?: 'pending' | 'paid' | 'failed';
  counterparty?: string;
  note?: string;
  toCurrency?: string;
  toAmount?: string;
};

export type Statement = { balances: Record<string, string>; transactions: Transaction[] };

export type Coin = {
  id: string; symbol: string; name: string; image: string; price: number; change24h: number | null; marketCap: number | null;
  sparkline: number[];
};

export type AdminOverview = {
  updated: string;
  totals: {
    users: number; newUsers: number; heldUsd: number; transactions: number; volumeUsd: number;
    pendingWithdrawals: number; unsubscribed: number;
  };
  held: { currency: string; amount: string; usd: number }[];
  byKind: Partial<Record<Transaction['kind'], { count: number; volumeUsd: number }>>;
  daily: { date: string; signups: number; count: number; volumeUsd: number }[];
  users: {
    name: string; email: string; phone: string; country: string; currency: string; created: string; heldUsd: number; payouts: boolean;
  }[];
  recent: {
    id: string; at: string; kind: Transaction['kind']; currency: string; amount: string; status: string; user: string; counterparty: string;
  }[];
};

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getIdToken();
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? 'Check the details and try again.' : body.detail;
    throw new ApiError(detail || 'Something went wrong. Please try again.', response.status);
  }
  return body as T;
}

const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) });

export const api = {
  config: () => request<{ cardPayments: boolean; fxFeeBps: number }>('/config'),
  me: () => request<{ profile: Profile; admin: boolean } & Statement>('/me'),
  updateProfile: (fields: Partial<Pick<Profile, 'currency' | 'country'>>) =>
    request<{ profile: Profile }>('/me', { method: 'PATCH', body: JSON.stringify(fields) }),
  rates: (base: string) => request<{ base: string; updated: string; rates: Record<string, number> }>(`/rates?base=${base}`),
  crypto: (vs: string) => request<{ vs: string; coins: Coin[] }>(`/crypto/markets?vs=${vs.toLowerCase()}`),
  topup: (amount: string, currency: string) => post<{ url: string }>('/topup', { amount, currency }),
  confirmTopup: (sessionId: string) =>
    post<{ credited: boolean; amount: string; currency: string } & Statement>('/topup/confirm', { sessionId }),
  quote: (source: string, target: string, amount: string) =>
    request<{ rate: string; receive: string; feeBps: number }>(
      `/convert/quote?${new URLSearchParams({ source, target, amount })}`,
    ),
  convert: (source: string, target: string, amount: string) =>
    post<{ receive: string } & Statement>('/convert', { source, target, amount }),
  recipient: (q: string) => request<{ name: string }>(`/recipient?${new URLSearchParams({ q })}`),
  transfer: (to: string, amount: string, currency: string, note: string) =>
    post<{ sentTo: string } & Statement>('/transfer', { to, amount, currency, note }),
  payouts: () => request<{ status: 'none' | 'incomplete' | 'ready'; currency: string }>('/payouts'),
  onboardPayouts: () => post<{ url: string }>('/payouts/onboard', {}),
  adminOverview: (days = 14) => request<AdminOverview>(`/admin/overview?days=${days}`),
  withdraw: (amount: string) => post<{ withdrawn: string; currency: string; status: 'paid' | 'pending' } & Statement>('/withdraw', { amount }),
};
