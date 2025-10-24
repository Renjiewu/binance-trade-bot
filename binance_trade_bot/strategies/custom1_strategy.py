import random
import sys
from datetime import datetime

from binance_trade_bot.auto_trader import AutoTrader


class Strategy(AutoTrader):
    def initialize(self):
        super().initialize()
        self.initialize_current_coin()
        # 从配置文件获取止损设置
        self.stop_loss_enabled = self.config.STOP_LOSS_ENABLED
        self.stop_loss_percentage = self.config.STOP_LOSS_PERCENTAGE
        self.trailing_stop_hours = self.config.TRAILING_STOP_HOURS
        self.logger.info(f"Trailing stop loss {'enabled' if self.stop_loss_enabled else 'disabled'}")
        self.logger.info(f"Stop loss threshold: {self.stop_loss_percentage}% from recent {self.trailing_stop_hours}h high")

    def scout(self, *args, **kwargs):
        """
        Scout for potential jumps from the current coin to another coin
        """
        current_time = kwargs.get("current_time", None)
        current_coin = self.db.get_current_coin()
        # Display on the console, the current coin+Bridge, so users can see *some* activity and not think the bot has
        # stopped. Not logging though to reduce log size.
        print(
            f"{datetime.now()} - CONSOLE - INFO - I am scouting the best trades. "
            f"Current coin: {current_coin + self.config.BRIDGE} ",
            end="\r",
        )

        

        # 如果当前币种是USDT，跳过价格获取和止损检查，直接进行币种选择
        if current_coin.symbol == "USDT":
            # self.logger.info("Current coin is USDT, skipping stop loss check and looking for best coin to buy")
            self._scout_from_usdt()
            return

        current_coin_price = self.manager.get_ticker_price(current_coin + self.config.BRIDGE)

        if current_coin_price is None:
            self.logger.info(f"Skipping scouting... current coin {current_coin + self.config.BRIDGE} not found")
            return

        # 记录当前价格点用于追踪最高价
        # self._record_price_point(current_coin, current_coin_price, current_time=current_time)

        # 检查追踪止损条件
        if self.stop_loss_enabled and self._check_trailing_stop_loss(current_coin, current_coin_price, current_time=current_time):
            return

        self._jump_to_best_coin(current_coin, current_coin_price)

    def _scout_from_usdt(self):
        """
        当前币种为USDT时的特殊处理逻辑
        """
        try:
            # 获取所有启用的币种
            all_coins = self.db.get_coins(only_enabled=True)
            
            # 排除USDT自己
            target_coins = [coin for coin in all_coins if coin.symbol != "USDT"]
            
            if not target_coins:
                self.logger.warning("No target coins available for USDT trading")
                return
            
            best_coin = None
            best_potential = 0
            
            # 评估每个币种的潜力
            for coin in target_coins:
                try:
                    coin_price = self.manager.get_ticker_price(coin + self.config.BRIDGE)
                    if coin_price is None:
                        continue
                    
                    # 获取该币种的比率字典来评估潜力
                    ratio_dict = self._get_ratios(coin, coin_price)
                    
                    # 计算平均正收益潜力
                    positive_ratios = [ratio for ratio in ratio_dict.values() if ratio > 0]
                    if positive_ratios:
                        avg_potential = sum(positive_ratios) / len(positive_ratios)
                        if avg_potential > best_potential:
                            best_potential = avg_potential
                            best_coin = coin
                    
                except Exception as e:
                    self.logger.debug(f"Error evaluating coin {coin.symbol}: {e}")
                    continue
            
            if best_coin and best_potential > 0:
                self.logger.info(f"USDT -> {best_coin.symbol}: potential {best_potential:.4f}")
                
                # 执行购买
                result = self.manager.buy_alt(best_coin, self.config.BRIDGE)
                if result:
                    self.db.set_current_coin(best_coin)
                    self.logger.info(f"Successfully bought {best_coin.symbol} from USDT")
                else:
                    self.logger.warning(f"Failed to buy {best_coin.symbol} from USDT")
            else:
                # self.logger.info("No profitable opportunities found from USDT, staying in USDT")
                print(111)
                
        except Exception as e:
            self.logger.error(f"Error in USDT scouting: {e}")

    def _record_price_point(self, current_coin, current_price, current_time=None):
        """
        记录当前价格点用于追踪最高价
        """
        try:
            # 如果当前币种是USDT，跳过价格记录
            if current_coin.symbol == "USDT":
                return

            self.db.record_price_point(current_coin, current_price, current_time=current_time)
        except Exception as e:
            self.logger.error(f"Error recording price point: {e}")

    def _check_trailing_stop_loss(self, current_coin, current_price, current_time=None):
        """
        检查追踪止损条件 - 基于最近最高价
        """
        try:
            # 如果当前币种是USDT，跳过止损检查
            if current_coin.symbol == "USDT":
                return False
            
            # 获取最近N小时的最高价
            recent_high_price = self._get_recent_high_price(current_coin, current_time=current_time)
            
            if recent_high_price is None:
                self.logger.debug(f"No recent high price data for {current_coin.symbol}")
                return False
                
            # 计算从最高价的跌幅百分比
            price_drop_percentage = ((recent_high_price - current_price) / recent_high_price) * 100
            
            # self.logger.info(
            #     f"Trailing stop check - Current: {current_price:.8f}, "
            #     f"Recent High ({self.trailing_stop_hours}h): {recent_high_price:.8f}, "
            #     f"Drop: {price_drop_percentage:.2f}%"
            # )
            
            # 如果跌幅超过止损百分比，执行止损
            if price_drop_percentage >= self.stop_loss_percentage:
                # self.logger.warning(
                #     f"Trailing stop loss triggered! {current_coin.symbol} dropped {price_drop_percentage:.2f}% "
                #     f"from recent high of {recent_high_price:.8f}"
                # )
                return self._execute_stop_loss(current_coin)
                
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking trailing stop loss: {e}")
            return False

    def _get_recent_high_price(self, current_coin, current_time=None):
        """
        获取最近N小时的最高价
        """
        try:
            # 方法1: 从CoinValue表获取最近的最高价
            high_price_from_values = self.db.get_coin_high_price_from_values(current_coin, self.trailing_stop_hours // 24 + 1, current_time=current_time)
            
            # 方法2: 从侦察历史获取最近的最高价
            high_price_from_scout = self.db.get_coin_recent_high_price(current_coin, self.trailing_stop_hours, current_time=current_time)

            # 取两者中的较大值，如果都没有则使用当前价格
            if high_price_from_values and high_price_from_scout:
                return max(high_price_from_values, high_price_from_scout)
            elif high_price_from_values:
                return high_price_from_values
            elif high_price_from_scout:
                return high_price_from_scout
            else:
                # 如果没有历史数据，使用当前价格作为参考
                current_price = self.manager.get_ticker_price(current_coin + self.config.BRIDGE)
                self.logger.warning(f"No high price history for {current_coin.symbol}, using current price as reference")
                return current_price
                
        except Exception as e:
            self.logger.error(f"Error getting recent high price: {e}")
            return None

    def _check_stop_loss(self, current_coin, current_price):
        """
        检查是否触发止损条件（保留原有方法以向后兼容）
        """
        return self._check_trailing_stop_loss(current_coin, current_price)

    def _get_entry_price(self, current_coin):
        """
        获取币种的买入价格
        """
        try:
            # 从交易历史获取最近一次买入该币种的价格
            latest_trade = self.db.get_latest_buy_trade(current_coin)
            
            if latest_trade and latest_trade.crypto_trade_amount and latest_trade.alt_trade_amount:
                # 计算买入价格: crypto_amount / alt_amount
                entry_price = latest_trade.crypto_trade_amount / latest_trade.alt_trade_amount
                return entry_price
                
        except Exception as e:
            self.logger.error(f"Error getting entry price: {e}")
            
        return None

    def _execute_stop_loss(self, current_coin):
        """
        执行止损操作：卖出当前币种，换成USDT
        """
        try:
            balance = self.manager.get_currency_balance(current_coin.symbol)
            
            if balance <= 0:
                self.logger.info("No balance to sell for stop loss")
                return False
                
            # 检查是否有足够的余额进行交易
            current_price = self.manager.get_ticker_price(current_coin + self.config.BRIDGE)
            min_notional = self.manager.get_min_notional(current_coin.symbol, self.config.BRIDGE.symbol)
            
            if balance * current_price < min_notional:
                self.logger.info("Balance too small for stop loss trade")
                return False
            
            # 执行止损：先卖成桥接币
            self.logger.info(f"Executing stop loss: selling {current_coin.symbol}")
            
            sell_result = self.manager.sell_alt(current_coin, self.config.BRIDGE)
            
            if sell_result is None:
                self.logger.error("Failed to sell coin for stop loss")
                return False
            
            # 如果桥接币不是USDT，则换成USDT
            if self.config.BRIDGE.symbol != "USDT":
                usdt_coin = None
                for coin in self.db.get_coins():
                    if coin.symbol == "USDT":
                        usdt_coin = coin
                        break
                
                if usdt_coin:
                    buy_result = self.manager.buy_alt(usdt_coin, self.config.BRIDGE)
                    if buy_result:
                        self.db.set_current_coin(usdt_coin)
                        self.logger.info("Stop loss completed: converted to USDT")
                    else:
                        self.logger.error("Failed to convert to USDT after stop loss")
                else:
                    self.logger.warning("USDT coin not found, staying in bridge coin")
            else:
                # 桥接币就是USDT，无需额外操作
                usdt_coin = self.config.BRIDGE
                self.db.set_current_coin(usdt_coin)
                self.logger.info("Stop loss completed: converted to USDT")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error executing stop loss: {e}")
            return False

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
