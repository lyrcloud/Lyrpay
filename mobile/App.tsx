import { StatusBar } from 'expo-status-bar';
import { SafeAreaView, StyleSheet, Text, View, Button } from 'react-native';
import { useEffect, useState } from 'react';
import { fetchMarketTicker, fetchWalletBalance } from './src/services/api';

export default function App() {
  const [market, setMarket] = useState({ symbol: 'BTCUSDT', price: 0 });
  const [balance, setBalance] = useState({ USD: 0, BTC: 0 });

  useEffect(() => {
    fetchMarketTicker('BTCUSDT').then(setMarket);
    fetchWalletBalance().then((data) => setBalance({ USD: data.balances.USD, BTC: data.balances.BTC }));
  }, []);

  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.title}>Lyrpay Mobile</Text>
      <View style={styles.card}>
        <Text style={styles.heading}>Market</Text>
        <Text>{market.symbol}</Text>
        <Text>{market.price.toFixed(2)} USD</Text>
      </View>
      <View style={styles.card}>
        <Text style={styles.heading}>Balances</Text>
        <Text>USD {balance.USD.toFixed(2)}</Text>
        <Text>BTC {balance.BTC}</Text>
      </View>
      <Button title="Refresh" onPress={() => { fetchMarketTicker('BTCUSDT').then(setMarket); fetchWalletBalance().then((data) => setBalance({ USD: data.balances.USD, BTC: data.balances.BTC })); }} />
      <StatusBar style="light" />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#050816',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
  },
  title: {
    fontSize: 28,
    color: '#e2e8f0',
    marginBottom: 20,
  },
  card: {
    width: '100%',
    backgroundColor: '#0f172a',
    borderRadius: 18,
    padding: 18,
    marginBottom: 16,
  },
  heading: {
    fontSize: 18,
    color: '#93c5fd',
    marginBottom: 10,
  },
});
