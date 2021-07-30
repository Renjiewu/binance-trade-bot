import random
import sys
from datetime import datetime
from typing import Dict

from binance_trade_bot.auto_trader import AutoTrader
from binance_trade_bot.models import Pair


class Strategy(AutoTrader):
    def initialize(self):
        super().initialize()
        self.initialize_current_coin()

    def scout(self):
        """
        Scout for potential jumps from the current coin to another coin
        """

        current_coin = self.db.get_current_coin()
        # Display on the console, the current coin+Bridge, so users can see *some* activity and not think the bot has
        # stopped. Not logging though to reduce log size.
        print(
            f"{datetime.now()} - CONSOLE - INFO - I am scouting the best trades. "
            f"Current coin: {current_coin + self.config.BRIDGE} ",
            end="\n",
        )

        current_coin_price = self.manager.get_ticker_price(current_coin + self.config.BRIDGE)

        if current_coin_price is None:
            self.logger.info("Skipping scouting... current coin {} not found".format(current_coin + self.config.BRIDGE))
            return

        self._jump_to_best_coin(current_coin, current_coin_price)

    def transaction_through_bridge(self, pair):
        """
        Jump from the source coin to the destination coin through bridge coin
        """
        if pair.from_coin.symbol == 'USDT' or pair.to_coin.symbol == 'USDT':
            # print(0)
            pass
        can_sell = False
        balance = self.manager.get_currency_balance(pair.from_coin.symbol)
        from_coin_price = self.manager.get_ticker_price(pair.from_coin + self.config.BRIDGE)

        if pair.from_coin.symbol != 'USDT' and balance and balance * from_coin_price > self.manager.get_min_notional(
                pair.from_coin, self.config.BRIDGE):
            can_sell = True
        else:
            self.logger.info("Skipping sell")

        if can_sell and self.manager.sell_alt(pair.from_coin, self.config.BRIDGE) is None:
            self.logger.info("Couldn't sell, going back to scouting mode...")
            return None

        if pair.to_coin.symbol != 'USDT':
            result = self.manager.buy_alt(pair.to_coin, self.config.BRIDGE)
        else:
            result = {"price": 1}

        if result is not None:
            self.db.set_current_coin(pair.to_coin)
            self.update_trade_threshold(pair.to_coin, result.price)
            return result

        self.logger.info("Couldn't buy, going back to scouting mode...")
        return None

    def bridge_scout(self):
        current_coin = self.db.get_current_coin()
        if self.manager.get_currency_balance(current_coin.symbol) > self.manager.get_min_notional(
                current_coin.symbol, self.config.BRIDGE.symbol
        ):
            # Only scout if we don't have enough of the current coin
            return
        new_coin = super().bridge_scout()
        if new_coin is not None:
            self.db.set_current_coin(new_coin)

    def initialize_current_coin(self):
        """
        Decide what is the current coin, and set it up in the DB.
        """
        if self.db.get_current_coin() is None:
            current_coin_symbol = self.config.CURRENT_COIN_SYMBOL
            if not current_coin_symbol:
                current_coin_symbol = random.choice(self.config.SUPPORTED_COIN_LIST)

            self.logger.info(f"Setting initial coin to {current_coin_symbol}")

            if current_coin_symbol not in self.config.SUPPORTED_COIN_LIST:
                sys.exit("***\nERROR!\nSince there is no backup file, a proper coin name must be provided at init\n***")
            self.db.set_current_coin(current_coin_symbol)

            # if we don't have a configuration, we selected a coin at random... Buy it so we can start trading.
            if self.config.CURRENT_COIN_SYMBOL == "":
                current_coin = self.db.get_current_coin()
                self.logger.info(f"Purchasing {current_coin} to begin trading")
                self.manager.buy_alt(current_coin, self.config.BRIDGE)
                self.logger.info("Ready to start trading")

    def _get_ratios(self, coin, coin_price: float):
        """
        Given a coin, get the current price ratio for every other enabled coin
        """
        ratio_dict: Dict[Pair, float] = {}

        if coin.symbol == 'USDT':
            cmt = self.config.SCOUT_MULTIPLIER * 1.4
        else:
            cmt = self.config.SCOUT_MULTIPLIER

        for pair in self.db.get_pairs_from(coin):
            if pair.to_coin.symbol == 'USDT':
                optional_coin_price = 1
                fee_out = 0
            else:
                optional_coin_price = self.manager.get_ticker_price(pair.to_coin + self.config.BRIDGE)
                fee_out = self.manager.get_fee(pair.to_coin, self.config.BRIDGE, False)

            if optional_coin_price is None:
                self.logger.info(
                    "Skipping scouting... optional coin {} not found".format(pair.to_coin + self.config.BRIDGE)
                )
                continue

            self.db.log_scout(pair, pair.ratio, coin_price, optional_coin_price)

            # Obtain (current coin)/(optional coin)
            coin_opt_coin_ratio = coin_price / optional_coin_price

            transaction_fee = self.manager.get_fee(pair.from_coin, self.config.BRIDGE, True) + fee_out
            if pair.to_coin.symbol == 'USDT':
                # optional_coin_price = coin_price
                mt = -0.001 / transaction_fee
            else:
                mt = cmt

            ratio_dict[pair] = coin_opt_coin_ratio * (1 - transaction_fee * mt) - pair.ratio

        return ratio_dict

