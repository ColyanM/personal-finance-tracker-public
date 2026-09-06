SANDBOX_PROVIDER = "plaid-sandbox-us"  #Retained only for identifying historical test rows
PRODUCTION_PROVIDER = "plaid-production-us"

PRODUCTION_CONFIG = {
    "name": "production",
    "label": "Plaid Production",
    "host": "production.plaid.com",
    "client_id_name": "plaid-production-client-id",
    "client_id_label": "Plaid Production Client ID",
    "secret_name": "plaid-production-secret",
    "secret_label": "Plaid Production Secret",
    "user_id_name": "plaid-production-user-id",
    "item_secret": "plaid-production-item",
    "item_prefix": "plaid-production-item-",
    "provider": PRODUCTION_PROVIDER,
}


def get_plaid_config():  #Uses the configured Production connection for every US refresh
    return PRODUCTION_CONFIG
