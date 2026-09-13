package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"math"
	"net"
	"net/netip"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	api "github.com/osrg/gobgp/v4/api"
	"github.com/osrg/gobgp/v4/pkg/apiutil"
	"github.com/osrg/gobgp/v4/pkg/packet/bgp"
	"github.com/osrg/gobgp/v4/pkg/server"
)

const phaseCount = 8

type config struct {
	localAS     uint32
	peerAS      uint32
	routerID    string
	localIP     string
	peerIP      string
	prefixPool  netip.Prefix
	base        int
	amplitude   int
	period      time.Duration
	step        time.Duration
	phaseSlot   int
	maxPrefixes int
}

func env(name string) (string, error) {
	value := os.Getenv(name)
	if value == "" {
		return "", fmt.Errorf("%s is required", name)
	}
	return value, nil
}

func envInt(name string) (int, error) {
	value, err := env(name)
	if err != nil {
		return 0, err
	}
	n, err := strconv.Atoi(value)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", name, err)
	}
	return n, nil
}

func loadConfig() (config, error) {
	localAS, err := envInt("LOCAL_AS")
	if err != nil {
		return config{}, err
	}
	peerAS, err := envInt("PEER_AS")
	if err != nil {
		return config{}, err
	}
	routerID, err := env("ROUTER_ID")
	if err != nil {
		return config{}, err
	}
	localAddress, err := env("LOCAL_ADDRESS")
	if err != nil {
		return config{}, err
	}
	localPrefix, err := netip.ParsePrefix(localAddress)
	if err != nil || !localPrefix.Addr().Is4() {
		return config{}, fmt.Errorf("LOCAL_ADDRESS must be an IPv4 prefix: %q", localAddress)
	}
	peerIP, err := env("PEER_ADDRESS")
	if err != nil {
		return config{}, err
	}
	if address, parseErr := netip.ParseAddr(peerIP); parseErr != nil || !address.Is4() {
		return config{}, fmt.Errorf("PEER_ADDRESS must be IPv4: %q", peerIP)
	}
	poolValue, err := env("PREFIX_POOL")
	if err != nil {
		return config{}, err
	}
	pool, err := netip.ParsePrefix(poolValue)
	if err != nil || !pool.Addr().Is4() {
		return config{}, fmt.Errorf("PREFIX_POOL must be an IPv4 prefix: %q", poolValue)
	}
	base, err := envInt("BASE_PREFIXES")
	if err != nil {
		return config{}, err
	}
	amplitude, err := envInt("AMPLITUDE")
	if err != nil {
		return config{}, err
	}
	periodSeconds, err := envInt("PERIOD_SECONDS")
	if err != nil {
		return config{}, err
	}
	stepSeconds, err := envInt("STEP_SECONDS")
	if err != nil {
		return config{}, err
	}
	phaseSlot, err := envInt("PHASE_SLOT")
	if err != nil {
		return config{}, err
	}
	maxPrefixes := base + amplitude
	if localAS < 1 || peerAS < 1 || base < 1 || amplitude < 0 || base-amplitude < 1 {
		return config{}, errors.New("ASNs and prefix bounds must be positive")
	}
	if periodSeconds < 1 || stepSeconds < 1 || periodSeconds%stepSeconds != 0 {
		return config{}, errors.New("PERIOD_SECONDS must be positive and divisible by STEP_SECONDS")
	}
	if phaseSlot < 0 || phaseSlot >= phaseCount {
		return config{}, fmt.Errorf("PHASE_SLOT must be between 0 and %d", phaseCount-1)
	}
	addresses := uint64(1) << (32 - pool.Bits())
	if uint64(maxPrefixes) > addresses {
		return config{}, fmt.Errorf("PREFIX_POOL has %d addresses but %d are required", addresses, maxPrefixes)
	}
	return config{
		localAS:     uint32(localAS),
		peerAS:      uint32(peerAS),
		routerID:    routerID,
		localIP:     localPrefix.Addr().String(),
		peerIP:      peerIP,
		prefixPool:  pool.Masked(),
		base:        base,
		amplitude:   amplitude,
		period:      time.Duration(periodSeconds) * time.Second,
		step:        time.Duration(stepSeconds) * time.Second,
		phaseSlot:   phaseSlot,
		maxPrefixes: maxPrefixes,
	}, nil
}

func targetCount(at time.Time, cfg config) int {
	position := math.Mod(float64(at.UnixNano()), float64(cfg.period)) / float64(cfg.period)
	phase := 2 * math.Pi * float64(cfg.phaseSlot) / phaseCount
	return cfg.base + int(math.Round(float64(cfg.amplitude)*math.Sin(2*math.Pi*position+phase)))
}

func prefixes(pool netip.Prefix, count int) []netip.Prefix {
	result := make([]netip.Prefix, 0, count)
	address := pool.Masked().Addr()
	for range count {
		result = append(result, netip.PrefixFrom(address, 32))
		address = address.Next()
	}
	return result
}

func routeDelta(current, target int) (add, withdraw []int) {
	if target > current {
		for index := current; index < target; index++ {
			add = append(add, index)
		}
	} else {
		for index := current - 1; index >= target; index-- {
			withdraw = append(withdraw, index)
		}
	}
	return add, withdraw
}

func waitForAddress(ctx context.Context, address string) error {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		interfaces, err := net.InterfaceAddrs()
		if err == nil {
			for _, item := range interfaces {
				prefix, parseErr := netip.ParsePrefix(item.String())
				if parseErr == nil && prefix.Addr().String() == address {
					return nil
				}
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func path(prefix netip.Prefix, nextHop string) (*apiutil.Path, error) {
	nlri, err := bgp.NewIPAddrPrefix(prefix)
	if err != nil {
		return nil, err
	}
	next, err := bgp.NewPathAttributeNextHop(netip.MustParseAddr(nextHop))
	if err != nil {
		return nil, err
	}
	return &apiutil.Path{
		Family: bgp.RF_IPv4_UC,
		Nlri:   nlri,
		Attrs:  []bgp.PathAttributeInterface{bgp.NewPathAttributeOrigin(0), next},
	}, nil
}

func reconcile(bgpServer *server.BgpServer, cfg config, routes []netip.Prefix, current, target int) (int, error) {
	add, withdraw := routeDelta(current, target)
	if len(add) > 0 {
		paths := make([]*apiutil.Path, 0, len(add))
		for _, index := range add {
			item, err := path(routes[index], cfg.localIP)
			if err != nil {
				return current, err
			}
			paths = append(paths, item)
		}
		if _, err := bgpServer.AddPath(apiutil.AddPathRequest{Paths: paths}); err != nil {
			return current, err
		}
		current = target
	} else if len(withdraw) > 0 {
		paths := make([]*apiutil.Path, 0, len(withdraw))
		for _, index := range withdraw {
			item, err := path(routes[index], cfg.localIP)
			if err != nil {
				return current, err
			}
			paths = append(paths, item)
		}
		if err := bgpServer.DeletePath(apiutil.DeletePathRequest{Paths: paths}); err != nil {
			return current, err
		}
		current = target
	}
	return current, nil
}

func run(ctx context.Context, cfg config) error {
	if err := waitForAddress(ctx, cfg.localIP); err != nil {
		return fmt.Errorf("wait for local address: %w", err)
	}
	bgpServer := server.NewBgpServer()
	go bgpServer.Serve()
	defer bgpServer.Stop()
	if err := bgpServer.StartBgp(ctx, &api.StartBgpRequest{Global: &api.Global{
		Asn: cfg.localAS, RouterId: cfg.routerID, ListenPort: -1,
	}}); err != nil {
		return fmt.Errorf("start BGP: %w", err)
	}
	if err := bgpServer.AddPeer(ctx, &api.AddPeerRequest{Peer: &api.Peer{
		Conf:      &api.PeerConf{NeighborAddress: cfg.peerIP, PeerAsn: cfg.peerAS},
		Transport: &api.Transport{LocalAddress: cfg.localIP},
		AfiSafis: []*api.AfiSafi{{Config: &api.AfiSafiConfig{
			Family: &api.Family{Afi: api.Family_AFI_IP, Safi: api.Family_SAFI_UNICAST}, Enabled: true,
		}}},
	}}); err != nil {
		return fmt.Errorf("add peer: %w", err)
	}
	routes := prefixes(cfg.prefixPool, cfg.maxPrefixes)
	current := 0
	apply := func(at time.Time) error {
		target := targetCount(at, cfg)
		updated, err := reconcile(bgpServer, cfg, routes, current, target)
		current = updated
		if err == nil {
			log.Printf("advertised_prefixes=%d peer=%s pool=%s", current, cfg.peerIP, cfg.prefixPool)
		}
		return err
	}
	if err := apply(time.Now().UTC()); err != nil {
		return fmt.Errorf("seed routes: %w", err)
	}
	untilBoundary := cfg.step - time.Duration(time.Now().UnixNano())%cfg.step
	timer := time.NewTimer(untilBoundary)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return nil
	case at := <-timer.C:
		if err := apply(at.UTC()); err != nil {
			return fmt.Errorf("update routes: %w", err)
		}
	}
	ticker := time.NewTicker(cfg.step)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case at := <-ticker.C:
			if err := apply(at.UTC()); err != nil {
				return fmt.Errorf("update routes: %w", err)
			}
		}
	}
}

func main() {
	cfg, err := loadConfig()
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, cfg); err != nil {
		log.Fatal(err)
	}
}
