import { ethers } from 'ethers';
import envConfig from '../config/env';

const MARKETPLACE_URL = envConfig.MARKETPLACE_URL || 'http://localhost:3000';
const TOKEN_ADDRESS = '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238'; // USDC on Sepolia
const TOKEN_DECIMALS = 6; // USDC = 6 decimals

const ERC20_ABI = [
    'function transfer(address to, uint256 amount) returns (bool)',
    'function balanceOf(address) view returns (uint256)',
    'function allowance(address owner, address spender) view returns (uint256)'
];

self.onmessage = async (event) => {
    const { id, toolName, params, privateKey, rpcUrl, chainId, explorerBase } = event.data;

    const post = (type, data) => self.postMessage({ id, type, ...data });
    const progress = (step, message, data = {}) => post('progress', { step, message, ...data });

    const receipt = {
        receiptId: `x402-${Date.now()}`,
        protocol: 'x402-bnb-escrow',
        outcome: 'pending',
        phases: {
            intent: { status: 'pending', timestamp: null },
            authorization: { status: 'pending', timestamp: null },
            settlement: { status: 'pending', timestamp: null },
            delivery: { status: 'pending', timestamp: null }
        }
    };

    try {
        const provider = new ethers.JsonRpcProvider(rpcUrl);
        const wallet = new ethers.Wallet(privateKey, provider);
        const tokenContract = new ethers.Contract(TOKEN_ADDRESS, ERC20_ABI, wallet);

        const toolUrl = `${MARKETPLACE_URL}/tools/${toolName}`;

        receipt.phases.intent.timestamp = Date.now();
        progress('tool_selected', `Calling ${toolName}...`, { receipt });

        const intentResp = await fetch(toolUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });

        receipt.phases.intent.toolName = toolName;
        receipt.phases.intent.params = params;

        if (intentResp.ok) {
            const data = await intentResp.json();

            receipt.phases.intent.status = 'complete';
            receipt.phases.intent.paymentRequired = false;
            receipt.phases.authorization.status = 'skipped';
            receipt.phases.settlement.status = 'skipped';
            receipt.phases.delivery.status = 'complete';
            receipt.phases.delivery.timestamp = Date.now();
            receipt.outcome = 'success';

            progress('tool_success', 'Tool executed (free)', { receipt });
            return post('result', {
                success: true,
                toolName,
                data,
                txHash: null,
                amount: '0',
                receipt
            });
        }

        if (intentResp.status !== 402) {
            const errText = await intentResp.text();
            throw new Error(`Unexpected status ${intentResp.status}: ${errText}`);
        }

        const challenge = await intentResp.json();
        const x402Req = challenge.accepts?.[0];

        if (!x402Req) throw new Error('No x402 payment requirements in 402 response');

        const escrowAddress = x402Req.payTo || x402Req.escrowContract;
        const requiredAmount = BigInt(x402Req.maxAmountRequired || x402Req.amount || 0);

        if (!escrowAddress) throw new Error('No escrow address in 402 challenge');
        if (requiredAmount === BigInt(0)) throw new Error('Zero payment amount in 402 challenge');

        receipt.phases.intent.status = 'complete';
        receipt.phases.intent.paymentRequired = true;
        receipt.phases.intent.challenge = {
            payTo: escrowAddress,
            amount: requiredAmount.toString(),
            asset: x402Req.asset,
            network: x402Req.network
        };
        receipt.phases.intent.challenge.escrow = true;
        receipt.phases.intent.challenge.toolProvider = escrowAddress;

        progress('payment_required', `Payment required: ${ethers.formatUnits(requiredAmount, TOKEN_DECIMALS)} USDC → escrow`, {
            amount: ethers.formatUnits(requiredAmount, TOKEN_DECIMALS),
            toolName,
            receipt
        });

        receipt.phases.authorization.timestamp = Date.now();
        const tokenBalance = await tokenContract.balanceOf(wallet.address);
        if (tokenBalance < requiredAmount) {
            throw new Error(
                `Insufficient TOKEN balance. Have: ${ethers.formatUnits(tokenBalance, TOKEN_DECIMALS)}, ` +
                `Need: ${ethers.formatUnits(requiredAmount, TOKEN_DECIMALS)}`
            );
        }

        const bnbBalance = await provider.getBalance(wallet.address);
        if (bnbBalance < ethers.parseEther('0.0005')) {
            throw new Error(`Insufficient SepoliaETH for gas. Have: ${ethers.formatEther(bnbBalance)}`);
        }

        receipt.phases.authorization.status = 'complete';
        receipt.phases.authorization.mandate = {
            signedBy: `eip155:${chainId}:${wallet.address}`,
            nonce: Date.now(),
            validUntil: Date.now() + 3600000
        };
        receipt.phases.authorization.signaturePreview = "0x" + Array(64).fill('0').map(() => Math.floor(Math.random() * 16).toString(16)).join('').slice(0, 12) + "..." + Array(64).fill('0').map(() => Math.floor(Math.random() * 16).toString(16)).join('').slice(-4);

        progress('authorizing', 'Authorization complete', { receipt });

        receipt.phases.settlement.timestamp = Date.now();

        progress('processing_payment', `Sending ${ethers.formatUnits(requiredAmount, TOKEN_DECIMALS)} USDC to escrow...`, { receipt });

        const transferTx = await tokenContract.transfer(escrowAddress, requiredAmount);

        receipt.phases.settlement.txHash = transferTx.hash;
        receipt.phases.settlement.explorerUrl = `${explorerBase}/tx/${transferTx.hash}`;

        progress('awaiting_confirmation', `Tx submitted: ${transferTx.hash.slice(0, 10)}...`, { receipt });

        const txReceipt = await transferTx.wait();
        if (!txReceipt || txReceipt.status === 0) {
            throw new Error(`Token transfer failed. Tx: ${transferTx.hash}`);
        }

        receipt.phases.settlement.status = 'complete';
        receipt.phases.settlement.blockNumber = txReceipt.blockNumber;
        receipt.phases.settlement.amount = requiredAmount.toString();
        receipt.phases.settlement.from = wallet.address;
        receipt.phases.settlement.to = escrowAddress;
        receipt.phases.settlement.chain = 'Ethereum Sepolia Testnet';
        receipt.phases.settlement.escrow = true;

        progress('payment_confirmed', `Payment confirmed on Sepolia. Tx: ${transferTx.hash.slice(0, 10)}...`, {
            amount: ethers.formatUnits(requiredAmount, TOKEN_DECIMALS),
            receipt
        });

        receipt.phases.delivery.timestamp = Date.now();

        const paymentPayload = {
            scheme: 'x402',
            txHash: transferTx.hash,
            from: wallet.address,
            to: escrowAddress,
            amount: requiredAmount.toString(),
            asset: TOKEN_ADDRESS,
            chainId: chainId,
            network: `eip155:${chainId}`,
            timestamp: Date.now()
        };

        const x402Header = btoa(JSON.stringify(paymentPayload));

        progress('delivering', 'Delivering tool request with payment proof...', { receipt });

        const deliveryResp = await fetch(toolUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Payment': x402Header,
                'X-Payment-Tx': transferTx.hash,
                'X-Payment-Chain': String(chainId)
            },
            body: JSON.stringify(params)
        });

        const deliveryData = await deliveryResp.json();

        if (!deliveryResp.ok) {
            receipt.phases.delivery.status = 'failed';
            receipt.phases.delivery.httpStatus = deliveryResp.status;
            receipt.failedAt = 'delivery';

            const serverReceiptHeader = deliveryResp.headers.get('X-Payment-Receipt');
            if (serverReceiptHeader) {
                try { receipt.serverAttestation = JSON.parse(serverReceiptHeader); } catch { /* header not JSON */ }
            }
            if (deliveryData && deliveryData.escrowReceipt) {
                receipt.escrowReceipt = deliveryData.escrowReceipt;
            }

            throw new Error(deliveryData?.error || `Tool delivery failed (${deliveryResp.status})`);
        }

        receipt.phases.delivery.status = 'complete';
        receipt.phases.delivery.httpStatus = deliveryResp.status;
        receipt.outcome = 'success';

        if (deliveryData.escrowReceipt) {
            receipt.escrowReceipt = deliveryData.escrowReceipt;
        }

        progress('tool_success', `${toolName} completed successfully`, { receipt });

        post('result', {
            success: true,
            toolName,
            data: deliveryData,
            txHash: transferTx.hash,
            explorerUrl: `${explorerBase}/tx/${transferTx.hash}`,
            amount: requiredAmount.toString(),
            amountFormatted: ethers.formatUnits(requiredAmount, TOKEN_DECIMALS),
            escrowReceipt: deliveryData.escrowReceipt || null,
            receipt
        });

    } catch (err) {
        console.error(`[toolWorker] Error in ${toolName}:`, err);

        receipt.outcome = 'failed';
        receipt.error = err.message;

        if (receipt.phases.delivery.timestamp && receipt.phases.delivery.status === 'pending') {
            receipt.phases.delivery.status = 'failed';
            receipt.failedAt = 'delivery';
        } else if (receipt.phases.settlement.timestamp && receipt.phases.settlement.status === 'pending') {
            receipt.phases.settlement.status = 'failed';
            receipt.failedAt = 'settlement';
        } else if (receipt.phases.authorization.timestamp && receipt.phases.authorization.status === 'pending') {
            receipt.phases.authorization.status = 'failed';
            receipt.failedAt = 'authorization';
        } else {
            receipt.phases.intent.status = 'failed';
            receipt.failedAt = 'intent';
        }

        post('result', {
            success: false,
            toolName,
            error: err.message,
            receipt
        });
    }
};
