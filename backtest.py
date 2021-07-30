from datetime import datetime
import time

from matplotlib import pyplot as plt
import numpy as np

from binance_trade_bot import backtest

if __name__ == "__main__":
    history = []
    diff = []
    start = datetime(2021, 6, 10, 20, 0)
    end = datetime(2021, 6, 26, 23, 50)
    for manager in backtest(start, end):
        # time.sleep(1)
        # btc_value = manager.collate_coins("BTC")
        bridge_value = manager.collate_coins(manager.config.BRIDGE.symbol)
        # history.append((btc_value, bridge_value))
        history.append((bridge_value))
        # btc_diff = round((btc_value - history[0][0]) / history[0][0], 3)
        # bridge_diff = round((bridge_value - history[0][1]) / history[0][1], 3)
        bridge_diff = round((bridge_value - history[0]) / history[0], 3) * 100
        diff.append(bridge_diff)
        print("------")
        print("TIME:", manager.datetime)
        print("BALANCES:", manager.balances)
        # print("BTC VALUE:", btc_value, f"({btc_diff}%)")
        print(f"{manager.config.BRIDGE.symbol} VALUE:", bridge_value, f"({bridge_diff}%)")
        print("------")
        # time.sleep(3)

    ts = round((end-start).days + (end-start).seconds / 3600 / 24, 2)

    t = np.linspace(0, ts, len(diff))

    fig = plt.figure()
    ax1 = fig.add_subplot(111)
    ax1.set(xlim=[0, round(ts)+1], ylim=[min(diff)-10, max(diff)+10], title='res', ylabel='rate', xlabel='days')
    ax1.plot(t, diff)
    plt.savefig('./data/test_test1.png', dpi=300)
    plt.show()
    # print(111)
