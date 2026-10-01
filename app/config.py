from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_name: str = 'Лавка шамана'
    app_env: str = 'development'
    app_url: str = 'http://localhost:8000'
    database_url: str = 'sqlite:///./shaman.db'

    bot_token: str = ''
    bot_username: str = 'Lavkashamanbot'
    max_bot_token: str = ''
    max_bot_username: str = 'id026509411367_bot'
    max_channel_id: int = 0
    telegram_source_channel_id: int = 0
    admin_telegram_username: str = 'shamanchik007'
    dev_auth: bool = True

    payment_provider: str = 'mock'
    yookassa_shop_id: str = ''
    yookassa_secret_key: str = ''
    yookassa_return_url: str = 'http://localhost:8000/?payment=return'

    cdek_client_id: str = ''
    cdek_client_secret: str = ''
    cdek_api_base: str = 'https://api.cdek.ru'
    cdek_from_city: str = 'Октябрьский'
    cdek_from_region: str = 'Республика Башкортостан'
    cdek_from_country: str = 'RU'
    cdek_shipment_point: str = ''

    pochta_api_base: str = 'https://otpravka-api.pochta.ru'
    pochta_auth_token: str = ''
    pochta_user_auth_key: str = ''

    default_package_length_cm: int = 15
    default_package_width_cm: int = 20
    default_package_height_cm: int = 10
    default_product_weight_g: int = 200

settings = Settings()
