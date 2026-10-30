import * as SecureStore from 'expo-secure-store';
import { getIdToken } from './auth';
import { SITE_URL } from './config';

export { SITE_URL };

export type Coin = { id: string; symbol: string; name: string; price: number; change24h: number | null };

export type Transaction = {
  id: string;
  at: string;
  kind: 'topup' | 'send' | 'receive' | 'convert' | 'withdraw' | 'giftcard' | 'redeem' | 'refund' | 'bonus' | 'card';
  currency: string;
  amount: string;
  counterparty?: string;
  status?: 'pending' | 'paid' | 'failed';
};

export type Statement = { balances: Record<string, string>; transactions: Transaction[] };

export type Payouts = { status: 'none' | 'incomplete' | 'ready'; currency: string };

export type TopUpResult = { credited: boolean; pending?: boolean; amount: string; currency: string; bonus: string; couponNote: string } & Statement;

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

const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) });

export const fetchConfig = () => request<{ cardPayments: boolean; fxFeeBps: number }>('/config');

export const fetchCrypto = (vs = 'usd') => request<{ vs: string; coins: Coin[] }>(`/crypto/markets?vs=${vs}`);

export const fetchRates = (base = 'USD') =>
  request<{ base: string; updated: string; rates: Record<string, number> }>(`/rates?base=${base}`);

export const fetchMe = () =>
  request<{ profile: { name: string; currency: string } } & Statement>('/me');

export const sendMoney = (to: string, amount: string, currency: string, note: string) =>
  post<{ sentTo: string } & Statement>('/transfer', { to, amount, currency, note });

export const checkCoupon = (code: string, amount: string, currency: string) =>
  request<{ code: string; bonus: string; currency: string; description: string }>(
    `/coupons/check?${new URLSearchParams({ code, amount, currency })}`,
  );

export const quote = (source: string, target: string, amount: string) =>
  request<{ rate: string; receive: string; feeBps: number }>(`/convert/quote?${new URLSearchParams({ source, target, amount })}`);

export const convert = (source: string, target: string, amount: string) =>
  post<{ receive: string } & Statement>('/convert', { source, target, amount });

export const fetchPayouts = () => request<Payouts>('/payouts');

export const onboardPayouts = () => post<{ url: string }>('/payouts/onboard', {});

export const withdraw = (amount: string) =>
  post<{ withdrawn: string; currency: string; status: 'paid' | 'pending' } & Statement>('/withdraw', { amount });

// Top-ups: Stripe Checkout opens in the browser and returns to the website, where the person may not be signed in,
// so the app keeps each session it opened and confirms it itself until Stripe has an answer
const TOPUPS_KEY = 'lyrpay.topups';
// Checkout sessions expire after 24 hours; one still unpaid by then never will be
const TOPUP_LIFETIME = 25 * 3600_000;

type OpenTopUp = { sessionId: string; startedAt: number };

async function openTopUps(): Promise<OpenTopUp[]> {
  const raw = await SecureStore.getItemAsync(TOPUPS_KEY);
  return raw ? (JSON.parse(raw) as OpenTopUp[]) : [];
}

async function keepTopUps(topups: OpenTopUp[]) {
  await SecureStore.setItemAsync(TOPUPS_KEY, JSON.stringify(topups));
}

export async function startTopUp(amount: string, currency: string, coupon: string) {
  const { url, sessionId } = await post<{ url: string; sessionId: string }>('/topup', { amount, currency, coupon });
  // An API older than the app doesn't send the id; the website's return page confirms those as before
  if (sessionId) await keepTopUps([...(await openTopUps()), { sessionId, startedAt: Date.now() }]);
  return url;
}

let confirming: Promise<TopUpResult[]> | null = null;

/** Confirms every top-up opened from the app that Stripe has finished. Each is credited at most once by the API. */
export function confirmTopUps(): Promise<TopUpResult[]> {
  confirming ??= (async () => {
    const results: TopUpResult[] = [];
    const waiting: OpenTopUp[] = [];
    const checked = await openTopUps();
    for (const topup of checked) {
      try {
        results.push(await post<TopUpResult>('/topup/confirm', { sessionId: topup.sessionId }));
      } catch (error) {
        // Only a malformed session id is final. Anything else (not paid yet, signed out, offline) is asked again,
        // even after sign-out, so a paid top-up is credited when its owner is back; dropped once the session expired
        const invalid = error instanceof ApiError && error.status === 400;
        if (!invalid && Date.now() - topup.startedAt < TOPUP_LIFETIME) waiting.push(topup);
      }
    }
    // Keep top-ups started while these were being checked
    const ids = new Set(checked.map(t => t.sessionId));
    await keepTopUps([...waiting, ...(await openTopUps()).filter(t => !ids.has(t.sessionId))]);
    return results;
  })().finally(() => {
    confirming = null;
  });
  return confirming;
}
