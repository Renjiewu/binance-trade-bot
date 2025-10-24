import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Optional, Union

from socketio import Client
from socketio.exceptions import ConnectionError as SocketIOConnectionError
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from .config import Config
from .logger import Logger
from .models import *  # pylint: disable=wildcard-import


class Database:
    def __init__(self, logger: Logger, config: Config, uri="sqlite:///data/crypto_trading.db"):
        self.logger = logger
        self.config = config
        self.engine = create_engine(uri)
        self.SessionMaker = sessionmaker(bind=self.engine)
        self.socketio_client = Client()

    def socketio_connect(self):
        if self.socketio_client.connected and self.socketio_client.namespaces:
            return True
        try:
            if not self.socketio_client.connected:
                self.socketio_client.connect("http://api:5123", namespaces=["/backend"])
            while not self.socketio_client.connected or not self.socketio_client.namespaces:
                time.sleep(0.1)
            return True
        except SocketIOConnectionError:
            return False

    @contextmanager
    def db_session(self):
        """
        Creates a context with an open SQLAlchemy session.
        """
        session: Session = scoped_session(self.SessionMaker)
        yield session
        session.commit()
        session.close()

    def set_coins(self, symbols: List[str]):
        session: Session

        # Add coins to the database and set them as enabled or not
        with self.db_session() as session:
            # For all the coins in the database, if the symbol no longer appears
            # in the config file, set the coin as disabled
            coins: List[Coin] = session.query(Coin).all()
            for coin in coins:
                if coin.symbol not in symbols:
                    coin.enabled = False

            # For all the symbols in the config file, add them to the database
            # if they don't exist
            for symbol in symbols:
                coin = next((coin for coin in coins if coin.symbol == symbol), None)
                if coin is None:
                    session.add(Coin(symbol))
                else:
                    coin.enabled = True

        # For all the combinations of coins in the database, add a pair to the database
        with self.db_session() as session:
            coins: List[Coin] = session.query(Coin).filter(Coin.enabled).all()
            for from_coin in coins:
                for to_coin in coins:
                    if from_coin != to_coin:
                        pair = session.query(Pair).filter(Pair.from_coin == from_coin, Pair.to_coin == to_coin).first()
                        if pair is None:
                            session.add(Pair(from_coin, to_coin))

    def get_coins(self, only_enabled=True) -> List[Coin]:
        session: Session
        with self.db_session() as session:
            if only_enabled:
                coins = session.query(Coin).filter(Coin.enabled).all()
            else:
                coins = session.query(Coin).all()
            session.expunge_all()
            return coins

    def get_coin(self, coin: Union[Coin, str]) -> Coin:
        if isinstance(coin, Coin):
            return coin
        session: Session
        with self.db_session() as session:
            coin = session.query(Coin).get(coin)
            session.expunge(coin)
            return coin

    def set_current_coin(self, coin: Union[Coin, str]):
        coin = self.get_coin(coin)
        session: Session
        with self.db_session() as session:
            if isinstance(coin, Coin):
                coin = session.merge(coin)
            cc = CurrentCoin(coin)
            session.add(cc)
            self.send_update(cc)

    def get_current_coin(self) -> Optional[Coin]:
        session: Session
        with self.db_session() as session:
            current_coin = session.query(CurrentCoin).order_by(CurrentCoin.datetime.desc()).first()
            if current_coin is None:
                return None
            coin = current_coin.coin
            session.expunge(coin)
            return coin

    def get_pair(self, from_coin: Union[Coin, str], to_coin: Union[Coin, str]):
        from_coin = self.get_coin(from_coin)
        to_coin = self.get_coin(to_coin)
        session: Session
        with self.db_session() as session:
            pair: Pair = session.query(Pair).filter(Pair.from_coin == from_coin, Pair.to_coin == to_coin).first()
            session.expunge(pair)
            return pair

    def get_pairs_from(self, from_coin: Union[Coin, str], only_enabled=True) -> List[Pair]:
        from_coin = self.get_coin(from_coin)
        session: Session
        with self.db_session() as session:
            pairs = session.query(Pair).filter(Pair.from_coin == from_coin)
            if only_enabled:
                pairs = pairs.filter(Pair.enabled.is_(True))
            pairs = pairs.all()
            session.expunge_all()
            return pairs

    def get_pairs(self, only_enabled=True) -> List[Pair]:
        session: Session
        with self.db_session() as session:
            pairs = session.query(Pair)
            if only_enabled:
                pairs = pairs.filter(Pair.enabled.is_(True))
            pairs = pairs.all()
            session.expunge_all()
            return pairs

    def get_latest_buy_trade(self, coin: Union[Coin, str]) -> Optional[Trade]:
        """
        Get the latest completed buy trade for a specific coin
        """
        coin = self.get_coin(coin)
        session: Session
        with self.db_session() as session:
            trade = (
                session.query(Trade)
                .filter(
                    Trade.alt_coin == coin,
                    Trade.selling.is_(False),
                    Trade.state == TradeState.COMPLETE
                )
                .order_by(Trade.datetime.desc())
                .first()
            )
            if trade:
                session.expunge(trade)
            return trade

    def get_coin_recent_high_price(self, coin: Union[Coin, str], hours: int = 24, current_time: Optional[datetime] = None) -> Optional[float]:
        """
        Get the highest price for a coin in recent hours from scout history
        """
        coin = self.get_coin(coin)
        session: Session
        with self.db_session() as session:
            time_diff = current_time - timedelta(hours=hours) if current_time else datetime.now() - timedelta(hours=hours)
            
            # 从侦察历史中获取最近的最高价
            pairs_from = session.query(Pair).filter(Pair.from_coin == coin).all()
            
            if not pairs_from:
                return None
                
            max_price = 0
            for pair in pairs_from:
                scout_records = (
                    session.query(ScoutHistory)
                    .filter(
                        ScoutHistory.pair == pair,
                        ScoutHistory.datetime >= time_diff
                    )
                    .all()
                )
                
                for record in scout_records:
                    if record.current_coin_price and record.current_coin_price > max_price:
                        max_price = record.current_coin_price
            
            return max_price if max_price > 0 else None

    def record_price_point(self, coin: Union[Coin, str], price: float, current_time: Optional[datetime] = None):
        """
        Record a price point for tracking high prices
        """
        coin = self.get_coin(coin)
        session: Session
        with self.db_session() as session:
            # 创建价格记录点用于追踪
            if isinstance(coin, Coin):
                coin = session.merge(coin)
            price_record = CoinValue(
                coin=coin,
                balance=1.0,  # 使用1.0作为基准
                usd_price=price,
                btc_price=price,  # 简化处理
                interval=Interval.MINUTELY,
                datetime=current_time or datetime.now()
            )
            session.add(price_record)
            # session.flush()
            self.send_update(price_record)
            # print(111)
            # session.commit()
        # print(2222)

    def get_coin_high_price_from_values(self, coin: Union[Coin, str], hours: int = 24, current_time: Optional[datetime] = None) -> Optional[float]:
        """
        Get the highest price for a coin from CoinValue records
        """
        coin = self.get_coin(coin)
        session: Session
        with self.db_session() as session:
            time_diff = current_time - timedelta(hours=hours) if current_time else datetime.now() - timedelta(hours=hours)
            
            # 直接查询所有记录然后在Python中找最大值
            coin_values = (
                session.query(CoinValue)
                .filter(CoinValue.coin == coin)
                .filter(CoinValue.datetime >= time_diff)
                .all()
            )
            
            if not coin_values:
                return None
                
            # 提取价格值并找最大值
            prices = [cv.usd_price for cv in coin_values if cv.usd_price is not None]
            
            return max(prices) if prices else None

    def clear_coin_values_before(self, before_time: datetime, coin: Union[Coin, str] = None, interval: Optional[str] = None) -> int:
        """
        Clear CoinValue records before a given time
        
        Args:
            before_time: Delete records older than this datetime
            coin: Optional - specific coin to clear (if None, clears all coins)
            interval: Optional - specific interval to clear (MINUTELY, HOURLY, DAILY, WEEKLY)
        
        Returns:
            Number of records deleted
        """
        session: Session
        with self.db_session() as session:
            query = session.query(CoinValue).filter(CoinValue.datetime < before_time)
            
            # 如果指定了币种，添加币种过滤
            if coin is not None:
                coin = self.get_coin(coin)
                query = query.filter(CoinValue.coin == coin)
            
            # 如果指定了时间间隔，添加间隔过滤
            if interval is not None:
                from .models.coin_value import Interval
                interval_enum = getattr(Interval, interval.upper(), None)
                if interval_enum:
                    query = query.filter(CoinValue.interval == interval_enum)
                else:
                    self.logger.warning(f"Invalid interval: {interval}")
                    return 0
            
            # 计算要删除的记录数
            count = query.count()
            
            if count > 0:
                # 执行删除
                query.delete()
                self.logger.info(f"Cleared {count} CoinValue records before {before_time}")
            else:
                self.logger.info("No CoinValue records found to clear")
            
            return count

    def clear_old_coin_values(self, days: int = 30, coin: Union[Coin, str] = None) -> int:
        """
        Clear CoinValue records older than specified days
        
        Args:
            days: Number of days to keep (delete records older than this)
            coin: Optional - specific coin to clear (if None, clears all coins)
        
        Returns:
            Number of records deleted
        """
        cutoff_time = datetime.now() - timedelta(days=days)
        return self.clear_coin_values_before(cutoff_time, coin)

    def clear_coin_values_by_interval(self, interval: str, keep_days: int) -> int:
        """
        Clear CoinValue records by specific interval and age
        
        Args:
            interval: Time interval (MINUTELY, HOURLY, DAILY, WEEKLY)
            keep_days: Number of days to keep for this interval
        
        Returns:
            Number of records deleted
        """
        cutoff_time = datetime.now() - timedelta(days=keep_days)
        return self.clear_coin_values_before(cutoff_time, interval=interval)

    def log_scout(
        self,
        pair: Pair,
        target_ratio: float,
        current_coin_price: float,
        other_coin_price: float,
    ):
        session: Session
        with self.db_session() as session:
            pair = session.merge(pair)
            sh = ScoutHistory(pair, target_ratio, current_coin_price, other_coin_price)
            session.add(sh)
            self.send_update(sh)

    def prune_scout_history(self):
        time_diff = datetime.now() - timedelta(hours=self.config.SCOUT_HISTORY_PRUNE_TIME)
        session: Session
        with self.db_session() as session:
            session.query(ScoutHistory).filter(ScoutHistory.datetime < time_diff).delete()

    def prune_value_history(self):
        session: Session
        with self.db_session() as session:
            # Sets the first entry for each coin for each hour as 'hourly'
            hourly_entries: List[CoinValue] = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.strftime("%H", CoinValue.datetime)).all()
            )
            for entry in hourly_entries:
                entry.interval = Interval.HOURLY

            # Sets the first entry for each coin for each day as 'daily'
            daily_entries: List[CoinValue] = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.date(CoinValue.datetime)).all()
            )
            for entry in daily_entries:
                entry.interval = Interval.DAILY

            # Sets the first entry for each coin for each month as 'weekly'
            # (Sunday is the start of the week)
            weekly_entries: List[CoinValue] = (
                session.query(CoinValue).group_by(CoinValue.coin_id, func.strftime("%Y-%W", CoinValue.datetime)).all()
            )
            for entry in weekly_entries:
                entry.interval = Interval.WEEKLY

            # The last 24 hours worth of minutely entries will be kept, so
            # count(coins) * 1440 entries
            time_diff = datetime.now() - timedelta(hours=24)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.MINUTELY, CoinValue.datetime < time_diff
            ).delete()

            # The last 28 days worth of hourly entries will be kept, so count(coins) * 672 entries
            time_diff = datetime.now() - timedelta(days=28)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.HOURLY, CoinValue.datetime < time_diff
            ).delete()

            # The last years worth of daily entries will be kept, so count(coins) * 365 entries
            time_diff = datetime.now() - timedelta(days=365)
            session.query(CoinValue).filter(
                CoinValue.interval == Interval.DAILY, CoinValue.datetime < time_diff
            ).delete()

            # All weekly entries will be kept forever

    def create_database(self):
        Base.metadata.create_all(self.engine)

    def start_trade_log(self, from_coin: Coin, to_coin: Coin, selling: bool):
        return TradeLog(self, from_coin, to_coin, selling)

    def send_update(self, model):
        if not self.socketio_connect():
            return

        self.socketio_client.emit(
            "update",
            {"table": model.__tablename__, "data": model.info()},
            namespace="/backend",
        )

    def migrate_old_state(self):
        """
        For migrating from old dotfile format to SQL db. This method should be removed in
        the future.
        """
        if os.path.isfile(".current_coin"):
            with open(".current_coin") as f:
                coin = f.read().strip()
                self.logger.info(f".current_coin file found, loading current coin {coin}")
                self.set_current_coin(coin)
            os.rename(".current_coin", ".current_coin.old")
            self.logger.info(f".current_coin renamed to .current_coin.old - You can now delete this file")

        if os.path.isfile(".current_coin_table"):
            with open(".current_coin_table") as f:
                self.logger.info(f".current_coin_table file found, loading into database")
                table: dict = json.load(f)
                session: Session
                with self.db_session() as session:
                    for from_coin, to_coin_dict in table.items():
                        for to_coin, ratio in to_coin_dict.items():
                            if from_coin == to_coin:
                                continue
                            pair = session.merge(self.get_pair(from_coin, to_coin))
                            pair.ratio = ratio
                            session.add(pair)

            os.rename(".current_coin_table", ".current_coin_table.old")
            self.logger.info(".current_coin_table renamed to .current_coin_table.old - " "You can now delete this file")


class TradeLog:
    def __init__(self, db: Database, from_coin: Coin, to_coin: Coin, selling: bool):
        self.db = db
        session: Session
        with self.db.db_session() as session:
            from_coin = session.merge(from_coin)
            to_coin = session.merge(to_coin)
            self.trade = Trade(from_coin, to_coin, selling)
            session.add(self.trade)
            # Flush so that SQLAlchemy fills in the id column
            session.flush()
            self.db.send_update(self.trade)

    def set_ordered(self, alt_starting_balance, crypto_starting_balance, alt_trade_amount):
        session: Session
        with self.db.db_session() as session:
            trade: Trade = session.merge(self.trade)
            trade.alt_starting_balance = alt_starting_balance
            trade.alt_trade_amount = alt_trade_amount
            trade.crypto_starting_balance = crypto_starting_balance
            trade.state = TradeState.ORDERED
            self.db.send_update(trade)

    def set_complete(self, crypto_trade_amount):
        session: Session
        with self.db.db_session() as session:
            trade: Trade = session.merge(self.trade)
            trade.crypto_trade_amount = crypto_trade_amount
            trade.state = TradeState.COMPLETE
            self.db.send_update(trade)


if __name__ == "__main__":
    database = Database(Logger(), Config())
    database.create_database()
