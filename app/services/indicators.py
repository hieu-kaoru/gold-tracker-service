"""Chỉ báo kỹ thuật trên chuỗi giá đóng cửa."""
import pandas as pd


def sma(closes: pd.Series, window: int) -> pd.Series:
    return closes.rolling(window).mean()


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """RSI theo cách làm mượt của Wilder."""
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss
    return (100 - 100 / (1 + rs)).where(loss != 0, 100.0).where(gain.notna())


def macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = closes.ewm(span=fast, adjust=False).mean() - closes.ewm(span=slow, adjust=False).mean()
    signal_line = line.ewm(span=signal, adjust=False).mean()
    return line, signal_line, line - signal_line
