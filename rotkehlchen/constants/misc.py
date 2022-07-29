from rotkehlchen.assets.types import AssetType
from rotkehlchen.fval import FVal

CURRENCYCONVERTER_API_KEY = '7ad371210f296db27c19'

ZERO = FVal(0)
ONE = FVal(1)
EXP18 = FVal(1e18)

NFT_DIRECTIVE = '_nft_'

# API URLS
KRAKEN_BASE_URL = 'https://api.kraken.com'
KRAKEN_API_VERSION = '0'
# KRAKEN_BASE_URL = 'http://localhost:5001/kraken'
# KRAKEN_API_VERSION = 'mock'
# BINANCE_BASE_URL = 'http://localhost:5001/binance/api/'

ASSET_TYPES_EXCLUDED_FOR_USERS = {AssetType.NFT}

DEFAULT_MAX_LOG_SIZE_IN_MB = 300
DEFAULT_MAX_LOG_BACKUP_FILES = 3
DEFAULT_SQL_VM_INSTRUCTIONS_CB = 5000
