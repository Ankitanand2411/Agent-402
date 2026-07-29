/**
 * Skale Channel Manager
 * 
 * Manages a pool of "worker wallets" (channels) for parallel execution 
 * on SKALE. Each channel is a distinct EVM address derived from a random private key.
 * 
 * Features:
 * - Generates & persists channel keys
 * - Funds channels from the Main Agent Wallet (sFUEL + USDC)
 * - Locks/Unlocks channels to prevent nonce collisions
 * - Exposes channel state via subscription for UI
 * - Auto-funds idle workers periodically
 */
import { ethers } from 'ethers';
import { SKALE_CHAIN, USDC_ADDRESS } from '../config/skale';
import { getAgentWallet } from './agentWallet';

const STORAGE_KEY = 'agent402_skale_channels';
const CHANNEL_COUNT = 4; // Use 4 worker threads
const MIN_SFUEL = 0.000001; // Minimum sFUEL for gas
const TARGET_SFUEL = 0.00002; // Target sFUEL top-up
const MIN_USDC_THRESHOLD = 3.0; // Fund if below this amount (User request)
const TOPUP_USDC = 3.0; // Top-up amount for USDC (User request)
const AUTO_FUND_INTERVAL = 30000; // Check idle funding every 30s

// State subscribers
const subscribers = new Set();
const notify = (channels) => subscribers.forEach(fn => fn([...channels]));

class SkaleChannelManager {
    constructor() {
        this.channels = []; // { address, privateKey, isBusy, lastUsed, balance: { sfuel, usdc } }
        this.initialized = false;
        this.provider = new ethers.JsonRpcProvider(SKALE_CHAIN.rpcUrls[0]);
        this.isFunding = false;
        this.fundingInterval = null;
    }

    init() {
        if (this.initialized) return;

        let stored = localStorage.getItem(STORAGE_KEY);
        let keys = stored ? JSON.parse(stored) : [];

        // Generate missing channels
        if (keys.length < CHANNEL_COUNT) {
            console.log(`[SkaleChannelManager] Generating ${CHANNEL_COUNT - keys.length} new worker wallets...`);
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
            balance: { sfuel: '?', usdc: '?' }
        }));

        this.initialized = true;
        console.log(`[SkaleChannelManager] Initialized with ${this.channels.length} worker wallets.`);
        notify(this.channels);

        // Start background balance check & auto-funding loop
        this.checkBalancesAndFund();
        this.fundingInterval = setInterval(() => this.checkBalancesAndFund(), AUTO_FUND_INTERVAL);
    }

    subscribe(fn) {
        if (!this.initialized) this.init();
        subscribers.add(fn);
        fn([...this.channels]);
        return () => subscribers.delete(fn);
    }

    async acquireChannel(requiredUSDC = 0) {
        if (!this.initialized) this.init();

        // 1. Find free channel (LRU)
        const freeChannels = this.channels.filter(c => !c.isBusy);

        if (freeChannels.length === 0) {
            throw new Error('All worker wallets are busy. Please wait.');
        }

        // Sort by lastUsed (ascending) -> pick least recently used
        freeChannels.sort((a, b) => a.lastUsed - b.lastUsed);
        const channel = freeChannels[0];

        // 2. Lock - CRITICAL: Mark BUSY immediately to prevent race conditions
        channel.isBusy = true;
        channel.lastUsed = Date.now();
        notify(this.channels);
        console.log(`[SkaleChannelManager] Locked worker: ${channel.address.slice(0, 6)}...`);

        // 3. Check Balance & Fund if needed (wait for funding)
        try {
            await this.ensureFunds(channel, requiredUSDC);
        } catch (e) {
            // Unmark if funding fails so it can be retried or picked up by maintenance
            channel.isBusy = false;
            notify(this.channels);
            console.error(`[SkaleChannelManager] Funding failed for ${channel.address}:`, e);
            throw new Error(`Worker wallet funding failed: ${e.message}`);
        }

        console.log(`[SkaleChannelManager] Acquired worker: ${channel.address.slice(0, 6)}...`);
        return channel;
    }

    releaseChannel(address) {
        const channel = this.channels.find(c => c.address === address);
        if (channel) {
            channel.isBusy = false;
            console.log(`[SkaleChannelManager] Released worker: ${address.slice(0, 6)}...`);
            notify(this.channels);
            // Opportunistic balance refresh
            this.updateChannelBalance(channel);
        }
    }

    /**
     * Ensures the channel has enough sFUEL and USDC.
     * Funds from Main Agent Wallet if needed.
     */
    async ensureFunds(channel, requiredUSDC) {
        // Parallel fetch
        const [sfuelBal, usdcBal] = await Promise.all([
            this.provider.getBalance(channel.address),
            this.getUSDCBalance(channel.address)
        ]);

        const sfuel = parseFloat(ethers.formatEther(sfuelBal));
        const usdc = parseFloat(ethers.formatUnits(usdcBal, 6)); // USDC 6 decimals

        // Update local cache for UI
        channel.balance = { sfuel: sfuel.toFixed(6), usdc: usdc.toFixed(2) };
        notify(this.channels);

        const needsSFuel = sfuel < MIN_SFUEL;
        const strictRequirement = parseFloat(requiredUSDC) + 0.1;
        const triggerThreshold = Math.max(MIN_USDC_THRESHOLD, strictRequirement);
        const needsUSDC = usdc < triggerThreshold;

        // Calculate how much to add:
        let usdcToAdd = 0;
        if (needsUSDC) {
            usdcToAdd = TOPUP_USDC; // Default 1.0
            if ((usdc + usdcToAdd) < strictRequirement) {
                usdcToAdd = strictRequirement - usdc + 1.0; // Add enough to cover + 1 extra
            }
        }

        if (needsSFuel || needsUSDC) {
            console.log(`[SkaleChannelManager] Funding ${channel.address.slice(0, 6)}... (Have: ${usdc} USDC, Need: ${triggerThreshold}. Adding: ${usdcToAdd})`);
            await this.fundWorker(channel.address, needsSFuel ? TARGET_SFUEL : 0, needsUSDC ? usdcToAdd : 0);

            // Re-verify after funding
            await this.updateChannelBalance(channel);

            // Strict check
            if (needsUSDC) {
                const newUsdc = parseFloat(channel.balance.usdc);
                if (newUsdc < strictRequirement) {
                    // Wait a moment and check ONE MORE TIME (RPC lag?)
                    await new Promise(r => setTimeout(r, 2000));
                    await this.updateChannelBalance(channel);
                    const finalUsdc = parseFloat(channel.balance.usdc);

                    if (finalUsdc < strictRequirement) {
                        throw new Error(`Funding appeared successful but balance is still low (${finalUsdc} < ${strictRequirement}).`);
                    }
                }
            }
        }
    }

    async getUSDCBalance(address) {
        const usdc = new ethers.Contract(
            USDC_ADDRESS,
            ['function balanceOf(address) view returns (uint256)'],
            this.provider
        );
        return usdc.balanceOf(address);
    }

    async updateChannelBalance(channel) {
        try {
            const [sfuelBal, usdcBal] = await Promise.all([
                this.provider.getBalance(channel.address),
                this.getUSDCBalance(channel.address)
            ]);
            channel.balance = {
                sfuel: parseFloat(ethers.formatEther(sfuelBal)).toFixed(6),
                usdc: parseFloat(ethers.formatUnits(usdcBal, 6)).toFixed(2)
            };
            notify(this.channels);
        } catch (e) {
            console.warn(`Failed to update balance for ${channel.address}`, e);
        }
    }

    /**
     * Funds a worker wallet from the Main Agent Wallet.
     */
    async fundWorker(workerAddress, sfuelAmount, usdcAmount) {
        if (this.isFunding) {
            // Avoid console spam, just simple wait
            // console.log('[SkaleChannelManager] Funding busy, waiting...');
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

            // Check Main Wallet funds
            const mainSFuelBal = await this.provider.getBalance(mainWallet.address);
            const mainUSDCBal = await this.getUSDCBalance(mainWallet.address);

            const mainSFuel = parseFloat(ethers.formatEther(mainSFuelBal));
            const mainUSDC = parseFloat(ethers.formatUnits(mainUSDCBal, 6));

            if (sfuelAmount > 0 && mainSFuel < sfuelAmount) {
                throw new Error(`Main Agent Wallet low on sFUEL (${mainSFuel}).`);
            }
            if (usdcAmount > 0 && mainUSDC < usdcAmount) {
                throw new Error(`Main Agent Wallet low on USDC (${mainUSDC}).`);
            }

            if (sfuelAmount > 0) {
                // console.log(`[SkaleChannelManager] Sending ${sfuelAmount} sFUEL to ${workerAddress}...`);
                const tx = await mainWallet.sendTransaction({
                    to: workerAddress,
                    value: ethers.parseEther(sfuelAmount.toString())
                });
                await tx.wait();
                // console.log(`[SkaleChannelManager] sFUEL funded.`);
            }

            if (usdcAmount > 0) {
                // console.log(`[SkaleChannelManager] Sending ${usdcAmount} USDC to ${workerAddress}...`);
                const usdc = new ethers.Contract(
                    USDC_ADDRESS,
                    ['function transfer(address to, uint256 amount) returns (bool)'],
                    mainWallet
                );
                const units = ethers.parseUnits(usdcAmount.toString(), 6);
                const tx = await usdc.transfer(workerAddress, units);
                await tx.wait();
                // console.log(`[SkaleChannelManager] USDC funded.`);
            }

        } catch (e) {
            // Check for duplicate transaction error - if so, retry logic would be nice but for now just fail up
            console.error('[SkaleChannelManager] Funding failed:', e);
            throw e;
        } finally {
            this.isFunding = false;
        }
    }

    /**
     * Idle auto-funding check
     */
    async checkBalancesAndFund() {
        if (!this.initialized) return;

        // Stagger checks, and if idle, trigger funding
        for (const ch of this.channels) {
            // Only fund if REALLY idle (not busy)
            if (!ch.isBusy && !this.isFunding) {
                try {
                    // Check and fund with 0 required (this uses the default threshold of 3.0)
                    await this.ensureFunds(ch, 0);
                } catch (e) {
                    // Ignore errors in background loop
                }
            } else {
                // Just update balance if busy or funding busy
                await this.updateChannelBalance(ch);
            }
            await new Promise(r => setTimeout(r, 500));
        }
    }
}

export const skaleChannelManager = new SkaleChannelManager();
