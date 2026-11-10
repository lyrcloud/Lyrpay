import { useEffect, useState } from 'react';
import fetchStripeSignature from '@stripe/ui-extension-sdk/signature';
import type { ExtensionContextValue } from '@stripe/ui-extension-sdk/context';

const API = 'https://lyrpay.com/api/stripe-app';

/** Calls the Lyrpay API, signed by Stripe for this Dashboard user so Lyrpay knows the request is from its own account. */
export async function lyrpay<T>(path: string, context: ExtensionContextValue, extra: Record<string, string> = {}): Promise<T> {
  const response = await fetch(`${API}/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Stripe-Signature': await fetchStripeSignature() },
    body: JSON.stringify({ user_id: context.userContext?.id, account_id: context.userContext?.account.id, ...extra }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Lyrpay answered ${response.status}.`);
  return body as T;
}

export function useLyrpay<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    setData(null);
    setError('');
    load().then(d => alive && setData(d)).catch(e => alive && setError(e.message));
    return () => { alive = false; };
  }, deps);
  return { data, error };
}

export const usd = (n: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);

export function amount(value: string, currency: string) {
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(Number(value));
  } catch {
    return `${value} ${currency}`;
  }
}

export const KIND_LABELS: Record<string, string> = {
  topup: 'Top-up', send: 'Sent', receive: 'Received', convert: 'Converted', withdraw: 'Withdrawal',
  gift: 'Gift card', redeem: 'Gift card redeemed', refund: 'Refund', reward: 'Reward', card: 'Card payment',
};
