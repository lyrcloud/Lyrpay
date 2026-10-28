import { StatusBar } from 'expo-status-bar';
import { Button, FlatList, Linking, SafeAreaView, StyleSheet, Text, View } from 'react-native';
import { useEffect, useState } from 'react';
import { Coin, SITE_URL, fetchCrypto, fetchRates } from './src/services/api';

const WATCHED = ['EUR', 'GBP', 'INR', 'JPY', 'NGN', 'BDT', 'MXN', 'PHP'];

export default function App() {
  const [coins, setCoins] = useState<Coin[]>([]);
  const [rates, setRates] = useState<Record<string, number>>({});
  const [error, setError] = useState('');

  const refresh = () => {
    setError('');
    fetchCrypto().then(r => setCoins(r.coins.slice(0, 10))).catch(() => setError('Markets are unavailable right now.'));
    fetchRates().then(r => setRates(r.rates)).catch(() => setError('Rates are unavailable right now.'));
  };

  useEffect(refresh, []);

  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.title}>Lyrpay</Text>
      {/* Balances, payments and sign-in live in the web wallet until the app has its own sign-in */}
      <Button title="Open my wallet" onPress={() => Linking.openURL(SITE_URL)} />
      {!!error && <Text style={styles.error}>{error}</Text>}
      <View style={styles.card}>
        <Text style={styles.heading}>1 USD in</Text>
        {WATCHED.filter(c => rates[c]).map(c => (
          <Text key={c} style={styles.row}>{c}  {rates[c].toFixed(2)}</Text>
        ))}
      </View>
      <View style={[styles.card, styles.grow]}>
        <Text style={styles.heading}>Crypto</Text>
        <FlatList
          data={coins}
          keyExtractor={c => c.id}
          renderItem={({ item }) => (
            <Text style={styles.row}>
              {item.symbol}  ${item.price.toLocaleString()}  {item.change24h?.toFixed(2) ?? '–'}%
            </Text>
          )}
        />
      </View>
      <Button title="Refresh" onPress={refresh} />
      <StatusBar style="light" />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0b1020',
    padding: 20,
    gap: 12,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#eef0fa',
    marginTop: 12,
  },
  card: {
    width: '100%',
    backgroundColor: '#131a2e',
    borderRadius: 14,
    padding: 16,
  },
  grow: {
    flex: 1,
  },
  heading: {
    fontSize: 16,
    color: '#7b7bff',
    marginBottom: 8,
  },
  row: {
    color: '#eef0fa',
    paddingVertical: 3,
  },
  error: {
    color: '#ff7a7a',
  },
});
