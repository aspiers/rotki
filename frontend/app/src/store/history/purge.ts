import { type SupportedExchange } from '@/types/exchanges';
import { Section } from '@/types/status';
import { ALL_CENTRALIZED_EXCHANGES } from '@/types/session/purge';

export const usePurgeStore = defineStore('history/purge', () => {
  const { fetchTrades } = useTradeStore();
  const { fetchAssetMovements } = useAssetMovements();
  const { fetchLedgerActions } = useLedgerActionStore();
  const { fetchTransactions } = useTransactionStore();

  const purgeHistoryLocation = async (
    exchange: SupportedExchange
  ): Promise<void> => {
    await Promise.allSettled([
      fetchTrades(true, exchange),
      fetchAssetMovements(true, exchange),
      fetchLedgerActions(true, exchange)
    ]);
  };

  const purgeExchange = async (
    exchange: SupportedExchange | typeof ALL_CENTRALIZED_EXCHANGES
  ): Promise<void> => {
    const { resetStatus } = useStatusUpdater(Section.TRADES);

    if (exchange === ALL_CENTRALIZED_EXCHANGES) {
      resetStatus();
      resetStatus(Section.ASSET_MOVEMENT);
      resetStatus(Section.LEDGER_ACTIONS);
    } else {
      await purgeHistoryLocation(exchange);
    }
  };

  const purgeTransactions = async (): Promise<void> => {
    const { resetStatus } = useStatusUpdater(Section.TX);
    resetStatus();
    await fetchTransactions();
  };

  return {
    purgeExchange,
    purgeHistoryLocation,
    purgeTransactions
  };
});

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(usePurgeStore, import.meta.hot));
}
