import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, Pressable, RefreshControl, SafeAreaView, ScrollView, Text, View } from 'react-native';
import { AddMoney, Convert, SendMoney, Withdraw } from './src/screens/MoneyActions';
import SignIn from './src/screens/SignIn';
import {
  ApiError, Coin, Statement, TopUpResult, Transaction, confirmTopUps, fetchConfig, fetchCrypto, fetchMe,
} from './src/services/api';
import { isSignedIn, signOut } from './src/services/auth';
import { Tabs, money, styles } from './src/ui';

const KIND_LABEL: Record<Transaction['kind'], string> = {
  topup: 'Added money', send: 'Sent', receive: 'Received', convert: 'Converted', withdraw: 'Withdrew to bank',
  giftcard: 'Gift card', redeem: 'Redeemed gift card', refund: 'Refund', bonus: 'Bonus', card: 'Card payment',
};

type Action = 'send' | 'add' | 'convert' | 'withdraw';

function topUpNotice({ amount, currency, bonus, couponNote, pending }: TopUpResult) {
  if (pending) return `${money(amount, currency)} is on its way from your bank. It usually reaches your balance within 4 business days.`;
  const extra = Number(bonus) > 0 ? ` plus a ${money(bonus, currency)} promo bonus 🎉` : couponNote ? ` (${couponNote})` : '';
  return `${money(amount, currency)} added to your balance${extra}.`;
}

function Wallet({ onSignedOut }: { onSignedOut: () => void }) {
  const [name, setName] = useState('');
  const [home, setHome] = useState('USD');
  const [statement, setStatement] = useState<Statement | null>(null);
  const [coins, setCoins] = useState<Coin[]>([]);
  const [cardPayments, setCardPayments] = useState(false);
  const [action, setAction] = useState<Action>('send');
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Counts returns to the app, so screens waiting on the browser (Stripe) check again
  const [resumed, setResumed] = useState(0);

  const leave = useCallback(async () => {
    await signOut();
    onSignedOut();
  }, [onSignedOut]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError('');
    try {
      // Top-ups paid in the browser are credited first, so the balances below include them
      const topups = (await confirmTopUps()).filter(t => t.credited || t.pending);
      if (topups.length) setNotice(topups.map(topUpNotice).join('\n'));
      const me = await fetchMe();
      setName(me.profile.name);
      setHome(me.profile.currency || 'USD');
      setStatement(me);
      fetchConfig().then(c => setCardPayments(c.cardPayments)).catch(() => {});
      setCoins((await fetchCrypto()).coins.slice(0, 10));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return leave();
      setError((err as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [leave]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Back from Stripe Checkout or Stripe's bank-account setup in the browser
  const appState = useRef(AppState.currentState);
  useEffect(() => {
    const subscription = AppState.addEventListener('change', next => {
      if (appState.current.match(/inactive|background/) && next === 'active') {
        refresh();
        setResumed(n => n + 1);
      }
      appState.current = next;
    });
    return () => subscription.remove();
  }, [refresh]);

  const balances = statement?.balances ?? {};
  const shared = { balances, home, onStatement: setStatement, onNotice: setNotice };

  return (
    <ScrollView
      contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
    >
      <View style={styles.header}>
        <Text style={styles.title}>Lyrpay</Text>
        <Pressable onPress={leave}><Text style={styles.link}>Sign out</Text></Pressable>
      </View>
      {!!name && <Text style={styles.muted}>Hi {name}</Text>}
      {!!notice && <Text style={styles.notice}>{notice}</Text>}
      {!!error && <Text style={styles.error}>{error}</Text>}

      <View style={styles.card}>
        <Text style={styles.heading}>Balances</Text>
        {!statement && <ActivityIndicator color="#7b7bff" />}
        {statement && !Object.keys(balances).length && <Text style={styles.muted}>No money yet. Add some below.</Text>}
        {Object.entries(balances).map(([code, value]) => (
          <View key={code} style={styles.row}><Text style={styles.text}>{code}</Text><Text style={styles.strong}>{money(value, code)}</Text></View>
        ))}
      </View>

      {statement && (
        <View style={styles.card}>
          <Tabs
            value={action} onChange={setAction}
            options={[['send', 'Send'], ['add', 'Add'], ['convert', 'Convert'], ['withdraw', 'Withdraw']]}
          />
          {action === 'send' && <SendMoney {...shared} />}
          {action === 'add' && <AddMoney {...shared} cardPayments={cardPayments} />}
          {action === 'convert' && <Convert {...shared} />}
          {action === 'withdraw' && <Withdraw {...shared} resumed={resumed} enabled={cardPayments} />}
        </View>
      )}

      <View style={styles.card}>
        <Text style={styles.heading}>Activity</Text>
        {statement && !statement.transactions.length && <Text style={styles.muted}>No activity yet.</Text>}
        {statement?.transactions.slice(0, 20).map(tx => (
          <View key={tx.id} style={styles.row}>
            <Text style={[styles.text, styles.grow]} numberOfLines={1}>
              {KIND_LABEL[tx.kind] ?? tx.kind}{tx.counterparty && tx.kind !== 'topup' ? ` · ${tx.counterparty}` : ''}
              {tx.status === 'pending' ? ' · Processing' : tx.status === 'failed' ? ' · Failed' : ''}
            </Text>
            <Text style={tx.amount.startsWith('-') ? styles.text : styles.positive}>
              {tx.amount.startsWith('-') ? '' : '+'}{money(tx.amount, tx.currency)}
            </Text>
          </View>
        ))}
      </View>

      <View style={styles.card}>
        <Text style={styles.heading}>Crypto</Text>
        {coins.map(coin => (
          <View key={coin.id} style={styles.row}>
            <Text style={styles.text}>{coin.symbol}</Text>
            <Text style={styles.text}>
              {money(coin.price, 'USD')}{'  '}
              <Text style={(coin.change24h ?? 0) >= 0 ? styles.positive : styles.negative}>{coin.change24h?.toFixed(2) ?? '–'}%</Text>
            </Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

export default function App() {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  useEffect(() => {
    isSignedIn().then(setSignedIn);
  }, []);

  return (
    <SafeAreaView style={styles.container}>
      {signedIn === null ? (
        <ActivityIndicator style={styles.grow} color="#7b7bff" />
      ) : signedIn ? (
        <Wallet onSignedOut={() => setSignedIn(false)} />
      ) : (
        <SignIn onSignedIn={() => setSignedIn(true)} />
      )}
      <StatusBar style="light" />
    </SafeAreaView>
  );
}
