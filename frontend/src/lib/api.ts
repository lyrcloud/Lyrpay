import { getIdToken } from './auth';

export type TopUpMethods = {
  country: string; cards: string[]; local: { type: string; label: string; currency: string }[]; cardsOnly: boolean; currency: string; chargedIn: string;
  paypal?: boolean; paypalTest?: boolean; paypalChargedIn?: string;
  dlocal?: boolean; dlocalTest?: boolean;
};
export type TopUpMethod = 'stripe' | 'paypal' | 'dlocal';
export type Profile = { name: string; email: string; phone: string; currency: string; country: string };

export type Transaction = {
  id: string;
  at: string;
  kind: 'topup' | 'send' | 'receive' | 'convert' | 'withdraw' | 'giftcard' | 'redeem' | 'refund' | 'bonus' | 'card' | 'payment';
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

export type Card = {
  cardId: string; last4: string; brand: string; expMonth: number; expYear: number; currency: string;
  status: 'active' | 'inactive' | 'canceled'; created: string; name: string;
};

export type CardsInfo = {
  live: boolean; country: string; currency: string; eligible: boolean; cards: Card[]; waitlisted: boolean;
  publishableKey: string; termsUrl: string;
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
  points: Points;
  giftCards: GiftCard[];
  limits: { giftMinUsd: string; giftMaxUsd: string };
};

export type Checkout = {
  id: string; merchant: string; amount: string; currency: string; description: string;
  status: 'pending' | 'paid' | 'expired' | 'cancelled' | 'refunded' | 'partially_refunded';
  expires: string; balance: string; enough: boolean; returnUrl: string; cancelUrl: string;
};

export type Merchant = {
  id: string; name: string; webhookUrl: string; returnOrigins: string[]; active: boolean; keyHint: string; created: string;
  balances: Record<string, string>;
};

export type MerchantPayment = {
  id: string; merchant: string; reference: string; status: string; amount: string; currency: string; refunded: string;
  created: string; payerName: string; callbackError: string;
};

export type Points = {
  balance: number; lifetime: number; valueUsd: string; tier: string; nextTier: { name: string; at: number } | null;
  badges: { id: string; label: string; description: string; earned: boolean }[];
  affiliatePoints: number; minRedeem: number; pointsPerFeeUsd: string; affiliatePercent: string;
};

export type CouponAudience = 'everyone' | 'business' | 'personal';

export type Coupon = {
  code: string; kind: 'percent' | 'fixed'; value: string; maxBonusUsd: string; minTopupUsd: string; maxUses: number;
  uses: number; oncePerPerson: boolean; expires: string; active: boolean; description: string; created: string;
  audience?: CouponAudience;
};

export type RewardSettings = {
  referrerBonusUsd: string; friendBonusUsd: string; referralMinTopupUsd: string; businessFeeBps: number;
  pointsPerFeeUsd: string; affiliatePercent: string;
};

export type BusinessApplication = Business & { sub: string; name: string; email: string; phone: string };

export type Statement = { balances: Record<string, string>; transactions: Transaction[] };

export type Coin = {
  id: string; symbol: string; name: string; image: string; price: number; change24h: number | null; marketCap: number | null;
  sparkline: number[];
};

export type StockGroup = 'ai' | 'etf' | 'index' | 'stock' | 'commodity';
export type Quote = {
  symbol: string; name: string; price: number; change: number | null; currency: string; exchange: string;
  high52: number | null; low52: number | null; dayHigh: number | null; dayLow: number | null;
  volume: number | null; time: number | null; sparkline: number[];
};

export type AdminOverview = {
  updated: string;
  totals: {
    users: number; newUsers: number; heldUsd: number; transactions: number; volumeUsd: number;
    pendingWithdrawals: number; unsubscribed: number; giftCardsOutstandingUsd: number; giftCardsSold: number;
    activeCoupons: number; referrals: number; businesses: number; businessApplications: number;
    cardsIssued: number; cardWaitlist: number;
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

/** unavailable: bank setup isn't switched on yet (Stripe Connect off). provider 'plaid': bank is the linked account's label and test is true for Plaid's sandbox (admins only) */
export type Payouts = {
  status: 'none' | 'incomplete' | 'ready' | 'unavailable'; currency: string; provider: 'plaid' | 'stripe'; bank?: string; test?: boolean;
  /** Withdrawals through Wise, in any currency: null while off. accounts: saved bank account labels by currency */
  wise?: WiseInfo | null;
};
export type WiseInfo = { test: boolean; accounts: Record<string, string> };
/** Wise's bank details form (docs.wise.com, account requirements): one entry per kind of account */
export type WiseField = {
  key: string; name: string; type: 'text' | 'select' | 'radio' | 'date'; required: boolean; example?: string;
  validationRegexp?: string | null; minLength?: number | null; maxLength?: number | null;
  refreshRequirementsOnChange?: boolean; valuesAllowed?: { key: string; name: string }[] | null;
};
export type WiseAccountType = { type: string; title: string; fields: { name: string; group: WiseField[] }[] };

const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) });

export const api = {
  config: () => request<{ cardPayments: boolean; fxFeeBps: number }>('/config'),
  me: () => request<{ profile: Profile; admin: boolean; business: Business | null } & Statement>('/me'),
  updateProfile: (fields: Partial<Pick<Profile, 'currency' | 'country'>>) =>
    request<{ profile: Profile }>('/me', { method: 'PATCH', body: JSON.stringify(fields) }),
  rates: (base: string) => request<{ base: string; updated: string; rates: Record<string, number> }>(`/rates?base=${base}`),
  crypto: (vs: string) => request<{ vs: string; coins: Coin[] }>(`/crypto/markets?vs=${vs.toLowerCase()}`),
  stocks: (group: StockGroup) => request<{ group: StockGroup; quotes: Quote[] }>(`/stocks/markets?group=${group}`),
  topupMethods: (currency: string) => request<TopUpMethods>(`/topup/methods?${new URLSearchParams({ currency })}`),
  topup: (amount: string, currency: string, coupon = '', method: TopUpMethod = 'stripe') =>
    post<{ url: string }>('/topup', { amount, currency, coupon, method }),
  confirmTopup: (sessionId: string) =>
    post<{ credited: boolean; pending?: boolean; provider?: string; amount: string; currency: string; bonus: string; couponNote: string } & Statement>('/topup/confirm', { sessionId }),
  checkCoupon: (code: string, amount: string, currency: string) =>
    request<{ code: string; bonus: string; currency: string; description: string }>(`/coupons/check?${new URLSearchParams({ code, amount, currency })}`),
  rewards: () => request<Rewards>('/rewards'),
  claimReferral: (code: string) => post<{ referrer: string }>('/referral/claim', { code }),
  buyGiftCard: (order: { amount: string; currency: string; toEmail: string; toName: string; message: string }) =>
    post<{ card: GiftCard; emailed: boolean } & Statement>('/giftcards', order),
  checkout: (id: string) => request<Checkout>(`/pay/${id}`),
  approveCheckout: (id: string) => post<{ redirect: string }>(`/pay/${id}/approve`, {}),
  cancelCheckout: (id: string) => post<{ redirect: string }>(`/pay/${id}/cancel`, {}),
  redeemPoints: (points: number) => post<{ usd: string } & Statement>('/points/redeem', { points }),
  redeemGiftCard: (code: string) => post<{ amount: string; currency: string; from: string } & Statement>('/giftcards/redeem', { code }),
  cancelGiftCard: (code: string) => post<{ amount: string; currency: string } & Statement>('/giftcards/cancel', { code }),
  business: () => request<{ business: Business | null; feeBps: number; standardFeeBps: number; businessFeeBps: number; bulkMax: number }>('/business'),
  applyBusiness: (fields: Partial<Business>) => post<{ business: Business }>('/business/apply', fields),
  bulkPay: (currency: string, payments: { to: string; amount: string; note: string }[]) =>
    post<{ paid: number; total: string; currency: string } & Statement>('/business/pay', { currency, payments }),
  cards: () => request<CardsInfo>('/cards'),
  joinCardWaitlist: () => post<{ waitlisted: boolean }>('/cards/waitlist', {}),
  newCard: (fields: Record<string, string | boolean>) => post<{ card: Card }>('/cards', fields),
  freezeCard: (id: string, frozen: boolean) => post<{ card: Card }>(`/cards/${encodeURIComponent(id)}/freeze`, { frozen }),
  revealCard: (id: string, nonce: string) => post<{ secret: string; version: string }>(`/cards/${encodeURIComponent(id)}/reveal`, { nonce }),
  adminCoupons: () => request<{ coupons: Coupon[] }>('/admin/coupons'),
  createCoupon: (coupon: Partial<Coupon>) => post<{ coupon: Coupon }>('/admin/coupons', coupon),
  toggleCoupon: (code: string, active: boolean) =>
    request<{ coupon: Coupon }>(`/admin/coupons/${encodeURIComponent(code)}`, { method: 'PATCH', body: JSON.stringify({ active }) }),
  adminMerchants: () => request<{ merchants: Merchant[]; payments: MerchantPayment[] }>('/admin/merchants'),
  saveMerchant: (body: Record<string, unknown>) =>
    post<Merchant & { secretKey?: string; webhookSecret?: string }>('/admin/merchants', body),
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
  payouts: () => request<Payouts>('/payouts'),
  onboardPayouts: () => post<{ url: string }>('/payouts/onboard', {}),
  adminOverview: (days = 14) => request<AdminOverview>(`/admin/overview?days=${days}`),
  withdraw: (amount: string, currency = '', rail = '') =>
    post<{ withdrawn: string; currency: string; status: 'paid' | 'pending' } & Statement>('/withdraw', { amount, currency, rail }),
  wiseRequirements: (currency: string, details?: unknown) =>
    post<{ currency: string; types: WiseAccountType[] }>('/wise/requirements', { currency, details: details ?? {} }),
  wiseRecipient: (currency: string, type: string, details: Record<string, string>) =>
    post<{ wise: WiseInfo }>('/wise/recipient', { currency, type, details }),
  wiseQuote: (currency: string, amount: string) =>
    request<{ currency: string; amount: string; receive: string; fee: string; notice: string }>(`/wise/quote?${new URLSearchParams({ currency, amount })}`),
};
