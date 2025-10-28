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
        self.min_purchase_score = self.config.MIN_PURCHASE_SCORE
        self.logger.info(f"Trailing stop loss {'enabled' if self.stop_loss_enabled else 'disabled'}")
        self.logger.info(f"Stop loss threshold: {self.stop_loss_percentage}% from recent {self.trailing_stop_hours}h high")
        self.logger.info(f"Minimum purchase score for USDT trading: {self.min_purchase_score}")

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
            self._scout_from_usdt(current_time=current_time)
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

    def _scout_from_usdt(self, current_time=None):
        """
        当前币种为USDT时的智能评估策略
        策略核心：
        1. 评估币种的潜在收益
        2. 检查币种是否处于上升趋势
        3. 避免购买刚刚止损的币种
        4. 只有当发现明确的机会时才购买
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
            best_score = 0
            coin_evaluations = []
            
            # 评估每个币种
            for coin in target_coins:
                try:
                    score, avg_ratio, is_last_trade = self._evaluate_coin_simple(coin, current_time)
                    if is_last_trade:
                        self.logger.debug(f"Skipping due to recent stop loss")
                        break  # 刚刚止损过，跳过
                    if score > 0:
                        coin_evaluations.append({
                            'coin': coin,
                            'score': score,
                            'avg_ratio': avg_ratio
                        })
                        
                        if score > best_score:
                            best_score = score
                            best_coin = coin
                except Exception as e:
                    self.logger.debug(f"Error evaluating coin {coin.symbol}: {e}")
                    continue

            if coin_evaluations:
                # 归一化avg_ratio
                max_avg_ratio = max(e['avg_ratio'] for e in coin_evaluations)
                for evaluation in coin_evaluations:
                    if max_avg_ratio > 0:
                        evaluation['normalized_avg_ratio'] = evaluation['avg_ratio'] / max_avg_ratio
                    else:
                        evaluation['normalized_avg_ratio'] = 0.0

            
            # 记录评估结果
            if coin_evaluations:
                self.logger.info(f"Evaluated {len(coin_evaluations)} coins from USDT")
                for evaluation in sorted(coin_evaluations, key=lambda x: x['score'], reverse=True)[:5]:
                    self.logger.info(f"  {evaluation['coin'].symbol}: score {evaluation['score']:.4f}, "
                                     f"normalized avg ratio {evaluation['normalized_avg_ratio']:.4f}")

            # 使用配置的最低购买分数
            min_purchase_score = self.min_purchase_score
            
            if best_coin and best_score >= min_purchase_score:
                # 使用normalized_avg_ratio最大的币种购买
                sorted_by_ratio = sorted(coin_evaluations, key=lambda x: x['normalized_avg_ratio'], reverse=True)
                best_coin = sorted_by_ratio[0]['coin']
                best_score = sorted_by_ratio[0]['score']

                self.logger.info(f"Decision: Buy {best_coin.symbol} (score: {best_score:.4f})")
                
                # 执行购买
                result = self.manager.buy_alt(best_coin, self.config.BRIDGE)
                if result:
                    self.db.set_current_coin(best_coin)
                    self.logger.info(f"Successfully bought {best_coin.symbol} from USDT")
                else:
                    self.logger.warning(f"Failed to buy {best_coin.symbol} from USDT")
            else:
                self.logger.info(f"Decision: Stay in USDT (best score: {best_score:.4f}, threshold: {min_purchase_score})")
                
        except Exception as e:
            self.logger.error(f"Error in USDT scouting: {e}")

    def _get_recent_price_points(self, coin, hours=24, current_time=None):
        """
        获取最近N小时的价格点（每小时一个点）
        返回价格列表，按时间从旧到新排序
        """
        return self.db.get_recent_price_points(coin, hours=hours, current_time=current_time)

    def _find_local_lows(self, price_points, window=3):
        """
        查找局部低点
        返回局部低点的索引列表
        """
        if len(price_points) < window * 2 + 1:
            return []
        
        local_lows = []
        
        for i in range(window, len(price_points) - window):
            # 检查当前点是否比前后window个点都低
            is_low = True
            current_price = price_points[i]
            
            for j in range(i - window, i + window + 1):
                if j != i and price_points[j] <= current_price:
                    is_low = False
                    break
            
            if is_low:
                local_lows.append(i)
        
        return local_lows

    def _check_trend_reversal(self, price_points, min_points=5):
        """
        检查趋势是否反转（下跌后开始上涨）
        返回信心评分 0-100
        """
        if len(price_points) < min_points:
            return 0.0
        
        try:
            # 取最近的N个点
            recent_points = price_points[-min_points:]
            
            # 计算连续上涨的次数
            upward_count = 0
            for i in range(1, len(recent_points)):
                if recent_points[i] > recent_points[i-1]:
                    upward_count += 1
            
            # 如果最近几个点持续上涨，说明可能反转
            upward_ratio = upward_count / (len(recent_points) - 1)
            
            # 检查是否有明显的V型反转
            # 找到最低点
            min_idx = recent_points.index(min(recent_points))
            
            # 如果最低点在中间附近，且之后持续上涨
            if min_idx > 0 and min_idx < len(recent_points) - 1:
                # 检查最低点之后的上涨
                after_low = recent_points[min_idx:]
                if len(after_low) >= 2:
                    after_upward = sum(1 for i in range(1, len(after_low)) if after_low[i] > after_low[i-1])
                    after_ratio = after_upward / (len(after_low) - 1)
                    
                    # V型反转信心评分
                    v_shape_score = after_ratio * 100
                    
                    # 综合评分
                    return min(100.0, (upward_ratio * 50 + v_shape_score * 0.5))
            
            # 否则只返回上涨趋势评分
            return upward_ratio * 80
            
        except Exception as e:
            self.logger.error(f"Error checking trend reversal: {e}")
            return 0.0

    def _is_downtrend_ending(self, coin, current_time=None):
        """
        判断币种的下跌是否已经结束
        返回一个信心评分 0-100，分数越高表示下跌结束的可能性越大
        
        评分标准：
        1. 价格反弹幅度（40分）：从最低点反弹的程度
        2. 价格位置（30分）：当前价格在区间中的位置
        3. 趋势反转信号（30分）：是否有明显的反转迹象
        
        总分>=50: 下跌可能已经结束
        总分>=70: 下跌很可能已经结束
        总分>=85: 下跌基本确认结束
        """
        try:
            score = 0.0
            
            # 1. 获取当前价格
            current_price = self.manager.get_ticker_price(coin + self.config.BRIDGE)
            if current_price is None:
                return 0.0
            
            # 2. 获取最近24小时的价格点
            price_points = self._get_recent_price_points(coin, hours=24, current_time=current_time)
            if len(price_points) < 5:
                return 0.0  # 数据不足
            
            # 3. 获取最高价和最低价
            recent_high = self._get_recent_high_price(coin, current_time=current_time)
            recent_low = self._get_recent_low_price(coin, current_time=current_time)
            
            if not recent_high or not recent_low:
                return 0.0
            
            # 4. 评估价格反弹幅度（40分）
            if recent_high > recent_low:
                price_range = recent_high - recent_low
                rebound = current_price - recent_low
                rebound_ratio = (rebound / price_range) * 100
                
                # 反弹越多，分数越高
                if rebound_ratio >= 50:
                    score += 40.0  # 已经反弹50%以上
                elif rebound_ratio >= 30:
                    score += 30.0  # 反弹30-50%
                elif rebound_ratio >= 15:
                    score += 20.0  # 反弹15-30%
                elif rebound_ratio >= 5:
                    score += 10.0  # 反弹5-15%
                # 否则不加分
            
            # 5. 评估价格位置（30分）
            # 如果价格接近最低点，说明可能还在下跌
            # 如果价格已经明显离开最低点，说明可能见底
            price_from_low_ratio = ((current_price - recent_low) / recent_low) * 100
            
            if price_from_low_ratio >= 20:
                score += 30.0  # 已经远离最低点20%以上
            elif price_from_low_ratio >= 10:
                score += 20.0  # 离开最低点10-20%
            elif price_from_low_ratio >= 5:
                score += 10.0  # 离开最低点5-10%
            # 否则不加分（还在最低点附近）
            
            # 6. 评估趋势反转（30分）
            reversal_score = self._check_trend_reversal(price_points)
            score += (reversal_score / 100) * 30.0
            
            # 7. 查找局部低点
            local_lows = self._find_local_lows(price_points)
            if local_lows:
                # 如果最近出现了局部低点，且之后价格上涨
                last_low_idx = local_lows[-1]
                if last_low_idx < len(price_points) - 2:
                    # 检查低点之后的价格走势
                    after_low_prices = price_points[last_low_idx:]
                    if len(after_low_prices) >= 2:
                        # 如果低点之后持续上涨，增加信心
                        upward_after_low = sum(1 for i in range(1, len(after_low_prices)) 
                                              if after_low_prices[i] > after_low_prices[i-1])
                        if upward_after_low == len(after_low_prices) - 1:
                            score += 10.0  # 奖励持续上涨
            
            return min(100.0, score)
            
        except Exception as e:
            self.logger.error(f"Error checking downtrend ending for {coin.symbol}: {e}")
            return 0.0

    def _evaluate_coin_simple(self, coin, current_time=None):
        """
        简单的币种评估算法（参考最高价和最低价）
        返回一个综合评分，分数越高表示购买价值越大
        """
        try:
            score = 0.0

            weight = [0.0, 0.0, 0.7, 0.0]  # 潜在收益、价格位置、下跌结束检测、止损历史的权重分配
            
            # 1. 获取当前价格
            current_price = self.manager.get_ticker_price(coin + self.config.BRIDGE)
            if current_price is None:
                return 0.0
            
            # 2. 评估潜在收益（基于ratio）- 权重：0%
            ratio_dict = self._get_ratios(coin, current_price)
            positive_ratios = [ratio for ratio in ratio_dict.values() if ratio > 0]
            
            if positive_ratios:
                avg_ratio = sum(positive_ratios) / len(positive_ratios)
                # 潜在收益分数
                # score += avg_ratio * 100 * weight[0]
            else:
                # 如果没有正收益，直接返回0
                return 0.0, 0.0, False
            
            # 3. 获取最高价和最低价
            recent_high = self._get_recent_high_price(coin, current_time=current_time)
            recent_low = self._get_recent_low_price(coin, current_time=current_time)
            
            # 4. 评估价格位置 - 权重：70%
            if recent_high and recent_low and recent_high > recent_low:
                # 计算价格在最高和最低之间的位置（0-100%）
                price_range = recent_high - recent_low
                price_from_low = current_price - recent_low
                price_position_in_range = (price_from_low / price_range) * 100
                
                # 同时计算相对最高价的位置
                price_position_from_high = (current_price / recent_high) * 100
                
                # 综合评分：
                # - 如果价格接近最低点（0-30%），说明处于底部区域，潜力大
                # - 如果价格在中间区域（30-70%），中等潜力
                # - 如果价格接近最高点（70-100%），但还在上升，也给予较高分数
                
                if price_position_in_range <= 30:
                    # 接近最低点，可能是买入好时机
                    score += 50.0 * weight[1]
                    self.logger.debug(f"{coin.symbol} near low point ({price_position_in_range:.1f}% from low)")
                elif price_position_in_range <= 50:
                    # 在低位区域
                    score += 35.0 * weight[1]
                elif price_position_in_range <= 70:
                    # 中间区域
                    score += 20.0 * weight[1]
                else:
                    # 接近最高点，需要结合趋势判断
                    if price_position_from_high >= 95:
                        # 仍在强势上涨
                        score += 30.0 * weight[1]
                    elif price_position_from_high >= 90:
                        score += 15.0 * weight[1]
                    else:
                        # 已经开始下跌
                        score += 5.0 * weight[1]
                
                # 记录详细的价格分析信息
                self.logger.debug(
                    f"{coin.symbol} price analysis: "
                    f"Current={current_price:.8f}, "
                    f"High={recent_high:.8f}, "
                    f"Low={recent_low:.8f}, "
                    f"Position in range={price_position_in_range:.1f}%"
                )
                
            elif recent_high:
                # 只有最高价数据，使用原有逻辑
                price_position = (current_price / recent_high) * 100
                
                if price_position >= 95:
                    score += 40.0 * weight[1]
                elif price_position >= 90:
                    score += 25.0 * weight[1]
                elif price_position >= 85:
                    score += 10.0 * weight[1]
                elif price_position < 80:
                    score -= 15.0 * weight[1]
            
            # 5. 检查下跌是否结束 - 权重：20%
            downtrend_ending_score = self._is_downtrend_ending(coin, current_time=current_time)
            if downtrend_ending_score >= 60:
                # 下跌可能已经结束，给予奖励分数
                score += (downtrend_ending_score / 100) * 20 * weight[2]
                self.logger.debug(f"{coin.symbol} downtrend may be ending (confidence: {downtrend_ending_score:.1f})")
            elif downtrend_ending_score > 10:
                # 有一定信号，但不够强
                score += (downtrend_ending_score / 100) * 10 * weight[2]
            else:
                # 否则扣分
                score -= 40
            
            # 6. 检查是否刚刚止损过这个币种 - 权重：10%
            last_trade = self.db.get_latest_sell_trade(coin=coin)
            # TODO 生产环境换成1小时（3600秒）
            if last_trade and datetime.utcnow().timestamp() - last_trade.datetime.timestamp() < 5:
                score -= 40.0
                self.logger.debug(f"{coin.symbol} recently sold, reducing score")
                is_last_trade = True
            else:
                is_last_trade = False
            # elif not last_trade:
            #     score -= 40.0

            return max(0.0, score), avg_ratio, is_last_trade  # 确保分数不为负

        except Exception as e:
            self.logger.error(f"Error evaluating coin {coin.symbol}: {e}")
            return 0.0

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
            # self.logger.info(price_drop_percentage)

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
            high_price_from_values = self.db.get_coin_high_price_from_values(current_coin, self.trailing_stop_hours, current_time=current_time)
            
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

    def _get_recent_low_price(self, current_coin, current_time=None):
        """
        获取最近N小时的最低价
        """
        try:
            # 方法1: 从CoinValue表获取最近的最低价
            low_price_from_values = self.db.get_coin_low_price_from_values(current_coin, self.trailing_stop_hours, current_time=current_time)
            
            # 方法2: 从侦察历史获取最近的最低价
            low_price_from_scout = self.db.get_coin_recent_low_price(current_coin, self.trailing_stop_hours, current_time=current_time)

            # 取两者中的较小值
            if low_price_from_values and low_price_from_scout:
                return min(low_price_from_values, low_price_from_scout)
            elif low_price_from_values:
                return low_price_from_values
            elif low_price_from_scout:
                return low_price_from_scout
            else:
                # 如果没有历史数据，使用当前价格作为参考
                current_price = self.manager.get_ticker_price(current_coin + self.config.BRIDGE)
                self.logger.warning(f"No low price history for {current_coin.symbol}, using current price as reference")
                return current_price
                
        except Exception as e:
            self.logger.error(f"Error getting recent low price: {e}")
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
