import { getIdToken } from './auth';

export type Profile = { name: string; email: string; phone: string; currency: string; country: string };

export type Transaction = {
  id: string;
  at: string;
  kind: 'topup' | 'send' | 'receive' | 'convert' | 'withdraw' | 'giftcard' | 'redeem' | 'refund' | 'bonus';
  currency: string;
  amount: string;
  status?: 'pending' | 'paid' | 'failed';
  counterparty?: string;
  note?: string;
  toCurrency?: string;
  toAmount?: string;
  reference?: string;
  batch?: boolean;
};

export type Business = {
  company: string; country: string; registrationId: string; website: string; monthlyVolume: string; about: string;
  status: 'pending' | 'approved' | 'rejected'; applied: string; reviewed: string; note: string;
};

export type GiftCard = {
  code: string; currency: string; amount: string; status: 'active' | 'redeemed' | 'cancelled';
  toEmail: string; toName: string; message: string; created: string; redeemedAt: string; fromName: string;
};

export type Rewards = {
  referral: {
    code: string; link: string; signups: number; rewarded: number; earnedUsd: string;
    referrerBonusUsd: string; friendBonusUsd: string; minTopupUsd: string;
  };
  referredBy: boolean;
  giftCards: GiftCard[];
  limits: { giftMinUsd: string; giftMaxUsd: string };
};

export type Coupon = {
  code: string; kind: 'percent' | 'fixed'; value: string; maxBonusUsd: string; minTopupUsd: string; maxUses: number;
  uses: number; oncePerPerson: boolean; expires: string; active: boolean; description: string; created: string;
};

export type RewardSettings = { referrerBonusUsd: string; friendBonusUsd: string; referralMinTopupUsd: string; businessFeeBps: number };

export type BusinessApplication = Business & { sub: string; name: string; email: string; phone: string };

export type Statement = { balances: Record<string, string>; transactions: Transaction[] };

export type Coin = {
  id: string; symbol: string; name: string; image: string; price: number; change24h: number | null; marketCap: number | null;
  sparkline: number[];
};

export type AdminOverview = {
  updated: string;
  totals: {
    users: number; newUsers: number; heldUsd: number; transactions: number; volumeUsd: number;
    pendingWithdrawals: number; unsubscribed: number; giftCardsOutstandingUsd: number; giftCardsSold: number;
    activeCoupons: number; referrals: number; businesses: number; businessApplications: number;
  };
  held: { currency: string; amount: string; usd: number }[];
  byKind: Partial<Record<Transaction['kind'], { count: number; volumeUsd: number }>>;
  daily: { date: string; signups: number; count: number; volumeUsd: number }[];
  users: {
    name: string; email: string; phone: string; country: string; currency: string; created: string; heldUsd: number; payouts: boolean;
    business: string; referred: boolean;
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
  me: () => request<{ profile: Profile; admin: boolean; business: Business | null } & Statement>('/me'),
  updateProfile: (fields: Partial<Pick<Profile, 'currency' | 'country'>>) =>
    request<{ profile: Profile }>('/me', { method: 'PATCH', body: JSON.stringify(fields) }),
  rates: (base: string) => request<{ base: string; updated: string; rates: Record<string, number> }>(`/rates?base=${base}`),
  crypto: (vs: string) => request<{ vs: string; coins: Coin[] }>(`/crypto/markets?vs=${vs.toLowerCase()}`),
  topup: (amount: string, currency: string, coupon = '') => post<{ url: string }>('/topup', { amount, currency, coupon }),
  confirmTopup: (sessionId: string) =>
    post<{ credited: boolean; amount: string; currency: string; bonus: string; couponNote: string } & Statement>('/topup/confirm', { sessionId }),
  checkCoupon: (code: string, amount: string, currency: string) =>
    request<{ code: string; bonus: string; currency: string; description: string }>(`/coupons/check?${new URLSearchParams({ code, amount, currency })}`),
  rewards: () => request<Rewards>('/rewards'),
  claimReferral: (code: string) => post<{ referrer: string }>('/referral/claim', { code }),
  buyGiftCard: (order: { amount: string; currency: string; toEmail: string; toName: string; message: string }) =>
    post<{ card: GiftCard; emailed: boolean } & Statement>('/giftcards', order),
  redeemGiftCard: (code: string) => post<{ amount: string; currency: string; from: string } & Statement>('/giftcards/redeem', { code }),
  cancelGiftCard: (code: string) => post<{ amount: string; currency: string } & Statement>('/giftcards/cancel', { code }),
  business: () => request<{ business: Business | null; feeBps: number; standardFeeBps: number; businessFeeBps: number; bulkMax: number }>('/business'),
  applyBusiness: (fields: Partial<Business>) => post<{ business: Business }>('/business/apply', fields),
  bulkPay: (currency: string, payments: { to: string; amount: string; note: string }[]) =>
    post<{ paid: number; total: string; currency: string } & Statement>('/business/pay', { currency, payments }),
  adminCoupons: () => request<{ coupons: Coupon[] }>('/admin/coupons'),
  createCoupon: (coupon: Partial<Coupon>) => post<{ coupon: Coupon }>('/admin/coupons', coupon),
  toggleCoupon: (code: string, active: boolean) =>
    request<{ coupon: Coupon }>(`/admin/coupons/${encodeURIComponent(code)}`, { method: 'PATCH', body: JSON.stringify({ active }) }),
  adminBusinesses: () => request<{ businesses: BusinessApplication[] }>('/admin/businesses'),
  reviewBusiness: (sub: string, status: 'approved' | 'rejected', note: string) =>
    post<{ business: Business }>(`/admin/businesses/${encodeURIComponent(sub)}/review`, { status, note }),
  adminSettings: () => request<{ settings: RewardSettings; standardFeeBps: number }>('/admin/settings'),
  saveSettings: (settings: Partial<Record<keyof RewardSettings, string | number>>) =>
    request<{ settings: RewardSettings; standardFeeBps: number }>('/admin/settings', { method: 'PUT', body: JSON.stringify(settings) }),
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
