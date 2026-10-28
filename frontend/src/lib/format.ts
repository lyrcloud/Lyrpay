import countryToCurrency from 'country-to-currency';
import { getCountries, getCountryCallingCode, parsePhoneNumberFromString, type CountryCode } from 'libphonenumber-js';

const regionNames = new Intl.DisplayNames(undefined, { type: 'region' });
const currencyNames = new Intl.DisplayNames(undefined, { type: 'currency' });

export const countryName = (code: string) => regionNames.of(code) ?? code;
export const currencyName = (code: string) => currencyNames.of(code) ?? code;

export const flag = (country: string) =>
  String.fromCodePoint(...[...country.toUpperCase()].map(c => 0x1f1a5 + c.charCodeAt(0)));

export const COUNTRIES = getCountries()
  .map(code => ({ code, name: countryName(code), dial: getCountryCallingCode(code) }))
  .sort((a, b) => a.name.localeCompare(b.name));

export const currencyOf = (country: string): string =>
  (countryToCurrency as Record<string, string>)[country] ?? 'USD';

/** The visitor's country from their browser language, e.g. en-GB -> GB. */
export function guessCountry(): string {
  for (const tag of navigator.languages ?? [navigator.language]) {
    const region = new Intl.Locale(tag).maximize().region;
    if (region && COUNTRIES.some(c => c.code === region)) return region;
  }
  return 'US';
}

export function toE164(number: string, country: string): string | null {
  const parsed = parsePhoneNumberFromString(number, country as CountryCode);
  return parsed?.isValid() ? parsed.number : null;
}

export function formatMoney(amount: string | number, currency: string) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(amount));
  } catch {
    return `${amount} ${currency}`;
  }
}

export function formatPrice(amount: number, currency: string) {
  return new Intl.NumberFormat(undefined, {
    style: 'currency', currency, maximumSignificantDigits: amount < 1 ? 4 : undefined,
  }).format(amount);
}

export const formatDate = (iso: string) =>
  new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso));
