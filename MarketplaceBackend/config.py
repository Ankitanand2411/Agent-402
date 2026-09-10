from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    GEMINI_API_KEY: str = ""
    MONGODB_URI: str = ""
    # The Atlas URI has no database path; the original code fell back to "test",
    # so that stays the default to keep pointing at the existing data.
    MONGODB_DB_NAME: str = "test"
    SEPOLIA_RPC: str = "https://ethereum-sepolia.publicnode.com"
    ESCROW_CONTRACT_ADDRESS: str = ""
    ESCROW_PRIVATE_KEY: str = ""
    DEFAULT_EVM_WALLET: str = ""
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""
    GROQ_API_KEY: str = ""
    PORT: int = 3000

    # --- CORS ---
    # Comma-separated browser origins allowed to call the API. The Vercel entries
    # are historical deployments; prune the ones no longer live.
    ALLOWED_ORIGINS: str = (
        "http://localhost:5174,http://localhost:5173,"
        "https://agent402-skale.vercel.app,https://agent402-goodvibes.vercel.app"
    )

    # --- Tool execution isolation ---
    # Environment variables a code tool's subprocess may read. Everything else
    # (ESCROW_PRIVATE_KEY, MONGODB_URI, GEMINI_API_KEY, ADMIN_API_KEY, ...) is
    # withheld. Add a tool's own secrets here explicitly.
    TOOL_ENV_ALLOWLIST: str = "GROQ_API_KEY,ADZUNA_APP_ID,ADZUNA_APP_KEY"
    TOOL_TIMEOUT_SECONDS: float = 30.0
    TOOL_MAX_MEMORY_MB: int = 512
    TOOL_MAX_CPU_SECONDS: int = 30
    # Proxy tools may only target public https hosts. Set true for local development.
    ALLOW_INSECURE_TOOL_URLS: bool = False
    PROXY_MAX_RESPONSE_BYTES: int = 1_000_000

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

    # --- Tool retrieval ---
    # Declare only the TOOL_RETRIEVAL_TOP_K tools most relevant to the request
    # (by embedding similarity) instead of the whole catalog. 0 disables
    # retrieval and declares everything, as before. Tools already used in the
    # conversation are always included so chained calls keep working.
    TOOL_RETRIEVAL_TOP_K: int = 8
    TOOL_EMBED_MODEL: str = "gemini-embedding-001"
    TOOL_EMBED_DIMENSIONS: int = 768

    # --- Server-side agent runs ---
    AGENT_MODEL: str = "gemini-2.5-flash"
    AGENT_MAX_ITERATIONS: int = 5
    AGENT_DEFAULT_MAX_SPEND_UNITS: int = 0        # 0 = no per-run budget unless the client sets one
    AGENT_CHECKPOINT_DB: str = "agent_checkpoints"  # MongoDB database for LangGraph checkpoints

    # --- MCP server ---
    # Exposes approved marketplace tools to any MCP client at /mcp (Streamable
    # HTTP). Tool calls are forwarded to this same service's /tools/{name}
    # endpoint so the x402 payment gate, ledger, spend cap and settlement all
    # apply unchanged.
    MCP_ENABLED: bool = True
    SELF_BASE_URL: str = ""           # defaults to http://127.0.0.1:{PORT}
    MCP_ALLOWED_HOSTS: str = ""       # comma-separated; when set, enables DNS-rebinding protection for these hosts

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
