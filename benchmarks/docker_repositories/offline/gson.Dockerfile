ARG BASE_IMAGE=swebench/sweb.eval.x86_64.google_1776_gson-1014:latest
FROM ${BASE_IMAGE}
RUN cd /testbed \
    && mvnd test -B -T 1 -pl gson -Dtest=com.google.gson.stream.JsonReaderPathTest \
    && git diff --exit-code \
    && mvnd --stop
