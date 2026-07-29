// Ethereum Sepolia Testnet Configuration
export const BNB_CHAIN = {
    id: 11155111,
    name: "Ethereum Sepolia Testnet",
    rpcUrls: ["https://ethereum-sepolia.publicnode.com"],
    blockExplorerUrls: ["https://sepolia.etherscan.io"],
    nativeCurrency: {
        name: "Sepolia ETH",
        symbol: "ETH",
        decimals: 18
    }
};

// USDC on Ethereum Sepolia Testnet (Circle official — 6 decimals)
export const TOKEN_ADDRESS = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238";
export const TOKEN_DECIMALS = 6;
export const TOKEN_SYMBOL = "USDC";
