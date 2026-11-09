ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Preparation can download dependencies; inference and grading use network=none.
RUN cd /testbed && git diff > /tmp/lucene-baseline.diff \
    && BASH_ENV=/dev/null ./gradlew --no-daemon --max-workers=2 \
    -Dorg.gradle.jvmargs=-Xmx1536m \
    :lucene:queries:test --tests org.apache.lucene.queries.intervals.TestIntervalBuilder
RUN cd /testbed && git diff > /tmp/lucene-after.diff \
    && cmp /tmp/lucene-baseline.diff /tmp/lucene-after.diff \
    && rm /tmp/lucene-baseline.diff /tmp/lucene-after.diff

# The official harness invokes the root test task, resolving every module's classpath.
RUN cd /testbed && git diff > /tmp/lucene-baseline.diff \
    && BASH_ENV=/dev/null ./gradlew --no-daemon --max-workers=2 \
    -Dorg.gradle.jvmargs=-Xmx1536m \
    test --tests org.apache.lucene.queries.intervals.TestIntervalBuilder \
    && git diff > /tmp/lucene-after.diff \
    && cmp /tmp/lucene-baseline.diff /tmp/lucene-after.diff \
    && rm /tmp/lucene-baseline.diff /tmp/lucene-after.diff
