FROM golang:1.24.5-alpine3.22 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . ./
RUN go test ./... && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /route-injector .

FROM alpine:3.22.1
RUN apk add --no-cache iproute2
COPY --from=build /route-injector /usr/local/bin/route-injector
ENTRYPOINT ["/usr/local/bin/route-injector"]
