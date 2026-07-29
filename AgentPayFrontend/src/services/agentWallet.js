import { ethers } from 'ethers';
import { BNB_CHAIN, TOKEN_ADDRESS, TOKEN_DECIMALS } from '../config/bnb';

const STORAGE_KEY = 'agent402_wallet_key';
const RPC_URL = BNB_CHAIN.rpcUrls[0];

let agentWallet = null;

export const getAgentWallet = () => {
    if (agentWallet) return agentWallet;

    const provider = new ethers.JsonRpcProvider(RPC_URL);
    let privateKey = import.meta.env.VITE_AGENT_PRIVATE_KEY || localStorage.getItem(STORAGE_KEY);

    if (!privateKey) {
        const newWallet = ethers.Wallet.createRandom();
        privateKey = newWallet.privateKey;
        localStorage.setItem(STORAGE_KEY, privateKey);
        console.log('[AgentWallet] New wallet generated:', newWallet.address);
    }

    agentWallet = new ethers.Wallet(privateKey, provider);
    console.log('[AgentWallet] Loaded:', agentWallet.address);
    return agentWallet;
};

export const getAgentAddress = () => {
    return getAgentWallet().address;
};

export const hasAgentWallet = () => {
    return !!(import.meta.env.VITE_AGENT_PRIVATE_KEY || localStorage.getItem(STORAGE_KEY));
};

export const getAgentBalances = async (tokenAddress) => {
    const wallet = getAgentWallet();
    const provider = wallet.provider;
    const tokenAddr = tokenAddress || TOKEN_ADDRESS;

    const [bnbBal, tokenBal] = await Promise.all([
        provider.getBalance(wallet.address),
        (async () => {
            if (!tokenAddr) return BigInt(0);
            const token = new ethers.Contract(
                tokenAddr,
                ['function balanceOf(address) view returns (uint256)'],
                provider
            );
            return token.balanceOf(wallet.address);
        })()
    ]);

    return {
        bnb: ethers.formatEther(bnbBal),
        token: ethers.formatUnits(tokenBal, TOKEN_DECIMALS),
        bnbRaw: bnbBal,
        tokenRaw: tokenBal,
        sfuel: ethers.formatEther(bnbBal),
        usdc: ethers.formatUnits(tokenBal, TOKEN_DECIMALS),
        sfuelRaw: bnbBal,
        usdcRaw: tokenBal
    };
};

export const fundAgentWallet = async (tokenAddress, amount) => {
    if (!window.ethereum) throw new Error('MetaMask not found');

    await ensureNetwork();
    const tokenAddr = tokenAddress || TOKEN_ADDRESS;
    const browserProvider = new ethers.BrowserProvider(window.ethereum);
    const accounts = await window.ethereum.request({ method: 'eth_accounts' });
    if (!accounts || accounts.length === 0) {
        await window.ethereum.request({ method: 'eth_requestAccounts' });
    }
    const metamaskAccounts = await window.ethereum.request({ method: 'eth_accounts' });
    const signer = await browserProvider.getSigner(metamaskAccounts[0]);

    const token = new ethers.Contract(
        tokenAddr,
        ['function transfer(address to, uint256 amount) returns (bool)'],
        signer
    );

    const agentAddr = getAgentAddress();
    const amountUnits = ethers.parseUnits(amount.toString(), TOKEN_DECIMALS);

    console.log(`[AgentWallet] Funding ${agentAddr} with ${amount} USDC from MetaMask...`);
    const tx = await token.transfer(agentAddr, amountUnits);
    await tx.wait();
    console.log(`[AgentWallet] Funded! Tx: ${tx.hash}`);
    return tx.hash;
};

export const fundAgentBNB = async (amount = '0.005') => {
    if (!window.ethereum) throw new Error('MetaMask not found');

    await ensureNetwork();
    const browserProvider = new ethers.BrowserProvider(window.ethereum);
    const accounts = await window.ethereum.request({ method: 'eth_accounts' });
    if (!accounts || accounts.length === 0) {
        await window.ethereum.request({ method: 'eth_requestAccounts' });
    }
    const metamaskAccounts = await window.ethereum.request({ method: 'eth_accounts' });
    const signer = await browserProvider.getSigner(metamaskAccounts[0]);

    const agentAddr = getAgentAddress();
    const value = ethers.parseEther(amount);

    console.log(`[AgentWallet] Funding ${agentAddr} with ${amount} ETH from MetaMask...`);
    const tx = await signer.sendTransaction({ to: agentAddr, value });
    await tx.wait();
    console.log(`[AgentWallet] ETH funded! Tx: ${tx.hash}`);
    return tx.hash;
};

const ensureNetwork = async () => {
    if (!window.ethereum) return;
    const chainIdHex = await window.ethereum.request({ method: 'eth_chainId' });
    const requiredChainId = '0x' + BNB_CHAIN.id.toString(16);

    if (chainIdHex !== requiredChainId) {
        try {
            await window.ethereum.request({
                method: 'wallet_switchEthereumChain',
                params: [{ chainId: requiredChainId }],
            });
        } catch (error) {
            if (error.code === 4902) {
                await window.ethereum.request({
                    method: 'wallet_addEthereumChain',
                    params: [{
                        chainId: requiredChainId,
                        chainName: BNB_CHAIN.name,
                        rpcUrls: BNB_CHAIN.rpcUrls,
                        nativeCurrency: BNB_CHAIN.nativeCurrency,
                        blockExplorerUrls: BNB_CHAIN.blockExplorerUrls
                    }],
                });
            } else {
                throw error;
            }
        }
    }
};

export const fundAgentSFuel = fundAgentBNB;

export const resetAgentWallet = () => {
    localStorage.removeItem(STORAGE_KEY);
    agentWallet = null;
    console.log('[AgentWallet] Wallet reset');
};
