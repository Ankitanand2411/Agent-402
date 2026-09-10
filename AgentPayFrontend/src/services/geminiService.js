import { ethers } from "ethers";
import { BNB_CHAIN, TOKEN_ADDRESS, TOKEN_DECIMALS } from "../config/bnb";
import envConfig from '../config/env';
import { getAgentWallet } from './agentWallet';
import { canPayWithNitrolite, payToolWithNitrolite } from './nitroliteService';

const toolDataMap = new Map();

export const wakeUpServices = async () => {
  const services = [
    envConfig.MARKETPLACE_URL
  ];

  console.log('[WakeUp] Pinging services to wake them up...');

  services.forEach(url => {
    fetch(url, { method: 'GET', mode: 'no-cors' })
      .then(() => console.log(`[WakeUp] Ping sent to ${url}`))
      .catch(err => console.warn(`[WakeUp] Failed to ping ${url}:`, err));
  });
};

const sanitizeParameters = (params) => {
  if (!params || typeof params !== 'object') {
    return {
      type: "object",
      properties: {},
      required: []
    };
  }

  const sanitized = {
    type: params.type || "object"
  };

  if (params.properties && typeof params.properties === 'object') {
    const cleanProperties = {};
    for (const [key, value] of Object.entries(params.properties)) {
      if (value && typeof value === 'object') {
        cleanProperties[key] = {
          type: value.type || "string",
          ...(value.description && { description: value.description }),
          ...(value.enum && Array.isArray(value.enum) && { enum: value.enum })
        };
      }
    }
    sanitized.properties = cleanProperties;
  } else {
    sanitized.properties = {};
  }

  if (params.required && Array.isArray(params.required)) {
    sanitized.required = params.required.filter(item => typeof item === 'string');
  }

  return sanitized;
};

export const fetchMarketplaceTools = async () => {
  try {
    const MARKETPLACE_TOOLS_URL = (envConfig.MARKETPLACE_URL || 'http://localhost:3000').replace(/\/$/, '');
    const response = await fetch(`${MARKETPLACE_TOOLS_URL}/tools`);
    if (!response.ok) {
      throw new Error(`Failed to fetch tools: ${response.status}`);
    }
    const tools = await response.json();

    console.log('Raw tools from marketplace:', tools);

    toolDataMap.clear();

    const functionDeclarations = tools.map(tool => {
      let extractedPrice = 0;
      if (tool.price !== undefined && tool.price !== null) {
        extractedPrice = parseFloat(tool.price);
      } else {
        const priceMatch = tool.description?.match(/COSTS?:\s*(\d+(?:\.\d+)?)\s*(?:USDC|XLM)/i)
          || tool.description?.match(/COSTS?:\s*(\d+(?:\.\d+)?)/i);
        extractedPrice = priceMatch ? parseFloat(priceMatch[1]) : 0;
      }

      toolDataMap.set(tool.name, {
        ...tool,
        price: extractedPrice
      });

      const llmDescription = `${tool.description} (Cost: ${extractedPrice} USDC on Ethereum Sepolia Testnet)`;

      const declaration = {
        type: "function",
        function: {
          name: tool.name,
          description: llmDescription,
          parameters: sanitizeParameters(tool.parameters)
        }
      };

      console.log(`Formatted tool: ${tool.name}, Price: ${extractedPrice} USDC`, declaration);
      return declaration;
    });

    console.log('Function declarations:', JSON.stringify(functionDeclarations, null, 2));
    return functionDeclarations;
  } catch (error) {
    console.error('Error fetching marketplace tools:', error);
    return [];
  }
};


export const callPaidTool = async (toolName, params) => {
  console.log(`[x402] calling paid tool: ${toolName}`);

  const baseUrl = (envConfig.MARKETPLACE_URL || 'http://localhost:3000').replace(/\/$/, '');
  const toolUrl = `${baseUrl}/tools/${toolName}`;

  // ── Yellow Nitrolite fast path ─────────────────────────────────────────────
  // If the Yellow ClearNode channel is connected and funded, use instant
  // off-chain payment. Otherwise fall through to Sepolia escrow (existing flow).
  const toolData = toolDataMap.get(toolName);
  const toolPrice = toolData?.price || 0;
  const toolProviderWallet = toolData?.walletAddress || null;

  if (canPayWithNitrolite(toolPrice) && toolProviderWallet) {
    console.log(`[Nitrolite] Using off-chain Yellow payment for ${toolName} (${toolPrice} LINK)`);
    try {
      const nitroReceipt = await payToolWithNitrolite(toolName, toolPrice, toolProviderWallet);

      // Deliver the tool request — backend doesn't require payment header
      // because Nitrolite payment is off-chain (ledger settled by ClearNode)
      // We pass a special header to inform the backend without requiring on-chain verification
      const deliveryResp = await fetch(toolUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Payment-Method': 'nitrolite',
          'X-Nitrolite-Session': nitroReceipt.appSessionId || '',
          'X-Nitrolite-Amount': String(toolPrice),
          'X-Nitrolite-From': nitroReceipt.from || '',
          'X-Nitrolite-Proof': btoa(JSON.stringify(nitroReceipt.proof || {})),
        },
        body: JSON.stringify(params)
      });

      // If backend returns 402 (doesn't know about Nitrolite yet), fallback to Sepolia
      if (deliveryResp.status === 402) {
        console.warn('[Nitrolite] Backend returned 402 — falling back to Sepolia escrow');
      } else if (deliveryResp.ok) {
        const data = await deliveryResp.json();
        const receipt = {
          receiptId: `nitro-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          protocol: 'nitrolite-erc7824',
          paymentMethod: 'yellow-nitrolite',
          phases: {
            intent: { status: 'complete', timestamp: new Date().toISOString(), toolName, params },
            authorization: { status: 'complete', timestamp: new Date().toISOString(), method: 'off-chain-erc7824' },
            settlement: {
              status: 'complete',
              timestamp: new Date().toISOString(),
              amount: toolPrice.toString(),
              asset: 'USDC',
              chain: 'Ethereum Sepolia Testnet (Yellow off-chain)',
              instantSettlement: true,
              appSessionId: nitroReceipt.appSessionId,
            },
            delivery: { status: 'complete', timestamp: new Date().toISOString(), httpStatus: deliveryResp.status }
          },
          outcome: 'success',
          nitrolite: nitroReceipt,
        };
        return { success: true, data, toolName, txHash: null, receipt, paymentMethod: 'nitrolite' };
      }
      // If non-ok non-402, fall through to Sepolia path
    } catch (nitroErr) {
      console.warn('[Nitrolite] Off-chain payment failed, falling back to Sepolia escrow:', nitroErr.message);
    }
  }

  // ── Sepolia escrow path (existing, unchanged) ─────────────────────────────────
  const receipt = {
    receiptId: `x402-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    protocol: "x402-bnb-escrow",
    phases: {
      intent: { status: "pending", timestamp: null },
      authorization: { status: "pending", timestamp: null },
      settlement: { status: "pending", timestamp: null },
      delivery: { status: "pending", timestamp: null }
    },
    outcome: "pending",
    failedAt: null,
    error: null
  };

  try {
    receipt.phases.intent.timestamp = new Date().toISOString();
    receipt.phases.intent.toolName = toolName;
    receipt.phases.intent.toolEndpoint = toolUrl;
    receipt.phases.intent.params = params;

    let response = await fetch(toolUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params)
    });

    if (response.status !== 402) {
      receipt.phases.intent.status = "complete";
      receipt.phases.intent.paymentRequired = false;
      receipt.phases.authorization.status = "skipped";
      receipt.phases.settlement.status = "skipped";

      if (response.ok) {
        receipt.phases.delivery.timestamp = new Date().toISOString();
        receipt.phases.delivery.status = "complete";
        receipt.phases.delivery.httpStatus = response.status;
        const data = await response.json();
        receipt.phases.delivery.toolResponse = data;
        receipt.outcome = "success";
        return { success: true, data, toolName, receipt };
      } else {
        throw new Error(`Tool returned ${response.status} ${response.statusText}`);
      }
    }

    const challenge = await response.json();
    const x402Req = challenge.accepts?.[0];

    if (!x402Req) {
      throw new Error("No x402 payment requirements in 402 challenge");
    }

    const escrowAddress = x402Req.payTo || x402Req.escrowContract;
    const requiredAmount = BigInt(x402Req.maxAmountRequired || x402Req.amount || 0);

    receipt.phases.intent.status = "complete";
    receipt.phases.intent.paymentRequired = true;
    receipt.phases.intent.challenge = {
      payTo: escrowAddress,
      amount: requiredAmount.toString(),
      asset: x402Req.asset,
      network: x402Req.network
    };
    console.log("[x402] Intent complete. Challenge:", receipt.phases.intent.challenge);

    receipt.phases.authorization.timestamp = new Date().toISOString();

    const agentWallet = getAgentWallet();
    const address = agentWallet.address;
    const signer = agentWallet;

    receipt.phases.authorization.status = "complete";
    receipt.phases.authorization.authorizedBy = address;
    receipt.phases.authorization.network = `eip155:${BNB_CHAIN.id}`;
    console.log("[x402] Authorization: using agent wallet", address);

    receipt.phases.settlement.timestamp = new Date().toISOString();

    const ERC20_ABI = [
      "function transfer(address to, uint256 amount) returns (bool)",
      "function balanceOf(address account) view returns (uint256)"
    ];
    const tokenContract = new ethers.Contract(TOKEN_ADDRESS, ERC20_ABI, signer);

    const tokenBalance = await tokenContract.balanceOf(address);
    console.log(`[x402] USDC Balance: ${ethers.formatUnits(tokenBalance, TOKEN_DECIMALS)}, Required: ${ethers.formatUnits(requiredAmount, TOKEN_DECIMALS)}`);

    if (tokenBalance < requiredAmount) {
      receipt.phases.settlement.status = "failed";
      receipt.outcome = "failed";
      receipt.failedAt = "settlement";
      receipt.error = `Insufficient USDC balance. Have: ${ethers.formatUnits(tokenBalance, TOKEN_DECIMALS)}, Need: ${ethers.formatUnits(requiredAmount, TOKEN_DECIMALS)}`;
      throw new Error(receipt.error);
    }

    const txResponse = await tokenContract.transfer(escrowAddress, requiredAmount);
    console.log(`[x402] Tx Sent:`, txResponse.hash);
    const txReceipt = await txResponse.wait();

    if (!txReceipt || txReceipt.status === 0) {
      throw new Error(`Token transfer failed. Tx: ${txResponse.hash}`);
    }

    receipt.phases.settlement.status = "complete";
    receipt.phases.settlement.txHash = txResponse.hash;
    receipt.phases.settlement.chain = "Ethereum Sepolia Testnet";
    receipt.phases.settlement.chainId = BNB_CHAIN.id;
    receipt.phases.settlement.from = address;
    receipt.phases.settlement.to = escrowAddress;
    receipt.phases.settlement.amount = requiredAmount.toString();
    receipt.phases.settlement.asset = "USDC";
    receipt.phases.settlement.blockNumber = txReceipt?.blockNumber;
    receipt.phases.settlement.explorerUrl = `https://sepolia.etherscan.io/tx/${txResponse.hash}`;
    console.log("[x402] Settlement complete. Block:", txReceipt?.blockNumber);

    receipt.phases.delivery.timestamp = new Date().toISOString();

    const paymentPayload = {
      scheme: 'x402',
      txHash: txResponse.hash,
      from: address,
      to: escrowAddress,
      amount: requiredAmount.toString(),
      asset: TOKEN_ADDRESS,
      chainId: BNB_CHAIN.id,
      network: `eip155:${BNB_CHAIN.id}`,
      timestamp: Date.now()
    };
    const paymentHeader = btoa(JSON.stringify(paymentPayload));

    response = await fetch(toolUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Payment": paymentHeader,
        "X-Payment-Tx": txResponse.hash,
        "X-Payment-Chain": String(BNB_CHAIN.id)
      },
      body: JSON.stringify(params)
    });

    if (!response.ok) {
      receipt.phases.delivery.status = "failed";
      receipt.phases.delivery.httpStatus = response.status;
      receipt.outcome = "failed";
      receipt.failedAt = "delivery";
      receipt.error = `Tool returned ${response.status} after payment`;
      throw new Error(receipt.error);
    }

    const serverReceiptHeader = response.headers.get('X-Payment-Receipt');
    if (serverReceiptHeader) {
      try { receipt.serverAttestation = JSON.parse(serverReceiptHeader); } catch { /* header not JSON */ }
    }

    const data = await response.json();
    receipt.phases.delivery.status = "complete";
    receipt.phases.delivery.httpStatus = response.status;
    receipt.outcome = "success";
    console.log("[x402] Delivery complete. Full receipt:", receipt.receiptId);

    return {
      success: true,
      data,
      toolName,
      txHash: txResponse.hash,
      receipt
    };

  } catch (err) {
    if (receipt.outcome !== "failed") {
      receipt.outcome = "failed";
      for (const phase of ["intent", "authorization", "settlement", "delivery"]) {
        if (receipt.phases[phase].status === "pending" && receipt.phases[phase].timestamp) {
          receipt.phases[phase].status = "failed";
          receipt.failedAt = phase;
          break;
        }
      }
      receipt.error = err.message;
    }

    console.error("[x402] Flow failed at:", receipt.failedAt, err.message);
    return {
      success: false,
      error: err.message,
      toolName,
      receipt
    };
  }
};
export const processQueryWithGemini = async (userQuery, availableTools, onProgress, chatHistory = []) => {
  try {
    onProgress?.({ step: 'analyzing', message: 'Analyzing your request with Gemini...' });

    const geminiTools = availableTools.map(t => t.function);

    const SERVER_URL = envConfig.MARKETPLACE_URL;
    const chatEndpoint = `${SERVER_URL}/gemini/chat`;

    let response = await fetch(chatEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        history: chatHistory,
        message: userQuery,
        tools: geminiTools
      })
    });

    if (!response.ok) {
      let errorMessage = `Backend API Error: ${response.statusText}`;
      try {
        const errorPayload = await response.json();
        if (errorPayload?.error) {
          errorMessage = `Backend API Error: ${errorPayload.error}`;
          if (errorPayload.details) {
            errorMessage += ` | ${typeof errorPayload.details === 'string' ? errorPayload.details : JSON.stringify(errorPayload.details)}`;
          }
        }
      } catch { /* body was not JSON */ }
      throw new Error(errorMessage);
    }

    let result = await response.json();


    let iterationCount = 0;
    const MAX_ITERATIONS = 5;
    const allToolsUsed = [];
    const allToolResponses = [];
    const executionDetails = [];
    let totalCost = 0;

    while (iterationCount < MAX_ITERATIONS) {

      const textResponse = result.text;
      const functionCalls = result.functionCalls;

      if (!functionCalls || functionCalls.length === 0) {
        return {
          success: true,
          finalResponse: textResponse,
          toolUsed: allToolsUsed.length > 0 ? allToolsUsed.join(', ') : null,
          toolResponses: allToolResponses,
          executionDetails: executionDetails,
          cost: totalCost
        };
      }


      console.log(`Iteration ${iterationCount + 1}: Received ${functionCalls.length} tool calls`);

      let geminiPlan = null;
      if (result.parts && result.parts.length > 0) {
        const textPart = result.parts.find(p => p.text && !p.functionCall);
        if (textPart) {
          geminiPlan = textPart.text;
        }
        const thoughtPart = result.parts.find(p => p.thought);
        if (thoughtPart) {
          geminiPlan = thoughtPart.thought;
        }
      }
      if (!geminiPlan && result.text) {
        geminiPlan = result.text;
      }

      if (geminiPlan) {
        onProgress?.({
          step: 'planning_complete',
          message: 'Plan established.',
          plan: geminiPlan,
          toolsParam: functionCalls.map(c => c.name)
        });
      }

      const { executeToolInWorker } = await import('./workerPool.js');

      const executionPromises = functionCalls.map(async (call, index) => {
        const functionName = call.name;
        const functionArgs = call.args;
        const reasoning = (index === 0) ? geminiPlan : null;

        allToolsUsed.push(functionName);

        const toolData = toolDataMap.get(functionName);
        const toolPrice = toolData?.price || 0;
        totalCost += parseFloat(toolPrice);

        const workerResult = await executeToolInWorker(
          functionName,
          functionArgs,
          (progress) => onProgress?.(progress)
        );

        const toolResponse = workerResult;

        let finalCost = toolPrice;
        if (toolResponse.receipt?.phases?.settlement?.amount) {
          finalCost = (Number(toolResponse.receipt.phases.settlement.amount) / 1e18).toFixed(6);
        }

        executionDetails.push({
          toolName: functionName,
          args: functionArgs,
          reasoning: reasoning,
          cost: finalCost,
          txHash: toolResponse.txHash,
          receipt: toolResponse.receipt,
          output: toolResponse.data || toolResponse.error,
          status: toolResponse.success ? 'success' : 'failed'
        });

        if (!toolResponse.success) {
          onProgress?.({
            step: 'tool_failed',
            message: `Tool ${functionName} failed: ${toolResponse.error}`,
            toolName: functionName,
            error: toolResponse.error
          });

          return {
            functionResponse: {
              name: functionName,
              response: {
                result: {
                  status: "failed",
                  error: toolResponse.error,
                  message: "This tool call failed. Do not retry unless parameters were wrong."
                }
              }
            }
          };
        }

        const toolOutput = toolResponse.data;

        if (toolOutput) {
          allToolResponses.push(toolOutput);
        }

        let responseForGemini = { result: toolOutput };
        if (toolOutput && toolOutput.type === 'audio') {
          responseForGemini = {
            result: {
              status: "success",
              message: "Audio generated successfully and is being played to the user."
            }
          };
        }

        return {
          functionResponse: {
            name: functionName,
            response: responseForGemini
          }
        };
      });

      const functionResponses = await Promise.all(executionPromises);

      onProgress?.({ step: 'generating_response', message: 'Sending tool outputs to AI...' });

      if (iterationCount === 0) {
        chatHistory.push({ role: 'user', parts: [{ text: userQuery }] });
      }

      const modelParts = result.parts || functionCalls.map(fc => ({
        functionCall: {
          name: fc.name,
          args: fc.args
        }
      }));

      chatHistory.push({
        role: 'model',
        parts: modelParts
      });

      chatHistory.push({
        role: 'function',
        parts: functionResponses.map(fr => ({
          functionResponse: {
            name: fr.functionResponse.name,
            response: fr.functionResponse.response
          }
        }))
      });

      const nextMessageParts = functionResponses.map(fr => ({
        functionResponse: {
          name: fr.functionResponse.name,
          response: fr.functionResponse.response
        }
      }));

      response = await fetch(chatEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          history: chatHistory,
          message: { parts: nextMessageParts },
          tools: geminiTools
        })
      });

      result = await response.json();

      iterationCount++;
    }

    let stopMsg = "\n\n(Stopped after maximum execution steps)";
    const pendingTools = result.functionCalls?.map(fc => fc.name).join(', ');
    if (pendingTools) {
      stopMsg += `\nPending tools not executed: ${pendingTools}`;
    }

    return {
      success: true,
      finalResponse: (result.text || "") + stopMsg,
      toolUsed: allToolsUsed.join(', '),
      toolResponses: allToolResponses,
      executionDetails: executionDetails,
      cost: totalCost
    };

  } catch (error) {
    console.error('Error processing query with Gemini:', error);
    return {
      success: false,
      finalResponse: error.message
    };
  }
};
