
import React, { useState, useEffect } from "react";
import { ethers } from "ethers";
import { BNB_CHAIN } from "../config/bnb";
import Button from "./Button";
import "./WalletManager.css";

const WalletManager = () => {
    const [account, setAccount] = useState(null);
    const [balance, setBalance] = useState("0");
    const [chainId, setChainId] = useState(null);

    // Force-update MetaMask's cached chain config with our correct RPC URL
    const ensureCorrectChainConfig = async () => {
        try {
            await window.ethereum.request({
                method: 'wallet_addEthereumChain',
                params: [{
                    chainId: "0x" + BNB_CHAIN.id.toString(16),
                    chainName: BNB_CHAIN.name,
                    rpcUrls: BNB_CHAIN.rpcUrls,
                    nativeCurrency: BNB_CHAIN.nativeCurrency,
                    blockExplorerUrls: BNB_CHAIN.blockExplorerUrls
                }],
            });
        } catch (e) {
            // Some wallets reject if chain already exists — that's fine
            console.log("Chain config update:", e.message || "already exists");
        }
    };

    // Silent check: populate state from already-authorized accounts (no popup)
    const silentCheck = async () => {
        if (!window.ethereum) return;
        try {
            const accounts = await window.ethereum.request({ method: 'eth_accounts' });
            if (accounts && accounts.length > 0) {
                const addr = accounts[0];
                setAccount(addr);

                // Force-update MetaMask's cached RPC URL before any RPC calls
                await ensureCorrectChainConfig();

                const provider = new ethers.BrowserProvider(window.ethereum);
                const net = await provider.getNetwork();
                setChainId(net.chainId);

                if (net.chainId === BigInt(BNB_CHAIN.id)) {
                    try {
                        const bal = await provider.getBalance(addr);
                        setBalance(ethers.formatEther(bal));
                    } catch (balErr) {
                        console.warn("Could not fetch balance:", balErr.message);
                        setBalance("0");
                    }
                }
            }
        } catch (e) {
            console.warn("Silent wallet check failed:", e.message);
        }
    };

    // Active connect: triggers Metamask popup (only called by user click)
    const connectWallet = async () => {
        if (!window.ethereum) {
            console.error("Metamask not found");
            return;
        }

        if (window.__walletConnecting) {
            console.log("Wallet connection already in progress... skipping.");
            return;
        }

        window.__walletConnecting = true;
        try {
            const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
            if (!accounts || accounts.length === 0) return;

            const addr = accounts[0];
            setAccount(addr);

            // Force-update MetaMask's cached RPC URL BEFORE any RPC calls
            await ensureCorrectChainConfig();

            const provider = new ethers.BrowserProvider(window.ethereum);
            const net = await provider.getNetwork();
            setChainId(net.chainId);

            // Switch chain if needed
            if (net.chainId !== BigInt(BNB_CHAIN.id)) {
                try {
                    await window.ethereum.request({
                        method: 'wallet_switchEthereumChain',
                        params: [{ chainId: "0x" + BNB_CHAIN.id.toString(16) }],
                    });
                    return; // chainChanged listener will reload the page
                } catch (switchError) {
                    console.error("Chain switch failed:", switchError);
                    return;
                }
            }

            // On correct chain, fetch balance (with error handling)
            try {
                const bal = await provider.getBalance(addr);
                setBalance(ethers.formatEther(bal));
            } catch (balErr) {
                console.warn("Could not fetch balance:", balErr.message);
                setBalance("0");
            }

        } catch (e) {
            console.error("Wallet connection failed", e);
        } finally {
            window.__walletConnecting = false;
        }
    };

    useEffect(() => {
        if (!window.ethereum) return;

        const handleAccountsChanged = (accounts) => {
            if (accounts.length > 0) silentCheck();
            else setAccount(null);
        };

        const handleChainChanged = () => window.location.reload();

        window.ethereum.on('accountsChanged', handleAccountsChanged);
        window.ethereum.on('chainChanged', handleChainChanged);

        // On mount: only do a SILENT check (no popup)
        silentCheck();

        return () => {
            window.ethereum.removeListener('accountsChanged', handleAccountsChanged);
            window.ethereum.removeListener('chainChanged', handleChainChanged);
        };
    }, []);

    if (!account) return (
        <div className="wallet-manager">
            <Button onClick={connectWallet}>Connect Metamask</Button>
        </div>
    );

    const isWrongNetwork = chainId && BigInt(chainId) !== BigInt(BNB_CHAIN.id);

    return (
        <div className="wallet-manager">
            <div className="wallet-info">
                <div className="wallet-label">SEPOLIA WALLET</div>
                <div className="wallet-balance">{parseFloat(balance).toFixed(4)} ETH</div>
                <div className="wallet-address" title={account} onClick={() => navigator.clipboard.writeText(account)}>
                    {account.slice(0, 6)}...{account.slice(-4)}
                </div>
            </div>
            {isWrongNetwork && (
                <div style={{ color: '#ff4444', fontSize: '0.8em', marginTop: '5px' }}>
                    Wrong Network (Expected {BNB_CHAIN.name})
                    <button onClick={connectWallet} style={{ marginLeft: '5px', fontSize: '0.9em', background: 'transparent', border: '1px solid currentColor', color: 'inherit' }}>Switch</button>
                </div>
            )}
        </div>
    );
};

export default WalletManager;
