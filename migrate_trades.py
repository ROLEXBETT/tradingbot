import json
from pathlib import Path

OPEN_TRADES_FILE = "open_trades.json"
BACKUP_FILE = "open_trades_backup.json"

def migrate_trade(trade):
    """Convert old trade format to new format"""
    
    # Create a backup of original trade
    migrated = trade.copy()
    
    # Add missing fields for new format
    entry = trade.get("entry", trade.get("entry_price", 0))
    
    # Add entry range (entry ± 1%)
    entry_range_percent = 0.01
    migrated["entry_low"] = entry * (1 - entry_range_percent)
    migrated["entry_high"] = entry * (1 + entry_range_percent)
    
    # Add tp_levels (categorized by trade style)
    trade_style = trade.get("trade_style", "day")
    stop_distance = abs(trade["stop_loss"] - entry)
    
    # Recreate TP levels based on trade style
    if trade_style == "scalping":
        multipliers = [0.3, 0.6, 1.0]
        tp_levels = {
            "scalping": [],
            "day_trading": [],
            "swing_trading": []
        }
        if trade["side"] == "SHORT":
            for m in multipliers:
                tp_levels["scalping"].append(entry - (stop_distance * m))
        else:
            for m in multipliers:
                tp_levels["scalping"].append(entry + (stop_distance * m))
    elif trade_style == "day":
        multipliers = [1.2, 1.8, 2.5]
        tp_levels = {
            "scalping": [],
            "day_trading": [],
            "swing_trading": []
        }
        if trade["side"] == "SHORT":
            for m in multipliers:
                tp_levels["day_trading"].append(entry - (stop_distance * m))
        else:
            for m in multipliers:
                tp_levels["day_trading"].append(entry + (stop_distance * m))
    else:  # swing
        multipliers = [3.0, 4.5]
        tp_levels = {
            "scalping": [],
            "day_trading": [],
            "swing_trading": []
        }
        if trade["side"] == "SHORT":
            for m in multipliers:
                tp_levels["swing_trading"].append(entry - (stop_distance * m))
        else:
            for m in multipliers:
                tp_levels["swing_trading"].append(entry + (stop_distance * m))
    
    migrated["tp_levels"] = tp_levels
    
    # Add market regime (default to UNKNOWN for old trades)
    migrated["market_regime"] = "UNKNOWN"
    
    # Ensure targets list exists
    if "targets" not in migrated or not migrated["targets"]:
        all_targets = []
        for style_tps in tp_levels.values():
            all_targets.extend(style_tps)
        migrated["targets"] = all_targets
    
    # Ensure proximity_alerts exists
    if "proximity_alerts" not in migrated:
        migrated["proximity_alerts"] = []
    
    # Ensure stop_proximity_notified exists
    if "stop_proximity_notified" not in migrated:
        migrated["stop_proximity_notified"] = False
    
    return migrated


def main():
    # Check if open_trades.json exists
    if not Path(OPEN_TRADES_FILE).exists():
        print(f"{OPEN_TRADES_FILE} not found. Nothing to migrate.")
        return
    
    # Load existing trades
    with open(OPEN_TRADES_FILE, 'r') as f:
        trades = json.load(f)
    
    # Create backup
    with open(BACKUP_FILE, 'w') as f:
        json.dump(trades, f, indent=4)
    print(f"✅ Backup created: {BACKUP_FILE}")
    
    # Migrate each trade
    migrated_trades = []
    for trade in trades:
        if trade.get("status") == "OPEN":
            migrated = migrate_trade(trade)
            migrated_trades.append(migrated)
            print(f"✅ Migrated: {trade['symbol']} - {trade['side']}")
        else:
            migrated_trades.append(trade)
            print(f"⏭️ Skipped (closed): {trade['symbol']}")
    
    # Save migrated trades
    with open(OPEN_TRADES_FILE, 'w') as f:
        json.dump(migrated_trades, f, indent=4)
    
    print(f"\n✅ Migration complete! {len(migrated_trades)} trades updated.")
    print(f"📁 Updated file: {OPEN_TRADES_FILE}")
    print(f"💾 Backup saved as: {BACKUP_FILE}")


if __name__ == "__main__":
    main()
    