import logging
from dataclasses import dataclass
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, Literal

import requests

from rotkehlchen.chain.gnosis.modules.gnosis_pay.constants import CPT_GNOSIS_PAY
from rotkehlchen.db.cache import DBCacheStatic
from rotkehlchen.db.filtering import EvmEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.db.settings import CachedSettings
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.serialization.deserialize import deserialize_fval
from rotkehlchen.types import EVMTxHash, Location, Timestamp, deserialize_evm_tx_hash
from rotkehlchen.utils.misc import (
    iso8601ts_to_timestamp,
    set_user_agent,
    timestamp_to_iso8601,
    ts_now,
)
from rotkehlchen.utils.serialization import jsonloads_list

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


@dataclass(init=True, repr=True, eq=True, order=False, unsafe_hash=False, frozen=False)
class GnosisPayTransaction:
    """The merchant data we keep for a gnosis pay transaction"""
    tx_hash: EVMTxHash
    timestamp: Timestamp
    merchant_name: str
    merchant_city: str | None  # sometimes missing (due to being a phone)
    country: str  # the alpha2 country code
    mcc: int
    transaction_symbol: str
    transaction_amount: FVal
    billing_symbol: str | None  # only if different to the transaction one
    billing_amount: FVal | None  # only if different to the transaction one
    reversal_symbol: str | None  # only if there is a refund
    reversal_amount: FVal | None  # only if there is a refund
    reversal_tx_hash: EVMTxHash | None  # only if there is a refund


class GnosisPay:
    """This is the gnosis pay API interface

    https://api.gnosispay.com/api-docs/

    For now they have no api keys but you can get the __Secure-authjs.session-token
    cookie from your local storage once logged in and put it to rotki. Then all data
    is queriable.

    DMed by gnosis pay devs
    export enum PaymentStatus {
      Approved = "Approved",
      IncorrectPin = "IncorrectPin",
      InsufficientFunds = "InsufficientFunds",
      InvalidAmount = "InvalidAmount",
      PinEntryTriesExceeded = "PinEntryTriesExceeded",
      IncorrectSecurityCode = "IncorrectSecurityCode",
      Reversal = "Reversal",
      PartialReversal = "PartialReversal",
      Other = "Other",
    }

    mcc is the merchant code category and details can be seen here:
    https://usa.visa.com/content/dam/VCOM/download/merchants/visa-merchant-data-standards-manual.pdf
    """

    def __init__(self, database: 'DBHandler', session_token: str) -> None:
        self.database = database
        self.session = requests.session()
        self.session_token = session_token
        set_user_agent(self.session)

    def _query(
            self,
            endpoint: Literal['transactions'],
            params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query a gnosis pay API endpoint with the hacky session token authentication

        May raise:
        - RemoteError if there is a problem querying the API
        """
        querystr = 'https://app.gnosispay.com/api/v1/' + endpoint
        log.debug(f'Querying Gnosis Pay API {querystr} with {params=}')
        timeout = CachedSettings().get_timeout_tuple()
        try:
            response = self.session.get(
                url=querystr,
                params=params,
                timeout=timeout,
                cookies={'__Secure-authjs.session-token': self.session_token},
            )
        except requests.exceptions.RequestException as e:
            raise RemoteError(f'Querying {querystr} failed due to {e!s}') from e

        if response.status_code != 200:
            raise RemoteError(
                f'Gnosis Pay API request {response.url} failed '
                f'with HTTP status code {response.status_code} and text '
                f'{response.text}',
            )

        try:
            json_ret = jsonloads_list(response.text)
        except JSONDecodeError as e:
            raise RemoteError(
                f'Gnosis Pay API returned invalid JSON response: {response.text}',
            ) from e

        return json_ret

    def maybe_deserialize_transaction(self, data: dict[str, Any]) -> GnosisPayTransaction | None:
        try:
            if (kind := data['kind']) == 'Payment' and data['status'] in ('Approved', 'Reversal'):
                return None  # only use Approved/Reversal for payments

            if (city := data['merchant']['city'].rstrip()).startswith('+') or city.isdigit():
                city = None
            tx_currency_symbol = data['transactionCurrency']['symbol']
            tx_currency_amount = deserialize_fval(value=data['transactionAmount'], name='currency_amount', location='gnosis pay data') / FVal(10 ** data['transactionCurrency']['decimals'])  # noqa: E501
            if (billing_currency_symbol := data['billingCurrency']['symbol']) != tx_currency_symbol:  # noqa: E501
                billing_currency_amount = deserialize_fval(value=data['billingAmount'], name='billing_amount', location='gnosis pay data') / FVal(10 ** data['billingCurrency']['decimals'])  # noqa: E501
            else:
                billing_currency_symbol, billing_currency_amount = None, None

            reversal_currency_symbol, reversal_amount, reversal_tx_hash = None, None, None
            if kind == 'Reversal':
                reversal_currency_symbol = data['reversalCurrency']['symbol']
                reversal_amount = deserialize_fval(value=data['reversalAmount'], name='reversal_amount', location='gnosis pay data') / FVal(10 ** data['reversalCurrency']['decimals'])  # noqa: E501
                # assumption. There is two transactions in the list and second one is the refund.
                if len(data['transactions']) >= 2:
                    reversal_tx_hash = deserialize_evm_tx_hash(data['transactions'][1]['hash'])

            return GnosisPayTransaction(
                tx_hash=deserialize_evm_tx_hash(data['transactions'][0]['hash']),
                timestamp=iso8601ts_to_timestamp(data['createdAt']),
                merchant_name=data['merchant']['name'].rstrip(),
                merchant_city=city,
                country=data['merchant']['country']['alpha2'],
                mcc=int(data['mcc']),
                transaction_symbol=tx_currency_symbol,
                transaction_amount=tx_currency_amount,
                billing_symbol=billing_currency_symbol,
                billing_amount=billing_currency_amount,
                reversal_symbol=reversal_currency_symbol,
                reversal_amount=reversal_amount,
                reversal_tx_hash=reversal_tx_hash,
            )

        except KeyError as e:
            log.error(f'Could not find key {e!s} in Gnosis pay transaction response: {data}')
        except DeserializationError as e:
            log.error(f'Failed to read gnosis pay data {data} due to {e!s}')

        return None

    def write_txdata_to_db(self, transaction: GnosisPayTransaction) -> None:
        with self.database.user_write() as write_cursor:
            write_cursor.execute(
                'INSERT OR REPLACE INTO gnosispay_data(tx_hash, timestamp, merchant_name, '
                'merchant_city, country, mcc, transaction_symbol, transaction_amount, '
                'billing_symbol, billing_amount, reversal_symbol, reversal_amount) '
                'VALUES(?, ?, ?, ?, ? ,?, ?, ?, ?, ?, ?, ?)',
                (transaction.tx_hash, transaction.timestamp, transaction.merchant_name,
                 transaction.merchant_city, transaction.country, transaction.mcc,
                 transaction.transaction_symbol, str(transaction.transaction_amount),
                 transaction.billing_symbol,
                 str(transaction.billing_amount) if transaction.billing_amount else None,
                 transaction.reversal_symbol,
                 str(transaction.reversal_amount) if transaction.reversal_amount else None),
            )

    def get_data_for_transaction(
            self,
            tx_hash: EVMTxHash,
            tx_timestamp: Timestamp,
    ) -> str | None:
        """Gets the Gnosis pay data for the given transaction and returns its notes if found.

        Either from the DB or by querying the API
        """
        with self.database.conn.read_ctx() as cursor:
            cursor.execute(
                'SELECT tx_hash, timestamp, merchant_name, merchant_city, country, mcc, '
                'transaction_symbol, transaction_amount, billing_symbol, billing_amount '
                'reversal_symbol, reversal_amount, reversal_tx_hash '
                'FROM gnosispay_data WHERE tx_hash=? OR reversal_tx_hash=?',
                (tx_hash, tx_hash),
            )

            if (result := cursor.fetchone()) is not None:
                billing_symbol, billing_amount = None, None
                if result[8] is not None:
                    billing_symbol, billing_amount = result[8], FVal(result[9])

                reversal_symbol, reversal_amount = None, None
                if result[10] is not None:
                    reversal_symbol, reversal_amount = result[10], FVal(result[11])

                return self.create_notes_for_transaction(
                    transaction=GnosisPayTransaction(
                        tx_hash=(saved_tx_hash := deserialize_evm_tx_hash(result[0])),
                        timestamp=Timestamp(result[1]),
                        merchant_name=result[2],
                        merchant_city=result[3],
                        country=result[4],
                        mcc=result[5],
                        transaction_symbol=result[6],
                        transaction_amount=FVal(result[7]),
                        billing_symbol=billing_symbol,
                        billing_amount=billing_amount,
                        reversal_symbol=reversal_symbol,
                        reversal_amount=reversal_amount,
                        reversal_tx_hash=deserialize_evm_tx_hash(result[12]) if result[12] is not None else None,  # noqa: E501
                    ),
                    is_refund=tx_hash != saved_tx_hash,
                )

        # else we need to query the API
        try:
            data = self._query(
                endpoint='transactions',
                params={
                    'after': timestamp_to_iso8601(Timestamp(tx_timestamp - 1)),
                    'before': timestamp_to_iso8601(Timestamp(tx_timestamp + 1)),
                },
            )
        except RemoteError as e:
            log.error(f'Could not query Gnosis Pay API due to {e!s}')
            return None

        # since this probably contains more transactions than the one we need dont
        # let the query go to waste and update data for all and return only the one we need
        result_tx, is_refund = None, False
        for entry in data:
            if (transaction := self.maybe_deserialize_transaction(entry)) is None:
                continue

            if tx_hash == transaction.tx_hash:
                result_tx = transaction
            elif tx_hash == transaction.reversal_tx_hash:
                result_tx = transaction
                is_refund = True
            else:
                self.maybe_update_event_with_api_data(transaction)

        return self.create_notes_for_transaction(result_tx, is_refund) if result_tx else None

    def query_remote_for_tx_and_update_events(self, tx_timestamp: Timestamp) -> None:
        """Query the API for a single transaction and update the events if found"""
        try:
            data = self._query(
                endpoint='transactions',
                params={
                    'after': timestamp_to_iso8601(Timestamp(tx_timestamp - 10)),
                    'before': timestamp_to_iso8601(Timestamp(tx_timestamp + 10)),
                },
            )
        except RemoteError as e:
            log.error(f'Could not query Gnosis Pay API due to {e!s}')
            return None

        # since this may contain more transactions than the one we need dont
        # let the query go to waste and update data for all
        for entry in data:
            if (transaction := self.maybe_deserialize_transaction(entry)) is None:
                continue

            self.write_txdata_to_db(transaction)
            self.maybe_update_event_with_api_data(transaction)

    def create_notes_for_transaction(
            self,
            transaction: GnosisPayTransaction,
            is_refund: bool,
    ) -> str:
        """Create the modified notes for the gnosis pay transaction"""
        verb, preposition = 'Pay', 'to'
        if transaction.mcc == 6011:  # ATM cash withdrawal
            verb, preposition = 'Withdraw', 'from'

        if is_refund:
            preposition = 'from'
            notes = f'Receive refund of {transaction.reversal_amount} {transaction.reversal_symbol} '  # noqa: E501
        else:
            notes = f'{verb} {transaction.transaction_amount} {transaction.transaction_symbol}'
            if transaction.billing_symbol:
                notes += f' ({transaction.billing_amount} {transaction.billing_symbol})'

        notes += f' {preposition} {transaction.merchant_name}'
        if transaction.merchant_city:
            notes += f' in {transaction.merchant_city}'

        notes += f' :country:{transaction.country}:'
        return notes

    def maybe_update_event_with_api_data(self, transaction: GnosisPayTransaction) -> None:
        """Try to find the history event for the given Gnosis Pay merchant data and update it"""
        dbevents = DBHistoryEvents(self.database)
        with self.database.conn.read_ctx() as cursor:
            events = dbevents.get_history_events(
                cursor=cursor,
                filter_query=EvmEventFilterQuery.make(
                    tx_hashes=[transaction.tx_hash],
                    counterparties=[CPT_GNOSIS_PAY],
                    location=Location.GNOSIS,
                ),
                has_premium=True,
            )

        if len(events) != 1:
            log.error(f'Could not find gnosis pay event corresponding to {transaction.tx_hash.hex()} in the DB. Skipping.')  # pylint: disable=no-member # noqa: E501
            return

        notes = self.create_notes_for_transaction(transaction, is_refund=False)
        log.debug(f'Updating notes for gnosis pay event with tx_hash={transaction.tx_hash.hex()}')  # pylint: disable=no-member
        with self.database.user_write() as write_cursor:
            write_cursor.execute(
                'UPDATE history_events SET notes=? WHERE identifier=?',
                (notes, events[0].identifier),
            )

        if not transaction.reversal_tx_hash:
            return

        # else try to find and update the refund
        with self.database.conn.read_ctx() as cursor:
            events = dbevents.get_history_events(
                cursor=cursor,
                filter_query=EvmEventFilterQuery.make(
                    tx_hashes=[transaction.reversal_tx_hash],  # do not query CPT here as the only way to detect refund is via API  # noqa: E501
                    location=Location.GNOSIS,
                ),
                has_premium=True,
            )

        if len(events) != 1:
            log.error(f'Could not find gnosis pay refund event corresponding to {transaction.reversal_tx_hash.hex()} in the DB. Skipping.')  # pylint: disable=no-member # noqa: E501
            return

        notes = self.create_notes_for_transaction(transaction, is_refund=True)
        with self.database.user_write() as write_cursor:
            write_cursor.execute(
                'UPDATE history_events SET notes=?, type=?, subtype=? WHERE identifier=?',
                (notes, HistoryEventType.RECEIVE.serialize(), HistoryEventSubType.REFUND, events[0].identifier),  # noqa: E501
            )
            write_cursor.execute(
                'UPDATE evm_events_info SET counterparty=? WHERE identifier=?',
                (CPT_GNOSIS_PAY, events[0].identifier),
            )

    def get_and_process_transactions(
            self,
            after_ts: Timestamp,
    ) -> None:
        """Query for gnosis pay transactions and merchant data after a given timestamp.

        Then search for our events and if there is a matching event overlay the
        merchant data on top.
        """
        log.debug('Starting task to query for gnosis pay merchant transaction data')
        with self.database.conn.write_ctx() as write_cursor:
            write_cursor.execute(  # remember last time task ran
                'INSERT OR REPLACE INTO key_value_cache (name, value) VALUES (?, ?)',
                (DBCacheStatic.LAST_GNOSISPAY_QUERY_TS.value, str(ts_now())),
            )
        data = self._query(
            endpoint='transactions',
            params={'after': timestamp_to_iso8601(after_ts)},  # after is exclusive
        )
        for entry in data:
            if (transaction := self.maybe_deserialize_transaction(entry)) is None:
                continue

            self.write_txdata_to_db(transaction)
            self.maybe_update_event_with_api_data(transaction)


def init_gnosis_pay(database: 'DBHandler') -> GnosisPay | None:
    """Create a gnosis pay instance using the provided database"""
    with database.conn.read_ctx() as cursor:
        result = cursor.execute(
            'SELECT api_key FROM external_service_credentials WHERE name=?',
            ('gnosis_pay',),
        ).fetchone()
        if result is None:
            return None

    return GnosisPay(database=database, session_token=result[0])
