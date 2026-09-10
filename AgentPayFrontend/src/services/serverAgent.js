/**
 * Server-side agent runs.
 *
 * The planning loop runs on the backend as a checkpointed graph (POST /agent/runs).
 * When the plan needs paid tools the run pauses and hands back one x402
 * challenge per call; this module pays each one with the browser's own
 * wallets (Nitrolite channel when funded, Sepolia escrow otherwise), posts the
 * proofs to /pay, and repeats until the run finishes. The server never sees a
 * private key.
 *
 * Same result contract and progress `step` events as processQueryWithGemini,
 * so AgentInterface renders both paths identically.
 */

import { ethers } from 'ethers';
import { BNB_CHAIN, TOKEN_ADDRESS, TOKEN_DECIMALS } from '../config/bnb';
import envConfig from '../config/env';
import { getAgentWallet } from './agentWallet';
import { canPayWithNitrolite, payToolWithNitrolite } from './nitroliteService';

const baseUrl = () => (envConfig.MARKETPLACE_URL || 'http://localhost:3000').replace(/\/$/, '');
const unitsToUsdc = (units) => Number(units || 0) / 10 ** TOKEN_DECIMALS;

async function agentApi(path, body) {
  const res = await fetch(`${baseUrl()}${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.detail || json.error || `Agent API error ${res.status}`);
  return json;
}

const ERC20_ABI = [
  'function transfer(address to, uint256 amount) returns (bool)',
  'function balanceOf(address account) view returns (uint256)',
];

/** Pay one challenge on Sepolia: USDC transfer to the escrow. Returns the payment proof + a UI receipt. */
async function payOnChain(call, onProgress) {
  const { challenge } = call;
  const escrowAddress = challenge.payTo || challenge.escrowContract;
  const requiredAmount = BigInt(challenge.maxAmountRequired || 0);
  const wallet = getAgentWallet();
  const receipt = {
    receiptId: `x402-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    protocol: 'x402-bnb-escrow',
    phases: {
      intent: { status: 'complete', timestamp: new Date().toISOString(), toolName: call.name, params: call.args, paymentRequired: true,
        challenge: { payTo: escrowAddress, amount: requiredAmount.toString(), asset: challenge.asset, network: challenge.network } },
      authorization: { status: 'complete', timestamp: new Date().toISOString(), authorizedBy: wallet.address, network: `eip155:${BNB_CHAIN.id}` },
      settlement: { status: 'pending', timestamp: new Date().toISOString() },
      delivery: { status: 'pending', timestamp: null },
    },
    outcome: 'pending', failedAt: null, error: null,
  };

  onProgress?.({ step: 'processing_payment', message: `Paying ${unitsToUsdc(requiredAmount)} USDC for ${call.name}...`, args: call.args, receipt });
  const token = new ethers.Contract(TOKEN_ADDRESS, ERC20_ABI, wallet);
  const balance = await token.balanceOf(wallet.address);
  if (balance < requiredAmount) {
    receipt.phases.settlement.status = 'failed'; receipt.outcome = 'failed'; receipt.failedAt = 'settlement';
    receipt.error = `Insufficient USDC balance. Have: ${ethers.formatUnits(balance, TOKEN_DECIMALS)}, Need: ${ethers.formatUnits(requiredAmount, TOKEN_DECIMALS)}`;
    throw new Error(receipt.error);
  }
  const tx = await token.transfer(escrowAddress, requiredAmount);
  onProgress?.({ step: 'awaiting_confirmation', message: `Waiting for confirmation of ${tx.hash.slice(0, 10)}...`, args: call.args, receipt });
  const mined = await tx.wait();
  if (!mined || mined.status === 0) throw new Error(`Token transfer failed. Tx: ${tx.hash}`);

  Object.assign(receipt.phases.settlement, {
    status: 'complete', txHash: tx.hash, chain: 'Ethereum Sepolia Testnet', chainId: BNB_CHAIN.id, from: wallet.address,
    to: escrowAddress, amount: requiredAmount.toString(), asset: 'USDC', blockNumber: mined.blockNumber,
    explorerUrl: `https://sepolia.etherscan.io/tx/${tx.hash}`,
  });
  return { payload: { method: 'x402', tx_hash: tx.hash }, txHash: tx.hash, receipt };
}

/** Pay one challenge off-chain through the Yellow/Nitrolite channel. */
async function payWithNitrolite(call) {
  const price = unitsToUsdc(call.price_units);
  const nitro = await payToolWithNitrolite(call.name, price, call.challenge.toolProvider);
  const receipt = {
    receiptId: `nitro-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    protocol: 'nitrolite-erc7824', paymentMethod: 'yellow-nitrolite',
    phases: {
      intent: { status: 'complete', timestamp: new Date().toISOString(), toolName: call.name, params: call.args },
      authorization: { status: 'complete', timestamp: new Date().toISOString(), method: 'off-chain-erc7824' },
      settlement: { status: 'complete', timestamp: new Date().toISOString(), amount: String(price), asset: 'USDC', instantSettlement: true, appSessionId: nitro.appSessionId },
      delivery: { status: 'pending', timestamp: null },
    },
    outcome: 'pending', nitrolite: nitro,
  };
  return {
    payload: { method: 'nitrolite', proof: btoa(JSON.stringify(nitro.proof || {})), from: nitro.from || '' },
    txHash: null, receipt,
  };
}

async function payForCall(call, onProgress) {
  const price = unitsToUsdc(call.price_units);
  if (call.challenge?.toolProvider && canPayWithNitrolite(price)) {
    try { return await payWithNitrolite(call); }
    catch (e) { console.warn('[Nitrolite] off-chain payment failed, using Sepolia escrow:', e.message); }
  }
  return payOnChain(call, onProgress);
}

function toExecutionDetails(view) {
  return (view.results || []).map((r) => {
    const body = r.response || {};
    return {
      toolName: r.name, args: r.args, reasoning: view.plan_text,
      cost: unitsToUsdc(r.ok ? r.price_units : 0),
      txHash: body.escrowReceipt?.txHash || null,
      receipt: body.escrowReceipt || body.nitroliteReceipt || null,
      output: body.data ?? body.error ?? body,
      status: r.ok ? 'success' : 'failed',
    };
  });
}

/**
 * Drive one task through the server-side agent. Same contract as processQueryWithGemini.
 * `chatHistory` is passed to the server so multi-turn context survives the switch.
 */
export const processQueryWithServerAgent = async (userQuery, onProgress, chatHistory = []) => {
  try {
    onProgress?.({ step: 'analyzing', message: 'Planning with the agent...' });
    let view = await agentApi('/agent/runs', { task: userQuery, history: chatHistory.slice(-20) });

    while (view.status === 'awaiting_payment') {
      const names = view.pending_calls.map((c) => c.name);
      onProgress?.({ step: 'planning_complete', plan: view.plan_text || 'Executing tools', toolsParam: names });

      const payments = {};
      for (const call of view.pending_calls) {
        onProgress?.({ step: 'tool_selected', toolName: call.name, args: call.args });
        onProgress?.({ step: 'payment_required', toolName: call.name, amount: unitsToUsdc(call.price_units), args: call.args });
        const paid = await payForCall(call, onProgress);
        payments[call.id] = paid.payload;
        onProgress?.({ step: 'payment_confirmed', toolName: call.name, amount: unitsToUsdc(call.price_units), txHash: paid.txHash, args: call.args, receipt: paid.receipt });
      }

      onProgress?.({ step: 'delivering', message: 'Executing paid tools on the server...' });
      view = await agentApi(`/agent/runs/${view.run_id}/pay`, { payments });
    }

    if (view.status === 'budget_exceeded') {
      return { success: false, finalResponse: view.error || 'Run budget exceeded before payment.', runId: view.run_id };
    }

    onProgress?.({ step: 'generating_response', message: 'Composing the answer...' });
    const executionDetails = toExecutionDetails(view);
    const toolsUsed = [...new Set(executionDetails.map((d) => d.toolName))];
    const stop = view.status === 'iteration_cap' ? '\n\n(Stopped after maximum execution steps)' : '';
    return {
      success: true,
      finalResponse: (view.final_text || '') + stop,
      toolUsed: toolsUsed.length ? toolsUsed.join(', ') : null,
      toolResponses: executionDetails.map((d) => d.output),
      executionDetails,
      cost: executionDetails.reduce((sum, d) => sum + (d.cost || 0), 0),
      runId: view.run_id,
      usage: view.usage,
    };
  } catch (error) {
    console.error('[ServerAgent] run failed:', error);
    return { success: false, finalResponse: error.message };
  }
};
