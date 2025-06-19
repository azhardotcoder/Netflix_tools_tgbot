import requests
from decimal import Decimal
import json
from datetime import datetime, timedelta

# Constants
BSCSCAN_API_KEY = "B8V3A7J6SIHKHYHE7AQ8TWTX1821KFCK27"
USDT_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
MERCHANT_WALLET = "0xcF786182Bad53382c5b83E9a11c462B8922aF7B3"
BSCSCAN_API_URL = "https://api.bscscan.com/api"

# Subscription plans in USDT
SUBSCRIPTION_PLANS = {
    "1_day": Decimal("2"),
    "1_month": Decimal("10"),
    "1_year": Decimal("40"),
    "lifetime": Decimal("100")
}

def decode_bep20_amount(amount_hex: str) -> Decimal:
    """
    Decode BEP-20 token amount from hex to decimal
    USDT has 18 decimals on BSC
    """
    try:
        amount_int = int(amount_hex, 16)
        return Decimal(amount_int) / Decimal(10**18)
    except (ValueError, TypeError):
        return Decimal("0")

def verify_usdt_tx(txid: str, expected_amount_usdt: Decimal) -> tuple[bool, str]:
    """
    Verify a USDT transaction on BSC
    
    Args:
        txid: Transaction hash
        expected_amount_usdt: Expected USDT amount
        
    Returns:
        (success, message) tuple
    """
    try:
        # 1. Get transaction details
        params = {
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": txid,
            "apikey": BSCSCAN_API_KEY
        }
        
        response = requests.get(BSCSCAN_API_URL, params=params)
        if response.status_code != 200:
            return False, "Failed to fetch transaction details"
            
        tx_data = response.json()
        if "result" not in tx_data or not tx_data["result"]:
            return False, "Invalid transaction hash"
            
        tx = tx_data["result"]
        
        # 2. Verify it's a USDT transfer
        if tx["to"].lower() != USDT_CONTRACT.lower():
            return False, "Not a USDT transaction"
            
        # 3. Get transaction receipt to check status
        params = {
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": txid,
            "apikey": BSCSCAN_API_KEY
        }
        
        response = requests.get(BSCSCAN_API_URL, params=params)
        if response.status_code != 200:
            return False, "Failed to fetch transaction receipt"
            
        receipt = response.json().get("result", {})
        if not receipt:
            return False, "Transaction not found"
            
        # Check transaction status
        if receipt.get("status") != "0x1":
            return False, "Transaction failed"
            
        # 4. Decode transfer data
        # Transfer method ID for BEP-20: 0xa9059cbb
        # Format: 0xa9059cbb + padded address + padded amount
        input_data = tx["input"]
        if not input_data.startswith("0xa9059cbb"):
            return False, "Not a transfer transaction"
            
        # Extract recipient address (32 bytes after method ID)
        to_addr = "0x" + input_data[34:74]
        if to_addr.lower() != MERCHANT_WALLET.lower():
            return False, "Invalid recipient address"
            
        # Extract amount (last 32 bytes)
        amount_hex = input_data[74:]
        amount = decode_bep20_amount(amount_hex)
        
        if amount < expected_amount_usdt:
            return False, f"Insufficient payment: {amount} USDT (expected {expected_amount_usdt} USDT)"
            
        # 5. Check if transaction is recent (within last 24 hours)
        block_params = {
            "module": "proxy",
            "action": "eth_getBlockByHash",
            "hash": tx["blockHash"],
            "boolean": "false",
            "apikey": BSCSCAN_API_KEY
        }
        
        response = requests.get(BSCSCAN_API_URL, params=block_params)
        if response.status_code == 200:
            block_data = response.json().get("result", {})
            if block_data:
                # Convert hex timestamp to datetime
                timestamp = int(block_data["timestamp"], 16)
                tx_time = datetime.fromtimestamp(timestamp)
                if datetime.now() - tx_time > timedelta(hours=24):
                    return False, "Transaction is too old (>24 hours)"
        
        return True, f"Payment verified: {amount} USDT"
        
    except Exception as e:
        return False, f"Verification error: {str(e)}"

def get_subscription_price(plan: str) -> Decimal:
    """Get price for a subscription plan"""
    return SUBSCRIPTION_PLANS.get(plan, Decimal("0"))

def verify_subscription_payment(txid: str, plan: str) -> tuple[bool, str]:
    """
    Verify payment for a subscription plan
    
    Args:
        txid: Transaction hash
        plan: Subscription plan (1_day, 1_month, 1_year, lifetime)
        
    Returns:
        (success, message) tuple
    """
    expected_amount = get_subscription_price(plan)
    if expected_amount == 0:
        return False, "Invalid subscription plan"
        
    return verify_usdt_tx(txid, expected_amount) 