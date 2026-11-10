import React from 'react';
import { Badge, Banner, Box, ContextView, Divider, Inline, PropertyList, PropertyListItem, Spinner } from '@stripe/ui-extension-sdk/ui';
import type { ExtensionContextValue } from '@stripe/ui-extension-sdk/context';
import BrandIcon from './brand_icon.svg';
import { KIND_LABELS, amount, lyrpay, usd, useLyrpay } from '../lyrpay';

type Overview = {
  updated: string;
  totals: {
    users: number; newUsers: number; heldUsd: number; transactions: number; volumeUsd: number; pendingWithdrawals: number;
    giftCardsOutstandingUsd: number; businesses: number; businessApplications: number; cardsIssued: number; cardWaitlist: number;
  };
  recent: { id: string; at: string; kind: string; currency: string; amount: string; status: string; user: string; counterparty: string }[];
};

/** Lyrpay at a glance from anywhere in the Stripe Dashboard. */
const Overview = (context: ExtensionContextValue) => {
  const { data, error } = useLyrpay(() => lyrpay<Overview>('overview', context), []);
  const t = data?.totals;
  return (
    <ContextView
      title="Lyrpay"
      description="Wallets, balances and activity on Lyrpay"
      brandColor="#5b5bf6"
      brandIcon={BrandIcon}
      externalLink={{ label: 'Open Lyrpay admin', href: 'https://lyrpay.com/?tab=admin' }}
    >
      {error && <Banner type="critical" title="Couldn't load Lyrpay" description={error} />}
      {!data && !error && <Box css={{ padding: 'large', alignX: 'center' }}><Spinner /></Box>}
      {t && (
        <Box css={{ stack: 'y', gap: 'medium' }}>
          <PropertyList>
            <PropertyListItem label="Money held in wallets" value={usd(t.heldUsd)} />
            <PropertyListItem label="Wallets" value={`${t.users} (${t.newUsers} new in 30 days)`} />
            <PropertyListItem label="Volume, 30 days" value={usd(t.volumeUsd)} />
            <PropertyListItem label="Pending" value={t.pendingWithdrawals ? <Badge type="warning">{t.pendingWithdrawals} pending</Badge> : 'None'} />
            <PropertyListItem label="Gift cards outstanding" value={usd(t.giftCardsOutstandingUsd)} />
            <PropertyListItem label="Business accounts" value={`${t.businesses}${t.businessApplications ? ` · ${t.businessApplications} to review` : ''}`} />
            <PropertyListItem label="Cards" value={`${t.cardsIssued} issued · ${t.cardWaitlist} waiting`} />
          </PropertyList>
          <Divider />
          <Inline css={{ font: 'heading' }}>Latest activity</Inline>
          {data.recent.length === 0 && <Inline css={{ color: 'secondary' }}>Nothing yet.</Inline>}
          {data.recent.map(tx => (
            <Box key={tx.id} css={{ stack: 'x', distribute: 'space-between', alignY: 'center', gap: 'small' }}>
              <Box css={{ stack: 'y' }}>
                <Inline css={{ font: 'bodyEmphasized' }}>{KIND_LABELS[tx.kind] ?? tx.kind}{tx.user ? ` · ${tx.user}` : ''}</Inline>
                <Inline css={{ font: 'caption', color: 'secondary' }}>{new Date(tx.at).toLocaleString()}{tx.counterparty ? ` · ${tx.counterparty}` : ''}</Inline>
              </Box>
              <Box css={{ stack: 'y', alignX: 'end' }}>
                <Inline>{amount(tx.amount, tx.currency)}</Inline>
                {tx.status && tx.status !== 'done' && <Badge type={tx.status === 'pending' ? 'warning' : 'neutral'}>{tx.status}</Badge>}
              </Box>
            </Box>
          ))}
          <Inline css={{ font: 'caption', color: 'secondary' }}>Values in US dollars at today's rates · {new Date(data.updated).toLocaleTimeString()}</Inline>
        </Box>
      )}
    </ContextView>
  );
};

export default Overview;
