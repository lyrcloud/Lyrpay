import { useEffect, useState } from 'react';
import { Linking, Text, TextInput } from 'react-native';
import {
  Payouts, Statement, checkCoupon, convert, fetchPayouts, onboardPayouts, quote, sendMoney, startTopUp, withdraw,
} from '../services/api';
import { AmountField, Button, Link, money, styles, useAction } from '../ui';

type Props = {
  balances: Record<string, string>;
  home: string;
  onStatement: (statement: Statement) => void;
  onNotice: (text: string) => void;
};

const available = (balances: Record<string, string>, currency: string) => `Available: ${money(balances[currency] ?? 0, currency)}`;

export function SendMoney({ balances, onStatement, onNotice }: Props) {
  const held = Object.keys(balances);
  const [to, setTo] = useState('');
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState(held[0] ?? '');
  const { busy, error, run } = useAction();

  useEffect(() => {
    if (!held.includes(currency) && held[0]) setCurrency(held[0]);
  }, [held.join()]);

  const send = run(async () => {
    const result = await sendMoney(to, amount, currency, '');
    onStatement(result);
    onNotice(`Sent ${money(amount, currency)} to ${result.sentTo || to}.`);
    setTo('');
    setAmount('');
  });

  if (!held.length) return <Text style={styles.muted}>Add money first, then you can pay anyone on Lyrpay.</Text>;

  return (
    <>
      <TextInput
        style={styles.input} placeholder="Their email or +mobile number" placeholderTextColor="#6b7394"
        autoCapitalize="none" value={to} onChangeText={setTo}
      />
      <AmountField amount={amount} onAmount={setAmount} currency={currency} onCurrency={setCurrency} />
      <Text style={styles.hint}>{available(balances, currency)}</Text>
      {!!error && <Text style={styles.error}>{error}</Text>}
      <Button title="Send now" onPress={send} busy={busy} />
    </>
  );
}

export function AddMoney({ home, onNotice, cardPayments }: Props & { cardPayments: boolean }) {
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState(home);
  const [promo, setPromo] = useState('');
  const [promoOpen, setPromoOpen] = useState(false);
  const [bonus, setBonus] = useState<{ text: string; ok: boolean } | null>(null);
  const { busy, error, run } = useAction();

  useEffect(() => setCurrency(home), [home]);

  // Shows the bonus a promo code adds, a moment after typing stops
  useEffect(() => {
    setBonus(null);
    if (!promo.trim() || !Number(amount) || currency.length !== 3) return;
    const timer = setTimeout(() => {
      checkCoupon(promo, amount, currency)
        .then(r => setBonus({ ok: true, text: `+${money(r.bonus, r.currency)} bonus${r.description ? ` · ${r.description}` : ''}` }))
        .catch(err => setBonus({ ok: false, text: err.message }));
    }, 450);
    return () => clearTimeout(timer);
  }, [promo, amount, currency]);

  const pay = run(async () => {
    await Linking.openURL(await startTopUp(amount, currency, bonus?.ok ? promo : ''));
    onNotice('Finish paying in your browser, then come back here. Your balance updates once Stripe confirms the payment.');
    setAmount('');
    setPromo('');
    setPromoOpen(false);
  });

  return (
    <>
      <Text style={styles.hint}>Pay by card, Apple Pay, Google Pay, bank or a local payment method, through Stripe.</Text>
      <AmountField amount={amount} onAmount={setAmount} currency={currency} onCurrency={setCurrency} />
      {promoOpen ? (
        <>
          <TextInput
            style={styles.input} placeholder="Promo code" placeholderTextColor="#6b7394" autoCapitalize="characters"
            autoCorrect={false} value={promo} onChangeText={text => setPromo(text.toUpperCase())}
          />
          {bonus && <Text style={bonus.ok ? styles.positive : styles.error}>{bonus.text}</Text>}
        </>
      ) : (
        <Link title="Have a promo code?" onPress={() => setPromoOpen(true)} />
      )}
      {!!error && <Text style={styles.error}>{error}</Text>}
      <Button title="Continue to payment" onPress={pay} busy={busy} disabled={!cardPayments} />
      {!cardPayments && <Text style={styles.hint}>Card payments are being switched on. Check back soon.</Text>}
    </>
  );
}

export function Convert({ balances, home, onStatement, onNotice }: Props) {
  const held = Object.keys(balances);
  const [source, setSource] = useState(held[0] ?? home);
  const [target, setTarget] = useState(home === (held[0] ?? home) ? (home === 'EUR' ? 'USD' : 'EUR') : home);
  const [amount, setAmount] = useState('');
  const [offer, setOffer] = useState<{ rate: string; receive: string; feeBps: number } | null>(null);
  const { busy, error, run } = useAction();

  useEffect(() => {
    setOffer(null);
    if (!Number(amount) || source === target || source.length !== 3 || target.length !== 3) return;
    const timer = setTimeout(() => quote(source, target, amount).then(setOffer).catch(() => setOffer(null)), 350);
    return () => clearTimeout(timer);
  }, [source, target, amount]);

  const submit = run(async () => {
    const result = await convert(source, target, amount);
    onStatement(result);
    onNotice(`Converted ${money(amount, source)} to ${money(result.receive, target)}.`);
    setAmount('');
  });

  if (!held.length) return <Text style={styles.muted}>Add money first, then convert it to any currency.</Text>;

  return (
    <>
      <Text style={styles.hint}>From</Text>
      <AmountField amount={amount} onAmount={setAmount} currency={source} onCurrency={setSource} />
      <Text style={styles.hint}>{available(balances, source)}</Text>
      <Text style={styles.hint}>To</Text>
      <TextInput
        style={[styles.input, styles.code]} autoCapitalize="characters" maxLength={3}
        value={target} onChangeText={code => setTarget(code.toUpperCase())} accessibilityLabel="To currency"
      />
      {offer && (
        <Text style={styles.text}>
          You get <Text style={styles.strong}>{money(offer.receive, target)}</Text>
          <Text style={styles.hint}>{`\n1 ${source} = ${Number(offer.rate).toPrecision(6)} ${target} · includes ${offer.feeBps / 100}% fee`}</Text>
        </Text>
      )}
      {!!error && <Text style={styles.error}>{error}</Text>}
      <Button title="Convert" onPress={submit} busy={busy} disabled={source === target} />
    </>
  );
}

/** `resumed` changes each time the app comes back to the front, e.g. from Stripe's bank-account setup. */
export function Withdraw({ balances, onStatement, onNotice, resumed, enabled }: Props & { resumed: number; enabled: boolean }) {
  const [payouts, setPayouts] = useState<Payouts | null>(null);
  const [amount, setAmount] = useState('');
  const { busy, error, setError, run } = useAction();

  useEffect(() => {
    if (enabled) fetchPayouts().then(setPayouts).catch(err => setError(err.message));
  }, [enabled, resumed]);

  const setUp = run(async () => {
    await Linking.openURL((await onboardPayouts()).url);
  });

  const submit = run(async () => {
    const result = await withdraw(amount);
    onStatement(result);
    onNotice(result.status === 'paid'
      ? `${money(result.withdrawn, result.currency)} is on its way to your bank.`
      : `Your ${money(result.withdrawn, result.currency)} withdrawal is processing. We'll finish it shortly; it won't be sent twice.`);
    setAmount('');
  });

  if (!enabled) return <Text style={styles.muted}>Bank withdrawals are being switched on. Check back soon.</Text>;
  if (!payouts) return error ?<Text style={styles.error}>{error}</Text> : <Text style={styles.muted}>Loading…</Text>;

  if (payouts.status !== 'ready') {
    return (
      <>
        <Text style={styles.hint}>
          {payouts.status === 'none'
            ? 'To withdraw, add your bank account. Stripe checks your identity and keeps your bank details; Lyrpay never sees them.'
            : 'Stripe still needs a few details before you can withdraw.'}
        </Text>
        {!!error && <Text style={styles.error}>{error}</Text>}
        <Button title={payouts.status === 'none' ? 'Add bank account' : 'Finish setup'} onPress={setUp} busy={busy} />
      </>
    );
  }

  const { currency } = payouts;
  return (
    <>
      <AmountField amount={amount} onAmount={setAmount} currency={currency} currencyLocked />
      <Text style={styles.hint}>
        {available(balances, currency)}. Withdrawals are paid in {currency}; convert other currencies first. Stripe pays
        out to your bank in about 2 business days.
      </Text>
      {!!error && <Text style={styles.error}>{error}</Text>}
      <Button title="Withdraw" onPress={submit} busy={busy} />
      <Link title="Change bank account" onPress={setUp} disabled={busy} />
    </>
  );
}
