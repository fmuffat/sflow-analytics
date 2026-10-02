# syntax=docker/dockerfile:1
FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG VERSION=dev
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/collector ./cmd/collector \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/sflow-gen ./cmd/sflow-gen

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/collector /usr/local/bin/collector
COPY --from=build /out/sflow-gen /usr/local/bin/sflow-gen
COPY --from=build /src/tests/fixtures /fixtures
EXPOSE 6343/udp 8081/tcp
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD ["/usr/local/bin/collector", "-healthcheck"]
ENTRYPOINT ["/usr/local/bin/collector"]
