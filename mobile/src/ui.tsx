import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

export function money(amount: string | number, currency: string) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(amount));
  } catch {
    return `${amount} ${currency}`;
  }
}

export function Button({ title, onPress, busy, secondary, disabled }: {
  title: string; onPress: () => void; busy?: boolean; secondary?: boolean; disabled?: boolean;
}) {
  return (
    <Pressable
      style={[styles.button, secondary && styles.buttonSecondary, disabled && styles.buttonDisabled]}
      onPress={onPress} disabled={busy || disabled} accessibilityRole="button"
    >
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>{title}</Text>}
    </Pressable>
  );
}

export function Link({ title, onPress, disabled }: { title: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityRole="button">
      <Text style={[styles.link, disabled && styles.muted]}>{title}</Text>
    </Pressable>
  );
}

/** An amount with its 3-letter currency code beside it. */
export function AmountField({ amount, onAmount, currency, onCurrency, currencyLocked }: {
  amount: string; onAmount: (value: string) => void; currency: string; onCurrency?: (code: string) => void; currencyLocked?: boolean;
}) {
  return (
    <View style={styles.row}>
      <TextInput
        style={[styles.input, styles.grow]} placeholder="0.00" placeholderTextColor="#6b7394"
        keyboardType="decimal-pad" value={amount} onChangeText={onAmount} accessibilityLabel="Amount"
      />
      <TextInput
        style={[styles.input, styles.code]} autoCapitalize="characters" maxLength={3} editable={!currencyLocked}
        value={currency} onChangeText={code => onCurrency?.(code.toUpperCase())} accessibilityLabel="Currency"
      />
    </View>
  );
}

/** Runs one action at a time, keeping its error to show. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = (action: () => Promise<unknown>) => async () => {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, setError, run };
}

export const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0b1020' },
  page: { padding: 20, gap: 12 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 },
  title: { fontSize: 28, fontWeight: '700', color: '#eef0fa' },
  subtitle: { fontSize: 20, fontWeight: '700', color: '#eef0fa' },
  card: { backgroundColor: '#131a2e', borderRadius: 14, padding: 16, gap: 8 },
  heading: { fontSize: 16, color: '#7b7bff', marginBottom: 4 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8, paddingVertical: 3 },
  center: { alignItems: 'center' },
  grow: { flex: 1 },
  code: { width: 80, textAlign: 'center' },
  text: { color: '#eef0fa' },
  strong: { color: '#eef0fa', fontWeight: '700' },
  muted: { color: '#9aa1c0' },
  hint: { color: '#9aa1c0', fontSize: 13 },
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
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: '#fff', fontWeight: '600' },
  tabs: { flexDirection: 'row', backgroundColor: '#1b2340', borderRadius: 10, padding: 3, gap: 3 },
  tab: { flex: 1, paddingVertical: 8, borderRadius: 8, alignItems: 'center' },
  tabActive: { backgroundColor: '#5b5bf6' },
  tabText: { color: '#9aa1c0', fontWeight: '600' },
  tabTextActive: { color: '#fff' },
});

/** A row of mutually exclusive choices. */
export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (value: T) => void }) {
  return (
    <View style={styles.tabs} accessibilityRole="tablist">
      {options.map(([key, label]) => (
        <Pressable
          key={key} style={[styles.tab, value === key && styles.tabActive]} onPress={() => onChange(key)}
          accessibilityRole="tab" accessibilityState={{ selected: value === key }}
        >
          <Text style={[styles.tabText, value === key && styles.tabTextActive]}>{label}</Text>
        </Pressable>
      ))}
    </View>
  );
}
