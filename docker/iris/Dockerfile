ARG IRIS_IMAGE=containers.intersystems.com/intersystems/iris-community:2026.1
FROM ${IRIS_IMAGE}

COPY --chown=irisowner:irisowner seed /seed

# Start IRIS, create namespaces/users, load the seed code, stop IRIS.
RUN iris start IRIS \
 && iris session IRIS -U %SYS < /seed/seed.script \
 && iris stop IRIS quietly
