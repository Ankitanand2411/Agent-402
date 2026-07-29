/**
 * BNB Channel Manager
 *
 * Manages a pool of "worker wallets" (channels) for parallel execution
 * on BNB Smart Chain Testnet. Each channel is a distinct EVM address
 * derived from a random private key.
 *
 * Features:
 * - Generates & persists channel keys in localStorage
 * - Funds channels from the Main Agent Wallet (ETH + USDC)
 * - Locks/Unlocks channels to prevent nonce collisions
 * - Exposes channel state via subscription for UI
 * - Auto-funds idle workers periodically
 *
 * Chain: BNB Smart Chain Testnet (Chain ID: 97)
 * Token: 0x84b9B910527Ad5C03A9Ca831909E21e236EA7b06 (18 decimals)
 */
import { ethers } from 'ethers';
import { BNB_CHAIN, TOKEN_ADDRESS, TOKEN_DECIMALS } from '../config/bnb';
import { getAgentWallet } from './agentWallet';

const STORAGE_KEY = 'agent402_bnb_channels';
const CHANNEL_COUNT = 4;
const MIN_BNB = 0.001;
const TARGET_BNB = 0.002;
const MIN_TOKEN_THRESHOLD = 3.0;
const TOPUP_TOKEN = 3.0;
const AUTO_FUND_INTERVAL = 30000;

const subscribers = new Set();
const notify = (channels) => subscribers.forEach(fn => fn([...channels]));

class BnbChannelManager {
    constructor() {
        this.channels = [];
        this.initialized = false;
        this.provider = new ethers.JsonRpcProvider(BNB_CHAIN.rpcUrls[0]);
        this.isFunding = false;
        this.fundingInterval = null;
    }

    init() {
        if (this.initialized) return;

        let stored = localStorage.getItem(STORAGE_KEY);
        let keys = stored ? JSON.parse(stored) : [];

        if (keys.length < CHANNEL_COUNT) {
            console.log(`[BnbChannelManager] Generating ${CHANNEL_COUNT - keys.length} new worker wallets...`);
            for (let i = keys.length; i < CHANNEL_COUNT; i++) {
                const wallet = ethers.Wallet.createRandom();
                keys.push(wallet.privateKey);
            }
            localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
        }

        this.channels = keys.map(pk => ({
            privateKey: pk,
            address: new ethers.Wallet(pk).address,
            isBusy: false,
            lastUsed: 0,
            balance: { bnb: '?', token: '?', sfuel: '?', usdc: '?' }
        }));

        this.initialized = true;
        console.log(`[BnbChannelManager] Initialized with ${this.channels.length} worker wallets.`);
        notify(this.channels);

        this.checkBalancesAndFund();
        this.fundingInterval = setInterval(() => this.checkBalancesAndFund(), AUTO_FUND_INTERVAL);
    }

    subscribe(fn) {
        if (!this.initialized) this.init();
        subscribers.add(fn);
        fn([...this.channels]);
        return () => subscribers.delete(fn);
    }

    async acquireChannel(requiredToken = 0) {
        if (!this.initialized) this.init();

        const freeChannels = this.channels.filter(c => !c.isBusy);
        if (freeChannels.length === 0) throw new Error('All worker wallets are busy. Please wait.');

        freeChannels.sort((a, b) => a.lastUsed - b.lastUsed);
        const channel = freeChannels[0];

        channel.isBusy = true;
        channel.lastUsed = Date.now();
        notify(this.channels);
        console.log(`[BnbChannelManager] Locked worker: ${channel.address.slice(0, 6)}...`);

        try {
            await this.ensureFunds(channel, requiredToken);
        } catch (e) {
            channel.isBusy = false;
            notify(this.channels);
            console.error(`[BnbChannelManager] Funding failed for ${channel.address}:`, e);
            throw new Error(`Worker wallet funding failed: ${e.message}`);
        }

        console.log(`[BnbChannelManager] Acquired worker: ${channel.address.slice(0, 6)}...`);
        return channel;
    }

    releaseChannel(address) {
        const channel = this.channels.find(c => c.address === address);
        if (channel) {
            channel.isBusy = false;
            console.log(`[BnbChannelManager] Released worker: ${address.slice(0, 6)}...`);
            notify(this.channels);
            this.updateChannelBalance(channel);
        }
    }

    async ensureFunds(channel, requiredToken) {
        const [bnbBal, tokenBal] = await Promise.all([
            this.provider.getBalance(channel.address),
            this.getTokenBalance(channel.address)
        ]);

        const bnb = parseFloat(ethers.formatEther(bnbBal));
        const token = parseFloat(ethers.formatUnits(tokenBal, TOKEN_DECIMALS));

        channel.balance = { bnb: bnb.toFixed(6), token: token.toFixed(6), sfuel: bnb.toFixed(6), usdc: token.toFixed(6) };
        notify(this.channels);

        const needsBNB = bnb < MIN_BNB;
        const strictRequirement = parseFloat(requiredToken) + 0.1;
        const triggerThreshold = Math.max(MIN_TOKEN_THRESHOLD, strictRequirement);
        const needsToken = token < triggerThreshold;

        let tokenToAdd = 0;
        if (needsToken) {
            tokenToAdd = TOPUP_TOKEN;
            if ((token + tokenToAdd) < strictRequirement) tokenToAdd = strictRequirement - token + 1.0;
        }

        if (needsBNB || needsToken) {
            console.log(`[BnbChannelManager] Funding ${channel.address.slice(0, 6)}... (Have: ${token} USDC, Need: ${triggerThreshold}. Adding: ${tokenToAdd})`);
            await this.fundWorker(channel.address, needsBNB ? TARGET_BNB : 0, needsToken ? tokenToAdd : 0);
            await this.updateChannelBalance(channel);

            if (needsToken) {
                const newToken = parseFloat(channel.balance.token);
                if (newToken < strictRequirement) {
                    await new Promise(r => setTimeout(r, 2000));
                    await this.updateChannelBalance(channel);
                    const finalToken = parseFloat(channel.balance.token);
                    if (finalToken < strictRequirement) {
                        throw new Error(`Funding appeared successful but balance is still low (${finalToken} < ${strictRequirement}).`);
                    }
                }
            }
        }
    }

    async getTokenBalance(address) {
        const token = new ethers.Contract(
            TOKEN_ADDRESS,
            ['function balanceOf(address) view returns (uint256)'],
            this.provider
        );
        return token.balanceOf(address);
    }

    async updateChannelBalance(channel) {
        try {
            const [bnbBal, tokenBal] = await Promise.all([
                this.provider.getBalance(channel.address),
                this.getTokenBalance(channel.address)
            ]);
            const bnb = parseFloat(ethers.formatEther(bnbBal)).toFixed(6);
            const token = parseFloat(ethers.formatUnits(tokenBal, TOKEN_DECIMALS)).toFixed(6);
            channel.balance = { bnb, token, sfuel: bnb, usdc: token };
            notify(this.channels);
        } catch (e) {
            console.warn(`Failed to update balance for ${channel.address}`, e);
        }
    }

    async fundWorker(workerAddress, bnbAmount, tokenAmount) {
        if (this.isFunding) {
            let attempts = 0;
            while (this.isFunding && attempts < 20) {
                await new Promise(r => setTimeout(r, 500));
                attempts++;
            }
            if (this.isFunding) throw new Error('Funding timeout: Main wallet is busy.');
        }
        this.isFunding = true;

        try {
            const mainWallet = getAgentWallet();
            const mainBNBBal = await this.provider.getBalance(mainWallet.address);
            const mainTokenBal = await this.getTokenBalance(mainWallet.address);
            const mainBNB = parseFloat(ethers.formatEther(mainBNBBal));
            const mainToken = parseFloat(ethers.formatUnits(mainTokenBal, TOKEN_DECIMALS));

            console.log(`[BnbChannelManager] Main Wallet: ${mainWallet.address} | ETH: ${mainBNB} | USDC: ${mainToken}`);

            if (bnbAmount > 0) {
                const gasBuffer = 0.0005;
                if (mainBNB < (bnbAmount + gasBuffer)) {
                    throw new Error(`Main Agent Wallet low on ETH (${mainBNB}). Needed: ${bnbAmount} + gas. Please fund it via Agent Wallet panel.`);
                }
            }
            if (tokenAmount > 0 && mainToken < tokenAmount) throw new Error(`Main Agent Wallet low on USDC (${mainToken}). Please fund it.`);

            if (bnbAmount > 0) {
                const tx = await mainWallet.sendTransaction({ to: workerAddress, value: ethers.parseEther(bnbAmount.toString()) });
                await tx.wait();
                console.log(`[BnbChannelManager] ETH funded: ${bnbAmount} to ${workerAddress}`);
            }

            if (tokenAmount > 0) {
                const tokenContract = new ethers.Contract(
                    TOKEN_ADDRESS,
                    ['function transfer(address to, uint256 amount) returns (bool)'],
                    mainWallet
                );
                const units = ethers.parseUnits(tokenAmount.toString(), TOKEN_DECIMALS);
                const tx = await tokenContract.transfer(workerAddress, units);
                await tx.wait();
                console.log(`[BnbChannelManager] USDC funded: ${tokenAmount} to ${workerAddress}`);
            }
        } catch (e) {
            console.error('[BnbChannelManager] Funding failed:', e);
            throw e;
        } finally {
            this.isFunding = false;
        }
    }

    async checkBalancesAndFund() {
        if (!this.initialized) return;
        for (const ch of this.channels) {
            if (!ch.isBusy && !this.isFunding) {
                try { await this.ensureFunds(ch, 0); } catch (e) { /* ignore in background */ }
            } else {
                await this.updateChannelBalance(ch);
            }
            await new Promise(r => setTimeout(r, 500));
        }
    }
}

export const bnbChannelManager = new BnbChannelManager();

// Legacy alias for backward compatibility
export const skaleChannelManager = bnbChannelManager;
