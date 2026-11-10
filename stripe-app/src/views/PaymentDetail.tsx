import React from 'react';
import { Badge, Banner, Box, ContextView, Divider, Inline, PropertyList, PropertyListItem, Spinner } from '@stripe/ui-extension-sdk/ui';
import type { ExtensionContextValue } from '@stripe/ui-extension-sdk/context';
import BrandIcon from './brand_icon.svg';
import { KIND_LABELS, amount, lyrpay, useLyrpay } from '../lyrpay';

type Payment =
  | { lyrpay: false }
  | {
    lyrpay: true; session: string; status: 'credited' | 'pending' | 'not credited'; coupon: string;
    user: { name: string; email: string; phone: string; country: string; currency: string; created: string };
    balances: Record<string, string>;
    transactions: { id: string; at: string; kind: string; currency: string; amount: string; status?: string }[];
  };

const STATUS = {
  credited: { type: 'positive', text: 'Credited to wallet' },
  pending: { type: 'warning', text: 'Waiting for the bank' },
  'not credited': { type: 'negative', text: 'Not credited yet' },
} as const;

/** On a payment's page: the Lyrpay wallet it topped up, and whether Lyrpay has credited it. */
const PaymentDetail = (context: ExtensionContextValue) => {
  const payment = context.environment.objectContext?.id ?? '';
  const { data, error } = useLyrpay(() => lyrpay<Payment>('payment', context, { payment }), [payment]);
  return (
    <ContextView title="Lyrpay wallet" brandColor="#5b5bf6" brandIcon={BrandIcon}>
      {error && <Banner type="critical" title="Couldn't load Lyrpay" description={error} />}
      {!data && !error && <Box css={{ padding: 'large', alignX: 'center' }}><Spinner /></Box>}
      {data && !data.lyrpay && <Inline css={{ color: 'secondary' }}>This payment isn't a Lyrpay wallet top-up.</Inline>}
      {data && data.lyrpay && (
        <Box css={{ stack: 'y', gap: 'medium' }}>
          <Badge type={STATUS[data.status].type}>{STATUS[data.status].text}</Badge>
          <PropertyList>
            <PropertyListItem label="Name" value={data.user.name || '–'} />
            <PropertyListItem label="Email" value={data.user.email || '–'} />
            {data.user.phone && <PropertyListItem label="Mobile" value={data.user.phone} />}
            <PropertyListItem label="Country" value={data.user.country || '–'} />
            <PropertyListItem label="Member since" value={data.user.created ? new Date(data.user.created).toLocaleDateString() : '–'} />
            {data.coupon && <PropertyListItem label="Promo code" value={data.coupon} />}
          </PropertyList>
          <Divider />
          <Inline css={{ font: 'heading' }}>Balances</Inline>
          {Object.keys(data.balances).length === 0 && <Inline css={{ color: 'secondary' }}>Empty wallet.</Inline>}
          <PropertyList>
            {Object.entries(data.balances).map(([currency, value]) => (
              <PropertyListItem key={currency} label={currency} value={amount(value, currency)} />
            ))}
          </PropertyList>
          <Divider />
          <Inline css={{ font: 'heading' }}>Recent activity</Inline>
          {data.transactions.slice(0, 8).map(tx => (
            <Box key={tx.id} css={{ stack: 'x', distribute: 'space-between', gap: 'small' }}>
              <Box css={{ stack: 'y' }}>
                <Inline css={{ font: 'bodyEmphasized' }}>{KIND_LABELS[tx.kind] ?? tx.kind}</Inline>
                <Inline css={{ font: 'caption', color: 'secondary' }}>{new Date(tx.at).toLocaleString()}</Inline>
              </Box>
              <Inline>{amount(tx.amount, tx.currency)}</Inline>
            </Box>
          ))}
          <Inline css={{ font: 'caption', color: 'secondary' }}>Checkout session {data.session}</Inline>
        </Box>
      )}
    </ContextView>
  );
};

export default PaymentDetail;
