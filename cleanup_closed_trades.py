import json
from pathlib import Path

CLOSED_TRADES_FILE = "closed_trades.json"
BACKUP_FILE = "closed_trades_backup.json"

def cleanup_closed_trades():
    if not Path(CLOSED_TRADES_FILE).exists():
        print("File not found")
        return
    
    with open(CLOSED_TRADES_FILE, 'r', encoding='utf-8') as f:
        trades = json.load(f)
    
    # Create backup
    with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
        json.dump(trades, f, indent=4)
    print(f"✅ Backup created: {BACKUP_FILE}")
    print(f"Original count: {len(trades)}")
    
    # Remove duplicates (keep the one with most recent closed_at)
    unique_trades = {}
    
    for trade in trades:
        # Create a unique key
        key = f"{trade.get('symbol')}_{trade.get('entry_price')}_{trade.get('opened_at', '')}"
        
        if key not in unique_trades:
            unique_trades[key] = trade
        else:
            # Keep the one with more targets_hit or more recent close
            existing = unique_trades[key]
            if len(trade.get('targets_hit', [])) > len(existing.get('targets_hit', [])):
                unique_trades[key] = trade
            elif trade.get('closed_at', '') > existing.get('closed_at', ''):
                unique_trades[key] = trade
    
    cleaned_trades = list(unique_trades.values())
    
    # Save cleaned trades
    with open(CLOSED_TRADES_FILE, 'w', encoding='utf-8') as f:
        json.dump(cleaned_trades, f, indent=4)
    
    print(f"✅ Cleaned! New count: {len(cleaned_trades)}")
    print(f"Removed {len(trades) - len(cleaned_trades)} duplicates")


if __name__ == "__main__":
    cleanup_closed_trades()
