# Agent402 — Autonomous AI Agent Marketplace with Micropayments

> **AI meets Web3.** A marketplace where a Gemini-powered AI agent autonomously discovers, pays for, and calls external tools. Payment runs over x402 on-chain escrow (live in the shipped UI); a second, off-chain rail (Yellow Network / Nitrolite state channels) is implemented and verified end-to-end on the backend but not yet wired into the UI.
---

## What is Agent402?

Agent402 is an **AI tool marketplace with built-in micropayment infrastructure**. A Gemini 2.5 Flash agent reasons over a catalog of registered tools, decides which ones to call, pays for them autonomously using cryptocurrency, and returns the results — all without human intervention in the payment loop.

**Core idea:** HTTP 402 ("Payment Required") is a real status code that almost no one uses. Agent402 makes it meaningful — when a tool requires payment, the server responds with a 402 containing exact payment instructions. The agent's wallet pays automatically, and the server verifies the payment before executing the tool.

---

## System Architecture

```
User Query
    │
    ▼
React Frontend (Vite)
    │
    ├─── POST /gemini/chat ──────────────► Gemini 2.5 Flash AI
    │         ◄── functionCall response ──┘
    │
    ├─── POST /tools/{name}
    │         │
    │         ├── x402 path (on-chain)
    │         │     ├── Agent wallet sends USDC to Escrow Contract (Sepolia)
    │         │     ├── Backend reads tx receipt via web3.py
    │         │     └── Verifies Transfer event log → amount + recipient
    │         │
    │         └── Nitrolite path (off-chain)
    │               ├── Agent signs state channel update (ECDSA)
    │               ├── Yellow ClearNode validates off-chain
    │               └── Backend verifies proof via eth-account sig recovery
    │
    ▼
FastAPI Backend (Python)
    │
    ├── Tool type: proxy  ──► httpx POST to external API
    └── Tool type: code   ──► Node.js subprocess runs user_tools/*.js
            │
            └── On success: release_escrow() → USDC to tool provider
                On failure: refund_escrow()  → USDC back to user
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| AI | Google Gemini 2.5 Flash | Agent reasoning + tool selection |
| Backend | **FastAPI + Python 3.12** | REST API server |
| ASGI Server | Uvicorn | Async HTTP server |
| Database | MongoDB Atlas + **Motor** | Async document storage |
| Data Validation | **Pydantic v2** | Request/response schemas |
| Blockchain | **web3.py** | Read Ethereum tx receipts |
| Cryptography | **eth-account** | ECDSA signature recovery |
| HTTP Client | **httpx** | Async calls to proxy tools |
| Frontend | React 18 + Vite | User interface |
| Wallet | ethers.js v6 | Frontend blockchain interactions |
| State Channels | Yellow Network (Nitrolite / ERC-7824) | Off-chain micropayments |
| Token | USDC (6 decimals) on Sepolia | Payment currency |

---

## Payment Protocols

### 1. x402 — On-Chain Escrow

```
Agent Wallet ──USDC──► Escrow Contract ──on success──► Tool Provider Wallet
                                        ──on failure──► Agent Wallet (refund)
```

1. Backend returns HTTP 402 with escrow address and price
2. Agent wallet sends USDC to the escrow contract on Sepolia
3. Agent sends the transaction hash in `X-Payment-Tx` header
4. Backend reads the Ethereum receipt, verifies the Transfer event log
5. Tool executes → escrow releases USDC to provider (or refunds on failure)

**Contract:** `0x14b848bE61C159908C0F1127C53Aa70dD0F2cBed` (Sepolia)

### 2. Nitrolite — Off-Chain State Channels (ERC-7824)

```
Agent ──signed state update──► Yellow ClearNode ──proof──► Backend verifies ──► Tool executes
```

1. Agent opens a payment channel with the tool provider on Yellow Network
2. Per tool call, agent signs an off-chain state update allocating USDC to provider
3. Yellow ClearNode validates and returns a signed acknowledgement
4. Agent sends the encoded proof in `X-Nitrolite-Proof` header
5. Backend recovers the ECDSA signer, validates allocations, executes tool

**Benefit:** Zero gas cost, instant settlement, no on-chain transaction per tool call.

---

## Project Structure

```
Agent402-goodvibes/
├── MarketplaceBackend/          ← FastAPI Python backend
│   ├── main.py                  ← App entry, CORS, lifespan hooks
│   ├── config.py                ← Pydantic settings (reads .env)
│   ├── database.py              ← Motor async MongoDB client
│   ├── registry.py              ← In-memory tool registry (cache)
│   ├── requirements.txt         ← Python dependencies
│   │
│   ├── models/
│   │   └── tool.py              ← Pydantic request/response models
│   │
│   ├── routers/
│   │   ├── gemini.py            ← POST /gemini/chat
│   │   ├── tools.py             ← All /tools/* endpoints + payment gate
│   │   └── info.py              ← GET /health, /ping, /escrow-info
│   │
│   ├── services/
│   │   ├── nitrolite_verifier.py ← Off-chain proof verification (ECDSA)
│   │   ├── payment_verifier.py   ← On-chain tx receipt verification
│   │   └── escrow_service.py     ← Serial escrow release/refund queue
│   │
│   ├── tool_executor/
│   │   └── executor.py          ← Proxy (httpx) + code (Node subprocess)
│   │
│   └── user_tools/              ← JS tool files (run via Node subprocess)
│       ├── get_weather.js
│       ├── get_audio.js
│       └── adzuna_*.js
│
├── AgentPayFrontend/            ← React + Vite frontend
│   └── src/
│       ├── services/
│       │   ├── geminiService.js      ← Calls /gemini/chat
│       │   ├── nitroliteService.js   ← Manages Yellow Network channel
│       │   └── agentWallet.js        ← ethers.js wallet management
│       └── utils/
│           └── payment.js            ← x402 payment flow
│
├── AgentPayEscrow.sol           ← Solidity smart contract source
└── README.md
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Server status + contract addresses |
| `GET` | `/ping` | Liveness check |
| `GET` | `/escrow-info` | Escrow contract details |
| `GET` | `/tools` | List all approved marketplace tools |
| `POST` | `/tools/register` | Submit a new tool (starts as pending) |
| `POST` | `/tools/{name}/approve` | Approve a pending tool (admin) |
| `POST` | `/tools/{name}` | Call a tool (requires payment) |
| `POST` | `/gemini/chat` | AI agent chat with tool-use |

**Interactive docs:** `http://localhost:3000/docs` (Swagger UI, auto-generated)

---

## Local Setup

### Prerequisites
- Python 3.10+
- Node.js 18+ (required to run `user_tools/*.js` files)
- MongoDB Atlas account (or local MongoDB)

### 1. Clone

```bash
git clone https://github.com/Ankitanand2411/Agent402-goodvibes.git
cd Agent402-goodvibes
```

### 2. Backend Setup

```bash
cd MarketplaceBackend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables

Create `MarketplaceBackend/.env`:

```env
GEMINI_API_KEY=your_google_gemini_api_key
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
SEPOLIA_RPC=https://ethereum-sepolia.publicnode.com
ESCROW_CONTRACT_ADDRESS=0x14b848bE61C159908C0F1127C53Aa70dD0F2cBed
ESCROW_PRIVATE_KEY=your_escrow_admin_private_key

# Security / operations (see "Hardening" below)
ADMIN_API_KEY=long-random-string        # required for POST /tools/{name}/approve; endpoint returns 503 if unset
REQUIRE_PROVIDER_SIGNATURE=false        # true = /tools/register needs an EIP-191 signature from the payout wallet
SETTLEMENT_MODE=async                   # async = respond first, settle escrow in background; sync = original behaviour
DAILY_SPEND_CAP_UNITS=0                 # per-wallet daily cap in USDC atomic units (0 = off); over-cap payments are refunded
TOOL_RETRIEVAL_TOP_K=8                  # declare only the k most relevant tools per turn (0 = declare the whole catalog)
TOOL_EMBED_MODEL=gemini-embedding-001   # embedding model for tool descriptions and requests
TOOL_EMBED_DIMENSIONS=768
GROQ_API_KEY=your_groq_api_key
ADZUNA_APP_ID=your_adzuna_id
ADZUNA_APP_KEY=your_adzuna_key
```

Create `AgentPayFrontend/.env`:

```env
VITE_AGENT_PRIVATE_KEY=your_agent_wallet_private_key
VITE_MARKETPLACE_URL=http://localhost:3000
```

### 4. Run

**Terminal 1 — Backend:**
```bash
cd MarketplaceBackend
venv/bin/uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

**Terminal 2 — Frontend:**
```bash
cd AgentPayFrontend
npm install && npm run dev
```

Backend runs at `http://localhost:3000`  
Frontend runs at `http://localhost:5173`  
Swagger UI at `http://localhost:3000/docs`

---

## Registering a Tool

### Proxy Tool (delegates to your server)

```bash
curl -X POST http://localhost:3000/tools/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_api_tool",
    "description": "Fetches data from my API. COSTS: 0.01 USDC",
    "price": "0.01",
    "type": "proxy",
    "targetUrl": "https://your-api.com/endpoint",
    "walletAddress": "0xYourWalletAddress",
    "parameters": {
      "type": "object",
      "properties": {
        "query": { "type": "string", "description": "Search query" }
      },
      "required": ["query"]
    }
  }'
```

### Code Tool (JavaScript executed on the server)

```bash
curl -X POST http://localhost:3000/tools/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_code_tool",
    "description": "Runs custom logic. COSTS: 0.005 USDC",
    "price": "0.005",
    "type": "code",
    "code": "export default async function({ input }) { return { result: input.toUpperCase() }; }",
    "walletAddress": "0xYourWalletAddress"
  }'
```

Tools start as `pending`. Approve via:
```bash
curl -X POST http://localhost:3000/tools/my_tool_name/approve
```

---

## Smart Contracts

| Contract | Network | Address |
|---|---|---|
| USDC (Payment Token) | Ethereum Sepolia | `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238` |
| Escrow Contract | Ethereum Sepolia | `0x14b848bE61C159908C0F1127C53Aa70dD0F2cBed` |
| Yellow ClearNode | Yellow Network | `wss://clearnet.yellow.com/ws` |

Escrow contract source: [`AgentPayEscrow.sol`](./AgentPayEscrow.sol)  
Sepolia explorer: [View contract](https://sepolia.etherscan.io/address/0x14b848bE61C159908C0F1127C53Aa70dD0F2cBed)

---

## License

MIT

---

## Tests

```bash
cd MarketplaceBackend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ruff check .
pytest -q
```

No credentials or network are needed: MongoDB, the Sepolia RPC, Gemini and escrow settlement are replaced with in-process fakes.

| Area | What is verified |
|---|---|
| `services/payment_verifier` | Accepts a USDC `Transfer` to the escrow for ≥ price; rejects wrong recipient, wrong token contract, underpayment and reverted transactions; ignores non-Transfer logs; polls for the receipt and gives up after N attempts |
| `services/nitrolite_verifier` | Real secp256k1 signatures over the exact JS `JSON.stringify` payload (including non-ASCII); rejects wrong signer, tampered allocations, insufficient/wrong-asset allocations, tool/provider mismatch, ClearNode error responses, mismatched create-session proofs |
| `services/pricing` | Exact decimal → atomic-unit conversion (`0.0157` → `15700`, where float math gave `15699`); rejects unrepresentable prices |
| `routers/tools` | 402 challenge contents; verify → execute → release; failed tool → 502 + refund; verification failures never execute the tool; Nitrolite 403; register / approve / list behaviour with a fake collection |
| `routers/gemini` | Tool name and parameter-schema sanitisation before anything reaches Gemini |
| `services/payments_ledger` | Claim-then-replay raises; receipt lifecycle processing → delivered → settled |
| `services/spend_caps` | Accumulation up to the cap, single over-cap rejection, refund releases budget, per-wallet isolation, ten concurrent reservations against one cap admit exactly the affordable number |
| `services/auth` | Admin key via header or bearer, prefix/wrong key rejected, fail-closed when unset; EIP-191 signature accepted only from the payout wallet and only for the named tool |
| async settlement | Response returns while the on-chain call is still blocked; receipt shows `pending` → `released`/`refunded`/`release-failed` after the task completes; shutdown drain |
| `services/tool_retrieval` | Top-k by similarity with a bag-of-words fake embedder, must-include for tools already used, fail-open on disabled/small catalog/embedding failure/no key/blank query, embed-once caching, re-embed only changed descriptions, persistence and reload across index instances, foreign-model vectors ignored, query-text extraction rules |
| `/gemini/chat` | Fake SDK client: retrieval narrows declarations, tool-result turns keep the tool in use, retrieval disabled declares all, usage reported |

CI runs the same two commands on every push/PR touching `MarketplaceBackend/` (`.github/workflows/backend-ci.yml`).

## Hardening

| Concern | Mechanism | Response |
|---|---|---|
| Replay of a payment proof | Every verified payment is claimed in `payment_receipts` (`_id` = `x402:<tx>` or `nitrolite:<session>:<version>`) **before** the tool runs; the unique index makes a second claim fail | `409` with `paymentKey` |
| Unauthenticated approval (would let anyone make code executable on the server) | `POST /tools/{name}/approve` requires `Authorization: Bearer $ADMIN_API_KEY` (constant-time compare); fails closed | `401` / `503` if unset |
| Provider identity | `POST /tools/register` accepts `X-Provider-Signature`, an EIP-191 signature of `Agent402 tool registration\ntool: <name>\nwallet: <addr>` by `walletAddress`; verified when present, required if `REQUIRE_PROVIDER_SIGNATURE=true`; stored as `providerVerified` | `401` on mismatch |
| Settlement latency (~12 s on-chain wait inside the request) | `SETTLEMENT_MODE=async`: the response returns with `escrowRelease: {status: "pending", receiptId, poll}`; a tracked background task performs the release/refund and updates the ledger; shutdown drains in-flight tasks | `GET /receipts/{paymentKey}` |
| Runaway agent / leaked worker key | `DAILY_SPEND_CAP_UNITS`: atomic `find_one_and_update` reservation per (wallet, UTC day); over-cap payments are refunded, never executed; refunds return budget | `429` with refund receipt |
| Untrusted header data | Only the tx hash is read from `X-Payment`; payer and amount come from the on-chain receipt | — |

## Tool retrieval

Every function declaration sent to Gemini costs prompt tokens on every turn, and providers cap the number of declared tools, so declaring the whole catalog stops scaling past a few dozen tools. `services/tool_retrieval.py` applies retrieval (the same idea as RAG for documents) to the catalog:

1. Each tool's name, description and parameter descriptions are embedded once (`RETRIEVAL_DOCUMENT` task type) and cached in memory and in the `tool_embeddings` collection, keyed by a hash of that text, so a tool is re-embedded only when its description changes.
2. The request text (current message, or the last user message when the turn is a tool result) is embedded as a `RETRIEVAL_QUERY`.
3. The top-`TOOL_RETRIEVAL_TOP_K` tools by cosine similarity are declared, plus any tool already called in the conversation, so chained plans are never cut off.
4. Any failure (no key, embedding error, blank query) fails open and declares everything, as before.

The chat response now includes `toolsDeclared`, `toolsAvailable` and `usage` (prompt / candidates / total tokens), and the server logs the same, which gives the before/after token number for the catalog size you run.

## Known limitations (next up)

- An approved `code` tool runs on the server with the full environment (the "sandbox" is a CJS shim, not isolation). Fix: run untrusted tools in a separate container or a WASM/isolate runtime with no env access.
- Registry cache, in-memory escrow queue and settlement tasks assume a single instance. Fix: move the queue to a durable job store and reconcile `settlement.status == "pending"` receipts on startup.
