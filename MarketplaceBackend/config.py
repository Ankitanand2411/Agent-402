from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    GEMINI_API_KEY: str = ""
    MONGODB_URI: str = ""
    SEPOLIA_RPC: str = "https://ethereum-sepolia.publicnode.com"
    ESCROW_CONTRACT_ADDRESS: str = ""
    ESCROW_PRIVATE_KEY: str = ""
    DEFAULT_EVM_WALLET: str = ""
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""
    GROQ_API_KEY: str = ""
    PORT: int = 3000

    # EVM constants
    TOKEN_CONTRACT_ADDR: str = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"  # USDC on Sepolia
    TOKEN_DECIMALS: int = 6
    SEPOLIA_CHAIN_ID: int = 11155111

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
