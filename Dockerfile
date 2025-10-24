FROM --platform=$BUILDPLATFORM docker.1ms.run/python:3.13.2-bullseye AS builder

ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /install

RUN sed -i "s@http://\(deb\|security\).debian.org@https://mirrors.tencent.com@g" /etc/apt/sources.list && apt update && apt install -y rustc sqlite3

COPY requirements.txt /requirements.txt
RUN pip config set global.index-url "$PIP_INDEX" && \
    pip config set global.extra-index-url "$PIP_INDEX" && \
    pip install --prefix=/install -r /requirements.txt

FROM docker.1ms.run/python:3.13.2-slim-bullseye

WORKDIR /app

COPY --from=builder /install /usr/local
ENV TZ=Asia/Shanghai

COPY . .

CMD ["python", "-m", "binance_trade_bot"]
