import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator, Linking, Pressable, RefreshControl, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { ApiError, Coin, SITE_URL, Statement, Transaction, fetchCrypto, fetchMe, sendMoney } from './src/services/api';
import { isSignedIn, signIn, signOut } from './src/services/auth';

const KIND_LABEL: Record<Transaction['kind'], string> = {
  topup: 'Added money', send: 'Sent', receive: 'Received', convert: 'Converted', withdraw: 'Withdrew to bank',
};

function money(amount: string | number, currency: string) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(amount));
  } catch {
    return `${amount} ${currency}`;
  }
}

function Button({ title, onPress, busy, secondary }: { title: string; onPress: () => void; busy?: boolean; secondary?: boolean }) {
  return (
    <Pressable style={[styles.button, secondary && styles.buttonSecondary]} onPress={onPress} disabled={busy}>
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>{title}</Text>}
    </Pressable>
  );
}

function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      await signIn(login, password);
      onSignedIn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
      <Text style={styles.title}>Lyrpay</Text>
      <Text style={styles.muted}>Sign in with your Lyr account, the same one you use for Lyrtrip.</Text>
      <TextInput
        style={styles.input} placeholder="Email or +mobile number" placeholderTextColor="#6b7394"
        autoCapitalize="none" autoComplete="username" keyboardType="email-address" value={login} onChangeText={setLogin}
      />
      <TextInput
        style={styles.input} placeholder="Password" placeholderTextColor="#6b7394"
        secureTextEntry autoComplete="password" value={password} onChangeText={setPassword} onSubmitEditing={submit}
      />
      {!!error && <Text style={styles.error}>{error}</Text>}
      <Button title="Sign in" onPress={submit} busy={busy} />
      {/* Sign-up, Google and password resets run on the web, where the code and redirect flows live */}
      <Button title="Create an account or sign in with Google" secondary onPress={() => Linking.openURL(SITE_URL)} />
    </ScrollView>
  );
}

function Wallet({ onSignedOut }: { onSignedOut: () => void }) {
  const [name, setName] = useState('');
  const [statement, setStatement] = useState<Statement | null>(null);
  const [coins, setCoins] = useState<Coin[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [to, setTo] = useState('');
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState('');
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');

  const leave = useCallback(async () => {
    await signOut();
    onSignedOut();
  }, [onSignedOut]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError('');
    try {
      const me = await fetchMe();
      setName(me.profile.name);
      setStatement(me);
      setCurrency(current => current || Object.keys(me.balances)[0] || me.profile.currency);
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

  const send = async () => {
    setSending(true);
    setError('');
    setNotice('');
    try {
      const result = await sendMoney(to, amount, currency.toUpperCase(), '');
      setStatement(result);
      setNotice(`Sent ${money(amount, currency)} to ${result.sentTo || to}.`);
      setTo('');
      setAmount('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  };

  const balances = Object.entries(statement?.balances ?? {});

  return (
    <ScrollView contentContainerStyle={styles.page} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}>
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
        {statement && !balances.length && <Text style={styles.muted}>No money yet. Add some on the web.</Text>}
        {balances.map(([code, value]) => (
          <View key={code} style={styles.row}><Text style={styles.text}>{code}</Text><Text style={styles.strong}>{money(value, code)}</Text></View>
        ))}
        <Button title="Add money or withdraw" secondary onPress={() => Linking.openURL(SITE_URL)} />
      </View>

      {!!balances.length && (
        <View style={styles.card}>
          <Text style={styles.heading}>Send</Text>
          <TextInput
            style={styles.input} placeholder="Their email or +mobile number" placeholderTextColor="#6b7394"
            autoCapitalize="none" value={to} onChangeText={setTo}
          />
          <View style={styles.row}>
            <TextInput
              style={[styles.input, styles.grow]} placeholder="0.00" placeholderTextColor="#6b7394"
              keyboardType="decimal-pad" value={amount} onChangeText={setAmount}
            />
            <TextInput
              style={[styles.input, styles.code]} autoCapitalize="characters" maxLength={3}
              value={currency} onChangeText={setCurrency} accessibilityLabel="Currency"
            />
          </View>
          <Button title="Send now" onPress={send} busy={sending} />
        </View>
      )}

      <View style={styles.card}>
        <Text style={styles.heading}>Activity</Text>
        {statement && !statement.transactions.length && <Text style={styles.muted}>No activity yet.</Text>}
        {statement?.transactions.slice(0, 20).map(tx => (
          <View key={tx.id} style={styles.row}>
            <Text style={[styles.text, styles.grow]} numberOfLines={1}>
              {KIND_LABEL[tx.kind]}{tx.counterparty && tx.kind !== 'topup' ? ` · ${tx.counterparty}` : ''}
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

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0b1020' },
  page: { padding: 20, gap: 12 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 },
  title: { fontSize: 28, fontWeight: '700', color: '#eef0fa' },
  card: { backgroundColor: '#131a2e', borderRadius: 14, padding: 16, gap: 8 },
  heading: { fontSize: 16, color: '#7b7bff', marginBottom: 4 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8, paddingVertical: 3 },
  grow: { flex: 1 },
  code: { width: 80, textAlign: 'center' },
  text: { color: '#eef0fa' },
  strong: { color: '#eef0fa', fontWeight: '700' },
  muted: { color: '#9aa1c0' },
  positive: { color: '#3ccf91' },
  negative: { color: '#ff7a7a' },
  error: { color: '#ff7a7a' },
  notice: { color: '#eef0fa', backgroundColor: '#1e2550', padding: 10, borderRadius: 10 },
  link: { color: '#7b7bff', fontWeight: '600' },
  input: {
    color: '#eef0fa', backgroundColor: '#1b2340', borderColor: '#252e4d', borderWidth: 1, borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 10,
  },
  button: { backgroundColor: '#5b5bf6', borderRadius: 10, paddingVertical: 12, alignItems: 'center' },
  buttonSecondary: { backgroundColor: '#252e4d' },
  buttonText: { color: '#fff', fontWeight: '600' },
});
