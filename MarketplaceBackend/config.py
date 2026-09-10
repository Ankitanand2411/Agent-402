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

    # --- Security ---
    # Bearer token required on admin endpoints (/tools/{name}/approve). Approval
    # makes provider-submitted code executable on this server, so this endpoint
    # fails CLOSED (503) when the key is not configured.
    ADMIN_API_KEY: str = ""
    # When true, /tools/register requires an EIP-191 signature from the payout
    # wallet proving the registrant controls it. Off by default until the
    # frontend registration form signs; the backend verifies a signature whenever
    # one is supplied regardless of this flag.
    REQUIRE_PROVIDER_SIGNATURE: bool = False

    # --- Settlement ---
    # "async": respond as soon as the tool result is ready and settle escrow from
    #          a background task (receipt shows settlement "pending"; poll
    #          /receipts/{id}). "sync": await the on-chain release/refund inside
    #          the request, as the original implementation did (adds ~12 s).
    SETTLEMENT_MODE: str = "async"

    # --- Guardrails ---
    # Maximum a single payer wallet may spend per UTC day, in USDC atomic units.
    # 0 disables the cap. Payments above the cap are refunded, not executed.
    DAILY_SPEND_CAP_UNITS: int = 0

    # EVM constants
    TOKEN_CONTRACT_ADDR: str = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"  # USDC on Sepolia
    TOKEN_DECIMALS: int = 6
    SEPOLIA_CHAIN_ID: int = 11155111

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
