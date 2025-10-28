import random
import sys
from datetime import datetime

from binance_trade_bot.auto_trader import AutoTrader


class Strategy(AutoTrader):
    """
    趋势跟踪策略 - 基于看涨/看跌分析
    
    核心思想：
    1. 判断市场趋势（看涨/看跌/震荡）
    2. 在看涨时持有或买入强势币种
    3. 在看跌时卖出并持有USDT
    4. 震荡时保持当前状态或小幅调仓
    """
    
    def initialize(self):
        super().initialize()
        self.initialize_current_coin()
        
        # 趋势分析配置
        self.trend_window_hours = getattr(self.config, 'TREND_WINDOW_HOURS', 12)  # 趋势判断窗口
        self.bullish_threshold = getattr(self.config, 'BULLISH_THRESHOLD', 60.0)  # 看涨阈值
        self.bearish_threshold = getattr(self.config, 'BEARISH_THRESHOLD', 40.0)  # 看跌阈值
        
        self.logger.info("Trend strategy initialized")
        self.logger.info(f"Trend window: {self.trend_window_hours}h")
        self.logger.info(f"Bullish threshold: {self.bullish_threshold}, Bearish threshold: {self.bearish_threshold}")

    def scout(self, *args, **kwargs):
        """
        主扫描逻辑
        """
        current_time = kwargs.get("current_time", None)
        current_coin = self.db.get_current_coin()
        
        print(
            f"{datetime.now()} - CONSOLE - INFO - Analyzing market trends. "
            f"Current coin: {current_coin + self.config.BRIDGE} ",
            end="\r",
        )

        # 如果当前是USDT，分析是否应该买入
        if current_coin.symbol == "USDT":
            self._scout_from_usdt(current_time=current_time)
            return

        # 获取当前币种价格
        current_coin_price = self.manager.get_ticker_price(current_coin + self.config.BRIDGE)
        if current_coin_price is None:
            self.logger.info(f"Skipping scouting... current coin {current_coin + self.config.BRIDGE} not found")
            return

        # 分析当前币种的趋势
        market_sentiment = self._analyze_market_sentiment(current_coin, current_coin_price, current_time)
        
        print(
            f"{datetime.now()} - CONSOLE - INFO - Analyzing market trends. "
            f"Current coin: {current_coin + self.config.BRIDGE} ",
            f"sentiment: {market_sentiment['trend']} "
            f"(score: {market_sentiment['score']:.1f})",
            end="\r"
        )

        # 根据趋势决策
        if market_sentiment['trend'] == 'BEARISH':
            # 看跌：卖出转USDT
            self.logger.info(f"Bearish trend detected for {current_coin.symbol}, selling to USDT")
            self._sell_to_usdt(current_coin)
        elif market_sentiment['trend'] == 'BULLISH':
            # 看涨：寻找更好的币种或持有
            self._find_better_coin(current_coin, current_coin_price, current_time)
        else:
            # 震荡：保持当前持仓，但可以考虑切换到更强的币种
            self.logger.debug(f"Neutral trend for {current_coin.symbol}, checking for better options")
            self._find_better_coin(current_coin, current_coin_price, current_time)

    def _analyze_market_sentiment(self, coin, current_price, current_time=None) -> dict:
        """
        分析市场情绪（看涨/看跌/震荡）
        
        返回格式：
        {
            'trend': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
            'score': 0-100 (越高越看涨)
        }
        """
        try:
            score = 50.0  # 基准分数（中性）
            
            # 1. 价格趋势分析 (权重: 40%)
            price_trend_score = self._analyze_price_trend(coin, current_price, current_time)
            score += (price_trend_score - 50) * 0.4
            
            # 2. 动量分析 (权重: 30%)
            momentum_score = self._analyze_momentum(coin, current_time)
            score += (momentum_score - 50) * 0.3
            
            # 3. 价格位置分析 (权重: 30%)
            position_score = self._analyze_price_position(coin, current_price, current_time)
            score += (position_score - 50) * 0.3
            
            # 确定趋势
            if score >= self.bullish_threshold:
                trend = 'BULLISH'
            elif score <= self.bearish_threshold:
                trend = 'BEARISH'
            else:
                trend = 'NEUTRAL'
            
            return {
                'trend': trend,
                'score': score
            }
            
        except Exception as e:
            self.logger.error(f"Error analyzing market sentiment: {e}")
            return {'trend': 'NEUTRAL', 'score': 50.0}

    def _analyze_price_trend(self, coin, current_price, current_time=None) -> float:
        """
        分析价格趋势
        返回 0-100 的分数，50 为中性，>50 看涨，<50 看跌
        """
        try:
            # 获取最近的价格点
            price_points = self.db.get_recent_price_points(coin, hours=self.trend_window_hours, current_time=current_time)
            
            if len(price_points) < 3:
                return 50.0  # 数据不足，返回中性
            
            score = 50.0
            
            # 1. 计算价格变化率
            start_price = price_points[0]
            end_price = price_points[-1]
            price_change_pct = ((end_price - start_price) / start_price) * 100
            
            # 根据涨跌幅度评分
            if price_change_pct > 10:
                score = 90.0  # 大幅上涨
            elif price_change_pct > 5:
                score = 75.0  # 中等上涨
            elif price_change_pct > 2:
                score = 65.0  # 温和上涨
            elif price_change_pct > -2:
                score = 50.0  # 横盘
            elif price_change_pct > -5:
                score = 35.0  # 温和下跌
            elif price_change_pct > -10:
                score = 25.0  # 中等下跌
            else:
                score = 10.0  # 大幅下跌
            
            # 2. 检查连续上涨/下跌
            consecutive_ups = 0
            consecutive_downs = 0
            
            for i in range(1, len(price_points)):
                if price_points[i] > price_points[i-1]:
                    consecutive_ups += 1
                    consecutive_downs = 0
                elif price_points[i] < price_points[i-1]:
                    consecutive_downs += 1
                    consecutive_ups = 0
            
            # 连续上涨增加看涨分数
            if consecutive_ups >= 3:
                score += 10.0
            elif consecutive_ups >= 2:
                score += 5.0
            
            # 连续下跌降低分数
            if consecutive_downs >= 3:
                score -= 10.0
            elif consecutive_downs >= 2:
                score -= 5.0
            
            return max(0.0, min(100.0, score))
            
        except Exception as e:
            self.logger.error(f"Error analyzing price trend: {e}")
            return 50.0

    def _analyze_momentum(self, coin, current_time=None) -> float:
        """
        分析价格动量（加速度）
        返回 0-100 的分数
        """
        try:
            price_points = self.db.get_recent_price_points(coin, hours=self.trend_window_hours, current_time=current_time)
            
            if len(price_points) < 4:
                return 50.0
            
            # 计算最近一段时间的涨跌幅
            recent_half = len(price_points) // 2
            earlier_change = ((price_points[recent_half] - price_points[0]) / price_points[0]) * 100
            recent_change = ((price_points[-1] - price_points[recent_half]) / price_points[recent_half]) * 100
            
            # 动量 = 最近的涨跌幅 - 早期的涨跌幅
            momentum = recent_change - earlier_change
            
            # 评分 - 考虑绝对值和方向
            if momentum > 8:
                return 90.0  # 强烈加速上涨
            elif momentum > 3:
                return 75.0  # 加速上涨
            elif momentum > 0:
                return 60.0  # 轻微加速
            elif momentum > -4:
                return 40.0  # 轻微减速
            elif momentum > -10:
                return 25.0  # 明显减速或加速下跌
            else:
                return 10.0  # 强烈加速下跌
                
        except Exception as e:
            self.logger.error(f"Error analyzing momentum: {e}")
            return 50.0

    def _analyze_price_position(self, coin, current_price, current_time=None) -> float:
        """
        分析价格在区间中的位置
        返回 0-100 的分数
        """
        try:
            recent_high = self.db.get_coin_recent_high_price(coin, hours=self.trend_window_hours, current_time=current_time)
            recent_low = self.db.get_coin_recent_low_price(coin, hours=self.trend_window_hours, current_time=current_time)
            
            if not recent_high or not recent_low or recent_high == recent_low:
                return 50.0
            
            # 计算价格在区间中的位置（0-100%）
            position = ((current_price - recent_low) / (recent_high - recent_low)) * 100
            
            # 价格位置评分逻辑：
            # - 底部区域（0-20%）：最佳买入机会，反弹潜力大
            # - 低位区域（20-40%）：良好买入机会
            # - 中间区域（40-60%）：中性
            # - 高位区域（60-80%）：谨慎，可能回调
            # - 顶部区域（80-100%）：风险较高，但强势币种可能突破
            
            if position < 15:
                return 85.0  # 极低位，绝佳买入机会
            elif position < 30:
                return 75.0  # 低位，良好买入机会
            elif position < 45:
                return 65.0  # 中低位，较好机会
            elif position < 55:
                return 50.0  # 中间位置
            elif position < 70:
                return 35.0  # 中高位，谨慎
            elif position < 85:
                return 25.0  # 高位，风险增加
            else:
                return 40.0  # 极高位，可能突破或回调
                
        except Exception as e:
            self.logger.error(f"Error analyzing price position: {e}")
            return 50.0

    def _scout_from_usdt(self, current_time=None):
        """
        持有USDT时，寻找看涨的币种买入
        """
        try:
            all_coins = self.db.get_coins(only_enabled=True)
            target_coins = [coin for coin in all_coins if coin.symbol != "USDT"]
            
            if not target_coins:
                self.logger.warning("No target coins available")
                return
            
            best_coin = None
            best_score = 0
            evaluations = []
            
            # 评估所有币种
            for coin in target_coins:
                try:
                    current_price = self.manager.get_ticker_price(coin + self.config.BRIDGE)
                    if current_price is None:
                        continue
                    
                    sentiment = self._analyze_market_sentiment(coin, current_price, current_time)
                    
                    if sentiment['trend'] == 'BULLISH':
                        evaluations.append({
                            'coin': coin,
                            'score': sentiment['score']
                        })
                        
                        if sentiment['score'] > best_score:
                            best_score = sentiment['score']
                            best_coin = coin
                            
                except Exception as e:
                    self.logger.debug(f"Error evaluating {coin.symbol}: {e}")
                    continue
            
            # 记录评估结果
            if evaluations:
                self.logger.info(f"Found {len(evaluations)} bullish coins")
                for eval in sorted(evaluations, key=lambda x: x['score'], reverse=True)[:5]:
                    self.logger.info(f"  {eval['coin'].symbol}: {eval['score']:.1f}")
            
            # 只在强烈看涨时买入
            if best_coin and best_score >= self.bullish_threshold + 10:  # 需要更强的信号
                self.logger.info(f"Buying {best_coin.symbol} (bullish score: {best_score:.1f})")
                result = self.manager.buy_alt(best_coin, self.config.BRIDGE)
                if result:
                    self.db.set_current_coin(best_coin)
                    self.logger.info(f"Successfully bought {best_coin.symbol}")
            else:
                self.logger.info(f"Staying in USDT (best score: {best_score:.1f})")
                
        except Exception as e:
            self.logger.error(f"Error in USDT scouting: {e}")

    def _find_better_coin(self, current_coin, current_coin_price, current_time=None):
        """
        寻找比当前币种更好的币种
        """
        try:
            all_coins = self.db.get_coins(only_enabled=True)
            current_sentiment = self._analyze_market_sentiment(current_coin, current_coin_price, current_time)
            
            best_coin = None
            best_score = current_sentiment['score']
            
            for coin in all_coins:
                if coin.symbol == current_coin.symbol or coin.symbol == "USDT":
                    continue
                
                try:
                    coin_price = self.manager.get_ticker_price(coin + self.config.BRIDGE)
                    if coin_price is None:
                        continue
                    
                    sentiment = self._analyze_market_sentiment(coin, coin_price, current_time)
                    
                    # 需要明显更好才切换（避免频繁交易）
                    if sentiment['score'] > best_score + 15:
                        best_score = sentiment['score']
                        best_coin = coin
                        
                except Exception:
                    continue
            
            # 切换到更好的币种
            if best_coin:
                self.logger.info(
                    f"Found better coin: {best_coin.symbol} "
                    f"(score: {best_score:.1f} vs current: {current_sentiment['score']:.1f})"
                )
                self._jump_to_best_coin(current_coin, current_coin_price)
            else:
                self.logger.debug(f"No better coin found, staying with {current_coin.symbol}")
                
        except Exception as e:
            self.logger.error(f"Error finding better coin: {e}")

    def _sell_to_usdt(self, current_coin):
        """
        卖出当前币种，转为USDT
        """
        try:
            balance = self.manager.get_currency_balance(current_coin.symbol)
            
            if balance <= 0:
                self.logger.info("No balance to sell")
                return False
            
            current_price = self.manager.get_ticker_price(current_coin + self.config.BRIDGE)
            min_notional = self.manager.get_min_notional(current_coin.symbol, self.config.BRIDGE.symbol)
            
            if balance * current_price < min_notional:
                self.logger.info("Balance too small to sell")
                return False
            
            # 卖出
            sell_result = self.manager.sell_alt(current_coin, self.config.BRIDGE)
            
            if sell_result is None:
                self.logger.error("Failed to sell")
                return False
            
            # 转为USDT
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
                        self.logger.info("Converted to USDT")
                    else:
                        self.logger.error("Failed to convert to USDT")
            else:
                usdt_coin = self.config.BRIDGE
                self.db.set_current_coin(usdt_coin)
                self.logger.info("Converted to USDT")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error selling to USDT: {e}")
            return False

    def bridge_scout(self):
        current_coin = self.db.get_current_coin()
        if self.manager.get_currency_balance(current_coin.symbol) > self.manager.get_min_notional(
            current_coin.symbol, self.config.BRIDGE.symbol
        ):
            return
        new_coin = super().bridge_scout()
        if new_coin is not None:
            self.db.set_current_coin(new_coin)

    def initialize_current_coin(self):
        """
        初始化当前币种
        """
        if self.db.get_current_coin() is None:
            current_coin_symbol = self.config.CURRENT_COIN_SYMBOL
            if not current_coin_symbol:
                current_coin_symbol = random.choice(self.config.SUPPORTED_COIN_LIST)

            self.logger.info(f"Setting initial coin to {current_coin_symbol}")

            if current_coin_symbol not in self.config.SUPPORTED_COIN_LIST:
                sys.exit("***\nERROR!\nSince there is no backup file, a proper coin name must be provided at init\n***")
            self.db.set_current_coin(current_coin_symbol)

            if self.config.CURRENT_COIN_SYMBOL == "":
                current_coin = self.db.get_current_coin()
                self.logger.info(f"Purchasing {current_coin} to begin trading")
                self.manager.buy_alt(current_coin, self.config.BRIDGE)
                self.logger.info("Ready to start trading")
