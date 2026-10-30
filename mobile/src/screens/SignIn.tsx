import { useEffect, useState } from 'react';
import { Linking, ScrollView, Text, TextInput } from 'react-native';
import { SITE_URL } from '../services/config';
import {
  Login, PASSWORD_HINT, PASSWORD_RULES, confirmSignUp, forgotPassword, parseLogin, resendSignUpCode, resetPassword,
  signIn, signUp,
} from '../services/auth';
import { Button, Link, Tabs, styles, useAction } from '../ui';

type Mode = 'signin' | 'signup';
type Step = 'form' | 'confirm' | 'reset';

const sentTo = (login: Login) => ('email' in login ? login.email : login.phone);

export default function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [mode, setMode] = useState<Mode>('signin');
  const [step, setStep] = useState<Step>('form');
  const [login, setLogin] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [info, setInfo] = useState('');
  // Seconds until another code can be asked for, so a double tap doesn't burn the sending limit
  const [wait, setWait] = useState(0);
  const { busy, error, setError, run } = useAction();

  useEffect(() => {
    if (!wait) return;
    const timer = setTimeout(() => setWait(w => w - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);

  const openStep = (next: Step, text: string) => {
    setCode('');
    setStep(next);
    setInfo(text);
    setWait(30);
  };

  const codeSent = (who: Login, start: string) =>
    'email' in who ? `${start} emailed a 6-digit code to ${who.email}. Check your spam folder too.` : `${start} texted a 6-digit code to ${who.phone}.`;

  const submit = run(async () => {
    const who = parseLogin(login);
    if (mode === 'signin') {
      try {
        await signIn(who, password);
      } catch (err) {
        if ((err as Error).name !== 'UserNotConfirmedException' || !('email' in who)) throw err;
        await resendSignUpCode(who);
        return openStep('confirm', `Your email isn't confirmed yet. We sent a new code to ${who.email}.`);
      }
      return onSignedIn();
    }
    if (!PASSWORD_RULES.test(password)) throw new Error(PASSWORD_HINT);
    await signUp(who, password, name.trim());
    openStep('confirm', codeSent(who, 'We'));
  });

  const confirm = run(async () => {
    await confirmSignUp(parseLogin(login), code.trim(), password, name.trim());
    onSignedIn();
  });

  const startReset = run(async () => {
    if (!login.trim()) throw new Error('Enter your email or mobile number first.');
    const who = parseLogin(login);
    await forgotPassword(who);
    setPassword('');
    openStep('reset', 'email' in who
      ? `If ${who.email} has an account, we emailed it a code. Choose a new password below.`
      : `We texted a code to ${who.phone}. Choose a new password below.`);
  });

  const finishReset = run(async () => {
    if (!PASSWORD_RULES.test(password)) throw new Error(PASSWORD_HINT);
    await resetPassword(parseLogin(login), code.trim(), password);
    onSignedIn();
  });

  const resend = run(async () => {
    const who = parseLogin(login);
    if (step === 'reset') await forgotPassword(who);
    else await resendSignUpCode(who);
    setInfo(`We sent a new code to ${sentTo(who)}.`);
    setWait(30);
  });

  const switchMode = (next: Mode) => {
    setMode(next);
    setStep('form');
    setError('');
    setInfo('');
  };

  if (step !== 'form') {
    return (
      <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Lyrpay</Text>
        <Text style={styles.subtitle}>{step === 'confirm' ? 'Enter your code' : 'Reset your password'}</Text>
        {!!info && <Text style={styles.notice}>{info}</Text>}
        <TextInput
          style={styles.input} placeholder="6-digit code" placeholderTextColor="#6b7394"
          keyboardType="number-pad" autoComplete="one-time-code" textContentType="oneTimeCode" maxLength={6}
          value={code} onChangeText={setCode}
        />
        {step === 'reset' && (
          <>
            <TextInput
              style={styles.input} placeholder="New password" placeholderTextColor="#6b7394"
              secureTextEntry autoComplete="password-new" value={password} onChangeText={setPassword}
            />
            <Text style={styles.hint}>{PASSWORD_HINT}</Text>
          </>
        )}
        {!!error && <Text style={styles.error}>{error}</Text>}
        <Button title="Continue" onPress={step === 'confirm' ? confirm : finishReset} busy={busy} />
        <Link title={wait > 0 ? `Send a new code in ${wait}s` : 'Send a new code'} onPress={resend} disabled={busy || wait > 0} />
        <Link title="Back" onPress={() => switchMode(mode)} />
      </ScrollView>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
      <Text style={styles.title}>Lyrpay</Text>
      <Text style={styles.muted}>Use your Lyr account, the same one you use for Lyrtrip.</Text>
      <Tabs value={mode} onChange={switchMode} options={[['signin', 'Sign in'], ['signup', 'Create account']]} />
      {mode === 'signup' && (
        <TextInput
          style={styles.input} placeholder="Full name" placeholderTextColor="#6b7394"
          autoComplete="name" value={name} onChangeText={setName}
        />
      )}
      <TextInput
        style={styles.input} placeholder="Email or +mobile number" placeholderTextColor="#6b7394"
        autoCapitalize="none" autoComplete="username" keyboardType="email-address" value={login} onChangeText={setLogin}
      />
      <TextInput
        style={styles.input} placeholder="Password" placeholderTextColor="#6b7394" secureTextEntry
        autoComplete={mode === 'signin' ? 'password' : 'password-new'} value={password} onChangeText={setPassword}
        onSubmitEditing={submit}
      />
      {mode === 'signup' && <Text style={styles.hint}>{PASSWORD_HINT}</Text>}
      {!!error && <Text style={styles.error}>{error}</Text>}
      <Button title={mode === 'signin' ? 'Sign in' : 'Create account'} onPress={submit} busy={busy} />
      {mode === 'signin' && <Link title="Forgot password?" onPress={startReset} disabled={busy} />}
      {/* Google needs the Hosted UI redirect, which only the website is registered for */}
      <Button title="Continue with Google on the web" secondary onPress={() => Linking.openURL(SITE_URL)} />
    </ScrollView>
  );
}
