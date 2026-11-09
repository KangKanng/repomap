ARG BASE_IMAGE=swebench/sweb.eval.x86_64.tokio-rs_1776_tokio-4384:latest
FROM ${BASE_IMAGE}
RUN cd /testbed \
    && cargo fetch \
    && cargo metadata --offline --locked --format-version 1 > /dev/null \
    && git diff --exit-code
ENV CARGO_NET_OFFLINE=true
